#!/usr/bin/env python3
"""Compile and independently decode a fake-only StickS3 BLE provisioning QR."""

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path


TESTS = Path(__file__).resolve().parent
FAKE_SERVICE = "PROV_ABCDEF"
FAKE_POP = "0123456789ABCDEFGHJKMNPQRS"
EXPECTED = (
    '{"ver":"v1","name":"PROV_ABCDEF","pop":"'
    '0123456789ABCDEFGHJKMNPQRS","transport":"ble","security":1}'
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware-root", required=True, type=Path)
    parser.add_argument("--compiler", default="g++")
    parser.add_argument("--c-compiler", default="gcc")
    args = parser.parse_args()
    source = args.firmware_root.resolve() / "src"
    decoder = shutil.which("zbarimg")
    if decoder is None:
        raise SystemExit("Install zbar-tools to run the independent offline QR decode.")
    required = (
        source / "wifi_ble_qr.cpp",
        source / "wifi_ble_qr.h",
        source / "third_party/nayuki/qrcodegen.c",
        source / "third_party/nayuki/qrcodegen.h",
        source / "main.cpp",
        source / "face.cpp",
        source / "wifi_ble_provisioning.cpp",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("Apply the W2b-QR patch after the W2b-1 layer; missing: " + ", ".join(missing))

    with tempfile.TemporaryDirectory(prefix="wifi-w2bqr-host-") as temporary:
        temporary = Path(temporary)
        library_object = temporary / "qrcodegen.o"
        executable = temporary / "wifi_w2bqr_tests"
        image = temporary / "fake-only.pgm"
        subprocess.run(
            [args.c_compiler, "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror",
             "-c", str(source / "third_party/nayuki/qrcodegen.c"), "-o", str(library_object)],
            check=True,
        )
        subprocess.run(
            [args.compiler, "-std=c++11", "-O2", "-Wall", "-Wextra", "-Werror",
             "-I", str(source), str(source / "wifi_ble_qr.cpp"), str(TESTS / "test_wifi_ble_qr.cpp"),
             str(library_object), "-o", str(executable)],
            check=True,
        )
        subprocess.run([str(executable), str(image)], check=True)
        decoded = subprocess.run([decoder, "--quiet", "--raw", str(image)],
                                 check=False, capture_output=True)
        if decoded.returncode != 0 or decoded.stdout.rstrip(b"\r\n") != EXPECTED.encode("ascii"):
            raise SystemExit("Independent zbar decode did not match the exact fake v1/Security-1 vector.")

    print("Independent zbar decode matched the exact fake-only v1 / Security 1 QR vector.")


if __name__ == "__main__":
    main()
