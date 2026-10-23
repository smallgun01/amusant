import asyncio
import unittest
from types import SimpleNamespace

from fast_voice.contracts import (
    AudioFrame,
    CLIENT_INPUT_RATE_HZ,
    CLIENT_OUTPUT_RATE_HZ,
    LiveProvider,
    ProviderCapabilities,
    ProviderError,
    TranscriptEvent,
    TurnCompleteEvent,
)
from fast_voice.gemini import GeminiLiveProvider
from fast_voice.selection import ProviderSelectionError, select_live_provider

try:
    from google.genai import types as google_types
except ImportError:
    google_types = None


class FakeTypes:
    class Blob:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class ActivityStart: pass
    class ActivityEnd: pass
    class FunctionResponse:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)


class FakeSession:
    def __init__(self, messages=()):
        self.messages = list(messages)
        self.calls = []
        self.client_contents = []
        self.tool_responses = []
        self.closed = asyncio.Event()

    async def send_realtime_input(self, **kwargs): self.calls.append(kwargs)
    async def send_client_content(self, **kwargs): self.client_contents.append(kwargs)
    async def send_tool_response(self, **kwargs): self.tool_responses.append(kwargs)

    async def receive(self):
        messages, self.messages = self.messages, []
        for message in messages:
            yield message
        await self.closed.wait()


class FakeConnection:
    def __init__(self, session, fail=False): self.session, self.fail, self.exited = session, fail, False
    async def __aenter__(self):
        if self.fail: raise RuntimeError("synthetic connect failure")
        return self.session
    async def __aexit__(self, *_): self.exited = True; self.session.closed.set()


class FakeClient:
    def __init__(self, session, fail=False):
        self.connection = FakeConnection(session, fail)
        self.aio = SimpleNamespace(live=SimpleNamespace(connect=lambda **_: self.connection))


class FakeProvider:
    provider_id = "gemini"
    capabilities = ProviderCapabilities(
        streaming_audio_input=True, streaming_audio_output=True, tool_calling=True,
        barge_in=True, cancellation=False, text_injection=True,
        supported_languages=frozenset({"en", "zh-TW"}),
    )
    async def connect(self): pass
    async def close(self): pass
    async def send_audio(self, frame): pass
    async def start_turn(self): pass
    async def end_turn(self): pass
    async def send_tool_result(self, call_id, result): pass
    async def cancel_response(self): pass
    async def inject_text(self, text): pass
    def on_audio(self, callback): pass
    def on_transcript(self, callback): pass
    def on_tool_call(self, callback): pass
    def on_tool_calls_complete(self, callback): pass
    def on_interrupted(self, callback): pass
    def on_turn_complete(self, callback): pass
    def on_error(self, callback): pass


class ContractTests(unittest.TestCase):
    def test_fake_implements_spec_surface(self):
        self.assertIsInstance(FakeProvider(), LiveProvider)

    def test_audio_frame_validates_pcm_metadata(self):
        self.assertEqual(AudioFrame(b"\x00\x00", CLIENT_INPUT_RATE_HZ).sample_rate_hz, 16_000)
        self.assertEqual(CLIENT_OUTPUT_RATE_HZ, 24_000)
        with self.assertRaises(ValueError): AudioFrame(b"\x00", CLIENT_INPUT_RATE_HZ)
        with self.assertRaises(ValueError): AudioFrame(b"\x00\x00", 0)

    def test_transcript_and_turn_events_preserve_semantics(self):
        event = TranscriptEvent("hello", direction="input", is_final=False)
        self.assertFalse(event.is_final)
        self.assertEqual(event.direction, "input")
        self.assertTrue(TurnCompleteEvent(cancelled=True).cancelled)

    def test_explicit_selection_returns_requested_adapter(self):
        gemini = FakeProvider()
        other = type("OtherProvider", (FakeProvider,), {"provider_id": "other"})()
        self.assertIs(select_live_provider({"gemini": gemini, "other": other}, "other"), other)

    def test_missing_or_unknown_selection_fails_without_fallback(self):
        providers = {"gemini": FakeProvider()}
        for value in (None, "", "not-configured"):
            with self.subTest(value=value), self.assertRaises(ProviderSelectionError):
                select_live_provider(providers, value)

    def test_misregistered_adapter_fails_closed(self):
        with self.assertRaises(ProviderSelectionError): select_live_provider({"other": FakeProvider()}, "other")

    def test_capabilities_are_immutable_and_provider_specific(self):
        capabilities = FakeProvider.capabilities
        self.assertTrue(capabilities.streaming_audio_input)
        self.assertTrue(capabilities.barge_in)
        self.assertFalse(capabilities.cancellation)
        self.assertIn("zh-TW", capabilities.supported_languages)
        with self.assertRaises((AttributeError, TypeError)): capabilities.tool_calling = False

    def test_provider_error_exposes_retry_metadata_and_can_be_raised(self):
        error = ProviderError("gemini", "rate_limited", "Provider rate limited", retryable=True)
        self.assertEqual((error.provider_id, error.code, error.retryable), ("gemini", "rate_limited", True))
        with self.assertRaises(ProviderError): raise error


class GeminiAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_receive_failure_logs_safe_status_fields_only(self):
        session = FakeSession()

        class FakeAPIError(Exception):
            code = 1011
            status = None
            message = "private prompt and credential must not be logged"

        async def broken_receive():
            if False:
                yield None
            raise FakeAPIError("private prompt and credential must not be logged")

        session.receive = broken_receive
        provider = GeminiLiveProvider(FakeClient(session), "test-model", {}, FakeTypes)
        errors = []
        error_reported = asyncio.Event()

        def on_error(error):
            errors.append(error)
            error_reported.set()

        provider.on_error(on_error)
        with self.assertLogs("amusant.fast_voice.gemini", level="WARNING") as captured:
            await provider.connect()
            await asyncio.wait_for(error_reported.wait(), timeout=2)
            await provider.close()

        self.assertEqual(errors[0].code, "receive_failed")
        self.assertIn("error_type=FakeAPIError", captured.output[0])
        self.assertIn("error_code=1011", captured.output[0])
        self.assertIn("api_status=unknown", captured.output[0])
        self.assertNotIn("private prompt", "\n".join(captured.output))

    async def test_connect_sends_normalized_pcm_and_ptt_boundaries(self):
        session = FakeSession()
        client = FakeClient(session)
        provider = GeminiLiveProvider(client, "test-model", {}, FakeTypes)
        self.assertIsInstance(provider, LiveProvider)
        await provider.connect()
        await provider.send_audio(AudioFrame(b"\x00\x00", CLIENT_INPUT_RATE_HZ))
        await provider.start_turn()
        await provider.end_turn()
        await provider.inject_text("test")
        self.assertEqual(session.calls[0]["audio"].mime_type, "audio/pcm;rate=16000")
        self.assertIsInstance(session.calls[1]["activity_start"], FakeTypes.ActivityStart)
        self.assertIsInstance(session.calls[2]["activity_end"], FakeTypes.ActivityEnd)
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(session.client_contents, [{
            "turns": {"role": "user", "parts": [{"text": "test"}]},
            "turn_complete": True,
        }])
        await provider.close()
        self.assertTrue(client.connection.exited)

    @unittest.skipIf(google_types is None, "google-genai is installed only in the project uv environment")
    async def test_inject_text_has_sdk_serializable_user_role(self):
        session = FakeSession()
        provider = GeminiLiveProvider(FakeClient(session), "test-model", {}, google_types)
        await provider.connect()
        await provider.inject_text("Speak the saved task result.")
        self.assertEqual(len(session.client_contents), 1)
        content = session.client_contents[0]
        self.assertIs(content["turn_complete"], True)
        sdk_content = google_types.Content.model_validate(content["turns"])
        self.assertEqual(sdk_content.role, "user")
        self.assertEqual([part.text for part in sdk_content.parts], ["Speak the saved task result."])
        self.assertEqual(sdk_content.model_dump(exclude_none=True)["role"], "user")
        await provider.close()

    async def test_receive_dispatches_ordered_events_and_correlates_tool_result(self):
        function_call = SimpleNamespace(id="call-1", name="lookup", args={"q": "x"})
        tool_message = SimpleNamespace(
            tool_call=SimpleNamespace(function_calls=[function_call]),
            server_content=SimpleNamespace(
                interrupted=False,
                input_transcription=None,
                model_turn=None,
                output_transcription=None,
                turn_complete=True,
            ),
        )
        followup_message = SimpleNamespace(
            server_content=SimpleNamespace(
                interrupted=False,
                input_transcription=None,
                model_turn=SimpleNamespace(parts=[SimpleNamespace(inline_data=SimpleNamespace(data=b"\x00\x00", mime_type="audio/pcm;rate=24000"))]),
                output_transcription=SimpleNamespace(text="Accepted.", finished=True),
                turn_complete=True,
            ),
        )
        session = FakeSession([tool_message, followup_message])
        provider = GeminiLiveProvider(FakeClient(session), "test-model", {}, FakeTypes)
        seen = []
        complete = asyncio.Event()
        async def on_tool(event):
            seen.append(("tool", event.call_id, event.name, dict(event.arguments)))
            await provider.send_tool_result(event.call_id, {"ok": True})
        provider.on_tool_call(on_tool)
        provider.on_interrupted(lambda event: seen.append(("interrupted",)))
        provider.on_transcript(lambda event: seen.append(("transcript", event.direction, event.text, event.is_final)))
        provider.on_audio(lambda frame: seen.append(("audio", frame.sample_rate_hz, frame.data)))
        provider.on_turn_complete(lambda event: (seen.append(("complete", event.cancelled)), complete.set()))
        await provider.connect()
        await asyncio.wait_for(complete.wait(), timeout=2)
        self.assertEqual([item[0] for item in seen], ["tool", "audio", "transcript", "complete"])
        self.assertEqual(session.tool_responses[0]["function_responses"][0].name, "lookup")
        self.assertEqual(seen[-1], ("complete", False))
        await provider.close()

    async def test_unsupported_output_audio_format_is_reported(self):
        message = SimpleNamespace(server_content=SimpleNamespace(
            interrupted=False, input_transcription=None,
            model_turn=SimpleNamespace(parts=[SimpleNamespace(inline_data=SimpleNamespace(data=b"\x00\x00", mime_type="audio/opus"))]),
            output_transcription=None, turn_complete=False,
        ))
        session = FakeSession([message])
        provider = GeminiLiveProvider(FakeClient(session), "test-model", {}, FakeTypes)
        errors = []
        reported = asyncio.Event()
        def on_error(error):
            errors.append(error)
            reported.set()
        provider.on_error(on_error)
        await provider.connect()
        await asyncio.wait_for(reported.wait(), timeout=2)
        self.assertEqual(errors[0].code, "unsupported_audio_format")
        self.assertNotIn("opus", str(errors[0]).lower())
        await provider.close()

    async def test_barge_in_is_supported_but_standalone_cancel_fails_closed(self):
        provider = GeminiLiveProvider(FakeClient(FakeSession()), "test-model", {}, FakeTypes)
        self.assertTrue(provider.capabilities.barge_in)
        self.assertFalse(provider.capabilities.cancellation)
        with self.assertRaises(ProviderError) as raised:
            await provider.cancel_response()
        self.assertEqual(raised.exception.code, "unsupported_operation")

    async def test_connect_failure_is_sanitized(self):
        provider = GeminiLiveProvider(FakeClient(FakeSession(), fail=True), "test-model", {}, FakeTypes)
        with self.assertRaises(ProviderError) as raised:
            await provider.connect()
        self.assertEqual(raised.exception.code, "connect_failed")
        self.assertNotIn("synthetic", str(raised.exception))

    @unittest.skipIf(google_types is None, "google-genai is installed only in the project uv environment")
    async def test_actual_google_sdk_types_work_with_fake_session_no_network(self):
        function_call = SimpleNamespace(id="sdk-call", name="lookup", args={"q": "x"})
        session = FakeSession([SimpleNamespace(
            tool_call=SimpleNamespace(function_calls=[function_call]), server_content=None,
        )])
        config = google_types.LiveConnectConfig(response_modalities=["AUDIO"])
        provider = GeminiLiveProvider(FakeClient(session), "test-model", config, google_types)
        called = asyncio.Event()
        async def reply(event):
            await provider.send_tool_result(event.call_id, {"ok": True})
            called.set()
        provider.on_tool_call(reply)
        await provider.connect()
        await provider.send_audio(AudioFrame(b"\x00\x00", CLIENT_INPUT_RATE_HZ))
        await provider.start_turn()
        await provider.end_turn()
        await asyncio.wait_for(called.wait(), timeout=2)
        self.assertIsInstance(session.calls[0]["audio"], google_types.Blob)
        self.assertIsInstance(session.calls[1]["activity_start"], google_types.ActivityStart)
        self.assertIsInstance(session.calls[2]["activity_end"], google_types.ActivityEnd)
        self.assertIsInstance(session.tool_responses[0]["function_responses"][0], google_types.FunctionResponse)
        await provider.close()


    async def test_tool_call_batch_completion_follows_all_correlated_responses(self):
        calls = [
            SimpleNamespace(id="batch-one", name="check_task_status", args={"speak": False}),
            SimpleNamespace(id="batch-two", name="delegate_task", args={"task": "x"}),
        ]
        session = FakeSession([SimpleNamespace(
            tool_call=SimpleNamespace(function_calls=calls), server_content=None,
        )])
        provider = GeminiLiveProvider(FakeClient(session), "test-model", {}, FakeTypes)
        seen = []
        completed = asyncio.Event()
        async def handle(event):
            seen.append(("call", event.call_id))
            await provider.send_tool_result(event.call_id, {"status": "rejected"})
        provider.on_tool_call(handle)
        provider.on_tool_calls_complete(lambda: (seen.append(("batch",)), completed.set()))
        await provider.connect()
        await asyncio.wait_for(completed.wait(), timeout=2)
        self.assertEqual(seen, [("call", "batch-one"), ("call", "batch-two"), ("batch",)])
        self.assertEqual(len(session.tool_responses), 2)
        await provider.close()

    async def test_reused_tool_call_id_is_rejected_before_second_callback(self):
        repeated = SimpleNamespace(id="reused-call", name="delegate_task", args={"task": "x"})
        session = FakeSession([SimpleNamespace(
            tool_call=SimpleNamespace(function_calls=[repeated, repeated]), server_content=None,
        )])
        provider = GeminiLiveProvider(FakeClient(session), "test-model", {}, FakeTypes)
        calls = []
        errors = []
        observed = asyncio.Event()
        async def handle(event):
            calls.append(event.call_id)
            await provider.send_tool_result(event.call_id, {"status": "accepted"})
        def on_error(error):
            errors.append(error.code)
            observed.set()
        provider.on_tool_call(handle)
        provider.on_error(on_error)
        await provider.connect()
        await asyncio.wait_for(observed.wait(), timeout=2)
        self.assertEqual(calls, ["reused-call"])
        self.assertEqual(errors, ["duplicate_tool_call_id"])
        self.assertEqual(len(session.tool_responses), 1)
        await provider.close()


if __name__ == "__main__":
    unittest.main()
