import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "tools" / "provision_wifi.py"
SPEC = importlib.util.spec_from_file_location("provision_wifi", SCRIPT)
provision_wifi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provision_wifi)


class ProvisioningFrameTests(unittest.TestCase):
    def test_frame_is_length_bounded_and_raw(self):
        header, payload = provision_wifi.build_set_frame("lab-2g", "x" * 8)
        self.assertEqual(header, b"WIFI SET 6 8\n")
        self.assertEqual(payload, b"lab-2g" + b"x" * 8)

    def test_ssid_uses_utf8_byte_length(self):
        header, payload = provision_wifi.build_set_frame("網路", "x" * 8)
        self.assertEqual(header, b"WIFI SET 6 8\n")
        self.assertEqual(payload[:6], "網路".encode("utf-8"))

    def test_rejects_empty_oversize_or_malformed_values(self):
        for ssid, password in (
            ("", "x" * 8),
            ("s" * 33, "x" * 8),
            ("ssid", "x" * 7),
            ("ssid", "x" * 64),
            ("ssid", "bad\npass"),
        ):
            with self.subTest(ssid_length=len(ssid), password_length=len(password)):
                with self.assertRaises(ValueError):
                    provision_wifi.build_set_frame(ssid, password)

    def test_set_sends_password_only_in_usb_payload_and_never_prints_it(self):
        password = "x" * 8
        output = io.StringIO()
        writes = []

        class FakeSerial:
            def __init__(self, *args, **kwargs):
                self.responses = [
                    b"WIFI OK UNCONFIGURED OFFLINE\n",
                    b"WIFI MEM 120000 2000000\n",
                    b"WIFI READY\n",
                    b"WIFI OK SAVED\n",
                ]

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def write(self, data):
                writes.append(data)
                return len(data)

            def flush(self):
                pass

            def readline(self):
                return self.responses.pop(0)

        fake_serial = SimpleNamespace(Serial=FakeSerial)
        with patch.object(sys, "argv", ["provision_wifi.py", "--port", "COM1", "set"]):
            with patch.dict(sys.modules, {"serial": fake_serial}):
                with patch("builtins.input", return_value="lab-2g"):
                    with patch.object(provision_wifi, "read_password", return_value=password):
                        with redirect_stdout(output):
                            provision_wifi.main()

        self.assertEqual(writes[0], b"WIFI STATUS\n")
        self.assertEqual(writes[1], b"WIFI SET 6 8\n")
        self.assertEqual(writes[2], b"lab-2g" + password.encode("ascii"))
        self.assertNotIn(password, output.getvalue())

    def test_status_reports_link_and_memory_without_credentials(self):
        output = io.StringIO()

        class FakeSerial:
            def __init__(self, *args, **kwargs):
                self.responses = [
                    b"WIFI OK CONFIGURED OFFLINE\n",
                    b"WIFI MEM 120000 2000000\n",
                ]

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def write(self, data):
                return len(data)

            def flush(self):
                pass

            def readline(self):
                return self.responses.pop(0)

        fake_serial = SimpleNamespace(Serial=FakeSerial)
        with patch.object(sys, "argv", ["provision_wifi.py", "--port", "COM1", "status"]):
            with patch.dict(sys.modules, {"serial": fake_serial}):
                with redirect_stdout(output):
                    provision_wifi.main()

        self.assertEqual(
            output.getvalue(),
            "WIFI OK CONFIGURED OFFLINE\nWIFI MEM 120000 2000000\n",
        )
        self.assertTrue(provision_wifi.is_memory_response("WIFI MEM 1 2"))
        self.assertFalse(provision_wifi.is_memory_response("WIFI MEM password 2"))


if __name__ == "__main__":
    unittest.main()
