"""Synthetic private-network sentinels for the offline topology test."""
from __future__ import annotations

import argparse
import asyncio


async def main(endpoints: list[str]) -> None:
    servers = []

    async def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.read(64)
        finally:
            writer.close()
            await writer.wait_closed()

    for endpoint in endpoints:
        host, separator, raw_port = endpoint.rpartition(":")
        if not separator:
            raise ValueError("sentinel endpoint is invalid")
        servers.append(await asyncio.start_server(accept, host, int(raw_port)))
    await asyncio.gather(*(server.serve_forever() for server in servers))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", action="append", required=True)
    asyncio.run(main(parser.parse_args().listen))

