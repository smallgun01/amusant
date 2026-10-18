#!/usr/bin/env python3
"""Run offline host checks for the opt-in CJK diagnostics."""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


TESTS = Path(__file__).resolve().parent


def compile_and_run(command, executable):
    subprocess.run(command, check=True)
    subprocess.run([str(executable)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware-root", required=True, type=Path)
    parser.add_argument("--wifi-tests", required=True, type=Path,
                        help="W1.5 tests directory, providing Preferences.h fake")
    parser.add_argument("--compiler", default="g++")
    args = parser.parse_args()
    source = args.firmware_root.resolve() / "src"
    wifi_tests = args.wifi_tests.resolve()
    for required in (source / "wifi_config.cpp", source / "wifi_console.cpp",
                     source / "jellii_cjk_diagnostic.h"):
        if not required.is_file():
            raise SystemExit("Apply the W1.5 + CJK diagnostic patches first; missing: " + str(required))

    diagnostic_header = source / "jellii_cjk_diagnostic.h"
    header_text = diagnostic_header.read_text(encoding="utf-8")
    if not header_text.endswith("\n"):
        raise SystemExit("jellii_cjk_diagnostic.h must end with a newline")
    for line_number, line in enumerate(header_text.splitlines(), 1):
        if line.endswith((" ", "\t")):
            raise SystemExit(
                "jellii_cjk_diagnostic.h has trailing whitespace on line " +
                str(line_number)
            )

    with tempfile.TemporaryDirectory(prefix="cjk-diagnostic-host-") as temporary:
        temp = Path(temporary)
        scalar_test = temp / ("scalar.exe" if sys.platform == "win32" else "scalar")
        compile_and_run([
            args.compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
            "-I", str(source), str(TESTS / "test_cjk_diagnostic.cpp"),
            "-o", str(scalar_test),
        ], scalar_test)

        for enabled in (False, True):
            suffix = "enabled" if enabled else "disabled"
            command_test = temp / ("console-" + suffix + (".exe" if sys.platform == "win32" else ""))
            flags = ["-DJELLII_CJK_DIAGNOSTICS"] if enabled else []
            compile_and_run([
                args.compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                *flags,
                "-I", str(wifi_tests / "fakes"),
                "-I", str(source),
                str(source / "wifi_config.cpp"),
                str(source / "wifi_console.cpp"),
                str(TESTS / "test_cjk_console.cpp"),
                "-o", str(command_test),
            ], command_test)

    print("CJK Unicode scanner and opt-in USB command host checks passed.", flush=True)


if __name__ == "__main__":
    main()
