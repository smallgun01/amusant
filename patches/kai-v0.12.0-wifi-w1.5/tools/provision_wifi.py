#!/usr/bin/env python3
"""Set, clear, or inspect one Wi-Fi profile over the local USB serial port."""

import argparse
import getpass
import re
import time
import warnings


def build_set_frame(ssid, password):
    try:
        ssid_bytes = ssid.encode("utf-8")
        password_bytes = password.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("Use a UTF-8 SSID and a printable ASCII Wi-Fi password.") from exc

    if not 1 <= len(ssid_bytes) <= 32:
        raise ValueError("SSID must contain 1 to 32 UTF-8 bytes.")
    if any(byte < 0x20 or byte == 0x7F for byte in ssid_bytes):
        raise ValueError("SSID contains unsupported control characters.")
    if not 8 <= len(password_bytes) <= 63:
        raise ValueError("Wi-Fi password must contain 8 to 63 printable ASCII bytes.")
    if any(byte < 0x20 or byte > 0x7E for byte in password_bytes):
        raise ValueError("Wi-Fi password must contain printable ASCII characters only.")

    header = f"WIFI SET {len(ssid_bytes)} {len(password_bytes)}\n".encode("ascii")
    return header, ssid_bytes + password_bytes


def wait_for_response(port, expected, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = port.readline().decode("ascii", "ignore").strip()
        if line in expected:
            return line
        if line.startswith("WIFI ERR "):
            raise RuntimeError("Device rejected the USB Wi-Fi request.")
    raise TimeoutError("No matching response from the device over USB serial.")


def is_memory_response(line):
    return re.fullmatch(r"WIFI MEM [0-9]+ [0-9]+", line) is not None


def establish_console(port):
    responses = {
        "WIFI OK CONFIGURED CONNECTED",
        "WIFI OK CONFIGURED OFFLINE",
        "WIFI OK UNCONFIGURED OFFLINE",
        "WIFI OK INVALID OFFLINE",
    }
    for _ in range(5):
        port.write(b"WIFI STATUS\n")
        port.flush()
        deadline = time.monotonic() + 1.2
        status = None
        memory = None
        while time.monotonic() < deadline:
            line = port.readline().decode("ascii", "ignore").strip()
            if line in responses:
                status = line
            elif is_memory_response(line):
                memory = line
            elif line.startswith("WIFI ERR "):
                raise RuntimeError("Device rejected the USB Wi-Fi request.")
            if status is not None and memory is not None:
                return status, memory
    raise TimeoutError("The device did not answer on the selected USB serial port.")


def read_password():
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass("Wi-Fi password (input hidden): ")
        except getpass.GetPassWarning as exc:
            raise RuntimeError("This terminal cannot hide password input; no credentials were sent.") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="USB serial port, for example COM6 or /dev/ttyACM0")
    parser.add_argument("action", choices=("status", "set", "clear"))
    args = parser.parse_args()

    try:
        import serial
    except ImportError as exc:
        raise SystemExit("Install pyserial 3.5 in this local Python environment first.") from exc

    try:
        with serial.Serial(args.port, 115200, timeout=0.2, write_timeout=2) as port:
            status, memory = establish_console(port)

            if args.action == "status":
                print(status)
                print(memory)
                return
            if args.action == "clear":
                port.write(b"WIFI CLEAR\n")
                port.flush()
                print(wait_for_response(port, {"WIFI OK CLEARED"}, 5))
                return

            ssid = input("Wi-Fi SSID: ")
            password = read_password()
            header, payload = build_set_frame(ssid, password)
            port.write(header)
            port.flush()
            wait_for_response(port, {"WIFI READY"}, 5)
            port.write(payload)
            port.flush()
            print(wait_for_response(port, {"WIFI OK SAVED"}, 5))
            print("Profile saved. The device will now try Wi-Fi and the existing Relay.")
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
