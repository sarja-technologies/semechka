"""Email flows against a minimal SMTP protocol stub (not a real mail provider)."""
import re
import socket
import socketserver
import threading
import time

import pytest

from .conftest import PW, Api, make_user

INBOX: list[str] = []


class _H(socketserver.StreamRequestHandler):
    def handle(self):
        w = lambda s: self.wfile.write((s + "\r\n").encode())  # noqa: E731
        w("220 stub ESMTP")
        data_mode, buf = False, []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            t = line.decode(errors="replace").rstrip("\r\n")
            if data_mode:
                if t == ".":
                    INBOX.append("\n".join(buf))
                    data_mode, buf = False, []
                    w("250 queued")
                else:
                    buf.append(t[1:] if t.startswith("..") else t)
                continue
            cmd = t.upper()
            if cmd.startswith(("EHLO", "HELO")):
                w("250 stub")
            elif cmd.startswith(("MAIL", "RCPT")):
                w("250 ok")
            elif cmd == "DATA":
                w("354 go"); data_mode = True
            elif cmd == "QUIT":
                w("221 bye"); return
            else:
                w("250 ok")


@pytest.fixture
def smtp():
    INBOX.clear()
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def wait_mail(n=1):
    for _ in range(60):
        if len(INBOX) >= n:
            return INBOX[-1]
        time.sleep(0.05)
    raise AssertionError("no email arrived")


def test_verification_and_reset_flow(owner, app, smtp):
    r = owner.put("/api/admin/settings/signup.require_email_verification", json={"value": True, "confirm_weaker": True})
    assert r.status_code == 422  # no SMTP configured yet: refused with the reason
    for k, v in {"smtp.host": "127.0.0.1", "smtp.port": smtp, "smtp.security": "none", "smtp.from_address": "noreply@banana.example"}.items():
        assert owner.put(f"/api/admin/settings/{k}", json={"value": v}).status_code == 200
    assert owner.put("/api/admin/settings/signup.require_email_verification", json={"value": True}).status_code == 200
    owner.put("/api/admin/settings/signup.mode", json={"value": "public", "confirm_weaker": True})
    owner.put("/api/admin/settings/signup.require_approval", json={"value": False, "confirm_weaker": True})
    a = Api(app)
    assert a.post("/api/auth/signup", json={"email": "v@example.test", "password": PW, "display_name": "V"}).status_code == 200
    assert a.login("v@example.test").json()["detail"]["code"] == "pending_verification"
    token = re.search(r"verify\?token=([\w-]+)", wait_mail(1)).group(1)
    assert a.post("/api/auth/verify", json={"token": "wrong"}).status_code == 400
    assert a.post("/api/auth/verify", json={"token": token}).status_code == 200
    assert a.post("/api/auth/verify", json={"token": token}).status_code == 400  # single use
    assert a.login("v@example.test").status_code == 200
    # password reset: same response for known and unknown addresses
    b = Api(app)
    assert b.post("/api/auth/forgot", json={"email": "v@example.test"}).json() == b.post("/api/auth/forgot", json={"email": "ghost@example.test"}).json()
    reset = re.search(r"reset\?token=([\w-]+)", wait_mail(2)).group(1)
    assert b.post("/api/auth/reset", json={"token": reset, "password": "short"}).status_code == 422
    assert b.post("/api/auth/reset", json={"token": reset, "password": "a fresh long passphrase"}).status_code == 200
    assert a.get("/api/me").status_code == 401  # old sessions revoked
    assert Api(app).login("v@example.test", "a fresh long passphrase").status_code == 200
    assert len(INBOX) == 2  # nothing was mailed for the unknown address
