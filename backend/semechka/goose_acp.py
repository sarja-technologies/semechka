"""Minimal Agent Client Protocol (ACP) client over a child process's stdio.

This is transport plumbing for the REAL Goose agent runtime (`goose acp`, upstream aaif-goose/goose): newline-delimited
JSON-RPC 2.0 over the child's stdin/stdout, exactly the "Build an app with goose" path in Goose's own documentation. It
contains no agent logic: no prompt building, no tool loop, no model calls. Goose owns all of that.

`AcpPeer` runs a reader thread. Responses are matched to waiting callers; notifications go to `on_notification`;
agent-to-client requests (permission prompts, fs/terminal callbacks) go to `on_request`, which must return the JSON-RPC
`result` (or raise `AcpError` to answer with an error).
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from typing import Any, Callable

log = logging.getLogger(__name__)


class AcpError(Exception):
    def __init__(self, message: str, code: int = -32000, data: Any = None):
        super().__init__(message)
        self.code, self.data = code, data


class AcpClosed(AcpError):
    """The agent process went away (or was closed) while a request was outstanding."""


class AcpPeer:
    def __init__(self, argv: list[str], *, env: dict | None = None, cwd: str | None = None,
                 on_notification: Callable[[str, dict], None] | None = None,
                 on_request: Callable[[str, dict], Any] | None = None,
                 on_stderr: Callable[[str], None] | None = None,
                 on_close: Callable[[int | None], None] | None = None):
        self.argv = argv
        self.on_notification = on_notification or (lambda m, p: None)
        self.on_request = on_request
        self.on_stderr = on_stderr
        self.on_close = on_close
        self._lock = threading.Lock()
        self._wlock = threading.Lock()
        self._next = 0
        self._pending: dict[int, dict] = {}
        self.closed = False
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=cwd,
                                     bufsize=0)
        self._rd = threading.Thread(target=self._read_loop, name="acp-read", daemon=True)
        self._er = threading.Thread(target=self._err_loop, name="acp-err", daemon=True)
        self._rd.start()
        self._er.start()

    # ---- wire -------------------------------------------------------------------------------------------------
    def _send(self, obj: dict) -> None:
        data = json.dumps(obj, separators=(",", ":")).encode() + b"\n"
        with self._wlock:
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as e:
                raise AcpClosed("The agent process is not running.") from e

    def _err_loop(self) -> None:
        try:
            for raw in iter(self.proc.stderr.readline, b""):
                if self.on_stderr:
                    try:
                        self.on_stderr(raw.decode("utf-8", "replace").rstrip("\n"))
                    except Exception:  # noqa: BLE001 - a logging callback must never kill the reader
                        pass
        except (OSError, ValueError):
            pass

    def _read_loop(self) -> None:
        try:
            for raw in iter(self.proc.stdout.readline, b""):
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    log.warning("acp: non-JSON line from agent (%d bytes)", len(line))
                    continue
                try:
                    self._dispatch(msg)
                except Exception:  # noqa: BLE001
                    log.exception("acp: dispatch failed")
        except (OSError, ValueError):
            pass
        finally:
            self._finish()

    def _finish(self) -> None:
        self.closed = True
        with self._lock:
            waiters = list(self._pending.values())
            self._pending.clear()
        for w in waiters:
            w["error"] = AcpClosed("The agent process ended.")
            w["event"].set()
        code = None
        try:
            code = self.proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            pass
        if self.on_close:
            try:
                self.on_close(code)
            except Exception:  # noqa: BLE001
                log.exception("acp: on_close failed")

    def _dispatch(self, msg: dict) -> None:
        if "method" in msg and "id" in msg:  # request from the agent
            threading.Thread(target=self._serve_request, args=(msg,), name="acp-req", daemon=True).start()
        elif "method" in msg:                # notification
            self.on_notification(msg["method"], msg.get("params") or {})
        elif "id" in msg:                    # response
            with self._lock:
                w = self._pending.pop(msg["id"], None)
            if w is None:
                return
            if "error" in msg and msg["error"] is not None:
                e = msg["error"]
                w["error"] = AcpError(str(e.get("message", "error")), int(e.get("code", -32000)), e.get("data"))
            else:
                w["result"] = msg.get("result")
            w["event"].set()

    def _serve_request(self, msg: dict) -> None:
        mid, method, params = msg["id"], msg["method"], msg.get("params") or {}
        try:
            if self.on_request is None:
                raise AcpError(f"method not supported: {method}", -32601)
            result = self.on_request(method, params)
            self._send({"jsonrpc": "2.0", "id": mid, "result": result if result is not None else {}})
        except AcpError as e:
            self._try_send({"jsonrpc": "2.0", "id": mid, "error": {"code": e.code, "message": str(e)}})
        except AcpClosed:
            pass
        except Exception as e:  # noqa: BLE001
            log.exception("acp: request handler failed for %s", method)
            self._try_send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"internal error: {type(e).__name__}"}})

    def _try_send(self, obj: dict) -> None:
        try:
            self._send(obj)
        except AcpClosed:
            pass

    # ---- API ----------------------------------------------------------------------------------------------------
    def request(self, method: str, params: dict | None = None, timeout: float | None = 60.0) -> Any:
        with self._lock:
            self._next += 1
            rid = self._next
            w = {"event": threading.Event(), "result": None, "error": None}
            self._pending[rid] = w
        if self.closed:
            raise AcpClosed("The agent process is not running.")
        msg: dict = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        try:
            self._send(msg)
        except AcpClosed:
            with self._lock:
                self._pending.pop(rid, None)
            raise
        if not w["event"].wait(timeout):
            with self._lock:
                self._pending.pop(rid, None)
            raise AcpError(f"timed out waiting for {method}", -32001)
        if w["error"] is not None:
            raise w["error"]
        return w["result"]

    def notify(self, method: str, params: dict | None = None) -> None:
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def close(self, grace: float = 3.0) -> None:
        self.closed = True
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        t0 = time.monotonic()
        while self.proc.poll() is None and time.monotonic() - t0 < grace:
            time.sleep(0.05)
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
