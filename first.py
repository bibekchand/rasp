import socket
import threading
import cv2
import struct
import time
import RPi.GPIO as GPIO

# ---------------- CONFIG ----------------
SERVER_IP = "10.101.88.18"  # Your server IP
LANE_ID = "Lane_1"         # Change for this Pi, if controlling different lane

# BCM pins for each lane: Red, Yellow, Green
LIGHTS = {
    "Lane_1": {"R": 17, "Y": 27, "G": 22},
    "Lane_2": {"R": 5, "Y": 6,  "G": 13},
    "Lane_3": {"R": 19,  "Y": 26,  "G": 21},
    "Lane_4": {"R": 16, "Y": 20, "G": 12}
}

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# Setup all pins
for lane in LIGHTS.values():
    for pin in lane.values():
        GPIO.setup(pin, GPIO.OUT)
    GPIO.output(lane["R"], 1)
    GPIO.output(lane["Y"], 0)
    GPIO.output(lane["G"], 0)

current_green = None
lock = threading.Lock()

# ---------------- STREAM VIDEO ----------------


def stream():
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((SERVER_IP, 5555))
            # Send lane ID
            s.sendall(LANE_ID.encode())

            cap = cv2.VideoCapture(0)
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                frame = cv2.resize(frame, (640, 640))
                _, encoded = cv2.imencode(
                    '.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                data = encoded.tobytes()

                # Send frame length + frame data
                s.sendall(struct.pack("Q", len(data)) + data)
                time.sleep(0.08)  # ~12 FPS
        except:
            time.sleep(5)  # Reconnect if server unavailable

# ---------------- CONTROL TRAFFIC LIGHTS ----------------


def control():
    global current_green
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((SERVER_IP, 6666))
            while True:
                cmd = s.recv(1024).decode().strip()
                if not cmd or cmd == current_green:
                    continue

                with lock:
                    # Turn previous green to red via yellow
                    if current_green:
                        prev = LIGHTS[current_green]
                        GPIO.output(prev["G"], 0)
                        GPIO.output(prev["Y"], 1)
                        time.sleep(2)
                        GPIO.output(prev["Y"], 0)
                        GPIO.output(prev["R"], 1)

                    # Turn new lane green
                    new = LIGHTS[cmd]
                    GPIO.output(new["R"], 0)
                    GPIO.output(new["G"], 1)
                    current_green = cmd
        except:
            time.sleep(5)  # Reconnect if server unavailable


# ---------------- MAIN ----------------
if __name__ == "__main__":
    threading.Thread(target=stream, daemon=True).start()
    control()

