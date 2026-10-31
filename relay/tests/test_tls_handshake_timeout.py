"""Offline checks for the StickS3 TLS handshake deadline."""
import asyncio
from contextlib import asynccontextmanager
import json
import importlib.util
import logging
import os
import ssl
import time
import unittest
from unittest.mock import Mock, patch

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from relay.device_tls_psk import PSK_CIPHERS, build_server_context
from relay.live_bridge import make_handler, run

TOKEN = "fake-device-token-for-tests"
IDENTITY = "synthetic-device"
PSK = bytes(range(32))


def make_client_context(identity, key):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers(PSK_CIPHERS)
    context.set_ecdh_curve("X25519")
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.set_psk_client_callback(lambda _hint: (identity, key))
    return context


class TlsHandshakeTimeoutTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(
        hasattr(ssl.SSLContext, "set_psk_server_callback")
        and importlib.util.find_spec("av") and importlib.util.find_spec("numpy"),
        "TLS-PSK integration requires Python 3.13 and the hardware-opus extra",
    )
    async def test_run_applies_deadline_and_psk_handshakes(self):
        state = {}
        ready = asyncio.Event()
        provider_calls = []

        def provider_factory():
            provider_calls.append(True)
            return Mock()

        handler = make_handler(TOKEN, provider_factory)
        context = build_server_context({IDENTITY: PSK})

        @asynccontextmanager
        async def capture_serve(handler, host, port, **options):
            state["options"] = options.copy()
            async with serve(handler, host, port, **options) as server:
                state["port"] = server.sockets[0].getsockname()[1]
                ready.set()
                yield server

        def install(event, _loop):
            state["stop"] = event
            return ()

        env = {"AMUSANT_LIVE_PROVIDER": "gemini", "GEMINI_API_KEY": "fake-unused-key"}
        with (patch.dict(os.environ, env, clear=True),
              patch("relay.live_bridge.validate_hardware_bind_host"),
              patch("relay.live_bridge.server_context_from_environ", return_value=context),
              patch("relay.live_bridge.build_process_runtime", return_value=(handler, None)),
              patch("relay.live_bridge.serve", side_effect=capture_serve),
              patch("relay.live_bridge.install_shutdown_handlers", side_effect=install),
              patch("relay.live_bridge.remove_shutdown_handlers")):
            process = asyncio.create_task(run("127.0.0.1", 0, TOKEN, device_mode="sticks3"))
            try:
                await asyncio.wait_for(ready.wait(), 5)
                self.assertIs(state["options"]["ssl"], context)
                self.assertEqual(state["options"]["ssl_handshake_timeout"], 15.0)
                self.assertNotIn("open_timeout", state["options"])
                port = state["port"]
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                started = time.monotonic()
                try:
                    url = f"wss://127.0.0.1:{port}/ws"
                    async with connect(url, ssl=make_client_context(IDENTITY, PSK),
                                       additional_headers={"Authorization": f"Bearer {TOKEN}"},
                                       proxy=None) as ws:
                        await ws.send(json.dumps({"type": "hello", "codecs": ["opus"]}))
                    previous_disable = logging.root.manager.disable
                    logging.disable(logging.CRITICAL)
                    try:
                        with self.assertRaises((OSError, EOFError)):
                            await connect(url, ssl=make_client_context(IDENTITY, bytes([1]) * 32),
                                          open_timeout=2, proxy=None)
                    finally:
                        logging.disable(previous_disable)
                    self.assertEqual(await asyncio.wait_for(reader.read(1), 20), b"")
                    elapsed = time.monotonic() - started
                    self.assertGreaterEqual(elapsed, 13.0)
                    self.assertLess(elapsed, 20.0)
                    self.assertEqual(provider_calls, [])
                finally:
                    writer.close()
                    await writer.wait_closed()
            finally:
                if "stop" in state:
                    state["stop"].set()
                await asyncio.wait_for(process, 5)

    async def test_pcm_server_does_not_receive_tls_option(self):
        state = {}

        @asynccontextmanager
        async def capture_serve(_handler, _host, _port, **options):
            state.update(options)
            yield object()

        def install(event, _loop):
            event.set()
            return ()

        async def handler(_ws):
            pass

        env = {"AMUSANT_LIVE_PROVIDER": "gemini", "GEMINI_API_KEY": "fake-unused-key"}
        with (patch.dict(os.environ, env, clear=True),
              patch("relay.live_bridge.build_process_runtime", return_value=(handler, None)),
              patch("relay.live_bridge.serve", side_effect=capture_serve),
              patch("relay.live_bridge.install_shutdown_handlers", side_effect=install),
              patch("relay.live_bridge.remove_shutdown_handlers")):
            await run("127.0.0.1", 0, TOKEN, device_mode="pcm")
        self.assertNotIn("ssl", state)
        self.assertNotIn("ssl_handshake_timeout", state)
        self.assertNotIn("open_timeout", state)
