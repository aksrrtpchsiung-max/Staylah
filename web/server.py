"""StayLah server entry point; keep python -m web.server compatible."""
from __future__ import annotations
import argparse
import asyncio
from pathlib import Path
import sys

if __package__ in {None, ""}:
    # Retain direct script startup without shadowing stdlib http with web/http.py.
    sys.path[0] = str(Path(__file__).resolve().parents[1])
    __package__ = "web"

from http.server import ThreadingHTTPServer
import os
import threading
from .bridge import WebBridge
from .http import Handler, ROOT

def main():
    from dotenv import load_dotenv

    env_path = Path(__file__).resolve().parents[1] / '.env'
    load_dotenv(env_path)
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--host', default=os.getenv('STAYLAH_HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=os.getenv('STAYLAH_PORT', '8080'))
    args = parser.parse_args()
    async def run():
        from contextlib import AsyncExitStack
        async with AsyncExitStack() as stack:
            orchestrator = None
            if args.live:
                from property_agent.orchestration.postgres import postgres_conversation_runtime
                orchestrator = await stack.enter_async_context(postgres_conversation_runtime())
            server = ThreadingHTTPServer((args.host, args.port), Handler)
            server.daemon_threads = True
            server.loop = asyncio.get_running_loop()
            server.bridge = WebBridge(orchestrator)
            server.mode = 'live' if args.live else 'preview'
            threading.Thread(target=server.serve_forever, daemon=True).start()
            if args.host in {'0.0.0.0', '::'}:
                location = f'{args.host}:{server.server_port}'
                print(f'StayLah listening on {location} ({server.mode})', flush=True)
            else:
                print(f'StayLah: http://{args.host}:{server.server_port} ({server.mode})', flush=True)
            try:
                await asyncio.Event().wait()
            finally:
                server.shutdown()
                server.server_close()
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
