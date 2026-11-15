"""Runs inside the Work Mode container as its main process. Goose speaks HTTP to http://127.0.0.1:8081; this relays the
bytes, unchanged, to the unix socket that Semechka serves (bind-mounted at /run/llm/llm.sock). The container has no network;
this is its only way out, and the other end only forwards model requests. No logic lives here."""
import asyncio
import os
import sys

SOCK = os.environ.get("LLM_SOCK", "/run/llm/llm.sock")
PORT = int(os.environ.get("LLM_PORT", "8081"))


async def pipe(r, w):
    try:
        while True:
            d = await r.read(65536)
            if not d:
                break
            w.write(d)
            await w.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            w.close()
        except OSError:
            pass


async def handle(cr, cw):
    try:
        ur, uw = await asyncio.open_unix_connection(SOCK)
    except OSError:
        cw.close()
        return
    await asyncio.gather(pipe(cr, uw), pipe(ur, cw))


async def main():
    srv = await asyncio.start_server(handle, "127.0.0.1", PORT)
    async with srv:
        await srv.serve_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
