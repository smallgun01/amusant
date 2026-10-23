"""Synthetic StickS3 client for the G1 systemd-socket-proxyd test."""
from __future__ import annotations

import argparse
import asyncio
import json
import ssl

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidMessage


TEST_IDENTITY = "g1-offline-device"
TEST_PSK = bytes(range(32))
TEST_BEARER = "g1-offline-bearer-test-only"
MAX_FRAME_BYTES = 1024


def client_context(*, identity: str | None = TEST_IDENTITY, key: bytes | None = TEST_PSK) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers("ECDHE-PSK-AES128-CBC-SHA256")
    context.set_ecdh_curve("X25519")
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    if identity is not None and key is not None:
        context.set_psk_client_callback(lambda _hint: (identity, key))
    return context


def url(host: str, port: int, scheme: str = "wss") -> str:
    return f"{scheme}://{host}:{port}/ws"


async def valid_session(host: str, port: int) -> None:
    async with connect(
        url(host, port),
        ssl=client_context(),
        proxy=None,
        additional_headers={"Authorization": f"Bearer {TEST_BEARER}"},
        open_timeout=4,
        close_timeout=1,
        max_size=MAX_FRAME_BYTES,
    ) as websocket:
        await websocket.send(json.dumps({"type": "hello", "codecs": ["opus"]}))
        await websocket.send(json.dumps({"type": "ptt_start"}))
        await websocket.send(b"\x02synthetic-opus-frame")
        await websocket.send(json.dumps({"type": "ptt_end"}))
        first = json.loads(await asyncio.wait_for(websocket.recv(), timeout=3))
        second = json.loads(await asyncio.wait_for(websocket.recv(), timeout=3))
        assert first == {"type": "state", "state": "idle"}
        assert second == {"type": "turn_complete"}


async def rejected_tls(host: str, port: int, context: ssl.SSLContext, headers=None) -> None:
    try:
        async with connect(
            url(host, port), ssl=context, additional_headers=headers,
            proxy=None, open_timeout=3, close_timeout=1, max_size=MAX_FRAME_BYTES,
        ) as websocket:
            await asyncio.wait_for(websocket.recv(), timeout=3)
    except (OSError, ssl.SSLError, ConnectionClosed, TimeoutError, InvalidMessage):
        return
    raise AssertionError("invalid TLS/device authentication was accepted")


async def rejected_plaintext(host: str, port: int) -> None:
    try:
        async with connect(url(host, port, "ws"), proxy=None, open_timeout=3, close_timeout=1):
            pass
    except (OSError, ssl.SSLError, ConnectionClosed, TimeoutError, InvalidMessage):
        return
    raise AssertionError("plaintext WebSocket fallback was accepted")


async def oversized_frame(host: str, port: int) -> None:
    try:
        async with connect(
            url(host, port), ssl=client_context(),
            proxy=None,
            additional_headers={"Authorization": f"Bearer {TEST_BEARER}"},
            open_timeout=3, close_timeout=1, max_size=MAX_FRAME_BYTES,
        ) as websocket:
            await websocket.send(json.dumps({"type": "hello", "codecs": ["opus"]}))
            await websocket.send(json.dumps({"type": "ptt_start"}))
            await websocket.send(b"x" * (MAX_FRAME_BYTES * 4))
            await asyncio.wait_for(websocket.recv(), timeout=3)
    except (ConnectionClosed, OSError, TimeoutError):
        return
    raise AssertionError("oversized WebSocket frame was not closed")


async def concurrency_cap(host: str, port: int) -> None:
    active = []
    try:
        for expected_count in range(1, 5):
            try:
                websocket = await asyncio.wait_for(connect(
                    url(host, port), ssl=client_context(),
                    proxy=None,
                    additional_headers={"Authorization": f"Bearer {TEST_BEARER}"},
                    open_timeout=2, close_timeout=1,
                ), timeout=3)
            except (OSError, ssl.SSLError, ConnectionClosed, TimeoutError, InvalidMessage):
                if len(active) < 2:
                    raise AssertionError("ingress refused connections below the safe test minimum")
                return
            active.append(websocket)
            await websocket.send(json.dumps({"type": "g1_hold"}))
            observed = json.loads(await asyncio.wait_for(websocket.recv(), timeout=2))
            if observed != {"type": "active_connections", "count": expected_count}:
                raise AssertionError(f"unexpected synthetic active-connection response: {observed!r}")
            if len(active) > 3:
                raise AssertionError("systemd-socket-proxyd accepted a connection above the configured cap")
        raise AssertionError("systemd-socket-proxyd did not enforce its configured concurrency cap")
    finally:
        for websocket in active:
            await websocket.close()


async def run(args) -> None:
    if args.suite == "core":
        await valid_session(args.host, args.port)
        await rejected_tls(args.host, args.port, client_context(key=bytes(reversed(range(32)))),
                           {"Authorization": f"Bearer {TEST_BEARER}"})
        await rejected_tls(args.host, args.port, client_context(), {"Authorization": "Bearer wrong"})
        await rejected_tls(args.host, args.port, client_context(), None)
        await rejected_plaintext(args.host, args.port)
        await oversized_frame(args.host, args.port)
        await concurrency_cap(args.host, args.port)
        print("TLS_PSK_BEARER_PTT_FRAME_LIMIT_CONCURRENCY_PASS")
    elif args.suite == "basic":
        await valid_session(args.host, args.port)
        print("TLS_PSK_RECONNECT_PASS")
    elif args.suite == "private":
        await valid_session(args.host, args.port)
        print("PRIVATE_RELAY_PATH_PASS")
    elif args.suite == "expect-failure":
        try:
            await valid_session(args.host, args.port)
        except Exception:
            print("UPSTREAM_UNAVAILABLE_FAIL_CLOSED_PASS")
            return
        raise AssertionError("client reached a stopped synthetic Relay")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--suite", choices=("core", "basic", "private", "expect-failure"), required=True)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()

