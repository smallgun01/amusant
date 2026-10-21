#!/usr/bin/env python3
"""Enforce owner-setup-only display wiring and session cleanup."""

import argparse
import re
from pathlib import Path


def function_body(source, signature):
    match = re.search(signature + r"\s*\{(.*?)(?=\n\})", source, re.DOTALL)
    if match is None:
        raise SystemExit("Required firmware function was not found: " + signature)
    return match.group(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware-root", required=True, type=Path)
    args = parser.parse_args()
    source = args.firmware_root.resolve() / "src"
    main_cpp = (source / "main.cpp").read_text(encoding="utf-8")
    face_cpp = (source / "face.cpp").read_text(encoding="utf-8")
    provision_cpp = (source / "wifi_ble_provisioning.cpp").read_text(encoding="utf-8")
    provision_h = (source / "wifi_ble_provisioning.h").read_text(encoding="utf-8")

    owner_loop = function_body(main_cpp, r"static void serviceOwnerSetupBoot\(\)")
    assert "M5.BtnA.wasClicked()" in owner_loop
    assert "face::toggleProvisioningPage()" in owner_loop
    assert "M5.BtnB.wasClicked()" in owner_loop
    normal_buttons = function_body(main_cpp, r"static void handleButtons\(\)")
    assert "toggleProvisioningPage" not in normal_buttons

    assert main_cpp.count("face::showProvisioningQr(") == 1
    assert "provisioningServiceName()" in main_cpp
    first_boot = re.search(r"else if \(startBleForFirstBoot\)\s*\{(.*?)\n\s*\}", main_cpp, re.DOTALL)
    assert first_boot is not None and "face::showProvisioning(" in first_boot.group(1)
    assert "face::showProvisioningQr(" not in first_boot.group(1)

    owner_exit = function_body(main_cpp, r"static void requestOwnerSetupExit\(const char\* status\)")
    assert owner_exit.index("face::clearProvisioning()") < owner_exit.index("wifi_ble_provisioning::stop()")
    assert re.search(
        r"if \(ownerSetupBootActive\)\s*\{\s*face::clearProvisioning\(\);\s*"
        r"markOwnerSetupReturn\(\"Wi-Fi connected; saved\"\);",
        main_cpp,
        re.DOTALL,
    ) is not None

    draw = function_body(face_cpp, r"void drawProvisioning\(\)")
    assert "wifi_ble_qr::darkModule" in draw
    assert "wifi_ble_qr::begin" not in draw
    clear = function_body(face_cpp, r"void clearProvisioning\(\)")
    assert "wifi_ble_qr::clear()" in clear and "provisioningPage.clear()" in clear
    assert "const char* provisioningServiceName();" in provision_h
    assert "const char* provisioningServiceName() {" in provision_cpp
    assert "managerLifecycle.state() != State::Running || !session.active()" in provision_cpp
    print("Owner-setup-only QR controls and cleanup wiring checks passed.")


if __name__ == "__main__":
    main()
