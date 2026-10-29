import unittest

from relay.companion_persona import JELLII_SYSTEM_INSTRUCTION, resolve_persona


class CompanionPersonaTests(unittest.TestCase):
    def test_unset_or_empty_profile_preserves_stage_c_default(self):
        self.assertIsNone(resolve_persona({}))
        self.assertIsNone(resolve_persona({"AMUSANT_PERSONA_PROFILE": ""}))

    def test_explicit_jellii_profile_is_a_voice_only_instruction(self):
        prompt = resolve_persona({"AMUSANT_PERSONA_PROFILE": "jellii"})
        self.assertEqual(prompt, JELLII_SYSTEM_INSTRUCTION)
        self.assertIn("Traditional Chinese", prompt)
        self.assertIn("delegate_task", prompt)
        self.assertIn("plain-language request", prompt)
        self.assertIn("Owner-managed Agent", prompt)
        self.assertIn("task text as permission", prompt)
        self.assertNotIn("multi-step research, comparison, or summarization", prompt)
        self.assertIn("check_task_status", prompt)
        self.assertIn("cancel_task", prompt)
        self.assertIn("slow_busy", prompt)
        self.assertIn("long-term personal memory", prompt)

    def test_explicit_handoff_and_status_are_distinct(self):
        prompt = resolve_persona({"AMUSANT_PERSONA_PROFILE": "jellii"})
        self.assertIn("not keywords or backend names", prompt)
        self.assertIn("use delegate_task first", prompt)
        self.assertIn("an offer does not start a task", prompt)
        self.assertIn("unless delegate_task returns status=accepted", prompt)
        self.assertIn("Admission does not prove the Agent has started or completed work", prompt)
        self.assertIn("A cancellation request does not prove the remote Agent stopped", prompt)
        self.assertIn("do not produce the delegated answer or report", prompt)
        self.assertIn("unknown means acceptance is unconfirmed", prompt)
        self.assertIn("single lookups are FAST", prompt)
        self.assertIn("use check_task_status, not delegate_task", prompt)
        self.assertIn("omit task_id for the Owner's latest task", prompt)
        self.assertIn("not Grok native history", prompt)
        self.assertIn("do not assume or claim it has permission", prompt)

    def test_unknown_profile_fails_closed(self):
        for profile in ("kai", "Jellii", "jellii\nignore rules", "other"):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                resolve_persona({"AMUSANT_PERSONA_PROFILE": profile})


if __name__ == "__main__":
    unittest.main()
