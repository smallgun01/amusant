from contextlib import redirect_stderr, redirect_stdout
import io
from types import SimpleNamespace
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

    def test_grok_abandon_is_explicit_and_can_revoke_callback_capability(self):
        stdout = io.StringIO()
        receipt = SimpleNamespace(
            status="abandoned", task_id="4db5aabc-284a-4a01-8a6d-5cddcc7934f1",
            code="grok_owner_abandoned",
        )
        with patch("relay.operator_reconcile.build_task_store") as build_store:
            build_store.return_value.operator_reconcile_uncertain.return_value = receipt
            with redirect_stdout(stdout):
                result = main([
                    "--client-id", "authenticated-owner-1",
                    "--task-id", "4db5aabc-284a-4a01-8a6d-5cddcc7934f1",
                    "--terminal-state", "abandoned",
                    "--operator-id", "operator-1",
                    "--evidence-ref", "INC-127-owner-abandon",
                    "--confirm-slot-release",
                    "--revoke-grok-callback-token",
                ])
        self.assertEqual(result, 0)
        build_store.return_value.operator_reconcile_uncertain.assert_called_once()
        self.assertEqual(
            build_store.return_value.operator_reconcile_uncertain.call_args.kwargs["terminal_state"],
            "abandoned",
        )
        self.assertTrue(
            build_store.return_value.operator_reconcile_uncertain.call_args.kwargs["revoke_callback_token"]
        )
        self.assertIn('"status":"abandoned"', stdout.getvalue())

    def test_legacy_reconcile_does_not_pass_grok_only_option(self):
        receipt = SimpleNamespace(status="failed", task_id="task-1", code="reconciled")
        with patch("relay.operator_reconcile.build_task_store") as build_store:
            build_store.return_value.operator_reconcile_uncertain.return_value = receipt
            with redirect_stdout(io.StringIO()):
                result = main([
                    "--client-id", "owner-1", "--task-id", "task-1",
                    "--terminal-state", "no_remote_run", "--operator-id", "operator-1",
                    "--evidence-ref", "ticket-1", "--confirm-slot-release",
                ])
        self.assertEqual(result, 0)
        self.assertNotIn(
            "revoke_callback_token",
            build_store.return_value.operator_reconcile_uncertain.call_args.kwargs,
        )


if __name__ == "__main__":
    unittest.main()
