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
        self.assertEqual(schema.properties["task_kind"].enum, ["research", "compare", "summarize"])

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
