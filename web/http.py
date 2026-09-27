"""HTTP routing and same-origin boundaries; wire shapes are unchanged."""
from __future__ import annotations
import asyncio
from http.server import SimpleHTTPRequestHandler
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parent

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'public'), **kwargs)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        super().end_headers()

    def respond(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        # Local development entry point: reject cross-origin web writes, no external binding or CORS.
        origin = self.headers.get('Origin')
        request_host = self.headers.get('Host')
        same_host_origins = {f'http://{request_host}', f'https://{request_host}'}
        if origin and origin not in same_host_origins:
            return self.respond(403, {'error': 'Origin not allowed.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 32768:
                raise ValueError('Invalid request size.')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('Invalid request.')
            if self.path == '/api/session':
                async def create():
                    token = self.server.bridge.create_session(payload.get('conversation_id'))
                    return await self.server.bridge.session_info(token)
                info = asyncio.run_coroutine_threadsafe(create(), self.server.loop).result(10)
                return self.respond(200, {**info, 'mode': self.server.mode})
            if self.path == '/api/conversations':
                async def conversations():
                    return self.server.bridge.conversations()
                items = asyncio.run_coroutine_threadsafe(conversations(), self.server.loop).result(10)
                return self.respond(200, {'conversations': items})
            if self.path == '/api/favorites/list':
                return self.respond(200, {
                    'favorites': self.server.bridge.list_favorites(
                        self.headers.get('X-Session-ID')
                    )
                })
            if self.path == '/api/favorites/add':
                return self.respond(200, {
                    'favorite': self.server.bridge.add_favorite(
                        self.headers.get('X-Session-ID'), payload
                    )
                })
            if self.path == '/api/favorites/remove':
                return self.respond(200, self.server.bridge.remove_favorite(
                    self.headers.get('X-Session-ID'), payload
                ))
            if self.path == '/api/cancel':
                future = asyncio.run_coroutine_threadsafe(
                    self.server.bridge.cancel(self.headers.get('X-Session-ID'), payload), self.server.loop)
                return self.respond(200, future.result(15))
            if self.path == '/api/progress':
                future = asyncio.run_coroutine_threadsafe(
                    self.server.bridge.progress(self.headers.get('X-Session-ID'), payload), self.server.loop)
                return self.respond(200, future.result(15))
            if self.path != '/api/turn':
                return self.respond(404, {'error': 'Not found.'})
            future = asyncio.run_coroutine_threadsafe(self.server.bridge.turn(self.headers.get('X-Session-ID'), payload), self.server.loop)
            return self.respond(200, future.result())
        except (ValueError, TypeError) as exc:
            return self.respond(400, {'error': str(exc)})
        except Exception:
            return self.respond(503, {'error': 'The service could not complete this turn. Please retry.'})
