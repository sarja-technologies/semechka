from __future__ import annotations

import hashlib
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import audit, mailer, security, services
from ..deps import Ctx, current, get_db, get_state, issue_session, optional_ctx
from ..models import EmailToken, Invite, Role, SessionRow, User, UserRole
from ..state import AppState

router = APIRouter(prefix="/api")

GENERIC_LOGIN_ERROR = "Email or password is incorrect."


class SignupIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=300)
    display_name: str = Field(min_length=1, max_length=120)
    invite: str | None = Field(default=None, max_length=200)


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=300)
    totp: str | None = Field(default=None, max_length=12)
    recovery_code: str | None = Field(default=None, max_length=40)


def _norm_email(e: str) -> str:
    e = e.strip().lower()
    if "@" not in e or len(e) < 3 or e.startswith("@") or e.endswith("@") or " " in e:
        raise HTTPException(422, {"code": "invalid", "message": "Enter a valid email address."})
    return e


def signup_state(s, st: AppState) -> dict:
    mode = st.settings.deployment(s, "signup.mode")
    return {"mode": mode, "open": mode in ("public", "domain", "invite"),
            "needs_invite": mode == "invite", "requires_approval": bool(st.settings.deployment(s, "signup.require_approval"))
            and mode in ("public", "domain"),
            "message": "" if mode in ("public", "domain", "invite") else
            "Accounts are created by your administrator. Contact them to get access."}


@router.get("/public")
def public(s=Depends(get_db), st: AppState = Depends(get_state)):
    """Everything a signed-out visitor may know: branding, whether setup is pending, whether sign-up is open."""
    from .setup import needs_setup
    pub = st.settings.public_values(s)
    sg = signup_state(s, st)
    return {"needs_setup": needs_setup(s), "brand": {k.split(".", 1)[1]: v for k, v in pub.items() if k.startswith("brand.")},
            "privacy_notice": pub.get("privacy.notice", ""),
            "signup": {"open": sg["open"], "needs_invite": sg["needs_invite"], "requires_approval": sg["requires_approval"],
                       "message": sg["message"]}}


def _check_origin(request: Request):
    pass  # enforced globally in app middleware


@router.post("/auth/signup")
def signup(body: SignupIn, request: Request, response: Response, s=Depends(get_db), st: AppState = Depends(get_state)):
    ip = request.client.host if request.client else ""
    mode = st.settings.deployment(s, "signup.mode")
    from .sso import sso_enforced
    if sso_enforced(s, st):  # SSO: no password accounts when SSO is required
        raise HTTPException(403, {"code": "signup_closed", "message": "Accounts are created through single sign-on."})
    if mode not in ("public", "domain", "invite"):
        raise HTTPException(403, {"code": "signup_closed", "message": "Accounts are created by your administrator."})
    email = _norm_email(body.email)
    per_hour = st.settings.deployment(s, "signup.rate_per_hour")
    if not st.limiter.hit(f"signup:ip:{ip}", per_hour * 5, 3600) or not st.limiter.hit(f"signup:{email}", per_hour, 3600):
        raise HTTPException(429, {"code": "rate_limited", "message": "Too many attempts. Try again later."})
    domain = email.rsplit("@", 1)[1]
    if domain in st.settings.deployment(s, "signup.blocked_domains"):
        raise HTTPException(403, {"code": "domain_blocked", "message": "That email domain cannot register here."})
    invite = None
    role_id = st.settings.deployment(s, "signup.default_role")
    team_id = None
    if mode == "invite":
        invite = _get_invite(s, body.invite or "")
        if invite is None:
            raise HTTPException(403, {"code": "invite_required", "message": "A valid invitation is needed to register."})
        if invite.email and invite.email.lower() != email:
            raise HTTPException(403, {"code": "invite_required", "message": "A valid invitation is needed to register."})
        role_id, team_id = invite.role_id, invite.team_id
    elif mode == "domain":
        allowed = st.settings.deployment(s, "signup.allowed_domains")
        if domain not in allowed:
            raise HTTPException(403, {"code": "domain_not_allowed", "message": "Registration is limited to approved email domains."})
    problem = security.password_problem(body.password, email, st.settings.deployment(s, "auth.password_min_length"))
    if problem:
        raise HTTPException(422, {"code": "weak_password", "message": problem})
    role = s.get(Role, role_id) or s.get(Role, "member")
    existing = s.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing is not None:
        # No account enumeration: look the same as success for open modes.
        security.hash_password(body.password)
        return _signup_response(s, st, mode, invite is not None, None)
    needs_verify = bool(st.settings.deployment(s, "signup.require_email_verification")) and invite is None
    needs_approval = bool(st.settings.deployment(s, "signup.require_approval")) and mode in ("public", "domain")
    status = "pending_verification" if needs_verify else ("pending_approval" if needs_approval else "active")
    user = User(email=email, display_name=body.display_name.strip(), password_hash=security.hash_password(body.password),
                status=status)
    s.add(user)
    s.flush()
    s.add(UserRole(user_id=user.id, role_id=role.id))
    if team_id:
        from ..models import TeamMember
        s.add(TeamMember(team_id=team_id, user_id=user.id))
    if invite:
        invite.uses += 1
    audit.record(s, user.id, "account.signup", user.id, {"mode": mode, "status": status, "invite": bool(invite)}, ip)
    if status == "pending_verification":
        _send_token(s, st, user, "verify", request)
    return _signup_response(s, st, mode, invite is not None, status)


def _signup_response(s, st, mode, invited, status):
    msg = {"active": "Account created. You can sign in now.",
           "pending_approval": "Account created. An administrator must approve it before you can sign in.",
           "pending_verification": "Check your email to confirm your address."}.get(
        status, "If this address can register, you will receive next steps.")
    return {"ok": True, "message": msg}


def _get_invite(s, token: str):
    if not token:
        return None
    inv = s.execute(select(Invite).where(Invite.token_hash == hashlib.sha256(token.encode()).hexdigest())).scalar_one_or_none()
    if inv is None or inv.revoked or inv.expires_at < time.time() or inv.uses >= inv.max_uses:
        return None
    return inv


def _send_token(s, st, user: User, kind: str, request: Request):
    if not mailer.configured(st.settings, s):
        return False
    token = secrets.token_urlsafe(32)
    ttl = 24 * 3600 if kind == "verify" else 3600
    s.add(EmailToken(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id, kind=kind,
                     expires_at=time.time() + ttl))
    base = st.settings.deployment(s, "access.public_url") or str(request.base_url).rstrip("/")
    app = st.settings.deployment(s, "brand.app_name")
    if kind == "verify":
        subject, body = f"Confirm your {app} account", f"Open this link to confirm your address:\n\n{base}/verify?token={token}\n"
    else:
        subject, body = f"Reset your {app} password", f"Open this link to choose a new password (valid for one hour):\n\n{base}/reset?token={token}\n"
    mailer.send_background(mailer.smtp_params(st.settings, s), user.email, subject, body)
    return True


@router.post("/auth/verify")
def verify_email(body: dict, s=Depends(get_db), st: AppState = Depends(get_state)):
    tok = hashlib.sha256(str(body.get("token", "")).encode()).hexdigest()
    row = s.get(EmailToken, tok)
    if row is None or row.kind != "verify" or row.expires_at < time.time():
        raise HTTPException(400, {"code": "invalid_token", "message": "This link is invalid or has expired."})
    user = s.get(User, row.user_id)
    s.delete(row)
    if user and user.status == "pending_verification":
        needs_approval = bool(st.settings.deployment(s, "signup.require_approval")) and st.settings.deployment(s, "signup.mode") in ("public", "domain")
        user.status = "pending_approval" if needs_approval else "active"
        audit.record(s, user.id, "account.email_verified", user.id)
    return {"ok": True, "message": "Email confirmed."}


@router.post("/auth/forgot")
def forgot(body: dict, request: Request, s=Depends(get_db), st: AppState = Depends(get_state)):
    ip = request.client.host if request.client else ""
    email = str(body.get("email", "")).strip().lower()
    if not st.limiter.hit(f"forgot:{ip}", 10, 3600):
        raise HTTPException(429, {"code": "rate_limited", "message": "Too many attempts. Try again later."})
    user = s.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user and user.status == "active":
        _send_token(s, st, user, "reset", request)
    # Same answer whether or not the account exists, or whether email is configured.
    return {"ok": True, "message": "If that address has an account and email is set up, a reset link is on its way."}


@router.post("/auth/reset")
def reset(body: dict, s=Depends(get_db), st: AppState = Depends(get_state)):
    row = s.get(EmailToken, hashlib.sha256(str(body.get("token", "")).encode()).hexdigest())
    if row is None or row.kind != "reset" or row.expires_at < time.time():
        raise HTTPException(400, {"code": "invalid_token", "message": "This link is invalid or has expired."})
    user = s.get(User, row.user_id)
    pw = str(body.get("password", ""))
    problem = security.password_problem(pw, user.email, st.settings.deployment(s, "auth.password_min_length"))
    if problem:
        raise HTTPException(422, {"code": "weak_password", "message": problem})
    user.password_hash = security.hash_password(pw)
    s.delete(row)
    for sess in s.execute(select(SessionRow).where(SessionRow.user_id == user.id)).scalars():
        s.delete(sess)
    audit.record(s, user.id, "account.password_reset", user.id)
    return {"ok": True}


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response, s=Depends(get_db), st: AppState = Depends(get_state)):
    ip = request.client.host if request.client else ""
    email = body.email.strip().lower()
    if not st.limiter.hit(f"login:ip:{ip}", 60, 60) or not st.limiter.hit(f"login:{email}", 10, 60):
        raise HTTPException(429, {"code": "rate_limited", "message": "Too many attempts. Wait a minute and try again."})
    user = s.execute(select(User).where(User.email == email)).scalar_one_or_none()
    now = time.time()
    ok = security.verify_password(user.password_hash if user else None, body.password)
    if user and user.locked_until > now:
        raise HTTPException(429, {"code": "locked", "message": "This account is temporarily locked. Try again later."})
    if not user or not ok:
        if user:
            user.failed_logins += 1
            if user.failed_logins >= 8:
                user.locked_until = now + 900
                user.failed_logins = 0
                audit.record(s, user.id, "account.locked", user.id, {"reason": "failed_logins"}, ip)
            s.commit()
        raise HTTPException(401, {"code": "bad_credentials", "message": GENERIC_LOGIN_ERROR})
    if user.status != "active":
        # Distinguish only after a correct password, so existence is never leaked to guessers.
        msg = {"pending_approval": "Your account is waiting for administrator approval.",
               "pending_verification": "Confirm your email address first.",
               "suspended": "This account has been suspended. Contact your administrator."}.get(user.status, GENERIC_LOGIN_ERROR)
        raise HTTPException(403, {"code": user.status, "message": msg})
    from .sso import local_login_blocked  # SSO: password sign-in may be turned off (owners excepted)
    if local_login_blocked(s, st, user):
        raise HTTPException(403, {"code": "sso_required", "message": "Your organisation uses single sign-on. Use the sign-in button for your identity provider."})
    if user.totp_enabled:
        if not body.totp and not body.recovery_code:
            raise HTTPException(401, {"code": "mfa_required", "message": "Enter your authentication code."})
        if not st.limiter.hit(f"totp:{user.id}", 6, 300):
            raise HTTPException(401, {"code": "bad_totp", "message": "Too many codes tried. Wait five minutes."})
        if body.recovery_code:
            h = security.recovery_hash(body.recovery_code)
            left = list(user.mfa_recovery or [])
            if h not in left:
                raise HTTPException(401, {"code": "bad_totp", "message": "That recovery code is not valid or was already used."})
            left.remove(h)
            user.mfa_recovery = left
            audit.record(s, user.id, "account.mfa_recovery_used", user.id, {"remaining": len(left)}, ip)
        else:
            secret = st.vault.decrypt(user.totp_secret_enc, "totp")
            if not security.totp_verify(secret, body.totp):
                raise HTTPException(401, {"code": "bad_totp", "message": "That code is not valid."})
    user.failed_logins = 0
    user.last_login_at = now
    if security.needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(body.password)
    issue_session(s, st, request, response, user, mfa_ok=True)
    audit.record(s, user.id, "auth.login", user.id, {}, ip)
    return {"ok": True}


@router.post("/auth/logout")
def logout(request: Request, response: Response, s=Depends(get_db), ctx: Ctx | None = Depends(optional_ctx)):
    if ctx:
        sent = request.headers.get("x-csrf-token", "")
        if not secrets.compare_digest(sent, ctx.session.csrf):
            raise HTTPException(403, {"code": "csrf", "message": "Missing or invalid CSRF token."})
        s.delete(s.get(SessionRow, ctx.session.id_hash))
    response.delete_cookie(request.app.state.sem.cookie_name, path="/")
    return {"ok": True}


class PwChange(BaseModel):
    current: str = Field(max_length=300)
    new: str = Field(max_length=300)


@router.post("/auth/password")
def change_password(body: PwChange, request: Request, response: Response, ctx: Ctx = Depends(current)):
    if not security.verify_password(ctx.user.password_hash, body.current):
        raise HTTPException(403, {"code": "bad_credentials", "message": "Current password is incorrect."})
    problem = security.password_problem(body.new, ctx.user.email, ctx.st.settings.deployment(ctx.s, "auth.password_min_length"))
    if problem:
        raise HTTPException(422, {"code": "weak_password", "message": problem})
    ctx.user.password_hash = security.hash_password(body.new)
    for sess in ctx.s.execute(select(SessionRow).where(SessionRow.user_id == ctx.user.id)).scalars():
        ctx.s.delete(sess)
    ctx.s.flush()
    audit.record(ctx.s, ctx.user.id, "account.password_change", ctx.user.id, {}, ctx.ip)
    ctx.s.commit()
    new = issue_session(ctx.s, ctx.st, request, response, ctx.user, True)
    return {"ok": True, "csrf": new.csrf}


@router.get("/auth/sessions")
def sessions(ctx: Ctx = Depends(current)):
    rows = ctx.s.execute(select(SessionRow).where(SessionRow.user_id == ctx.user.id).order_by(SessionRow.last_seen.desc())).scalars()
    return [{"id": r.id_hash[:12], "created_at": r.created_at, "last_seen": r.last_seen, "ip": r.ip, "ua": r.ua,
             "current": r.id_hash == ctx.session.id_hash} for r in rows]


@router.delete("/auth/sessions/{sid}")
def revoke_session(sid: str, ctx: Ctx = Depends(current)):
    for r in ctx.s.execute(select(SessionRow).where(SessionRow.user_id == ctx.user.id)).scalars():
        if r.id_hash[:12] == sid and r.id_hash != ctx.session.id_hash:
            ctx.s.delete(r)
            return {"ok": True}
    raise HTTPException(404, {"code": "not_found", "message": "No such session."})


@router.post("/auth/mfa/begin")
def mfa_begin(ctx: Ctx = Depends(current)):
    if services.mfa_policy(ctx.s, ctx.st) == "off":
        raise HTTPException(403, {"code": "mfa_off", "message": "Your organisation has not enabled multi-factor authentication."})
    if ctx.user.totp_enabled:
        raise HTTPException(409, {"code": "already", "message": "MFA is already on."})
    secret = security.new_totp_secret()
    ctx.user.totp_secret_enc = ctx.st.vault.encrypt(secret, "totp")
    app = ctx.st.settings.deployment(ctx.s, "brand.app_name")
    return {"secret": secret, "uri": security.totp_uri(secret, ctx.user.email, app)}


@router.post("/auth/mfa/confirm")
def mfa_confirm(body: dict, ctx: Ctx = Depends(current)):
    if not ctx.user.totp_secret_enc:
        raise HTTPException(409, {"code": "not_started", "message": "Start setup first."})
    secret = ctx.st.vault.decrypt(ctx.user.totp_secret_enc, "totp")
    if not ctx.st.limiter.hit(f"totp:{ctx.user.id}", 6, 300) or not security.totp_verify(secret, str(body.get("code", ""))):
        raise HTTPException(422, {"code": "bad_totp", "message": "That code is not valid."})
    ctx.user.totp_enabled = True
    codes = security.new_recovery_codes()
    ctx.user.mfa_recovery = [security.recovery_hash(c) for c in codes]
    audit.record(ctx.s, ctx.user.id, "account.mfa_enabled", ctx.user.id, {}, ctx.ip)
    return {"ok": True, "recovery_codes": codes}


@router.get("/auth/mfa")
def mfa_state(ctx: Ctx = Depends(current)):
    return {"enabled": ctx.user.totp_enabled, "policy": services.mfa_policy(ctx.s, ctx.st),
            "required": services.mfa_required(ctx.s, ctx.st, ctx.principal),
            "recovery_codes_left": len(ctx.user.mfa_recovery or []) if ctx.user.totp_enabled else 0}


@router.post("/auth/mfa/recovery-codes")
def mfa_new_recovery(body: dict, ctx: Ctx = Depends(current)):
    """Replace the recovery codes. Needs the password, because whoever holds them can pass the second factor."""
    if not ctx.user.totp_enabled:
        raise HTTPException(409, {"code": "not_enabled", "message": "Turn on multi-factor authentication first."})
    if not ctx.st.limiter.hit(f"pw:{ctx.user.id}", 6, 300) or \
            not security.verify_password(ctx.user.password_hash, str(body.get("password", ""))):
        raise HTTPException(403, {"code": "bad_credentials", "message": "Password is incorrect."})
    codes = security.new_recovery_codes()
    ctx.user.mfa_recovery = [security.recovery_hash(c) for c in codes]
    audit.record(ctx.s, ctx.user.id, "account.mfa_recovery_regenerated", ctx.user.id, {}, ctx.ip)
    return {"recovery_codes": codes}


@router.post("/auth/mfa/disable")
def mfa_disable(body: dict, ctx: Ctx = Depends(current)):
    if services.mfa_required(ctx.s, ctx.st, ctx.principal):
        raise HTTPException(403, {"code": "mfa_required", "message": "Your organisation requires multi-factor authentication for you."})
    if not security.verify_password(ctx.user.password_hash, str(body.get("password", ""))):
        raise HTTPException(403, {"code": "bad_credentials", "message": "Password is incorrect."})
    ctx.user.totp_enabled, ctx.user.totp_secret_enc, ctx.user.mfa_recovery = False, None, None
    audit.record(ctx.s, ctx.user.id, "account.mfa_disabled", ctx.user.id, {}, ctx.ip)
    return {"ok": True}


@router.delete("/me")
def delete_me(body: dict, response: Response, ctx: Ctx = Depends(current)):
    if not ctx.st.settings.deployment(ctx.s, "auth.allow_user_delete"):
        raise HTTPException(403, {"code": "disabled", "message": "Account deletion is turned off by your administrator."})
    if not security.verify_password(ctx.user.password_hash, str(body.get("password", ""))):
        raise HTTPException(403, {"code": "bad_credentials", "message": "Password is incorrect."})
    from ..models import UserRole as UR
    owners = ctx.s.execute(select(UR).where(UR.role_id == "owner")).scalars().all()
    if any(o.user_id == ctx.user.id for o in owners) and len(owners) == 1:
        raise HTTPException(409, {"code": "last_owner", "message": "You are the only owner. Assign another owner first."})
    audit.record(ctx.s, ctx.user.id, "account.delete", ctx.user.id, {}, ctx.ip)
    services.purge_user_files(ctx.st, ctx.user.id)
    ctx.s.delete(ctx.user)
    response.delete_cookie(ctx.st.cookie_name, path="/")
    return {"ok": True}
