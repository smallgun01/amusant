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
from .gemini import GeminiLiveProvider
from .selection import ProviderSelectionError, select_live_provider

__all__ = [
    "AudioFrame",
    "GeminiLiveProvider",
    "LiveProvider",
    "ProviderCapabilities",
    "ProviderError",
    "ProviderSelectionError",
    "ResponseInterruptedEvent",
    "ToolCallEvent",
    "TranscriptEvent",
    "TurnCompleteEvent",
    "select_live_provider",
]
