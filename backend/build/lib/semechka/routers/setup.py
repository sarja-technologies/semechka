from __future__ import annotations

import hmac
import os
import secrets
import socket
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import audit, security, services, telemetry
from .. import adapters
from ..backends_svc import adapter_for, sync_models
from ..crypto import sha256_hex
from ..deps import get_db, get_state, issue_session
from ..models import Backend, Meta, Model, ModelGrant, User, UserRole
from ..presets import PRESETS
from ..settings_reg import BY_ID, SettingError
from ..state import AppState

router = APIRouter(prefix="/api/setup")


def needs_setup(s) -> bool:
    return s.get(Meta, "setup_complete") is None


def ensure_setup_token(st: AppState) -> str | None:
    """Create a fresh one-time owner token when setup is pending. Written 0600; deleted on completion."""
    with st.db.session() as s:
        if not needs_setup(s):
            return None
        token = secrets.token_urlsafe(18)
        row = s.get(Meta, "setup_token_hash")
        if row:
            row.value = sha256_hex(token)
        else:
            s.add(Meta(key="setup_token_hash", value=sha256_hex(token)))
    p = st.cfg.setup_token_path
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    return token


def _check_token(request: Request, s, st: AppState):
    if not needs_setup(s):
        raise HTTPException(404, {"code": "not_found", "message": "Setup is already complete."})
    ip = request.client.host if request.client else ""
    if not st.limiter.hit(f"setup:{ip}", 20, 60):
        raise HTTPException(429, {"code": "rate_limited", "message": "Too many attempts."})
    given = request.headers.get("x-setup-token", "")
    row = s.get(Meta, "setup_token_hash")
    if not row or not hmac.compare_digest(sha256_hex(given), row.value):
        raise HTTPException(403, {"code": "bad_setup_token", "message": "The setup token is missing or wrong."})


_CANDIDATES = [
    ("sarness", "Sarness Engine", "http://127.0.0.1:8742"),
    ("ollama", "Ollama", "http://127.0.0.1:11434"),
    ("openai_compat", "llama.cpp server", "http://127.0.0.1:8080/v1"),
    ("openai_compat", "vLLM", "http://127.0.0.1:8000/v1"),
]


@router.get("/status")
def status(s=Depends(get_db)):
    return {"needs_setup": needs_setup(s)}


@router.get("/hardware")
async def hardware(request: Request, s=Depends(get_db), st: AppState = Depends(get_state)):
    _check_token(request, s, st)
    found = []
    async with httpx.AsyncClient(timeout=1.5) as c:
        for adapter_id, label, url in _CANDIDATES:
            ad = adapters.make(adapter_id, url)
            h = await ad.health()
            if h["ok"] or "rejected" in h["detail"]:
                found.append({"adapter": adapter_id, "label": label, "base_url": url, "reachable": h["ok"],
                              "detail": h["detail"]})
    gpus = telemetry.gpus()
    return {"host": telemetry.host(), "storage": telemetry.storage(str(st.cfg.data_dir)),
            "gpus": gpus, "gpu_note": "nvidia-smi not found; only NVIDIA GPUs are detected in this release."
            if gpus is None else "", "backends": found, "adapters": [a.manifest.as_dict() for a in adapters.REGISTRY.values()],
            "presets": {k: {"label": v["label"], "description": v["description"], "settings": v["settings"]}
                        for k, v in PRESETS.items()},
            "bind": {"host": st.cfg.host, "port": st.cfg.port}}


class BackendProbe(BaseModel):
    adapter: str
    base_url: str = Field(max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    external: bool = False


@router.post("/backend-test")
async def backend_test(body: BackendProbe, request: Request, s=Depends(get_db), st: AppState = Depends(get_state)):
    _check_token(request, s, st)
    _validate_url(body.base_url)
    ad = adapters.make(body.adapter, body.base_url, body.api_key, {"external": body.external})
    h = await ad.health()
    models = []
    if h["ok"]:
        try:
            models = [{"real_id": m.real_id, "capabilities": m.capabilities, "meta": m.meta, "external": m.external}
                      for m in await ad.discover()]
        except adapters.AdapterError as e:
            h = {**h, "ok": False, "detail": str(e)}
    return {"health": h, "models": models, "manifest": ad.manifest.as_dict()}


def _validate_url(u: str):
    if not u.startswith(("http://", "https://")):
        raise HTTPException(422, {"code": "invalid", "message": "Backend address must start with http:// or https://"})


class OwnerIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=300)
    display_name: str = Field(min_length=1, max_length=120)


class CompleteIn(BaseModel):
    owner: OwnerIn
    preset: str = "business"
    settings: dict = Field(default_factory=dict)
    backend: BackendProbe | None = None
    backend_name: str = "Primary backend"
    enable_models: list[str] = Field(default_factory=list)  # real ids
    smoke_model: str | None = None


@router.post("/complete")
async def complete(body: CompleteIn, request: Request, response: Response, s=Depends(get_db),
                   st: AppState = Depends(get_state)):
    _check_token(request, s, st)
    ip = request.client.host if request.client else ""
    if body.preset not in PRESETS:
        raise HTTPException(422, {"code": "invalid", "message": "Unknown preset."})
    email = body.owner.email.strip().lower()
    if "@" not in email:
        raise HTTPException(422, {"code": "invalid", "message": "Enter a valid email address."})
    min_len = max(st.settings.deployment(s, "auth.password_min_length"),
                  int(body.settings.get("auth.password_min_length", 0) or 0))
    problem = security.password_problem(body.owner.password, email, min_len)
    if problem:
        raise HTTPException(422, {"code": "weak_password", "message": problem})

    # Validate every setting before changing anything (atomic apply).
    merged = dict(PRESETS[body.preset]["settings"])
    merged.update(body.settings)
    cleaned = {}
    for k, v in merged.items():
        try:
            cleaned[k] = st.settings.validate(s, k, v)
        except SettingError as e:
            raise HTTPException(422, {"code": "invalid_setting", "setting": k, "message": str(e)})

    user = User(email=email, display_name=body.owner.display_name.strip(),
                password_hash=security.hash_password(body.owner.password), status="active")
    s.add(user)
    s.flush()
    services.seed(s)
    s.flush()
    s.add(UserRole(user_id=user.id, role_id="owner"))
    batch = "setup"
    for k, v in cleaned.items():
        st.settings.set(s, user.id, k, v, batch=batch, ip=ip, confirm_weaker=True)
    # preset role grants
    from ..models import Role
    for rid, extra in PRESETS[body.preset]["role_grants"].items():
        r = s.get(Role, rid)
        if r:
            r.grants = sorted(set(list(r.grants or []) + extra))
    result = {"models_enabled": 0, "smoke": None}
    backend_id = None
    if body.backend:
        _validate_url(body.backend.base_url)
        b = Backend(name=body.backend_name[:120], adapter=body.backend.adapter, base_url=body.backend.base_url.rstrip("/"),
                    api_key_enc=st.vault.encrypt(body.backend.api_key, "backend") if body.backend.api_key else None,
                    external=body.backend.external)
        s.add(b)
        s.flush()
        backend_id = b.id
    audit.record(s, user.id, "setup.complete", user.id, {"preset": body.preset, "backend": bool(backend_id)}, ip)
    s.add(Meta(key="setup_complete", value="1"))
    row = s.get(Meta, "setup_token_hash")
    if row:
        s.delete(row)
    s.commit()
    try:
        st.cfg.setup_token_path.unlink()
    except FileNotFoundError:
        pass
    if backend_id:
        try:
            await sync_models(st.db.session, st, backend_id)
        except adapters.AdapterError as e:
            result["backend_error"] = str(e)
        with st.db.session() as s2:
            for m in s2.execute(select(Model).where(Model.backend_id == backend_id)).scalars():
                if m.real_id in body.enable_models:
                    m.enabled = True
                    for rid in ("owner", "member"):
                        s2.add(ModelGrant(model_id=m.id, subject_type="role", subject_id=rid, effect="allow"))
                    result["models_enabled"] += 1
            if body.smoke_model:
                result["smoke"] = await _smoke(st, s2, backend_id, body.smoke_model)
    with st.db.session() as s3:
        u = s3.get(User, user.id)
        issue_session(s3, st, request, response, u, True)
    return {"ok": True, **result}


async def _smoke(st, s, backend_id, real_id) -> dict:
    b = s.get(Backend, backend_id)
    ad = adapter_for(b, st)
    text = ""
    try:
        async for ev in ad.chat_stream(real_id, [{"role": "user", "content": "Reply with the single word: ready"}],
                                       {"max_tokens": 16, "temperature": 0}):
            if ev["type"] == "delta":
                text += ev["content"]
        return {"ok": bool(text.strip()), "reply": text.strip()[:80]}
    except adapters.AdapterError as e:
        return {"ok": False, "error": str(e)}
