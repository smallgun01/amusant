from pathlib import Path
import unittest

import toolset_fake_provider as preflight


ROOT = Path(__file__).resolve().parents[2]
PROJECT = Path(__file__).resolve().parent
EXPECTED_DIGEST = "sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7"


class OfflineToolsetPreflightContractTests(unittest.TestCase):
    def test_case_configs_are_explicitly_fake_isolated_and_no_mcp(self):
        case_a = (PROJECT / "hermes-config-toolset-a.yaml").read_text(encoding="utf-8")
        case_b = (PROJECT / "hermes-config-toolset-b.yaml").read_text(encoding="utf-8")
        for config in (case_a, case_b):
            self.assertIn("base_url: http://fake-provider:8000/v1", config)
            self.assertIn("    - amusant_option_d", config)
            self.assertIn("    - no_mcp", config)
            self.assertIn('enabled: "on"', config)
            self.assertIn("  enabled:\n    - amusant-option-d", config)
            self.assertIn("h12preflight:", config)
            self.assertIn("preflight_probe", config)
            self.assertIn("enabled: true", config)
        self.assertNotIn("web:\n", case_a)
        self.assertIn("    - search\n", case_b)
        self.assertNotIn("    - web\n", case_b)
        self.assertNotIn("    - web_extract\n", case_b)
        self.assertIn("search_backend: ddgs", case_b)

    def test_bridge_catalog_gate_requires_exact_save_report_only(self):
        valid = preflight._tool_search_summary(
            '{"total_available":1,"results":[{"query":"save_report","matches":["save_report"]}],"tools":{"save_report":{}}}'
        )
        self.assertEqual(valid, {"total_available": 1, "matches": ["save_report"]})
        extra = preflight._tool_search_summary(
            '{"total_available":2,"results":[{"matches":["save_report","mcp__bad__tool"]}]}'
        )
        self.assertNotEqual(extra, valid)

    def test_model_visible_tool_allowlist_is_checked_per_provider_request(self):
        expected = {"tool_search", "tool_describe", "tool_call"}
        self.assertEqual(preflight._visibility_mismatch(expected, expected), {"extra": [], "missing": []})
        self.assertEqual(preflight._visibility_mismatch(set(), expected), {"extra": [], "missing": []})
        self.assertEqual(
            preflight._visibility_mismatch(expected | {"web_extract"}, expected),
            {"extra": ["web_extract"], "missing": []},
        )

    def test_describe_and_commit_evidence_are_bounded_summaries(self):
        described = preflight._tool_describe_summary(
            '{"tools":{"save_report":{"description":"ignored"}},"not_found":["write_file"]}'
        )
        self.assertEqual(described["described_names"], ["save_report"])
        self.assertEqual(described["not_found_names"], ["write_file"])
        self.assertTrue(preflight._commit_confirmed('{"committed":true}'))
        self.assertFalse(preflight._commit_confirmed('{"error":"blocked"}'))

    def test_bridge_denials_require_hermes_policy_language(self):
        self.assertEqual(
            preflight._bridge_rejection('{"error":{"message":"terminal is a directly-listed tool, not a deferred one."}}'),
            "registered_not_deferred",
        )
        self.assertEqual(
            preflight._bridge_rejection('{"error":"not a known tool name"}'),
            "unknown_tool_name",
        )
        self.assertIsNone(preflight._bridge_rejection('{"result":"tool returned empty output"}'))
        self.assertIsNone(preflight._bridge_rejection('{"message":"browser page was empty"}'))

    def test_direct_filter_requires_the_exact_hermes_unknown_tool_response(self):
        self.assertTrue(preflight._direct_tool_filter_rejection(
            "Tool 'browser_navigate' does not exist. Available tools: tool_call", "browser_navigate"
        ))
        self.assertFalse(preflight._direct_tool_filter_rejection(
            "Browser request returned an error", "browser_navigate"
        ))

    def test_denial_evidence_requires_a_matched_tool_call_result_id(self):
        messages = [
            {"role": "assistant", "tool_calls": [{"id": "forged-1", "function": {
                "name": "browser_navigate", "arguments": "{}"
            }}]},
            {"role": "tool", "tool_call_id": "different-id", "name": "browser_navigate",
             "content": "Tool 'browser_navigate' does not exist. Available tools: tool_call"},
        ]
        calls = preflight._assistant_tool_calls(messages)
        result, match = preflight._result_for_call(calls[0], preflight._tool_results_by_id(messages))
        self.assertIsNone(result)
        self.assertFalse(match["matched_call_result_id"])

    def test_bridge_and_direct_denials_record_separate_stages_without_body_text(self):
        bridge = preflight._bridge_exchange(
            {"id": "bridge-1"},
            {"role": "tool", "name": "tool_call", "content":
             '{"error":{"message":"browser_navigate is a directly-listed tool, not a deferred one"}}'},
            {"call_id": "bridge-1", "result_call_id": "bridge-1", "result_count": 1,
             "matched_call_result_id": True, "result_role": "tool", "result_name": "tool_call"},
        )
        self.assertEqual(bridge["denial_stage"], "hermes_tool_search_deferred_name_validation")
        self.assertTrue(bridge["dispatch_prevented"])
        self.assertNotIn("content", bridge)
        direct = preflight._direct_exchange(
            {"id": "direct-1"},
            {"role": "tool", "name": "write_file", "content":
             "Tool 'write_file' does not exist. Available tools: tool_call"},
            {"call_id": "direct-1", "result_call_id": "direct-1", "result_count": 1,
             "matched_call_result_id": True, "result_role": "tool", "result_name": "write_file"},
            "write_file",
        )
        self.assertEqual(direct["denial_stage"], "agent_valid_tool_name_filter")
        self.assertTrue(direct["dispatch_prevented"])
        self.assertNotIn("content", direct)

    def test_sanitized_response_profile_contains_shape_not_tool_output(self):
        profile = preflight._response_shape(
            "Tool 'browser_navigate' does not exist. Available tools: tool_call"
        )
        self.assertEqual(profile["content_kind"], "plain_text")
        self.assertIn(profile["size_bucket"], {"1-256", "257-1024", ">1024"})
        self.assertNotIn("text", profile)

    def test_probe_set_covers_requested_privilege_classes(self):
        names = {name for name, _ in preflight.PROBES}
        self.assertNotIn("save_report", names)
        self.assertTrue({
            "write_file", "terminal", "browser_navigate", "browser_snapshot",
            "execute_code", "web_search", "web_extract", "mcp__h12preflight__preflight_probe",
        } <= names)
        self.assertEqual(len(names), len(preflight.PROBES))
        self.assertIn("web_search", preflight._expected_direct_probes("case_a"))
        self.assertNotIn("web_search", preflight._expected_direct_probes("case_b"))
        self.assertNotIn("web_search", preflight._expected_bridge_probes("case_b"))
        self.assertEqual(len(preflight.SEARCH_QUERIES), len(preflight.PROBES) + 1)

    def test_pinned_arm64_digest_and_no_stop_or_product_timing_mutation(self):
        compose = (PROJECT / "compose.yaml").read_text(encoding="utf-8")
        runner = (PROJECT / "run_toolset_preflight.py").read_text(encoding="utf-8")
        self.assertIn(EXPECTED_DIGEST, compose)
        self.assertIn('platform: linux/arm64', compose)
        self.assertIn('"docker_engine_not_native_arm64"', runner)
        self.assertIn('"product_timing_changed": False', runner)
        self.assertIn('"stop_test_performed": False', runner)
        self.assertNotIn('AMUSANT_HERMES_RUN_DEADLINE_SECONDS', compose[compose.index("  toolset-runner:"):])
        self.assertIn("inspect_network_isolation", runner)
        self.assertIn("external_egress_disabled", runner)
        self.assertIn("internal: true", compose)

    def test_existing_selective_arm64_workflow_keeps_e1_and_uploads_new_evidence(self):
        workflow = (ROOT / ".github/workflows/issue-85-pr-b2-e2e.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-24.04-arm", workflow)
        self.assertIn("run_e2e.py", workflow)
        self.assertIn("run_toolset_preflight.py", workflow)
        self.assertIn("paths:", workflow)
        self.assertIn("spikes/004-relay-facade-hermes-e2e/**", workflow)
        self.assertIn("evidence/toolset-preflight.json", workflow)

    def test_provider_and_audit_store_only_sanitized_denial_profiles(self):
        source = (PROJECT / "toolset_fake_provider.py").read_text(encoding="utf-8")
        audit = (PROJECT / "audit_toolset_preflight.py").read_text(encoding="utf-8")
        self.assertIn("matched_call_result_id", source)
        self.assertIn("agent_valid_tool_name_filter", source)
        self.assertIn("hermes_tool_search_deferred_name_validation", source)
        self.assertIn("response_shape", source)
        self.assertNotIn('"raw_response"', source)
        self.assertIn("bridge_denials_structurally_proven", audit)
        self.assertIn("direct_denials_structurally_proven", audit)
        self.assertIn("case_b_web_search_authorized_and_not_invoked", audit)

    def test_runner_logs_sanitized_case_evidence_for_failure_diagnosis(self):
        runner = (PROJECT / "run_toolset_preflight.py").read_text(encoding="utf-8")
        self.assertIn('"sanitized_cases": cases', runner)
        self.assertIn("Printing them makes a failed preflight diagnosable", runner)


if __name__ == "__main__":
    unittest.main()

