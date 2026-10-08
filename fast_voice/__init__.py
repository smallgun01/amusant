"""Provider-neutral contracts for Amusant Fast Voice."""

from .contracts import (
    AudioFrame,
    LiveProvider,
    ProviderCapabilities,
    ProviderError,
    ResponseInterruptedEvent,
    ToolCallEvent,
    TranscriptEvent,
    TurnCompleteEvent,
)
from .factory import ProviderConfigurationError, build_live_provider
from .gemini import GeminiLiveProvider
from .selection import ProviderSelectionError, select_live_provider

__all__ = [
    "AudioFrame",
    "GeminiLiveProvider",
    "LiveProvider",
    "ProviderCapabilities",
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderSelectionError",
    "ResponseInterruptedEvent",
    "ToolCallEvent",
    "TranscriptEvent",
    "TurnCompleteEvent",
    "build_live_provider",
    "select_live_provider",
]
