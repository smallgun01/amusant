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
    assert "syncOwnerSetupProvisioningBrightness()" in owner_loop
    assert "M5.BtnB.wasClicked()" in owner_loop
    normal_buttons = function_body(main_cpp, r"static void handleButtons\(\)")
    assert "toggleProvisioningPage" not in normal_buttons

    assert main_cpp.count("face::showProvisioningQr(") == 1
    assert "provisioningServiceName()" in main_cpp
    assert re.search(
        r"face::showProvisioningQr\(.*?\);\s*syncOwnerSetupProvisioningBrightness\(\);",
        main_cpp,
        re.DOTALL,
    ) is not None
    first_boot = re.search(r"else if \(startBleForFirstBoot\)\s*\{(.*?)\n\s*\}", main_cpp, re.DOTALL)
    assert first_boot is not None and "face::showProvisioning(" in first_boot.group(1)
    assert "face::showProvisioningQr(" not in first_boot.group(1)

    assert "static constexpr uint8_t BRIGHTNESS_AWAKE = 90;" in main_cpp
    assert "static constexpr uint8_t BRIGHTNESS_PROVISIONING_QR = 45;" in main_cpp
    sync_brightness = function_body(main_cpp, r"static void syncOwnerSetupProvisioningBrightness\(\)")
    assert "face::provisioningQrPage()" in sync_brightness
    assert "BRIGHTNESS_PROVISIONING_QR" in sync_brightness and "BRIGHTNESS_AWAKE" in sync_brightness
    clear_view = function_body(main_cpp, r"static void clearProvisioningView\(\)")
    assert "face::clearProvisioning()" in clear_view
    assert "if (ownerSetupBootActive) backlight(BRIGHTNESS_AWAKE);" in clear_view

    owner_exit = function_body(main_cpp, r"static void requestOwnerSetupExit\(const char\* status\)")
    assert owner_exit.index("clearProvisioningView()") < owner_exit.index("wifi_ble_provisioning::stop()")
    assert re.search(
        r"if \(ownerSetupBootActive\)\s*\{\s*clearProvisioningView\(\);\s*"
        r"markOwnerSetupReturn\(\"Wi-Fi connected; saved\"\);",
        main_cpp,
        re.DOTALL,
    ) is not None

    draw = function_body(face_cpp, r"void drawProvisioning\(\)")
    assert "wifi_ble_qr::darkModule" in draw
    assert "wifi_ble_qr::begin" not in draw
    qr_branch = draw.split(
        "if (provisioningPage.qrPage() && wifi_ble_qr::active()) {", 1
    )[1].split("\n  }\n\n", 1)[0]
    assert "canvas.fillSprite(white);" in qr_branch
    assert "drawString" not in qr_branch
    assert "kHeaderTop" not in draw and "kFooterTop" not in draw
    qr_source = (source / "wifi_ble_qr.cpp").read_text(encoding="utf-8")
    assert "qrcodegen_Ecc_LOW, kQrVersion, kQrVersion, qrcodegen_Mask_AUTO, false" in qr_source
    qr_header = (source / "wifi_ble_qr.h").read_text(encoding="utf-8")
    assert "constexpr int kQrVersion = 5;" in qr_header
    assert "constexpr int kPixelsPerModule = 3;" in qr_header
    assert "constexpr int kQuietZoneModules = 4;" in qr_header
    assert "constexpr int kQrPixelSize = (kModuleCount + 2 * kQuietZoneModules) * kPixelsPerModule;" in qr_header
    assert "constexpr int kQrLeft = (kCanvasWidth - kQrPixelSize) / 2;" in qr_header
    assert "constexpr int kQrTop = (kCanvasHeight - kQrPixelSize) / 2;" in qr_header
    assert "bool provisioningQrPage();" in (source / "face.h").read_text(encoding="utf-8")
    clear = function_body(face_cpp, r"void clearProvisioning\(\)")
    assert "wifi_ble_qr::clear()" in clear and "provisioningPage.clear()" in clear
    assert "const char* provisioningServiceName();" in provision_h
    assert "const char* provisioningServiceName() {" in provision_cpp
    assert "managerLifecycle.state() != State::Running || !session.active()" in provision_cpp
    print("Owner-setup-only QR controls and cleanup wiring checks passed.")


if __name__ == "__main__":
    main()
