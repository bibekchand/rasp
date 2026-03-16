import logging
import socket
import threading
import time
from typing import Optional

import cv2

from config import (
    ALL_RED_SECONDS,
    CAMERA_FPS,
    CONTROLLER_ID,
    CONTROL_PORT,
    FRAME_HEIGHT,
    FRAME_SEND_INTERVAL_SECONDS,
    FRAME_WIDTH,
    JPEG_QUALITY,
    LANE_ORDER,
    PI_CAMERA_LANE_ID,
    PI_CAMERA_SOURCE,
    RECONNECT_DELAY_SECONDS,
    SERVER_CONNECT_HOST,
    SOCKET_TIMEOUT_SECONDS,
    VIDEO_PORT,
    YELLOW_SECONDS,
)
from protocol import (
    parse_set_green,
    recv_line,
    send_controller_hello,
    send_frame,
    send_lane_hello,
)

try:
    import RPi.GPIO as GPIO
except ImportError:
    class MockGPIO:
        BCM = "BCM"
        OUT = "OUT"

        def setmode(self, mode) -> None:
            print(f"[MockGPIO] setmode({mode})")

        def setwarnings(self, value) -> None:
            print(f"[MockGPIO] setwarnings({value})")

        def setup(self, pin, mode) -> None:
            print(f"[MockGPIO] setup(pin={pin}, mode={mode})")

        def output(self, pin, value) -> None:
            print(f"[MockGPIO] output(pin={pin}, value={value})")

        def cleanup(self) -> None:
            print("[MockGPIO] cleanup()")

    GPIO = MockGPIO()


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(threadName)s - %(message)s",
)
LOGGER = logging.getLogger("traffic-controller")


LIGHTS = {
    "Lane_1": {"R": 17, "Y": 27, "G": 22},
    "Lane_2": {"R": 10, "Y": 9, "G": 11},
    "Lane_3": {"R": 0, "Y": 5, "G": 6},
    "Lane_4": {"R": 13, "Y": 19, "G": 26},
}


class IntersectionController:
    def __init__(self) -> None:
        self.current_green: Optional[str] = None
        self.current_deadline = 0.0

    def setup_gpio(self) -> None:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        for lane_id in LANE_ORDER:
            pins = LIGHTS[lane_id]
            for pin in pins.values():
                GPIO.setup(pin, GPIO.OUT)

        self.all_red()

    def cleanup(self) -> None:
        self.all_red()
        GPIO.cleanup()

    def all_red(self) -> None:
        for lane_id in LANE_ORDER:
            pins = LIGHTS[lane_id]
            GPIO.output(pins["R"], 1)
            GPIO.output(pins["Y"], 0)
            GPIO.output(pins["G"], 0)

    def apply_green(self, lane_id: str) -> None:
        if lane_id not in LIGHTS:
            raise ValueError(f"unknown lane id: {lane_id}")

        if self.current_green == lane_id:
            LOGGER.info("Keeping %s green", lane_id)
            return

        if self.current_green:
            old_pins = LIGHTS[self.current_green]
            GPIO.output(old_pins["G"], 0)
            GPIO.output(old_pins["Y"], 1)
            LOGGER.info("Transitioning %s to yellow for %ss", self.current_green, YELLOW_SECONDS)
            time.sleep(YELLOW_SECONDS)
            GPIO.output(old_pins["Y"], 0)
            GPIO.output(old_pins["R"], 1)

        LOGGER.info("All-red safety delay for %ss", ALL_RED_SECONDS)
        self.all_red()
        time.sleep(ALL_RED_SECONDS)

        new_pins = LIGHTS[lane_id]
        GPIO.output(new_pins["R"], 0)
        GPIO.output(new_pins["Y"], 0)
        GPIO.output(new_pins["G"], 1)
        self.current_green = lane_id
        LOGGER.info("%s is now green", lane_id)

    def fail_safe_if_expired(self) -> None:
        if self.current_green and time.monotonic() >= self.current_deadline:
            LOGGER.warning("Command timeout reached; switching intersection to all-red")
            self.all_red()
            self.current_green = None


def stream_pi_camera(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        sock = None
        cap = None
        try:
            LOGGER.info("Connecting Pi camera %s to %s:%s", PI_CAMERA_LANE_ID, SERVER_CONNECT_HOST, VIDEO_PORT)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(SOCKET_TIMEOUT_SECONDS)
            sock.connect((SERVER_CONNECT_HOST, VIDEO_PORT))
            send_lane_hello(sock, PI_CAMERA_LANE_ID)

            cap = cv2.VideoCapture(PI_CAMERA_SOURCE)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
            cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS)
            if not cap.isOpened():
                raise RuntimeError(f"failed to open Pi camera source {PI_CAMERA_SOURCE!r}")

            while not stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError("Pi camera frame read failed")

                frame = cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT))
                send_frame(sock, frame, JPEG_QUALITY)
                time.sleep(FRAME_SEND_INTERVAL_SECONDS)
        except (ConnectionError, OSError, RuntimeError, ValueError) as exc:
            LOGGER.warning("Pi camera stream error: %s", exc)
            time.sleep(RECONNECT_DELAY_SECONDS)
        finally:
            if cap is not None:
                cap.release()
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass


def run_controller() -> None:
    controller = IntersectionController()
    controller.setup_gpio()
    stop_event = threading.Event()
    camera_thread = threading.Thread(
        target=stream_pi_camera,
        args=(stop_event,),
        name="pi-camera-stream",
        daemon=True,
    )
    camera_thread.start()

    try:
        while True:
            sock = None
            try:
                LOGGER.info("Connecting to server %s:%s", SERVER_CONNECT_HOST, CONTROL_PORT)
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(SOCKET_TIMEOUT_SECONDS)
                sock.connect((SERVER_CONNECT_HOST, CONTROL_PORT))
                send_controller_hello(sock, CONTROLLER_ID)
                LOGGER.info("Connected to scheduler")

                while True:
                    try:
                        line = recv_line(sock)
                    except socket.timeout:
                        controller.fail_safe_if_expired()
                        continue

                    lane_id, green_seconds = parse_set_green(line)
                    if lane_id not in LIGHTS:
                        raise ValueError(f"server sent unknown lane id: {lane_id}")

                    controller.apply_green(lane_id)
                    controller.current_deadline = time.monotonic() + green_seconds
                    LOGGER.info("Green hold set to %ss for %s", green_seconds, lane_id)

            except (ConnectionError, OSError, ValueError) as exc:
                LOGGER.warning("Controller connection error: %s", exc)
                controller.all_red()
                controller.current_green = None
                time.sleep(RECONNECT_DELAY_SECONDS)
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
    except KeyboardInterrupt:
        LOGGER.info("Controller shutdown requested")
    finally:
        stop_event.set()
        camera_thread.join(timeout=1.0)
        controller.cleanup()


if __name__ == "__main__":
    run_controller()

