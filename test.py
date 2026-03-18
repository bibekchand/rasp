import RPi.GPIO as GPIO
import time

def test_pin():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    try:
        pin_input = input("Enter the BCM Pin number you want to test (e.g., 17): ")
        test_pin = int(pin_input)

        # Setup the pin
        GPIO.setup(test_pin, GPIO.OUT)
        
        print(f"--- Testing BCM Pin {test_pin} ---")
        print("Press Ctrl+C to stop the test.")

        while True:
            print(f"Pin {test_pin} is ON (High)")
            GPIO.output(test_pin, GPIO.HIGH)
            time.sleep(2)
            
            print(f"Pin {test_pin} is OFF (Low)")
            GPIO.output(test_pin, GPIO.LOW)
            time.sleep(1)

    except ValueError:
        print("Invalid input. Please enter a number.")
    except KeyboardInterrupt:
        print("\nTest stopped by user.")
    finally:
        GPIO.cleanup()
        print("GPIO cleaned up. Goodbye!")

if __name__ == "__main__":
    test_pin()
