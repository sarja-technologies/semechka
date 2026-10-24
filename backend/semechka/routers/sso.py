"""OpenID Connect sign-in (authorization code + PKCE) and its administration."""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from .. import audit, sso_svc
from ..deps import Ctx, client_ip, current, get_db, get_state, issue_session, optional_ctx, require
from ..models import Role, SessionRow, User, UserRole
from ..sso_models import SsoFlow, SsoIdentity, SsoProvider, SsoSession
from ..sso_svc import SsoError
from ..state import AppState
from . import admin_people

router = APIRouter()
FLOW_TTL = 600
SSO_ERRORS = {  # what people see; the reason for the audit log never reaches the browser
    "denied": "Sign-in was cancelled or refused by the identity provider.",
    "expired": "That sign-in attempt expired. Start again.",
    "unverified_email": "Your identity provider did not confirm your email address, so you cannot sign in with it.",
    "domain_not_allowed": "Your email domain is not allowed for this sign-in.",
    "existing_account": "An account with this email already exists. Sign in with your password, then connect SSO from your account page.",
    "not_registered": "Your organisation does not create accounts automatically. Ask an administrator for access.",
    "pending_approval": "Your account is waiting for administrator approval.",
    "suspended": "This account is not active. Contact your administrator.",
    "linked_elsewhere": "That identity is already connected to a different account.",
    "provider_error": "The identity provider could not be reached or returned something unexpected.",
    "rate_limited": "Too many attempts. Wait a minute and try again.",
    "failed": "Sign-in failed.",
}


def _err(status, code, message, **kw):
    return HTTPException(status, {"code": code, "message": message, **kw})


def _public_base(request: Request, s, st: AppState) -> str:
    configured = (st.settings.deployment(s, "access.public_url") or "").strip().rstrip("/")
    if configured.startswith(("http://", "https://")):
        return configured
    return str(request.base_url).rstrip("/")


def _redirect_uri(request: Request, s, st: AppState) -> str:
    return _public_base(request, s, st) + "/api/sso/callback"


def _h(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def sso_enforced(s, st: AppState) -> bool:
    if st.settings.deployment(s, "sso.local_login"):
        return False
    return s.execute(select(SsoProvider.id).where(SsoProvider.enabled == True).limit(1)).first() is not None  # noqa: E712


def local_login_blocked(s, st: AppState, user: User) -> bool:
    """Password sign-in is refused only when an admin turned it off AND an enabled provider exists.
    Owners are always exempt: that is the break-glass path if the identity provider breaks."""
    return sso_enforced(s, st) and not any(r.role_id == "owner" for r in user.roles)


def _login_url(code: str) -> RedirectResponse:
    return RedirectResponse(f"/login?sso_error={code if code in SSO_ERRORS else 'failed'}", status_code=303,
                            headers={"Cache-Control": "no-store"})


# ---- public ---------------------------------------------------------------------------------------------
@router.get("/api/sso/providers")
def public_providers(s=Depends(get_db), st: AppState = Depends(get_state)):
    rows = s.execute(select(SsoProvider).where(SsoProvider.enabled == True).order_by(SsoProvider.name)).scalars().all()  # noqa: E712
    return {"providers": [{"id": p.id, "name": p.name} for p in rows],
            "local_login": bool(st.settings.deployment(s, "sso.local_login")) or not rows,
            "owner_local_login": True, "errors": SSO_ERRORS}


@router.get("/api/sso/{pid}/start")
async def start(pid: str, request: Request, return_to: str = "/", link: int = 0, s=Depends(get_db),
                st: AppState = Depends(get_state), ctx: Ctx | None = Depends(optional_ctx)):
    ip = client_ip(request)
    if not st.limiter.hit(f"sso:start:{ip}", 30, 60):
        return _login_url("rate_limited")
    p = s.get(SsoProvider, pid)
    if p is None or not p.enabled:
        return _login_url("failed")
    if link and ctx is None:
        return _login_url("failed")
    try:
        disc = await sso_svc.discover(p.issuer, allow_private=p.allow_private_network)
    except SsoError as e:
        audit.record(s, "", "sso.login_failed", p.id, {"reason": e.code, "stage": "discovery"}, ip)
        s.commit()
        return _login_url("provider_error")
    state, nonce, verifier, binding = (secrets.token_urlsafe(32) for _ in range(4))
    redirect_uri = _redirect_uri(request, s, st)
    s.execute(delete(SsoFlow).where(SsoFlow.expires_at < time.time()))
    s.add(SsoFlow(state_hash=_h(state), provider_id=p.id, nonce=nonce, verifier_enc=st.vault.encrypt(verifier, "sso"),
                  binding_hash=_h(binding), redirect_uri=redirect_uri, return_to=sso_svc.safe_return_path(return_to),
                  link_user_id=ctx.user.id if (link and ctx) else None, expires_at=time.time() + FLOW_TTL))
    q = urlencode({"response_type": "code", "client_id": p.client_id, "redirect_uri": redirect_uri, "scope": p.scopes,
                   "state": state, "nonce": nonce, "code_challenge": pkce_challenge(verifier), "code_challenge_method": "S256"})
    sep = "&" if "?" in disc["authorization_endpoint"] else "?"
    resp = RedirectResponse(disc["authorization_endpoint"] + sep + q, status_code=303, headers={"Cache-Control": "no-store"})
    resp.set_cookie(st.cookie_name + "_sso", binding, httponly=True, samesite="lax", path="/api/sso", max_age=FLOW_TTL,
                    secure=redirect_uri.startswith("https://"))
    audit.record(s, ctx.user.id if ctx else "", "sso.start", p.id, {"link": bool(link and ctx)}, ip)
    return resp


@router.get("/api/sso/callback")
async def callback(request: Request, code: str = "", state: str = "", error: str = "", s=Depends(get_db),
                   st: AppState = Depends(get_state)):
    ip = client_ip(request)
    if not st.limiter.hit(f"sso:cb:{ip}", 30, 60):
        return _login_url("rate_limited")
    flow = s.get(SsoFlow, _h(state)) if state else None
    binding = request.cookies.get(st.cookie_name + "_sso", "")
    if flow is not None:
        s.delete(flow)  # single use, whatever happens next
        s.flush()
    if (flow is None or flow.expires_at < time.time() or not binding
            or not secrets.compare_digest(_h(binding), flow.binding_hash)):
        audit.record(s, "", "sso.login_failed", "", {"reason": "state"}, ip)
        s.commit()
        return _login_url("expired")
    p = s.get(SsoProvider, flow.provider_id)
    if p is None or not p.enabled:
        return _login_url("failed")
    resp = None
    try:
        if error or not code:
            raise SsoError("denied", f"idp error {error[:40]}")
        user, id_token = await _complete(request, s, st, p, flow, code, ip)
        resp = RedirectResponse(flow.return_to if not flow.link_user_id else "/account", status_code=303,
                                headers={"Cache-Control": "no-store"})
        if not flow.link_user_id:
            sess = issue_session(s, st, request, resp, user, mfa_ok=True)  # the IdP is responsible for MFA
            s.add(SsoSession(session_hash=sess.id_hash, provider_id=p.id, id_token_enc=st.vault.encrypt(id_token, "sso")))
            user.last_login_at = time.time()
            audit.record(s, user.id, "sso.login", p.id, {}, ip)
    except SsoError as e:
        public = e.code if e.code in SSO_ERRORS else ("provider_error" if e.code.startswith(("provider", "bad_", "token", "issuer", "blocked", "unresolv", "insecure", "response")) else "failed")
        audit.record(s, "", "sso.login_failed", p.id, {"reason": e.code, "detail": e.detail[:120]}, ip)
        s.commit()
        resp = _login_url(public)
    resp.delete_cookie(st.cookie_name + "_sso", path="/api/sso")
    return resp


async def _complete(request, s, st: AppState, p: SsoProvider, flow: SsoFlow, code: str, ip: str):
    priv = p.allow_private_network
    disc = await sso_svc.discover(p.issuer, allow_private=priv)
    secret = st.vault.decrypt(p.client_secret_enc, "sso") if p.client_secret_enc else None
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": flow.redirect_uri,
            "code_verifier": st.vault.decrypt(flow.verifier_enc, "sso"), "client_id": p.client_id}
    auth = None
    methods = disc.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
    if secret:
        if "client_secret_basic" in methods:
            auth = (p.client_id, secret)
            form.pop("client_id")
        else:
            form["client_secret"] = secret
    tok = await sso_svc.fetch_json(disc["token_endpoint"], method="POST", data=form, auth=auth, allow_private=priv)
    id_token = tok.get("id_token")
    if not isinstance(id_token, str):
        raise SsoError("provider_bad_response", "no id_token")
    claims = await sso_svc.verify_id_token(id_token, issuer=p.issuer, client_id=p.client_id, nonce=flow.nonce,
                                           jwks_uri=disc["jwks_uri"], allow_private=priv)
    email = str(claims.get(p.email_claim) or "").strip().lower()
    if "@" not in email:
        raise SsoError("unverified_email", "no email claim")
    verified = claims.get("email_verified")
    if not (verified is True or verified == "true" or (p.assume_email_verified and verified is None)):
        raise SsoError("unverified_email", "email_verified not true")
    if p.allowed_domains and email.rsplit("@", 1)[1] not in [d.lower().lstrip("@") for d in p.allowed_domains]:
        raise SsoError("domain_not_allowed", email.rsplit("@", 1)[1])
    sub = claims["sub"]
    groups = claims.get(p.groups_claim) or []
    groups = [str(g) for g in groups] if isinstance(groups, list) else [str(groups)]
    ident = s.execute(select(SsoIdentity).where(SsoIdentity.provider_id == p.id, SsoIdentity.subject == sub)).scalar_one_or_none()

    if flow.link_user_id:  # explicit link: the person is already signed in locally and proves the IdP account too
        cur = _session_user(request, s, st)
        if cur is None or cur.id != flow.link_user_id:
            raise SsoError("failed", "link session mismatch")
        if ident is not None and ident.user_id != cur.id:
            raise SsoError("linked_elsewhere")
        if ident is None:
            s.add(SsoIdentity(provider_id=p.id, subject=sub, user_id=cur.id, granted_roles=[]))
            audit.record(s, cur.id, "sso.link", p.id, {}, ip)
        return cur, id_token

    if ident is not None:
        user = s.get(User, ident.user_id)
    else:
        if s.execute(select(User).where(User.email == email)).scalar_one_or_none():
            raise SsoError("existing_account", "no silent linking")
        if not p.jit_create:
            raise SsoError("not_registered")
        user = User(email=email, display_name=(str(claims.get(p.name_claim) or "").strip() or email.split("@")[0])[:120],
                    password_hash=None, status="pending_approval" if p.require_approval else "active")
        s.add(user)
        s.flush()
        ident = SsoIdentity(provider_id=p.id, subject=sub, user_id=user.id, granted_roles=[])
        s.add(ident)
        audit.record(s, user.id, "sso.jit_create", p.id, {"status": user.status}, ip)
    if user is None or user.status != "active":
        raise SsoError("pending_approval" if user and user.status == "pending_approval" else "suspended")
    _sync_roles(s, p, user, ident, groups, ip)
    ident.last_login_at = time.time()
    return user, id_token


def _session_user(request, s, st: AppState) -> User | None:
    tok = request.cookies.get(st.cookie_name)
    row = s.get(SessionRow, _h(tok)) if tok else None
    return s.get(User, row.user_id) if row and row.expires_at > time.time() else None


def _sync_roles(s, p: SsoProvider, user: User, ident: SsoIdentity, groups: list[str], ip: str):
    wanted: set[str] = set()
    for g in groups:
        wanted.update(p.role_map.get(g, []))
    if not wanted:
        wanted = {p.default_role}
    wanted = {r for r in wanted if s.get(Role, r)}
    have = {ur.role_id for ur in user.roles}
    previous = set(ident.granted_roles or [])
    # Add what the groups give; remove only what an earlier SSO login gave and the groups no longer give.
    for r in wanted - have:
        s.add(UserRole(user_id=user.id, role_id=r))
    for ur in list(user.roles):
        if ur.role_id in previous - wanted:
            s.delete(ur)
    if wanted != previous:
        audit.record(s, user.id, "sso.roles_sync", p.id, {"roles": sorted(wanted)}, ip)
    ident.granted_roles = sorted(wanted)
    s.flush()
    s.refresh(user)
    if not [ur for ur in user.roles]:
        s.add(UserRole(user_id=user.id, role_id="guest"))


@router.post("/api/sso/logout")
def sso_logout(request: Request, response: Response, s=Depends(get_db), st: AppState = Depends(get_state),
               ctx: Ctx | None = Depends(optional_ctx)):
    """Ends the local session; returns an end_session URL only if the provider advertises one."""
    redirect = None
    if ctx:
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), ctx.session.csrf):
            raise _err(403, "csrf", "Missing or invalid CSRF token.")
        link = s.get(SsoSession, ctx.session.id_hash)
        if link:
            p = s.get(SsoProvider, link.provider_id)
            s.delete(link)
            if p:
                cached = sso_svc._disc_cache.get(p.issuer.rstrip("/"))
                end = (cached.doc if cached else {}).get("end_session_endpoint")
                if isinstance(end, str):
                    try:
                        sso_svc.check_url(end, allow_private=p.allow_private_network)
                        q = {"client_id": p.client_id}
                        if link.id_token_enc:
                            q["id_token_hint"] = st.vault.decrypt(link.id_token_enc, "sso")
                        q["post_logout_redirect_uri"] = _public_base(request, s, st) + "/login"
                        redirect = end + ("&" if "?" in end else "?") + urlencode(q)
                    except SsoError:
                        redirect = None
        s.delete(s.get(SessionRow, ctx.session.id_hash))
        audit.record(s, ctx.user.id, "auth.logout", "", {"sso": bool(redirect)}, client_ip(request))
    response.delete_cookie(st.cookie_name, path="/")
    return {"ok": True, "redirect": redirect}


# ---- my linked identities ------------------------------------------------------------------------------
@router.get("/api/sso/identities")
def my_identities(ctx: Ctx = Depends(current)):
    rows = ctx.s.execute(select(SsoIdentity, SsoProvider).join(SsoProvider, SsoProvider.id == SsoIdentity.provider_id)
                         .where(SsoIdentity.user_id == ctx.user.id)).all()
    return {"linked": [{"id": i.id, "provider": p.name, "provider_id": p.id} for i, p in rows],
            "available": [{"id": p.id, "name": p.name} for p in ctx.s.execute(select(SsoProvider).where(SsoProvider.enabled == True)).scalars()],  # noqa: E712
            "has_password": bool(ctx.user.password_hash)}


@router.delete("/api/sso/identities/{iid}")
def unlink(iid: int, ctx: Ctx = Depends(current)):
    i = ctx.s.get(SsoIdentity, iid)
    if i is None or i.user_id != ctx.user.id:
        raise _err(404, "not_found", "No such connection.")
    if not ctx.user.password_hash:
        raise _err(409, "would_lock_out", "Set a password first, otherwise you could not sign in again.")
    ctx.s.delete(i)
    audit.record(ctx.s, ctx.user.id, "sso.unlink", i.provider_id, {}, ctx.ip)
    return {"ok": True}


# ---- administration ------------------------------------------------------------------------------------
class ProviderIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    issuer: str = Field(max_length=500)
    client_id: str = Field(min_length=1, max_length=300)
    client_secret: str | None = Field(default=None, max_length=1000)  # write-only; omitted = keep
    scopes: str = Field(default="openid email profile", max_length=200)
    allowed_domains: list[str] = []
    email_claim: str = Field(default="email", max_length=60)
    name_claim: str = Field(default="name", max_length=60)
    groups_claim: str = Field(default="groups", max_length=60)
    role_map: dict[str, list[str]] = {}
    default_role: str = "member"
    require_approval: bool = False
    jit_create: bool = True
    assume_email_verified: bool = False
    allow_private_network: bool = False
    enabled: bool = False


def _out(p: SsoProvider, request: Request, s, st) -> dict:
    return {"id": p.id, "name": p.name, "issuer": p.issuer, "client_id": p.client_id, "has_secret": bool(p.client_secret_enc),
            "scopes": p.scopes, "allowed_domains": p.allowed_domains or [], "email_claim": p.email_claim,
            "name_claim": p.name_claim, "groups_claim": p.groups_claim, "role_map": p.role_map or {},
            "default_role": p.default_role, "require_approval": p.require_approval, "jit_create": p.jit_create,
            "assume_email_verified": p.assume_email_verified, "allow_private_network": p.allow_private_network,
            "enabled": p.enabled, "redirect_uri": _redirect_uri(request, s, st)}


def _validate(ctx: Ctx, body: ProviderIn):
    if not body.issuer.startswith(("https://", "http://")):
        raise _err(422, "invalid", "The issuer must be an https:// address.")
    if body.issuer.startswith("http://") and not sso_svc._is_dev_host(urlsplit(body.issuer).hostname or ""):
        raise _err(422, "invalid", "The issuer must use https (plain http is accepted only for localhost during development).")
    if "openid" not in body.scopes.split():
        raise _err(422, "invalid", "Scopes must include openid.")
    if (body.allow_private_network) and not admin_people.is_owner(ctx.principal):
        raise _err(403, "owner_only", "Only an owner can allow a provider on a private network.")
    targets = {body.default_role, *[r for rs in body.role_map.values() for r in rs]}
    for rid in targets:
        role = ctx.s.get(Role, rid)
        if role is None:
            raise _err(422, "invalid", f"Unknown role '{rid}'.")
        privileged = rid == "owner" or "*" in (role.grants or []) or any(g.startswith("admin.") for g in (role.grants or []))
        if privileged and not admin_people.is_owner(ctx.principal):
            raise _err(403, "owner_only", f"Only an owner can map people to the privileged role '{role.label}'.")
        admin_people._check_role_assignable(ctx, role)  # nobody can hand out more than they hold
    if len(body.role_map) > 200:
        raise _err(422, "invalid", "Too many group mappings.")


@router.get("/api/admin/sso/providers")
def list_providers(request: Request, ctx: Ctx = Depends(require("admin.sso.manage"))):
    return [_out(p, request, ctx.s, ctx.st) for p in ctx.s.execute(select(SsoProvider).order_by(SsoProvider.name)).scalars()]


@router.post("/api/admin/sso/providers")
def create_provider(body: ProviderIn, request: Request, ctx: Ctx = Depends(require("admin.sso.manage"))):
    _validate(ctx, body)
    if ctx.s.execute(select(SsoProvider).where(SsoProvider.name == body.name.strip())).scalar_one_or_none():
        raise _err(409, "exists", "A provider with that name exists.")
    p = SsoProvider(name=body.name.strip())
    _apply(ctx, p, body)
    ctx.s.add(p)
    ctx.s.flush()
    audit.record(ctx.s, ctx.user.id, "sso.provider.create", p.id, {"name": p.name, "issuer": p.issuer, "enabled": p.enabled}, ctx.ip)
    return _out(p, request, ctx.s, ctx.st)


def _apply(ctx: Ctx, p: SsoProvider, b: ProviderIn):
    p.name, p.issuer, p.client_id = b.name.strip(), b.issuer.rstrip("/"), b.client_id.strip()
    if b.client_secret:
        p.client_secret_enc = ctx.st.vault.encrypt(b.client_secret, "sso")
    p.scopes, p.allowed_domains = " ".join(b.scopes.split()), [d.strip().lower().lstrip("@") for d in b.allowed_domains if d.strip()]
    p.email_claim, p.name_claim, p.groups_claim = b.email_claim, b.name_claim, b.groups_claim
    p.role_map, p.default_role = {k: list(v) for k, v in b.role_map.items()}, b.default_role
    p.require_approval, p.jit_create, p.assume_email_verified = b.require_approval, b.jit_create, b.assume_email_verified
    p.allow_private_network, p.enabled = b.allow_private_network, b.enabled


@router.put("/api/admin/sso/providers/{pid}")
def update_provider(pid: str, body: ProviderIn, request: Request, ctx: Ctx = Depends(require("admin.sso.manage"))):
    p = ctx.s.get(SsoProvider, pid)
    if p is None:
        raise _err(404, "not_found", "No such provider.")
    _validate(ctx, body)
    _apply(ctx, p, body)
    sso_svc.clear_caches()
    audit.record(ctx.s, ctx.user.id, "sso.provider.update", p.id, {"name": p.name, "enabled": p.enabled,
                                                                    "secret_changed": bool(body.client_secret)}, ctx.ip)
    return _out(p, request, ctx.s, ctx.st)


@router.delete("/api/admin/sso/providers/{pid}")
def delete_provider(pid: str, ctx: Ctx = Depends(require("admin.sso.manage"))):
    p = ctx.s.get(SsoProvider, pid)
    if p is None:
        raise _err(404, "not_found", "No such provider.")
    audit.record(ctx.s, ctx.user.id, "sso.provider.delete", p.id, {"name": p.name}, ctx.ip)
    ctx.s.delete(p)
    return {"ok": True}


@router.post("/api/admin/sso/providers/{pid}/test")
async def test_provider(pid: str, ctx: Ctx = Depends(require("admin.sso.manage"))):
    """Really fetches the discovery document and the key set; reports what it found, nothing secret."""
    p = ctx.s.get(SsoProvider, pid)
    if p is None:
        raise _err(404, "not_found", "No such provider.")
    if not ctx.st.limiter.hit(f"sso:test:{ctx.user.id}", 20, 60):
        raise _err(429, "rate_limited", "Too many tests. Wait a minute.")
    out: dict = {"ok": False}
    try:
        doc = await sso_svc.discover(p.issuer, allow_private=p.allow_private_network, fresh=True)
        jwks = await sso_svc.get_jwks(doc["jwks_uri"], allow_private=p.allow_private_network, force=True)
        methods = doc.get("code_challenge_methods_supported")
        algs = doc.get("id_token_signing_alg_values_supported") or []
        out = {"ok": True, "issuer": doc["issuer"], "authorization_endpoint": doc["authorization_endpoint"],
               "token_endpoint": doc["token_endpoint"], "jwks_keys": len(jwks["keys"]),
               "pkce_s256": None if methods is None else "S256" in methods,
               "supported_algorithms": [a for a in algs if a in sso_svc.ALLOWED_ALGS],
               "compatible": (not algs) or any(a in sso_svc.ALLOWED_ALGS for a in algs),
               "end_session_endpoint": bool(doc.get("end_session_endpoint"))}
    except SsoError as e:
        out = {"ok": False, "error": e.code, "detail": e.detail[:160]}
    audit.record(ctx.s, ctx.user.id, "sso.provider.test", p.id, {"ok": out["ok"], "error": out.get("error", "")}, ctx.ip)
    return out
