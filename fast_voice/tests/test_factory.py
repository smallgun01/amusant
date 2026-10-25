import unittest
from types import SimpleNamespace

from fast_voice.contracts import ProviderError
from fast_voice.factory import (
    DEFAULT_GEMINI_MODEL,
    ProviderConfigurationError,
    build_live_provider,
)
from fast_voice.gemini import GeminiLiveProvider
from fast_voice.selection import ProviderSelectionError
from relay.slow_path import build_delegate_task_tool

try:
    from google.genai import types as google_types
except ImportError:
    google_types = None


class FakeSDKTypes:
    class LiveConnectConfig:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class AudioTranscriptionConfig: pass
    class RealtimeInputConfig:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class AutomaticActivityDetection:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class SpeechConfig:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class VoiceConfig:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class PrebuiltVoiceConfig:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class Tool:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class GoogleSearch:
        pass


class FactoryTests(unittest.TestCase):
    def test_provider_choice_is_required_and_fail_closed(self):
        with self.assertRaises(ProviderConfigurationError):
            build_live_provider({}, client_factory=lambda **_: self.fail("must not construct client"), sdk_types=FakeSDKTypes)
        with self.assertRaises(ProviderSelectionError):
            build_live_provider({"AMUSANT_LIVE_PROVIDER": "other"}, client_factory=lambda **_: self.fail("must not construct client"), sdk_types=FakeSDKTypes)

    def test_missing_key_fails_before_client_construction(self):
        with self.assertRaises(ProviderConfigurationError):
            build_live_provider({"AMUSANT_LIVE_PROVIDER": "gemini"}, client_factory=lambda **_: self.fail("must not construct client"), sdk_types=FakeSDKTypes)

    def test_explicit_gemini_config_builds_offline_without_network(self):
        seen = {}
        def client_factory(**kwargs):
            seen.update(kwargs)
            return SimpleNamespace(aio=SimpleNamespace())
        provider = build_live_provider(
            {"AMUSANT_LIVE_PROVIDER": "gemini", "GEMINI_API_KEY": "test-only-key", "AMUSANT_GEMINI_MAX_OUTPUT_TOKENS": "32"},
            client_factory=client_factory,
            sdk_types=FakeSDKTypes,
        )
        self.assertIsInstance(provider, GeminiLiveProvider)
        self.assertEqual(seen, {"api_key": "test-only-key"})
        self.assertEqual(provider._model, DEFAULT_GEMINI_MODEL)
        self.assertEqual(provider._config.response_modalities, ["AUDIO"])
        self.assertTrue(provider._config.realtime_input_config.automatic_activity_detection.disabled)
        self.assertEqual(provider._config.max_output_tokens, 32)
        self.assertFalse(hasattr(provider._config, "tools"))
        self.assertFalse(hasattr(provider._config, "system_instruction"))
        self.assertFalse(hasattr(provider._config, "speech_config"))

    def test_opt_in_jellii_instruction_and_zephyr_voice_preserve_tools(self):
        tools = [object()]
        provider = build_live_provider(
            {
                "AMUSANT_LIVE_PROVIDER": "gemini",
                "GEMINI_API_KEY": "test-only-key",
                "AMUSANT_GEMINI_VOICE": "Zephyr",
            },
            client_factory=lambda **_: SimpleNamespace(aio=SimpleNamespace()),
            sdk_types=FakeSDKTypes,
            tool_declarations=tools,
            system_instruction="You are Jellii.",
        )
        config = provider._config
        self.assertEqual(config.system_instruction, "You are Jellii.")
        self.assertEqual(config.speech_config.voice_config.prebuilt_voice_config.voice_name, "Zephyr")
        self.assertEqual(config.tools, tools)
        self.assertIs(config.tools[0], tools[0])
        self.assertTrue(config.realtime_input_config.automatic_activity_detection.disabled)

    def test_google_search_is_explicit_opt_in_and_preserves_function_tools(self):
        function_tool = object()
        tools = [function_tool]
        provider = build_live_provider(
            {
                "AMUSANT_LIVE_PROVIDER": "gemini",
                "GEMINI_API_KEY": "test-only-key",
                "AMUSANT_GEMINI_GOOGLE_SEARCH": "1",
                "AMUSANT_GEMINI_VOICE": "Zephyr",
            },
            client_factory=lambda **_: SimpleNamespace(aio=SimpleNamespace()),
            sdk_types=FakeSDKTypes,
            tool_declarations=tools,
            system_instruction="You are Jellii.",
        )
        config = provider._config
        self.assertEqual(len(config.tools), 2)
        self.assertIsInstance(config.tools[0].google_search, FakeSDKTypes.GoogleSearch)
        self.assertIs(config.tools[1], function_tool)
        self.assertEqual(tools, [function_tool])  # do not mutate the caller's list
        self.assertEqual(config.system_instruction, "You are Jellii.")
        self.assertEqual(config.speech_config.voice_config.prebuilt_voice_config.voice_name, "Zephyr")

    def test_search_only_mode_and_tool_free_inbox(self):
        env = {
            "AMUSANT_LIVE_PROVIDER": "gemini",
            "GEMINI_API_KEY": "test-only-key",
            "AMUSANT_GEMINI_GOOGLE_SEARCH": "1",
        }
        factory = lambda **_: SimpleNamespace(aio=SimpleNamespace())
        normal = build_live_provider(env, client_factory=factory, sdk_types=FakeSDKTypes)
        self.assertEqual(len(normal._config.tools), 1)
        self.assertIsInstance(normal._config.tools[0].google_search, FakeSDKTypes.GoogleSearch)
        spoken = build_live_provider(
            env, client_factory=factory, sdk_types=FakeSDKTypes, allow_google_search=False
        )
        self.assertFalse(hasattr(spoken._config, "tools"))

    def test_invalid_search_flag_fails_before_client_construction(self):
        for setting in ("", "true", "yes", "2", " 1"):
            with self.subTest(setting=setting), self.assertRaises(ProviderConfigurationError):
                build_live_provider(
                    {
                        "AMUSANT_LIVE_PROVIDER": "gemini",
                        "GEMINI_API_KEY": "test-only-key",
                        "AMUSANT_GEMINI_GOOGLE_SEARCH": setting,
                    },
                    client_factory=lambda **_: self.fail("must not construct client"),
                    sdk_types=FakeSDKTypes,
                )

    def test_invalid_voice_and_instruction_fail_before_client_construction(self):
        for voice in ("", "Zephyr;invalid", "two words", "x" * 33):
            with self.subTest(voice=voice), self.assertRaises(ProviderConfigurationError):
                build_live_provider(
                    {
                        "AMUSANT_LIVE_PROVIDER": "gemini",
                        "GEMINI_API_KEY": "test-only-key",
                        "AMUSANT_GEMINI_VOICE": voice,
                    },
                    client_factory=lambda **_: self.fail("must not construct client"),
                    sdk_types=FakeSDKTypes,
                )
        for instruction in ("", "   "):
            with self.subTest(instruction=instruction), self.assertRaises(ProviderConfigurationError):
                build_live_provider(
                    {"AMUSANT_LIVE_PROVIDER": "gemini", "GEMINI_API_KEY": "test-only-key"},
                    client_factory=lambda **_: self.fail("must not construct client"),
                    sdk_types=FakeSDKTypes,
                    system_instruction=instruction,
                )

    def test_invalid_output_token_limit_fails_before_client_construction(self):
        with self.assertRaises(ProviderConfigurationError):
            build_live_provider(
                {"AMUSANT_LIVE_PROVIDER": "gemini", "GEMINI_API_KEY": "test-only-key", "AMUSANT_GEMINI_MAX_OUTPUT_TOKENS": "lots"},
                client_factory=lambda **_: self.fail("must not construct client"),
                sdk_types=FakeSDKTypes,
            )

    def test_model_can_be_overridden_without_changing_provider_selection(self):
        provider = build_live_provider(
            {"AMUSANT_LIVE_PROVIDER": "gemini", "GEMINI_API_KEY": "test-only-key", "AMUSANT_GEMINI_MODEL": "explicit-model"},
            client_factory=lambda **_: SimpleNamespace(aio=SimpleNamespace()),
            sdk_types=FakeSDKTypes,
        )
        self.assertEqual(provider.provider_id, "gemini")
        self.assertEqual(provider._model, "explicit-model")

    @unittest.skipIf(google_types is None, "google-genai is installed only in the project uv environment")
    def test_actual_sdk_live_config_builds_without_network(self):
        provider = build_live_provider(
            {"AMUSANT_LIVE_PROVIDER": "gemini", "GEMINI_API_KEY": "test-only-key", "AMUSANT_GEMINI_MAX_OUTPUT_TOKENS": "32"},
            client_factory=lambda **_: SimpleNamespace(aio=SimpleNamespace()),
            sdk_types=google_types,
        )
        self.assertIsInstance(provider._config, google_types.LiveConnectConfig)
        self.assertEqual(provider._config.response_modalities, ["AUDIO"])
        self.assertTrue(provider._config.realtime_input_config.automatic_activity_detection.disabled)
        self.assertEqual(provider._config.max_output_tokens, 32)
        self.assertFalse(provider._config.tools)
        self.assertIsNone(provider._config.system_instruction)
        self.assertIsNone(provider._config.speech_config)

    @unittest.skipIf(google_types is None, "google-genai is installed only in the project uv environment")
    def test_actual_sdk_accepts_opt_in_persona_and_voice_without_network(self):
        provider = build_live_provider(
            {
                "AMUSANT_LIVE_PROVIDER": "gemini",
                "GEMINI_API_KEY": "test-only-key",
                "AMUSANT_GEMINI_VOICE": "Zephyr",
            },
            client_factory=lambda **_: SimpleNamespace(aio=SimpleNamespace()),
            sdk_types=google_types,
            system_instruction="You are Jellii.",
        )
        config = provider._config
        self.assertIsInstance(config, google_types.LiveConnectConfig)
        self.assertIn("Jellii", str(config.system_instruction))
        self.assertEqual(config.speech_config.voice_config.prebuilt_voice_config.voice_name, "Zephyr")

    @unittest.skipIf(google_types is None, "google-genai is installed only in the project uv environment")
    def test_actual_sdk_accepts_search_grounding_with_delegate_and_zephyr_without_network(self):
        function_tool = build_delegate_task_tool(google_types)
        provider = build_live_provider(
            {
                "AMUSANT_LIVE_PROVIDER": "gemini",
                "GEMINI_API_KEY": "test-only-key",
                "AMUSANT_GEMINI_GOOGLE_SEARCH": "1",
                "AMUSANT_GEMINI_VOICE": "Zephyr",
            },
            client_factory=lambda **_: SimpleNamespace(aio=SimpleNamespace()),
            sdk_types=google_types,
            tool_declarations=[function_tool],
            system_instruction="You are Jellii.",
        )
        config = provider._config
        self.assertIsInstance(config, google_types.LiveConnectConfig)
        self.assertEqual(len(config.tools), 2)
        self.assertIsInstance(config.tools[0].google_search, google_types.GoogleSearch)
        self.assertEqual(config.tools[1].function_declarations[0].name, "delegate_task")
        self.assertEqual(config.speech_config.voice_config.prebuilt_voice_config.voice_name, "Zephyr")
        self.assertTrue(config.realtime_input_config.automatic_activity_detection.disabled)

    @unittest.skipIf(google_types is None, "google-genai is installed only in the project uv environment")
    def test_delegate_tool_schema_is_explicitly_attached_to_live_config(self):
        tool = build_delegate_task_tool(google_types)
        provider = build_live_provider(
            {"AMUSANT_LIVE_PROVIDER": "gemini", "GEMINI_API_KEY": "test-only-key"},
            client_factory=lambda **_: SimpleNamespace(aio=SimpleNamespace()),
            sdk_types=google_types,
            tool_declarations=[tool],
        )
        self.assertEqual(provider._config.tools[0].function_declarations[0].name, "delegate_task")
        self.assertEqual(
            provider._config.tools[0].function_declarations[0].behavior,
            google_types.Behavior.BLOCKING,
        )
        schema = provider._config.tools[0].function_declarations[0].parameters
        self.assertEqual(schema.required, ["task"])
        self.assertEqual(set(schema.properties), {"task"})

    def test_sdk_setup_exception_is_redacted(self):
        def broken_client(**_):
            raise RuntimeError("secret test-only-key should not escape")
        with self.assertRaises(ProviderError) as raised:
            build_live_provider(
                {"AMUSANT_LIVE_PROVIDER": "gemini", "GEMINI_API_KEY": "test-only-key"},
                client_factory=broken_client,
                sdk_types=FakeSDKTypes,
            )
        self.assertEqual(raised.exception.code, "client_configuration_failed")
        self.assertNotIn("test-only-key", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
