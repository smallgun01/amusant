import unittest
import subprocess
import os
from pathlib import Path
from preflash import validate

GOOD = """#pragma once
#define RELAY_HOST "jellii.k100.uk"
#define RELAY_TLS_PORT 443
#define KAI_PSK_IDENTITY "fixture-device"
#define KAI_PSK_HEX "d24e7b19c06f83a5e71d493b6a2085cfb73e916c4d0528a1f6c947be320a5d18"
#define KAI_DEVICE_TOKEN "fixture-token-123"
"""

class PreflashTests(unittest.TestCase):
    def test_good_synthetic(self):
        self.assertTrue(validate(GOOD))

    def test_compile_guard(self):
        patch = (Path(__file__).resolve().parents[1] / "amusant-sticks3-base-v0.12.0.patch").read_text()
        lines = patch.splitlines()
        index = next(i for i, line in enumerate(lines) if line.startswith("+#if !defined(KAI_PSK_HEX)"))
        guard = "\n".join(line[1:] for line in lines[index:index + 6]) + "\n"
        self.assertIn("static_assert(sizeof(KAI_PSK_HEX) == 65", guard)
        values = {"KAI_PSK_HEX": "a" * 64, "KAI_PSK_IDENTITY": "id", "KAI_DEVICE_TOKEN": "token"}
        cases = [(values, True)]
        for name in values:
            cases.append(({k: v for k, v in values.items() if k != name}, False))
            cases.append((dict(values, **{name: ""}), False))
        cases.append((dict(values, KAI_PSK_HEX="a" * 63), False))
        for definitions, expected in cases:
            code = "".join('#define ' + name + ' "' + value + '"\n' for name, value in definitions.items()) + guard
            result = subprocess.run([os.environ.get("CXX", "c++"), "-x", "c++", "-std=c++11", "-fsyntax-only", "-"], input=code, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.assertEqual(result.returncode == 0, expected)

    def test_whitespace_and_duplicate_fields(self):
        for name, value in (("KAI_PSK_IDENTITY", "fixture-device"), ("KAI_DEVICE_TOKEN", "fixture-token-123")):
            for invalid in (" ", "\t", " fixture ", "fixture token"):
                with self.subTest(name=name, case="whitespace"):
                    self.assertFalse(validate(GOOD.replace('"' + value + '"', '"' + invalid + '"')))
        for name, value in (("RELAY_HOST", '"jellii.k100.uk"'), ("RELAY_TLS_PORT", "443"), ("KAI_PSK_IDENTITY", '"fixture-device"'), ("KAI_PSK_HEX", '"' + "a" * 64 + '"'), ("KAI_DEVICE_TOKEN", '"fixture-token-123"')):
            with self.subTest(name=name, case="duplicate"):
                self.assertFalse(validate(GOOD + "#define " + name + " " + value + "\n"))

    def test_bad_configs(self):
        cases = [GOOD.replace("jellii.k100.uk", "192.0.2.1"), GOOD.replace("443", "80"), GOOD.replace("fixture-device", ""), GOOD.replace("fixture-device", "kai-xxxx"), GOOD.replace("fixture-token-123", "change-me"), GOOD.replace("fixture-token-123", ""), GOOD.replace("d24e7b19c06f83a5e71d493b6a2085cfb73e916c4d0528a1f6c947be320a5d18", "0" * 64), GOOD.replace("d24e7b19c06f83a5e71d493b6a2085cfb73e916c4d0528a1f6c947be320a5d18", "deadbeef" * 8), GOOD.replace("d24e7b19c06f83a5e71d493b6a2085cfb73e916c4d0528a1f6c947be320a5d18", "z" * 64), GOOD.replace("d24e7b19c06f83a5e71d493b6a2085cfb73e916c4d0528a1f6c947be320a5d18", "a" * 63), GOOD + "#undef KAI_PSK_HEX\n", GOOD.replace("#define KAI_PSK_HEX", "// #define KAI_PSK_HEX"), GOOD + "#define RELAY_TLS_PORT 443\n"]
        for case in cases:
            with self.subTest(case=cases.index(case)):
                self.assertFalse(validate(case))

if __name__ == "__main__":
    unittest.main()
