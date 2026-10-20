#!/usr/bin/env python3
"""Run offline host tests for configured Wi-Fi re-provisioning policy."""

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path


TESTS = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware-root", required=True, type=Path)
    parser.add_argument("--wifi-tests", required=True, type=Path,
                        help="W1.5 tests directory, providing the fake Preferences.h")
    parser.add_argument("--compiler", default="g++")
    args = parser.parse_args()
    source = args.firmware_root.resolve() / "src"
    required = (source / "wifi_config.cpp", source / "wifi_ble_flow.cpp",
                source / "wifi_setup_intent.h", source / "wifi_provisioning_commit_gate.h",
                source / "wifi_provisioning_lifecycle.h")
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("Apply the W2b-1 patch after the pinned W1.5 + CJK layers; missing: " + ", ".join(missing))

    native = (source / "wifi_ble_provisioning.cpp").read_text(encoding="utf-8")
    service_end = re.search(
        r"case ProviderEvent::ServiceEnded:(.*?)case ProviderEvent::Deinitialized:", native, re.DOTALL
    )
    if service_end is None or "managerLifecycle.onServiceEnded()" not in service_end.group(1):
        raise SystemExit("The native service must record NETWORK_PROV_END before waiting for external DEINIT.")
    if "network_prov_mgr_deinit()" in service_end.group(1):
        raise SystemExit("Arduino-ESP32 owns manager deinit on NETWORK_PROV_END; the native loop must not call it.")
    if native.count("network_prov_mgr_deinit()") != 1:
        raise SystemExit("Only the guarded provisioning-start failure path may deinitialize the manager directly.")

    fake_preferences = args.wifi_tests.resolve() / "fakes"
    with tempfile.TemporaryDirectory(prefix="wifi-w2b1-host-") as temporary:
        executable = Path(temporary) / ("wifi_w2b1_tests.exe" if sys.platform == "win32" else "wifi_w2b1_tests")
        command = [
            args.compiler,
            "-std=c++11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(fake_preferences),
            "-I",
            str(source),
            str(source / "wifi_config.cpp"),
            str(source / "wifi_ble_flow.cpp"),
            str(TESTS / "test_wifi_w2b1.cpp"),
            "-o",
            str(executable),
        ]
        subprocess.run(command, check=True)
        subprocess.run([str(executable)], check=True)
    print("W2b-1 offline lifecycle ownership, event-order, cancellation, commit gate, timeout, and fake-NVS tests passed.", flush=True)


if __name__ == "__main__":
    main()

