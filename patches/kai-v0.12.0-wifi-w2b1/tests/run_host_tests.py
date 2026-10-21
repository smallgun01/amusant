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
    main_cpp = (source / "main.cpp").read_text(encoding="utf-8")
    owner_start = main_cpp.find("static void serviceOwnerSetupBoot() {")
    owner_end = main_cpp.find("// ---- buttons", owner_start)
    if owner_start < 0 or owner_end <= owner_start:
        raise SystemExit("The owner-setup service loop could not be isolated for its fallback timing check.")
    owner_setup = main_cpp[owner_start:owner_end]
    service_at = owner_setup.find("serviceWifiBleProvisioning();")
    drain_now_at = owner_setup.find("const uint32_t drainNow = millis();", service_at)
    fallback_at = owner_setup.find("managerDrainFallbackExpired(drainNow,", drain_now_at)
    if service_at < 0 or drain_now_at <= service_at or fallback_at <= drain_now_at:
        raise SystemExit("The fallback must use a fresh millis() snapshot taken after BLE service can set the return timestamp.")
    if "managerDrainFallbackExpired(now," in owner_setup:
        raise SystemExit("The pre-service loop timestamp must not drive the BLE manager-drain fallback.")
    required = (source / "wifi_config.cpp", source / "wifi_ble_flow.cpp",
                source / "wifi_setup_intent.h", source / "wifi_provisioning_commit_gate.h",
                source / "wifi_provisioning_event_queue.h",
                source / "wifi_provisioning_lifecycle.h")
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("Apply the W2b-1 patch after the pinned W1.5 + CJK layers; missing: " + ", ".join(missing))

    native = (source / "wifi_ble_provisioning.cpp").read_text(encoding="utf-8")
    service_body = native[native.find("void service("):]
    if "serviceQueueStep(queued, nowMs, !managerActive)" not in service_body:
        raise SystemExit("The native service must use the tested service-level overflow/terminal-event drain.")
    if "network_prov_mgr_deinit()" in service_body[service_body.find("case ProviderEvent::ServiceEnded:"):]:
        raise SystemExit("Arduino-ESP32 owns manager deinit on NETWORK_PROV_END; the native loop must not call it.")
    if native.count("network_prov_mgr_deinit()") != 1:
        raise SystemExit("Only the guarded provisioning-start failure path may deinitialize the manager directly.")

    event_queue = (source / "wifi_provisioning_event_queue.h").read_text(encoding="utf-8")
    if "serviceEndedPending_" not in event_queue or "deinitializedPending_" not in event_queue:
        raise SystemExit("END and DEINIT must use the dedicated lossless terminal-event path.")
    if "ServiceStep serviceStep(" not in event_queue or "manager.onServiceEnded()" not in event_queue or "manager.onDeinitialized()" not in event_queue:
        raise SystemExit("The tested service step must preserve overflowed terminal events and update lifecycle state.")
    if "CONFIG_NETWORK_PROV_AUTOSTOP_TIMEOUT" not in native:
        raise SystemExit("The compiled SDK auto-stop timeout must drive the setup drain fallback.")
    if "timer_start_observed_ms" in native or "autostop_end_after_success_ms" in native:
        raise SystemExit("Diagnostics must not imply that the SDK's internal timer start or firing was observed.")
    if "missing_END after_CRED_SUCCESS_callback_ms=" not in native:
        raise SystemExit("Diagnostics must report a missing END without claiming the SDK timer failed.")
    if "AMUSANT_WIFI_BLE_DIAGNOSTICS" not in native:
        raise SystemExit("The opt-in, non-secret BLE teardown diagnostics are missing.")
    diag_start = native.find("void reportProviderEventCounters()")
    diag_end = native.find("#endif", diag_start)
    diagnostic = native[diag_start:diag_end].lower() if diag_start >= 0 and diag_end > diag_start else ""
    if not diagnostic:
        raise SystemExit("The gated BLE teardown diagnostic implementation is missing.")
    if any(secret_field in diagnostic for secret_field in ("ssid", "password", "proofofpossession", "servicename")):
        raise SystemExit("BLE teardown diagnostics must not print credentials, PoP, or the provisioning service name.")
    platformio = (args.firmware_root.resolve() / "platformio.ini").read_text(encoding="utf-8")
    if "[env:sticks3_ble_diag]" not in platformio:
        raise SystemExit("The reproducible diagnostic PlatformIO environment is missing.")

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
    print("W2b-1 offline lifecycle ownership, service-level terminal-event overflow/order, cancellation, dynamic SDK timeout, commit gate, and fake-NVS tests passed.", flush=True)


if __name__ == "__main__":
    main()

