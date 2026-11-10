"""Per-session model gateway for Work Mode containers.

A Work Mode container has no network at all. The only thing it can reach is a unix socket that is bind-mounted into it
(`/run/llm/llm.sock`), and a few lines of Python inside the container turn Goose's loopback HTTP requests into bytes on that
socket. This module is the other end of the socket, run inside the Semechka service. It is transport plumbing, not an
agent: it forwards exactly two requests to the llama.cpp router and nothing else.

  POST /v1/chat/completions   forwarded (streaming or not); the `model` must be one the session is allowed to use
  GET  /v1/models             answered locally with the allowed models only (the router's full list is never shown)

What it adds or enforces:
  * the router API key is attached here; the container never holds it (any Authorization header it sends is dropped)
  * the model is pinned to the session's allowlist (a session cannot ask for a model it was not granted, even if it edits
    Goose's own configuration inside the container)
  * every other path or method gets 404/405; request bodies are size-capped; concurrent requests are capped
  * each request is counted, so the session can report usage and the audit trail can show model activity
"""
from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Callable

import httpx

log = logging.getLogger(__name__)
MAX_BODY = 16 * 1024 * 1024
MAX_CONCURRENT = 4
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authorization", "proxy-authenticate",
       "content-length", "content-encoding"}


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 16


class GatewayConfig:
    """Everything the gateway needs for one session. `key` is read lazily so a rotated key file is picked up."""

    def __init__(self, router_url: str, key_file: str, allowed_models: Callable[[], set[str]], default_model: str,
                 on_request: Callable[[dict], None] | None = None, upstream_timeout: float = 900.0):
        self.router_url = router_url.rstrip("/")
        self.key_file = key_file
        self.allowed_models = allowed_models
        self.default_model = default_model
        self.on_request = on_request
        self.upstream_timeout = upstream_timeout

    def key(self) -> str:
        try:
            return Path(self.key_file).read_text().strip()
        except OSError:
            return ""


class ModelGateway:
    """Serve one session's socket until `close()`. Create one per session container."""

    def __init__(self, sock_path: str, cfg: GatewayConfig):
        self.sock_path = sock_path
        self.cfg = cfg
        self.requests = 0
        self.errors = 0
        self.last_request_at = 0.0
        self._sem = threading.BoundedSemaphore(MAX_CONCURRENT)
        self._client = httpx.Client(timeout=httpx.Timeout(cfg.upstream_timeout, connect=10.0), trust_env=False)
        gw = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "semechka-gateway"
            sys_version = ""

            def log_message(self, fmt, *args):  # noqa: A003 - silence the default stderr access log
                pass

            def setup(self):
                super().setup()
                self.close_connection = True

            def do_GET(self):  # noqa: N802
                gw._handle(self, "GET")

            def do_POST(self):  # noqa: N802
                gw._handle(self, "POST")

            def do_PUT(self):  # noqa: N802
                self._deny(405)

            do_DELETE = do_PATCH = do_PUT  # noqa: N815

            def _deny(self, code: int, msg: str = "not allowed"):
                gw._reply_json(self, code, {"error": {"message": msg, "type": "gateway"}})

        os.makedirs(os.path.dirname(sock_path), exist_ok=True)
        try:
            os.unlink(sock_path)
        except FileNotFoundError:
            pass
        self.server = _Server(sock_path, Handler)
        os.chmod(sock_path, 0o666)  # the container's user is a sub-uid of another account; the directory mount is the access control
        self.thread = threading.Thread(target=self.server.serve_forever, name="goose-gw", daemon=True)
        self.thread.start()

    # ---- helpers --------------------------------------------------------------------------------------------
    @staticmethod
    def _reply_json(h: BaseHTTPRequestHandler, code: int, obj: dict) -> None:
        data = json.dumps(obj).encode()
        try:
            h.send_response(code)
            h.send_header("Content-Type", "application/json")
            h.send_header("Content-Length", str(len(data)))
            h.send_header("Connection", "close")
            h.end_headers()
            h.wfile.write(data)
            h.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _note(self, **kw) -> None:
        self.requests += 1
        self.last_request_at = time.time()
        if kw.get("status", 200) >= 400:
            self.errors += 1
        if self.cfg.on_request:
            try:
                self.cfg.on_request(kw)
            except Exception:  # noqa: BLE001 - accounting must never break a model call
                log.exception("gateway on_request failed")

    def _handle(self, h: BaseHTTPRequestHandler, method: str) -> None:
        path = h.path.split("?", 1)[0].rstrip("/")
        if path == "/v1/models" and method == "GET":
            data = [{"id": m, "object": "model", "owned_by": "semechka"} for m in sorted(self.cfg.allowed_models())]
            self._reply_json(h, 200, {"object": "list", "data": data})
            self._note(path=path, status=200)
            return
        if path != "/v1/chat/completions":
            self._reply_json(h, 404, {"error": {"message": "unknown path", "type": "gateway"}})
            return
        if method != "POST":
            self._reply_json(h, 405, {"error": {"message": "method not allowed", "type": "gateway"}})
            return
        try:
            n = int(h.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n <= 0 or n > MAX_BODY:
            self._reply_json(h, 413 if n > 0 else 411, {"error": {"message": "request body missing or too large", "type": "gateway"}})
            return
        raw = h.rfile.read(n)
        try:
            body = json.loads(raw)
            assert isinstance(body, dict)
        except (ValueError, AssertionError):
            self._reply_json(h, 400, {"error": {"message": "invalid JSON", "type": "gateway"}})
            return
        allowed = self.cfg.allowed_models()
        model = body.get("model") or self.cfg.default_model
        if model not in allowed:
            self._reply_json(h, 403, {"error": {"message": f"model '{str(model)[:80]}' is not available to this session", "type": "gateway"}})
            self._note(path=path, status=403, model=str(model)[:80])
            return
        body["model"] = model
        key = self.cfg.key()
        if not key:
            self._reply_json(h, 502, {"error": {"message": "the model service key is not configured", "type": "gateway"}})
            self._note(path=path, status=502, model=model)
            return
        if not self._sem.acquire(timeout=5):
            self._reply_json(h, 429, {"error": {"message": "too many concurrent model requests", "type": "gateway"}})
            return
        t0 = time.monotonic()
        status = 0
        try:
            req = self._client.build_request("POST", self.cfg.router_url + "/v1/chat/completions", json=body,
                                             headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                                      "Accept": h.headers.get("Accept", "*/*")})
            try:
                resp = self._client.send(req, stream=True)
            except httpx.HTTPError as e:
                self._reply_json(h, 502, {"error": {"message": f"model service unreachable ({type(e).__name__})", "type": "gateway"}})
                self._note(path=path, status=502, model=model)
                return
            status = resp.status_code
            try:
                h.send_response(status)
                for k, v in resp.headers.items():
                    if k.lower() not in HOP:
                        h.send_header(k, v)
                h.send_header("Connection", "close")  # body is delimited by the close; no chunk framing to get wrong
                h.end_headers()
                for chunk in resp.iter_raw():
                    h.wfile.write(chunk)
                    h.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # the agent hung up (cancel): stop reading from the router too
            finally:
                resp.close()
        finally:
            self._sem.release()
            self._note(path=path, status=status or 499, model=model, seconds=round(time.monotonic() - t0, 2))

    def close(self) -> None:
        try:
            self.server.shutdown()
            self.server.server_close()
        except OSError:
            pass
        self._client.close()
        try:
            os.unlink(self.sock_path)
        except OSError:
            pass


def unix_request(sock_path: str, request: bytes, timeout: float = 10.0) -> bytes:
    """Tiny raw client used by tests and health checks."""
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(timeout)
    s.connect(sock_path)
    s.sendall(request)
    out = b""
    while True:
        c = s.recv(65536)
        if not c:
            break
        out += c
    s.close()
    return out
