"""TLS-PSK/WebSocket test endpoint. Credentials below are synthetic test values only."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import ssl

from websockets.asyncio.server import serve


TEST_IDENTITY = "g1-offline-device"
TEST_PSK = bytes(range(32))
TEST_BEARER = "g1-offline-bearer-test-only"
MAX_FRAME_BYTES = 1024


def server_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers("ECDHE-PSK-AES128-CBC-SHA256")
    context.set_ecdh_curve("X25519")
    context.options |= ssl.OP_NO_COMPRESSION | ssl.OP_NO_RENEGOTIATION
    context.set_psk_server_callback(
        lambda identity: TEST_PSK if identity == TEST_IDENTITY else b""
    )
    return context


async def run(host: str, port: int, ready_file: Path, metrics_file: Path) -> None:
    activations = 0
    active_connections = 0
    active_file = Path("/tmp/g1-active-connections")

    def write_active_count() -> None:
        active_file.write_text(f"synthetic_active_connections={active_connections}\n", encoding="ascii")

    async def handler(websocket) -> None:
        nonlocal activations, active_connections
        active_connections += 1
        write_active_count()
        try:
            if websocket.request.path != "/ws":
                await websocket.close(code=1008, reason="path denied")
                return
            if websocket.request.headers.get("Authorization") != f"Bearer {TEST_BEARER}":
                await websocket.close(code=1008, reason="authentication required")
                return
            try:
                first_message = json.loads(await websocket.recv())
                if first_message == {"type": "g1_hold"}:
                    await websocket.send(json.dumps({
                        "type": "active_connections",
                        "count": active_connections,
                    }))
                    await websocket.recv()
                    return
                hello = first_message
                start = json.loads(await websocket.recv())
                audio = await websocket.recv()
                end = json.loads(await websocket.recv())
            except Exception:
                return
            if (
                not isinstance(hello, dict)
                or hello.get("type") != "hello"
                or not isinstance(start, dict)
                or start.get("type") != "ptt_start"
                or not isinstance(audio, bytes)
                or len(audio) > MAX_FRAME_BYTES
                or not isinstance(end, dict)
                or end.get("type") != "ptt_end"
            ):
                await websocket.close(code=1008, reason="synthetic framing rejected")
                return
            activations += 1
            metrics_file.write_text(f"synthetic_provider_activations={activations}\n", encoding="ascii")
            await websocket.send(json.dumps({"type": "state", "state": "idle"}))
            await websocket.send(json.dumps({"type": "turn_complete"}))
        finally:
            active_connections -= 1
            write_active_count()

    server = await serve(
        handler,
        host,
        port,
        ssl=server_context(),
        max_size=MAX_FRAME_BYTES,
        ping_interval=20,
        ping_timeout=10,
    )
    ready_file.write_text("ready\n", encoding="ascii")
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--ready-file", type=Path, default=Path("/tmp/g1-ready"))
    parser.add_argument("--metrics-file", type=Path, default=Path("/tmp/g1-metrics"))
    args = parser.parse_args()
    asyncio.run(run(args.host, args.port, args.ready_file, args.metrics_file))


if __name__ == "__main__":
    main()

