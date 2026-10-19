from __future__ import annotations

import unittest

from stop_classification import classify_stop_outcome


class StopOutcomeClassificationTests(unittest.TestCase):
    def test_accepts_only_the_observed_interrupt_failure_as_stop_handling(self):
        self.assertEqual(
            classify_stop_outcome(
                remote_status="failed",
                remote_error_category="interrupt",
                remote_interrupted="unknown",
                ledger_status="failed",
                ledger_code="hermes_failed",
                visible_status="failed",
                visible_code="hermes_failed",
            ),
            "interrupt_failed",
        )

    def test_accepts_source_confirmed_cancellation_with_owner_visible_cancelled(self):
        self.assertEqual(
            classify_stop_outcome(
                remote_status="cancelled",
                remote_error_category="unknown",
                remote_interrupted="unknown",
                ledger_status="failed",
                ledger_code="hermes_cancelled",
                visible_status="cancelled",
                visible_code="hermes_cancelled",
            ),
            "cancelled",
        )

    def test_rejects_unrelated_failure(self):
        self.assertIsNone(
            classify_stop_outcome(
                remote_status="failed",
                remote_error_category="model_api_error",
                remote_interrupted="false",
                ledger_status="failed",
                ledger_code="hermes_failed",
                visible_status="failed",
                visible_code="hermes_failed",
            )
        )

    def test_never_represents_interrupt_failure_as_cancelled(self):
        self.assertIsNone(
            classify_stop_outcome(
                remote_status="failed",
                remote_error_category="interrupt",
                remote_interrupted="unknown",
                ledger_status="failed",
                ledger_code="hermes_failed",
                visible_status="cancelled",
                visible_code="hermes_cancelled",
            )
        )

    def test_rejects_cancelled_status_without_source_confirmation(self):
        self.assertIsNone(
            classify_stop_outcome(
                remote_status="interrupted",
                remote_error_category="unknown",
                remote_interrupted="false",
                ledger_status="failed",
                ledger_code="hermes_cancelled",
                visible_status="cancelled",
                visible_code="hermes_cancelled",
            )
        )

    def test_rejects_cancellation_status_that_conflicts_with_interrupt_flag(self):
        self.assertIsNone(
            classify_stop_outcome(
                remote_status="cancelled",
                remote_error_category="unknown",
                remote_interrupted="false",
                ledger_status="failed",
                ledger_code="hermes_cancelled",
                visible_status="cancelled",
                visible_code="hermes_cancelled",
            )
        )

    def test_rejects_incomplete_or_mismatched_results(self):
        self.assertIsNone(
            classify_stop_outcome(
                remote_status="stopping",
                remote_error_category="unknown",
                remote_interrupted="unknown",
                ledger_status="uncertain",
                ledger_code="hermes_stop_unconfirmed",
                visible_status="uncertain",
                visible_code="hermes_stop_unconfirmed",
            )
        )


if __name__ == "__main__":
    unittest.main()
