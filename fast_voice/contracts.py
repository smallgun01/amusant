"""Provider-neutral realtime voice contract."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias, runtime_checkable

JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]

# Kai v0.12.0 boundary: mono PCM16LE, 16 kHz into the relay, 24 kHz to the client.
CLIENT_INPUT_RATE_HZ = 16_000
CLIENT_OUTPUT_RATE_HZ = 24_000


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """Per-adapter features; shared interface does not imply equal capability."""

    streaming_audio_input: bool
    streaming_audio_output: bool
    tool_calling: bool
    barge_in: bool
    cancellation: bool
    text_injection: bool
    supported_languages: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """A mono signed PCM16 little-endian chunk with an explicit sample rate."""

    data: bytes
    sample_rate_hz: int

    def __post_init__(self) -> None:
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if len(self.data) % 2:
            raise ValueError("PCM16LE data length must be divisible by two bytes")


@dataclass(frozen=True, slots=True)
class TranscriptEvent:
    text: str
    direction: Literal["input", "output"]
    is_final: bool


@dataclass(frozen=True, slots=True)
class ToolCallEvent:
    call_id: str
    name: str
    arguments: Mapping[str, JSONValue]


@dataclass(frozen=True, slots=True)
class ResponseInterruptedEvent:
    """The provider reported that generation was interrupted (not a local mute)."""


@dataclass(frozen=True, slots=True)
class TurnCompleteEvent:
    """Provider-confirmed response completion; cancellation is not optimistic."""

    cancelled: bool = False


class ProviderError(RuntimeError):
    """Sanitized provider error metadata; never include credentials or raw audio."""

    def __init__(self, provider_id: str, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.provider_id = provider_id
        self.code = code
        self.retryable = retryable


AudioCallback: TypeAlias = Callable[[AudioFrame], None | Awaitable[None]]
TranscriptCallback: TypeAlias = Callable[[TranscriptEvent], None | Awaitable[None]]
ToolCallCallback: TypeAlias = Callable[[ToolCallEvent], None | Awaitable[None]]
InterruptedCallback: TypeAlias = Callable[[ResponseInterruptedEvent], None | Awaitable[None]]
TurnCompleteCallback: TypeAlias = Callable[[TurnCompleteEvent], None | Awaitable[None]]
ErrorCallback: TypeAlias = Callable[[ProviderError], None | Awaitable[None]]


@runtime_checkable
class LiveProvider(Protocol):
    """Provider adapter surface aligned with SPEC.md section 17.

    Register callbacks before connect(). Implementations dispatch callbacks in the
    provider's receive order, awaiting async callbacks serially. start_turn/end_turn
    delimit PTT activity; start_turn may barge in on an active response.

    send_audio() accepts mono PCM16LE at CLIENT_INPUT_RATE_HZ. on_audio() emits
    mono PCM16LE normalized to CLIENT_OUTPUT_RATE_HZ. The adapter owns conversion
    from/to provider-specific encodings and rates. cancel_response() requests a stop;
    it must not report completion until the provider confirms completion/interruption.
    close() releases the session and its receive task. No implicit provider fallback.
    """

    @property
    def provider_id(self) -> str: ...

    @property
    def capabilities(self) -> ProviderCapabilities: ...

    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def send_audio(self, frame: AudioFrame) -> None: ...
    async def start_turn(self) -> None: ...
    async def end_turn(self) -> None: ...
    async def send_tool_result(self, call_id: str, result: Mapping[str, JSONValue]) -> None: ...
    async def cancel_response(self) -> None: ...
    async def inject_text(self, text: str) -> None: ...
    def on_audio(self, callback: AudioCallback) -> None: ...
    def on_transcript(self, callback: TranscriptCallback) -> None: ...
    def on_tool_call(self, callback: ToolCallCallback) -> None: ...
    def on_interrupted(self, callback: InterruptedCallback) -> None: ...
    def on_turn_complete(self, callback: TurnCompleteCallback) -> None: ...
    def on_error(self, callback: ErrorCallback) -> None: ...
