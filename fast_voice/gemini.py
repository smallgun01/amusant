"""Gemini Live adapter using an injected google-genai client and types module.

Injection keeps unit tests offline. Production wiring must provide the installed
Google Gen AI SDK; this module intentionally does not install or import it globally.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from .contracts import (
    AudioCallback,
    AudioFrame,
    CLIENT_INPUT_RATE_HZ,
    CLIENT_OUTPUT_RATE_HZ,
    ErrorCallback,
    InterruptedCallback,
    JSONValue,
    ProviderCapabilities,
    ProviderError,
    ResponseInterruptedEvent,
    ToolCallCallback,
    ToolCallEvent,
    TranscriptCallback,
    TranscriptEvent,
    TurnCompleteCallback,
    TurnCompleteEvent,
)

LOG = logging.getLogger("amusant.fast_voice.gemini")


class GeminiLiveProvider:
    provider_id = "gemini"
    capabilities = ProviderCapabilities(
        streaming_audio_input=True,
        streaming_audio_output=True,
        tool_calling=True,
        barge_in=True,
        cancellation=False,  # start_turn can barge in; standalone cancel is not exposed here
        text_injection=True,
    )

    def __init__(self, client: Any, model: str, config: Any, sdk_types: Any):
        self._client = client
        self._model = model
        self._config = config
        self._types = sdk_types
        self._connection: Any | None = None
        self._session: Any | None = None
        self._receiver_task: asyncio.Task[None] | None = None
        self._tool_names: dict[str, str] = {}
        self._was_interrupted = False
        self._on_audio: AudioCallback | None = None
        self._on_transcript: TranscriptCallback | None = None
        self._on_tool_call: ToolCallCallback | None = None
        self._on_interrupted: InterruptedCallback | None = None
        self._on_turn_complete: TurnCompleteCallback | None = None
        self._on_error: ErrorCallback | None = None

    def on_audio(self, callback: AudioCallback) -> None:
        self._on_audio = callback

    def on_transcript(self, callback: TranscriptCallback) -> None:
        self._on_transcript = callback

    def on_tool_call(self, callback: ToolCallCallback) -> None:
        self._on_tool_call = callback

    def on_interrupted(self, callback: InterruptedCallback) -> None:
        self._on_interrupted = callback

    def on_turn_complete(self, callback: TurnCompleteCallback) -> None:
        self._on_turn_complete = callback

    def on_error(self, callback: ErrorCallback) -> None:
        self._on_error = callback

    async def connect(self) -> None:
        if self._session is not None:
            return
        connection = self._client.aio.live.connect(model=self._model, config=self._config)
        try:
            session = await connection.__aenter__()
        except Exception as exc:
            raise ProviderError(self.provider_id, "connect_failed", "Gemini Live connection failed", retryable=True) from exc
        self._connection = connection
        self._session = session
        self._receiver_task = asyncio.create_task(self._receive_loop(session, connection))

    async def close(self) -> None:
        connection, task = self._connection, self._receiver_task
        self._connection = self._session = self._receiver_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if connection is not None:
            try:
                await connection.__aexit__(None, None, None)
            except Exception as exc:
                raise ProviderError(self.provider_id, "close_failed", "Gemini Live close failed", retryable=True) from exc
        self._tool_names.clear()
        self._was_interrupted = False

    def _require_session(self) -> Any:
        if self._session is None:
            raise ProviderError(self.provider_id, "not_connected", "Gemini Live session is not connected")
        return self._session

    async def _send(self, operation: Callable[[], Awaitable[None]]) -> None:
        try:
            await operation()
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self.provider_id, "send_failed", "Gemini Live send failed", retryable=True) from exc

    async def send_audio(self, frame: AudioFrame) -> None:
        if frame.sample_rate_hz != CLIENT_INPUT_RATE_HZ:
            raise ProviderError(self.provider_id, "unsupported_audio_rate", "Gemini adapter expects 16 kHz mono PCM16LE input")
        session = self._require_session()
        blob = self._types.Blob(data=frame.data, mime_type=f"audio/pcm;rate={frame.sample_rate_hz}")
        await self._send(lambda: session.send_realtime_input(audio=blob))

    async def start_turn(self) -> None:
        session = self._require_session()
        await self._send(lambda: session.send_realtime_input(activity_start=self._types.ActivityStart()))

    async def end_turn(self) -> None:
        session = self._require_session()
        await self._send(lambda: session.send_realtime_input(activity_end=self._types.ActivityEnd()))

    async def send_tool_result(self, call_id: str, result: Mapping[str, JSONValue]) -> None:
        name = self._tool_names.get(call_id)
        if name is None:
            raise ProviderError(self.provider_id, "unknown_tool_call", "Gemini tool call id is not active")
        session = self._require_session()
        response = self._types.FunctionResponse(id=call_id, name=name, response=dict(result))
        await self._send(lambda: session.send_tool_response(function_responses=[response]))
        self._tool_names.pop(call_id, None)

    async def cancel_response(self) -> None:
        raise ProviderError(
            self.provider_id,
            "unsupported_operation",
            "Gemini adapter supports barge-in on start_turn, not standalone cancellation",
        )

    async def inject_text(self, text: str) -> None:
        """Send one complete text turn through the Live API client-content path.

        ``send_realtime_input(text=...)`` is for realtime input events; the
        documented discrete text-turn path is ``send_client_content``.
        """
        session = self._require_session()
        turns = {"parts": [{"text": text}]}
        await self._send(lambda: session.send_client_content(turns=turns))

    async def _emit(self, callback: Callable[[Any], Any] | None, event: Any) -> None:
        if callback is None:
            return
        result = callback(event)
        if inspect.isawaitable(result):
            await result

    async def _notify_error(self, error: ProviderError) -> None:
        if self._on_error is None:
            return
        try:
            await self._emit(self._on_error, error)
        except Exception:
            pass  # error reporters must not recursively kill session cleanup

    async def _receive_loop(self, session: Any, connection: Any) -> None:
        ended_without_message = False
        try:
            while self._session is session:
                received = False
                async for message in session.receive():
                    received = True
                    try:
                        await self._handle_message(session, message)
                    except ProviderError as error:
                        await self._notify_error(error)
                if not received:
                    ended_without_message = True
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Preserve only the exception class for diagnosis; SDK messages may
            # contain request content, headers, or other sensitive details.
            error_code = getattr(exc, "code", None)
            if type(error_code) is int and 0 <= error_code <= 65535:
                pass
            elif isinstance(error_code, str) and re.fullmatch(r"[A-Z0-9_]{1,64}", error_code):
                pass
            else:
                error_code = "unknown"
            api_status = getattr(exc, "status", None)
            if not isinstance(api_status, str) or not re.fullmatch(r"[A-Z0-9_]{1,64}", api_status):
                api_status = "unknown"
            LOG.warning(
                "Gemini Live receive loop failed error_type=%s error_code=%s api_status=%s",
                type(exc).__name__, error_code, api_status,
            )
            await self._notify_error(ProviderError(self.provider_id, "receive_failed", "Gemini Live receive failed", retryable=True))
        finally:
            if self._session is session:
                self._connection = self._session = self._receiver_task = None
                try:
                    await connection.__aexit__(None, None, None)
                except Exception:
                    pass
            if ended_without_message:
                await self._notify_error(ProviderError(self.provider_id, "session_ended", "Gemini Live session ended", retryable=True))

    async def _handle_message(self, session: Any, message: Any) -> None:
        tool_call = getattr(message, "tool_call", None)
        function_calls = getattr(tool_call, "function_calls", None) or []
        for call in function_calls:
            call_id = str(getattr(call, "id", "") or "")
            name = str(getattr(call, "name", "") or "")
            if not call_id or not name:
                await self._notify_error(ProviderError(self.provider_id, "invalid_tool_call", "Gemini returned an invalid tool call"))
                continue
            self._tool_names[call_id] = name
            arguments = getattr(call, "args", None) or {}
            if not isinstance(arguments, Mapping):
                arguments = {}
            await self._emit(self._on_tool_call, ToolCallEvent(call_id, name, arguments))

        content = getattr(message, "server_content", None)
        if content is None:
            return
        if getattr(content, "interrupted", False):
            self._was_interrupted = True
            await self._emit(self._on_interrupted, ResponseInterruptedEvent())

        input_transcript = getattr(content, "input_transcription", None)
        if input_transcript is not None and getattr(input_transcript, "text", None):
            await self._emit(
                self._on_transcript,
                TranscriptEvent(input_transcript.text, "input", bool(getattr(input_transcript, "finished", False))),
            )

        model_turn = getattr(content, "model_turn", None)
        for part in getattr(model_turn, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            audio = getattr(inline, "data", None) if inline is not None else None
            if not audio:
                continue
            mime_type = getattr(inline, "mime_type", None)
            if not mime_type or not mime_type.startswith("audio/pcm"):
                raise ProviderError(self.provider_id, "unsupported_audio_format", "Gemini returned non-PCM audio")
            match = re.search(r"rate=(\d+)", mime_type)
            if match is None:
                raise ProviderError(self.provider_id, "unknown_audio_rate", "Gemini output audio sample rate was not specified")
            sample_rate = int(match.group(1))
            if sample_rate != CLIENT_OUTPUT_RATE_HZ:
                raise ProviderError(self.provider_id, "unsupported_audio_rate", "Gemini output was not 24 kHz PCM; resampling is not implemented")
            await self._emit(self._on_audio, AudioFrame(audio, sample_rate))

        output_transcript = getattr(content, "output_transcription", None)
        if output_transcript is not None and getattr(output_transcript, "text", None):
            await self._emit(
                self._on_transcript,
                TranscriptEvent(output_transcript.text, "output", bool(getattr(output_transcript, "finished", False))),
            )

        if getattr(content, "turn_complete", False):
            if function_calls:
                # A Live tool-call turn can finish before the model speaks its
                # post-FunctionResponse acknowledgement. Keep the Relay turn
                # open for the follow-up response instead of truncating it.
                return
            await self._emit(self._on_turn_complete, TurnCompleteEvent(cancelled=self._was_interrupted))
            self._was_interrupted = False
