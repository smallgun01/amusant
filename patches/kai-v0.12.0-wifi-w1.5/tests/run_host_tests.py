#!/usr/bin/env python3
"""Run offline C++ firmware-module tests and provisioning-helper tests."""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


TESTS = Path(__file__).resolve().parent
PACKAGE = TESTS.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware-root", required=True, type=Path, help="Kai firmware directory after Jellii + W1 + W1.5 patches")
    parser.add_argument("--compiler", default="g++", help="Host C++ compiler (default: g++)")
    args = parser.parse_args()
    source = args.firmware_root.resolve() / "src"
    required = (source / "wifi_config.cpp", source / "wifi_console.cpp", source / "wifi_ble_flow.cpp")
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("Apply the W1 patch to Kai first; missing: " + ", ".join(missing))

    with tempfile.TemporaryDirectory(prefix="wifi-w1-host-tests-") as temporary:
        executable = Path(temporary) / ("wifi_w1_tests.exe" if sys.platform == "win32" else "wifi_w1_tests")
        command = [
            args.compiler,
            "-std=c++11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(TESTS / "fakes"),
            "-I",
            str(source),
            str(source / "wifi_config.cpp"),
            str(source / "wifi_console.cpp"),
            str(source / "wifi_ble_flow.cpp"),
            str(TESTS / "test_wifi_w1.cpp"),
            "-o",
            str(executable),
        ]
        subprocess.run(command, check=True)
        subprocess.run([str(executable)], check=True)
    print("C++ fake-NVS, parser, power-policy, and BLE provisioning host tests passed.", flush=True)

    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(TESTS), "-p", "test_provision_wifi.py"],
        check=True,
    )
    print("Python provisioning-helper host tests passed.", flush=True)


if __name__ == "__main__":
    main()
