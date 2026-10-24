from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import delete, select

from . import __version__, audit, services
from .config import Config
from .models import AuditEvent, Conversation, UsageEvent
from .routers import admin_config, admin_people, auth, chat, me, misc, setup
from .state import AppState

log = logging.getLogger("semechka")


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config()
    st = AppState.build(cfg)
    with st.db.session() as s:
        services.seed(s)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        token = setup.ensure_setup_token(st)
        if token:
            print(f"\n  Semechka setup token (one time, expires when the owner account is created):\n    {token}\n"
                  f"  Also saved to {cfg.setup_token_path}\n", flush=True)
        task = asyncio.create_task(_sweeper(st)) if cfg.background else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Semechka", version=__version__, lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json",
                  redoc_url=None)
    app.state.sem = st

    @app.middleware("http")
    async def security_headers_and_origin(request: Request, call_next):
        # CSRF defence for unauthenticated unsafe requests too: a browser always sends Origin on cross-site POSTs.
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin:
                host = request.headers.get("host", "")
                if urlsplit(origin).netloc != host:
                    return JSONResponse({"detail": {"code": "bad_origin", "message": "Cross-site request refused."}}, status_code=403)
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if not request.url.path.startswith("/api/assets/"):
            resp.headers.setdefault("Content-Security-Policy",
                                    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                    f"script-src 'self' {' '.join(getattr(request.app.state, 'script_hashes', []))}; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.exception_handler(HTTPException)
    async def http_exc(_, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_exc(_, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
        return JSONResponse({"detail": {"code": "invalid", "message": f"{loc or 'Request'}: {first.get('msg', 'invalid')}"}}, status_code=422)

    for r in (setup.router, auth.router, me.router, chat.router, admin_people.router, admin_config.router, misc.router):
        app.include_router(r)

    web = cfg.web_dir
    if web and web.exists():
        _mount_spa(app, web)
    return app


def _inline_script_hashes(index: Path) -> list[str]:
    """SvelteKit's static shell has one inline bootstrap script; allow exactly its hash, never 'unsafe-inline'."""
    html = index.read_text(encoding="utf-8")
    return ["'sha256-" + base64.b64encode(hashlib.sha256(m.encode()).digest()).decode() + "'"
            for m in re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, flags=re.S) if m.strip()]


def _mount_spa(app: FastAPI, web: Path):
    index = web / "index.html"
    app.state.script_hashes = _inline_script_hashes(index)

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        f = (web / path).resolve()
        if path and f.is_file() and web.resolve() in f.parents:
            return FileResponse(f, headers={"Cache-Control": "public, max-age=3600" if "_app/immutable" not in path else "public, max-age=31536000, immutable"})
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


async def _sweeper(st: AppState):
    """Retention: delete expired conversations and aged usage/audit rows."""
    while True:
        try:
            await asyncio.to_thread(sweep_once, st)
        except Exception:  # noqa: BLE001
            log.exception("retention sweep failed")
        await asyncio.sleep(300)


def sweep_once(st: AppState) -> dict:
    now = time.time()
    with st.db.session() as s:
        expired = s.execute(select(Conversation).where(Conversation.expires_at != None, Conversation.expires_at < now)).scalars().all()  # noqa: E711
        for c in expired:
            s.delete(c)
        ud = st.settings.deployment(s, "privacy.usage_retention_days")
        n_usage = s.execute(delete(UsageEvent).where(UsageEvent.ts < now - ud * 86400)).rowcount
        n = len(expired)
        if n:
            audit.record(s, "", "retention.sweep", "", {"conversations_deleted": n})
    return {"conversations": n, "usage_rows": n_usage}
