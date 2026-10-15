"""Build the explicitly selected Fast Voice provider from runtime settings."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from .contracts import LiveProvider, ProviderError
from .gemini import GeminiLiveProvider
from .selection import ProviderSelectionError, select_live_provider

# Pinned Kai v0.12.0 relay/.env.example baseline. Override only with a
# model explicitly approved/available for the configured Gemini API project.
DEFAULT_GEMINI_MODEL = "gemini-3.8-live"


class ProviderConfigurationError(ValueError):
    """Runtime provider settings are missing, invalid, or unsupported."""


SUPPORTED_PROVIDER_IDS = frozenset({"gemini"})


def build_live_provider(
    environ: Mapping[str, str],
    *,
    client_factory: Any | None = None,
    sdk_types: Any | None = None,
    tool_declarations: list[Any] | None = None,
    system_instruction: str | None = None,
) -> LiveProvider:
    """Build Gemini only when explicitly selected; do not read files or call network.

    Runtime deployment should inject the Secrets Manager JSON field into
    ``GEMINI_API_KEY``. Never put the key in repository config or logs.
    ``client_factory``/``sdk_types`` exist for deterministic offline tests.
    """

    selected = environ.get("AMUSANT_LIVE_PROVIDER")
    if not selected:
        raise ProviderConfigurationError("AMUSANT_LIVE_PROVIDER must be set explicitly.")
    if selected not in SUPPORTED_PROVIDER_IDS:
        raise ProviderSelectionError(f"Unsupported Live provider: {selected!r}.")

    api_key = environ.get("GEMINI_API_KEY")
    if not api_key or not api_key.strip():
        raise ProviderConfigurationError("GEMINI_API_KEY is required for the selected Gemini provider.")
    api_key = api_key.strip()
    model = environ.get("AMUSANT_GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
    if not model:
        raise ProviderConfigurationError("AMUSANT_GEMINI_MODEL cannot be empty.")
    voice_name = environ.get("AMUSANT_GEMINI_VOICE")
    if voice_name is not None and (
        not isinstance(voice_name, str)
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", voice_name)
    ):
        raise ProviderConfigurationError("AMUSANT_GEMINI_VOICE must be a valid prebuilt voice name.")
    if system_instruction is not None and (
        not isinstance(system_instruction, str) or not system_instruction.strip()
    ):
        raise ProviderConfigurationError("system_instruction must be nonempty text when supplied.")
    max_tokens_value = environ.get("AMUSANT_GEMINI_MAX_OUTPUT_TOKENS")
    try:
        max_output_tokens = int(max_tokens_value) if max_tokens_value is not None else None
    except ValueError:
        raise ProviderConfigurationError("AMUSANT_GEMINI_MAX_OUTPUT_TOKENS must be an integer.") from None
    if max_output_tokens is not None and not 1 <= max_output_tokens <= 8192:
        raise ProviderConfigurationError("AMUSANT_GEMINI_MAX_OUTPUT_TOKENS is outside the supported range.")

    try:
        if client_factory is None or sdk_types is None:
            from google import genai
            from google.genai import types

            client_factory = client_factory or genai.Client
            sdk_types = sdk_types or types
        client = client_factory(api_key=api_key)
        config_options = {
            "response_modalities": ["AUDIO"],
            "input_audio_transcription": sdk_types.AudioTranscriptionConfig(),
            "output_audio_transcription": sdk_types.AudioTranscriptionConfig(),
            "realtime_input_config": sdk_types.RealtimeInputConfig(
                automatic_activity_detection=sdk_types.AutomaticActivityDetection(disabled=True)
            ),
        }
        if max_output_tokens is not None:
            config_options["max_output_tokens"] = max_output_tokens
        if system_instruction is not None:
            config_options["system_instruction"] = system_instruction
        if voice_name is not None:
            config_options["speech_config"] = sdk_types.SpeechConfig(
                voice_config=sdk_types.VoiceConfig(
                    prebuilt_voice_config=sdk_types.PrebuiltVoiceConfig(voice_name=voice_name)
                )
            )
        if tool_declarations is not None:
            config_options["tools"] = tool_declarations
        config = sdk_types.LiveConnectConfig(**config_options)
    except Exception:
        # SDK constructor errors may include request/config internals; redact them.
        raise ProviderError(
            "gemini", "client_configuration_failed", "Gemini client configuration failed"
        ) from None

    provider = GeminiLiveProvider(client, model, config, sdk_types)
    return select_live_provider({"gemini": provider}, selected)
