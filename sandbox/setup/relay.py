#!/usr/bin/env python3
"""Relay one private unix socket to the sandbox runtime's socket, byte for byte, honouring half-close.

Why this exists: see 3-service-proxy.sh. `systemd-socket-proxyd` was tried first and loses the container's output after the
client half-closes its side, which is exactly how the sandbox hands over its job (write the job, close stdin, read the result).

    relay.py <listen-socket> <runtime-socket>

The listening socket is created mode 0600 in a directory only this account can enter. Nothing is logged or inspected."""
from __future__ import annotations

import asyncio
import os
import sys


async def pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
    try:
        while chunk := await r.read(65536):
            w.write(chunk)
            await w.drain()
        if w.can_write_eof():
            w.write_eof()  # pass the half-close on; the other direction keeps flowing until its own end
    except (ConnectionError, OSError):
        w.close()


async def handle(cr: asyncio.StreamReader, cw: asyncio.StreamWriter, target: str) -> None:
    try:
        sr, sw = await asyncio.open_unix_connection(target)
    except OSError:
        cw.close()
        return
    await asyncio.gather(pipe(cr, sw), pipe(sr, cw))
    for w in (cw, sw):
        w.close()


async def main(listen: str, target: str) -> None:
    if os.path.exists(listen):
        os.unlink(listen)
    os.umask(0o177)  # the socket is created 0600
    server = await asyncio.start_unix_server(lambda r, w: handle(r, w, target), path=listen)
    os.chmod(listen, 0o600)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
