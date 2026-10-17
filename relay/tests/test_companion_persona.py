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
        self.assertIn("check_task_status", prompt)
        self.assertIn("long-term personal memory", prompt)

    def test_unknown_profile_fails_closed(self):
        for profile in ("kai", "Jellii", "jellii\nignore rules", "other"):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                resolve_persona({"AMUSANT_PERSONA_PROFILE": profile})


if __name__ == "__main__":
    unittest.main()
