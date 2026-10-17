from contextlib import redirect_stderr
import io
import unittest
from unittest.mock import patch

from relay.operator_reconcile import main


class OperatorReconcileCliTests(unittest.TestCase):
    def test_slot_release_requires_explicit_flag_before_store_access(self):
        stderr = io.StringIO()
        with patch("relay.operator_reconcile.build_task_store") as build_store:
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                main([
                    "--client-id", "authenticated-owner-1",
                    "--task-id", "4db5aabc-284a-4a01-8a6d-5cddcc7934f1",
                    "--terminal-state", "no_remote_run",
                    "--operator-id", "operator-1",
                    "--evidence-ref", "INC-61-verification",
                ])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--confirm-slot-release is required", stderr.getvalue())
        build_store.assert_not_called()


if __name__ == "__main__":
    unittest.main()
