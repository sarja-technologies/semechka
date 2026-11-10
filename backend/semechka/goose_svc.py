"""Work Mode on upstream Goose: session lifecycle, ACP relay, approvals, checkpoints (docs/WORK_MODE_GOOSE.md).

What this module is: the host side around an UNMODIFIED Goose. It starts Goose (`goose acp`) in a locked per-conversation
container, speaks the Agent Client Protocol to it over `docker exec -i`, turns Goose's session updates into the persisted event
stream the browser reads, relays permission questions to the person and their answers back, and enforces the limits.

What it is not: an agent. It never builds prompts, picks tools, runs tools, loops over model calls or calls a model on Goose's
behalf. Those are Goose's. The only model traffic Semechka itself makes is asking the router to load the chosen model.
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import func, select

from . import audit, goose_proxy, goose_runtime, work_svc
from .goose_acp import AcpClosed, AcpError, AcpPeer
from .goose_runtime import GOOSE_ROOT, GooseRuntime, GooseSpec
from .models import Conversation, Message, now
from .models_goose import GooseEvent, GooseSession
from .work_runtime import WORK_ROOT, RuntimeError_

log = logging.getLogger(__name__)
AUTONOMY_MODE = {"ask": "approve", "smart": "smart_approve", "auto": "auto"}
DECISIONS = {"allow_once": "allow_once", "allow_always": "allow_always", "deny": "reject_once", "deny_always": "reject_always"}
MAX_PROMPT = 20000
INPUT_VALUE_MAX = 4096
OUTPUT_MAX = 16 * 1024
KIND = {"shell": "execute", "edit": "edit", "write": "edit", "tree": "read", "read": "read", "search": "search"}


class GooseError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# ---- configuration ---------------------------------------------------------------------------------------------------
@dataclass
class Cfg:
    enabled: bool
    docker_host: str
    image: str
    router_url: str
    router_key_file: str
    verified_file: str
    models: list
    default_model: str
    autonomy_choices: list
    autonomy_default: str
    mcp: dict
    memory_mb: int
    cpus: float
    pids: int
    workspace_mb: int
    upload_mb: int
    max_running: int
    max_stored: int
    idle_min: int
    approval_s: int
    max_turns: int
    turn_s: int
    load_s: int
    sock_root: str
    mirror: bool


def cfg(st, s) -> Cfg:
    d = st.settings.deployment

    def g(k):
        return d(s, "goose." + k)
    host = g("docker_host") or d(s, "sandbox.docker_host")
    return Cfg(bool(g("enabled")), host, g("image"), g("router_url").rstrip("/"), g("router_key_file"), g("verified_file"), list(g("models") or []),
               g("default_model") or "", list(g("autonomy_choices") or ["ask"]), g("autonomy_default"), dict(g("mcp_servers") or {}),
               int(g("memory_mb")), float(g("cpus")), int(g("pids")), int(g("workspace_max_mb")), int(g("upload_max_mb")), int(g("max_running_total")),
               int(g("max_stored_per_user")), int(g("idle_stop_minutes")), int(g("approval_timeout_s")), int(g("max_turns")), int(g("turn_timeout_s")),
               int(g("model_load_timeout_s")), g("sock_root") or f"/tmp/semechka-goose-{os.getuid()}", bool(g("mirror_to_conversation")))


def gate(st, s, principal) -> Cfg:
    """403 with a truthful code unless this person may use Work on Goose right now."""
    c = cfg(st, s)
    if not st.settings.value_for(s, "modes.work.enabled", principal):
        raise GooseError("disabled", "Work Mode is turned off by your administrator.", 403)
    if not principal.can("mode.work"):
        raise GooseError("forbidden", "Your group has no access to Work Mode.", 403)
    if not c.enabled:
        raise GooseError("disabled", "Work Mode on Goose is not switched on for this server.", 403)
    return c


def runtime(st, c: Cfg) -> GooseRuntime:
    rt = st.__dict__.get("goose_runtime")  # tests assign a double; nothing in settings or the environment can
    return rt if rt is not None else GooseRuntime(c.docker_host)


def instance_id(st) -> str:
    return work_svc.instance_id(st)


# ---- models (router + verified file + the person's own access) ---------------------------------------------------------
def _key(c: Cfg) -> str:
    try:
        return Path(c.router_key_file).read_text().strip()
    except OSError:
        return ""


def router_models(c: Cfg, timeout: float = 8.0) -> dict[str, str]:
    """{model id: loaded|loading|unloaded} from the router; raises GooseError(503) when unreachable."""
    try:
        r = httpx.get(c.router_url + "/v1/models", headers={"Authorization": f"Bearer {_key(c)}"}, timeout=timeout, trust_env=False)
        r.raise_for_status()
        return {m["id"]: ((m.get("status") or {}).get("value") or "loaded") for m in r.json().get("data", [])}
    except (httpx.HTTPError, ValueError, KeyError) as e:
        raise GooseError("unavailable", f"The model service is not reachable ({type(e).__name__}).", 503) from None


def verified(c: Cfg) -> dict[str, dict]:
    try:
        raw = json.loads(Path(c.verified_file).read_text()).get("models", {})
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, dict) and v.get("coding_agent_compatible") is True}


def allowed_models(st, s, principal, c: Cfg, states: dict[str, str] | None = None) -> list[dict]:
    """Router models verified for coding agents that this person may use. Order: preferred model first, then by id."""
    from . import services
    ver = verified(c)
    if c.models:
        ver = {k: v for k, v in ver.items() if k in c.models}
    rows = {m.real_id: m for m, _b in services.accessible_models(s, principal, st, "chat")}
    from .models import Model
    known = {m.real_id for m in s.execute(select(Model)).scalars()}
    out = []
    for mid, v in ver.items():
        if mid in known and mid not in rows:
            continue                      # an administrator-managed model this person has no access to
        if mid not in known and not principal.can("models.use_all"):
            continue                      # not in the catalogue at all: only people who may use everything
        if states is not None and mid not in states:
            continue                      # the router does not serve it
        out.append({"id": mid, "label": rows[mid].alias if mid in rows else mid, "context": int(v.get("context_limit") or 65536),
                    "state": (states or {}).get(mid, "unloaded"), "memory_mib": v.get("loaded_memory_mib"), "default": False})
    out.sort(key=lambda m: (m["id"] != c.default_model, m["state"] != "loaded", m["id"]))
    if out:
        out[0]["default"] = True
    return out


# ---- sessions -------------------------------------------------------------------------------------------------------------
def enc(st, org_id: str, text: str) -> str:
    return work_svc.enc(st, org_id, text)


def dec(st, org_id: str, blob: str) -> str:
    return work_svc.dec(st, org_id, blob)


def get_row(s, user_id: str, cid: str) -> GooseSession:
    row = s.execute(select(GooseSession).where(GooseSession.conversation_id == cid)).scalar_one_or_none()
    if row is None or row.user_id != user_id or row.deleted_at:
        raise GooseError("no_session", "This conversation has no Work session.", 404)
    return row


def own_conversation(s, user_id: str, cid: str) -> Conversation:
    c = s.get(Conversation, cid)
    if c is None or c.user_id != user_id:
        raise GooseError("not_found", "Conversation not found.", 404)
    return c


def _check_choices(st, s, principal, c: Cfg, body: dict, org_id: str, user) -> dict:
    out = {}
    if body.get("model") is not None:
        models = {m["id"] for m in allowed_models(st, s, principal, c, router_models(c))}
        if body["model"] not in models:
            raise GooseError("model_not_allowed", "That model is not available for Work Mode.", 409)
        out["model"] = body["model"]
    if body.get("autonomy") is not None:
        if body["autonomy"] not in c.autonomy_choices:
            raise GooseError("bad_autonomy", "That approval mode is not available.", 422)
        out["autonomy"] = body["autonomy"]
    if body.get("mcp") is not None:
        ids = body["mcp"]
        if not isinstance(ids, list) or any(i not in c.mcp or not c.mcp[i].get("enabled", True) for i in ids):
            raise GooseError("bad_mcp", "One of those tools is not on the administrator's list.", 422)
        out["mcp"] = list(dict.fromkeys(ids))
    if body.get("skills") is not None:
        from . import work_skills
        try:
            out["skills"] = work_skills.check_ids(s, user, body["skills"])
        except Exception as e:  # noqa: BLE001 - SkillError carries a safe message
            raise GooseError("bad_skill", str(e), 422) from None
    return out


def ensure_session(st, s, user, principal, cid: str, body: dict, ip: str = "") -> GooseSession:
    c = gate(st, s, principal)
    conv = own_conversation(s, user.id, cid)
    row = s.execute(select(GooseSession).where(GooseSession.conversation_id == cid)).scalar_one_or_none()
    if row is not None and row.deleted_at is None:
        return row
    org_id = work_svc.user_org(s, user.id)
    if org_id is None:
        raise GooseError("no_org", "Work Mode needs an organisation membership.", 409)
    n = s.execute(select(func.count()).select_from(GooseSession).where(GooseSession.user_id == user.id, GooseSession.deleted_at.is_(None))).scalar() or 0
    if n >= c.max_stored:
        raise GooseError("limit_sessions", f"You already have {n} Work sessions (limit {c.max_stored}). Delete one first.", 429)
    models = allowed_models(st, s, principal, c, router_models(c))
    if not models:
        raise GooseError("model_not_allowed", "No coding-verified model is available to you.", 409)
    chosen = _check_choices(st, s, principal, c, body, org_id, user)
    model = chosen.get("model") or models[0]["id"]
    autonomy = chosen.get("autonomy") or (c.autonomy_default if c.autonomy_default in c.autonomy_choices else c.autonomy_choices[0])
    if row is None:
        row = GooseSession(conversation_id=conv.id, user_id=user.id, org_id=org_id)
        s.add(row)
    else:  # a deleted row is re-used so the unique conversation key holds
        row.deleted_at, row.goose_session_id, row.current_turn_id, row.last_error = None, "", None, ""
        s.query(GooseEvent).filter(GooseEvent.session_id == row.id).delete()
    row.model, row.autonomy, row.mcp, row.skills = model, autonomy, chosen.get("mcp", []), chosen.get("skills", [])
    row.container = goose_runtime.container_name(row.id or "") if row.id else ""
    s.flush()
    row.container = goose_runtime.container_name(row.id)
    audit.record(s, user.id, "work.goose.session.create", row.id, {"conversation": conv.id, "model": model, "autonomy": autonomy}, ip, org_id)
    return row


def update_session(st, s, user, principal, row: GooseSession, body: dict, ip: str = "") -> GooseSession:
    c = gate(st, s, principal)
    live = _mgr(st).live.get(row.id)
    chosen = _check_choices(st, s, principal, c, body, row.org_id, user)
    if live and live.turn and any(k in chosen for k in ("model", "mcp", "skills")):
        raise GooseError("turn_running", "Wait for the running prompt to finish before changing the model or tools.", 409)
    for k, v in chosen.items():
        setattr(row, k, v)
    row.updated_at = now()
    if live and "autonomy" in chosen:
        live.apply_mode(chosen["autonomy"])
    audit.record(s, user.id, "work.goose.session.update", row.id, {k: v for k, v in chosen.items()}, ip, row.org_id)
    return row


def session_out(st, s, row: GooseSession, c: Cfg | None = None) -> dict:
    live = _mgr(st).live.get(row.id)
    last = s.execute(select(func.max(GooseEvent.id)).where(GooseEvent.session_id == row.id)).scalar() or 0
    state = live.state if live else ("error" if row.last_error else "idle")
    return {"id": row.id, "conversation_id": row.conversation_id, "state": state, "model": row.model, "model_state": live.model_state if live else "unknown",
            "autonomy": row.autonomy, "mcp": row.mcp or [], "skills": row.skills or [], "goose_session_id": row.goose_session_id or None,
            "container": live.container_state if live else "unknown", "turn": ({"id": live.turn["id"], "started": live.turn["started"]} if live and live.turn else None),
            "pending_approvals": [a.public() for a in live.approvals.values() if not a.done.is_set()] if live else [], "last_seq": last,
            "title": dec(st, row.org_id, row.title_enc), "last_error": row.last_error or None, "created_at": row.created_at, "updated_at": row.updated_at,
            "last_active_at": row.last_active_at}


# ---- events -----------------------------------------------------------------------------------------------------------------
def events_since(st, s, row: GooseSession, since: int, limit: int = 500) -> list[dict]:
    rows = s.execute(select(GooseEvent).where(GooseEvent.session_id == row.id, GooseEvent.id > since).order_by(GooseEvent.id).limit(limit)).scalars().all()
    out = []
    for e in rows:
        try:
            body = json.loads(dec(st, row.org_id, e.payload_enc)) if e.payload_enc else {}
        except ValueError:
            body = {}
        out.append({"seq": e.id, "type": e.type, "ts": e.created_at, "turn_id": e.turn_id, **body})
    return out


def transcript(st, s, row: GooseSession) -> dict:
    """Fold the event history into renderable items (the same reduction a client does while streaming)."""
    items, tools, msgs = [], {}, {}
    last = 0
    for ev in events_since(st, s, row, 0, 100000):
        last = ev["seq"]
        t = ev["type"]
        tid = ev.get("turn_id")
        if t == "turn_start":
            items.append({"kind": "user", "turn_id": tid, "text": ev.get("text", "")})
        elif t in ("assistant_delta", "reasoning_delta"):
            mid = ev.get("message_id") or "m"
            it = msgs.get(mid)
            if it is None:
                it = msgs[mid] = {"kind": "assistant", "turn_id": tid, "message_id": mid, "text": "", "reasoning": ""}
                items.append(it)
            it["text" if t == "assistant_delta" else "reasoning"] += ev.get("text", "")
        elif t == "tool_start":
            it = tools[ev["tool_call_id"]] = {"kind": "tool", "turn_id": tid, "tool_call_id": ev["tool_call_id"], "tool": ev.get("tool"), "title": ev.get("title"),
                                              "input": ev.get("input"), "status": "running", "output": ""}
            items.append(it)
        elif t == "tool_output" and ev.get("tool_call_id") in tools:
            tools[ev["tool_call_id"]]["output"] = (tools[ev["tool_call_id"]]["output"] + ev.get("text", ""))[-OUTPUT_MAX:]
        elif t == "tool_end" and ev.get("tool_call_id") in tools:
            it = tools[ev["tool_call_id"]]
            it.update(status=ev.get("status"), output=ev.get("output") or it["output"])
            for k in ("diff", "exit_code", "locations"):
                if ev.get(k) is not None:
                    it[k] = ev[k]
        elif t == "approval_request":
            items.append({"kind": "approval", "turn_id": tid, **ev["approval"], "decision": None})
        elif t == "approval_resolved":
            for it in items:
                if it["kind"] == "approval" and it.get("id") == ev.get("approval_id"):
                    it["decision"] = ev.get("decision")
        elif t == "files_changed":
            items.append({"kind": "files", "turn_id": tid, "files": ev.get("files", []), "truncated": ev.get("truncated", False)})
        elif t in ("error", "notice"):
            items.append({"kind": t, "turn_id": tid, "code": ev.get("code"), "message": ev.get("message")})
        elif t == "turn_end":
            items.append({"kind": "turn_end", "turn_id": tid, "stop_reason": ev.get("stop_reason")})
    return {"items": items, "last_seq": last}


# ---- manager --------------------------------------------------------------------------------------------------------------------
class Approval:
    def __init__(self, tool_call_id: str, tool: str, extension: str, title: str, inp, options: list[dict], timeout_s: int):
        self.id = "ap_" + secrets.token_hex(8)
        self.tool_call_id, self.tool, self.extension, self.title, self.input = tool_call_id, tool, extension, title, inp
        self.acp_options = options
        self.created = time.time()
        self.expires = self.created + timeout_s
        self.done = threading.Event()
        self.decision: str | None = None
        self.by = ""

    def kinds(self) -> list[str]:
        have = {o.get("kind") for o in self.acp_options}
        return [d for d, k in DECISIONS.items() if k in have]

    def public(self) -> dict:
        return {"id": self.id, "tool_call_id": self.tool_call_id, "tool": self.tool, "extension": self.extension, "title": self.title, "input": self.input,
                "options": self.kinds(), "created_at": self.created, "expires_at": self.expires}


class Manager:
    def __init__(self, st):
        self.st = st
        self.live: dict[str, Live] = {}
        self.lock = threading.RLock()
        self.cond = threading.Condition()
        self.stop = threading.Event()
        self.reaper: threading.Thread | None = None

    def notify(self) -> None:
        with self.cond:
            self.cond.notify_all()

    def running_count(self) -> int:
        return sum(1 for lv in self.live.values() if lv.peer is not None and not lv.peer.closed)

    def start_reaper(self) -> None:
        if self.reaper and self.reaper.is_alive():
            return
        self.reaper = threading.Thread(target=self._reap_loop, name="goose-reaper", daemon=True)
        self.reaper.start()

    def _reap_loop(self) -> None:
        while not self.stop.wait(30):
            try:
                with self.st.db.session() as s:
                    idle = cfg(self.st, s).idle_min * 60
                for lv in list(self.live.values()):
                    if lv.turn is None and not lv.approvals_open() and time.time() - lv.last_active > idle:
                        lv.shutdown(reason="idle", stop_container=True)
            except Exception:  # noqa: BLE001
                log.exception("goose reaper")

    def close_all(self) -> None:
        self.stop.set()
        for lv in list(self.live.values()):
            lv.shutdown(reason="shutdown", stop_container=False)


def _mgr(st) -> Manager:
    m = st.__dict__.get("goose_mgr")
    if m is None:
        m = st.__dict__["goose_mgr"] = Manager(st)
    return m


def startup(st) -> None:
    """After a restart: a turn left running by the previous process ended with it. Mark it, and start the idle reaper."""
    with st.db.session(write=True) as s:
        for row in s.execute(select(GooseSession).where(GooseSession.current_turn_id.is_not(None))).scalars():
            tid = row.current_turn_id
            row.current_turn_id = None
            _emit_direct(st, s, row, "turn_end", tid, {"stop_reason": "interrupted"})
            _emit_direct(st, s, row, "approval_resolved", tid, {"approval_id": None, "decision": "cancelled", "by": "restart"})
            _emit_direct(st, s, row, "notice", tid, {"code": "restarted", "message": "Semechka restarted while this prompt was running. Send your next message to continue."})
    _mgr(st).start_reaper()


def _emit_direct(st, s, row: GooseSession, type_: str, turn_id: str | None, fields: dict) -> int:
    ev = GooseEvent(session_id=row.id, type=type_, turn_id=turn_id, payload_enc=enc(st, row.org_id, json.dumps(fields, default=str)) if fields else "")
    s.add(ev)
    s.flush()
    return ev.id


def get_live(st, row: GooseSession) -> "Live":
    m = _mgr(st)
    with m.lock:
        lv = m.live.get(row.id)
        if lv is None:
            lv = m.live[row.id] = Live(st, m, row)
        return lv


# ---- the live session ---------------------------------------------------------------------------------------------------------------
class Live:
    def __init__(self, st, mgr: Manager, row: GooseSession):
        self.st, self.mgr = st, mgr
        self.wid, self.cid, self.user_id, self.org_id = row.id, row.conversation_id, row.user_id, row.org_id
        self.name = goose_runtime.container_name(row.id)
        self.lock = threading.RLock()
        self.peer: AcpPeer | None = None
        self.gw: goose_proxy.ModelGateway | None = None
        self.state = "idle"
        self.model_state = "unknown"
        self.container_state = "unknown"
        self.turn: dict | None = None
        self.approvals: dict[str, Approval] = {}
        self.last_active = time.time()
        self.loading_history = False
        self.proc_model = ""
        self.proc_key: tuple = ()
        self.gid = row.goose_session_id or ""
        self.buf: dict[tuple, str] = {}
        self.buf_t = 0.0
        self.tools: dict[str, dict] = {}
        self.final_text: dict[str, str] = {}
        self.cancelled = False
        self.flusher: threading.Thread | None = None

    # ---- events ---------------------------------------------------------------------------------------------------------------
    def approvals_open(self) -> bool:
        return any(not a.done.is_set() for a in self.approvals.values())

    def emit(self, type_: str, fields: dict | None = None, *, flush: bool = True) -> int:
        if flush:
            self.flush_buffers()
        tid = self.turn["id"] if self.turn else None
        with self.st.db.session(write=True) as s:
            ev = GooseEvent(session_id=self.wid, type=type_, turn_id=tid,
                            payload_enc=enc(self.st, self.org_id, json.dumps(fields, default=str)) if fields else "")
            s.add(ev)
            s.flush()
            seq = ev.id
            row = s.get(GooseSession, self.wid)
            if row is not None:
                row.updated_at = now()
        self.mgr.notify()
        return seq

    def set_state(self, state: str, detail: str | None = None) -> None:
        if state != self.state:
            self.state = state
            self.emit("state", {"state": state, **({"detail": detail} if detail else {})})

    def buffer(self, type_: str, key: str, text: str, extra: dict | None = None) -> None:
        with self.lock:
            k = (type_, key, (extra or {}).get("stream", ""))
            self.buf[k] = self.buf.get(k, "") + text
            if not self.buf_t:
                self.buf_t = time.time()
            big = len(self.buf[k]) > 2000
        if big:
            self.flush_buffers()

    def flush_buffers(self) -> None:
        with self.lock:
            items, self.buf, self.buf_t = list(self.buf.items()), {}, 0.0
        for (type_, key, stream), text in items:
            if type_ == "assistant_delta":
                self.final_text[key] = self.final_text.get(key, "") + text
                self.emit(type_, {"message_id": key, "text": text}, flush=False)
            elif type_ == "reasoning_delta":
                self.emit(type_, {"message_id": key, "text": text}, flush=False)
            else:
                self.emit(type_, {"tool_call_id": key, "stream": stream, "text": text}, flush=False)

    def _flush_loop(self) -> None:
        while self.turn is not None:
            time.sleep(0.12)
            if self.buf_t and time.time() - self.buf_t >= 0.1:
                try:
                    self.flush_buffers()
                except Exception:  # noqa: BLE001
                    log.exception("goose flush")

    # ---- container + process ------------------------------------------------------------------------------------------------
    def _spec(self, c: Cfg) -> GooseSpec:
        sock_dir = os.path.join(c.sock_root, self.name)
        os.makedirs(c.sock_root, mode=0o711, exist_ok=True)
        os.chmod(c.sock_root, 0o711)
        os.makedirs(sock_dir, exist_ok=True)
        os.chmod(sock_dir, 0o755)
        return GooseSpec(session_id=self.wid, image=c.image, memory_mb=c.memory_mb, cpus=c.cpus, pids=c.pids, network="none", instance=instance_id(self.st),
                         sock_dir=sock_dir)

    def ensure_container(self, c: Cfg) -> str:
        rt = runtime(self.st, c)
        spec = self._spec(c)
        try:
            rt.create(spec)
        except RuntimeError_ as e:
            raise GooseError("unavailable", str(e), 503) from None
        self.container_state = "running"
        return self.name

    def start_gateway(self, c: Cfg, model: str) -> None:
        if self.gw is not None:
            return
        sock = os.path.join(c.sock_root, self.name, "llm.sock")
        if len(sock.encode()) > 100:
            raise GooseError("unavailable", "The socket directory path is too long (unix socket limit); choose a shorter goose.sock_root.", 503)
        counters = {"n": 0}

        def on_req(info: dict) -> None:
            counters["n"] += 1
        self.gw = goose_proxy.ModelGateway(sock, goose_proxy.GatewayConfig(
            c.router_url, c.router_key_file, lambda: {self.proc_model} if self.proc_model else set(), model, on_request=on_req))

    def write_goose_files(self, c: Cfg, rt: GooseRuntime, row_model: str, ctx_limit: int, skills: list[tuple[str, str, dict]]) -> None:
        prov = {"name": "semechka", "engine": "openai", "display_name": "Semechka model gateway", "description": "Models of this Semechka server",
                "api_key_env": "SEMECHKA_LLM_KEY", "base_url": "http://127.0.0.1:8081/v1/chat/completions", "supports_streaming": True,
                "models": [{"name": row_model, "context_limit": ctx_limit}]}
        rt.put_file(self.name, ".home/goose/config/custom_providers/semechka.json", json.dumps(prov).encode())
        # skills: exactly the ones chosen for this session (stale ones from earlier choices are removed first)
        try:
            rt.delete(self.name, ".home/.agents/skills")
        except RuntimeError_:
            pass
        for name, md, files in skills:
            rt.put_file(self.name, f".home/.agents/skills/{name}/SKILL.md", md.encode())
            for fn, body in files.items():
                rt.put_file(self.name, f".home/.agents/skills/{name}/{fn}", body.encode())

    def collect_skills(self, s, row: GooseSession) -> list[tuple[str, str, dict]]:
        from . import work_skills
        from .models_work import WorkSkill
        out = []
        for sid in row.skills or []:
            sk = s.get(WorkSkill, sid)
            if sk is None or not sk.enabled:
                continue
            if not ((sk.scope == "org" and sk.org_id == row.org_id) or (sk.scope == "user" and sk.owner_user_id == row.user_id)):
                continue  # never export someone else's skill
            body = work_skills._body(self.st, sk)
            if not body.lstrip().startswith("---"):
                body = f"---\nname: {sk.name}\ndescription: {json.dumps(sk.description or sk.title or sk.name)}\n---\n\n{body}"
            files = {}
            for f in work_skills._files(self.st, sk)[:50]:
                p, content = str(f.get("path", "")), f.get("content")
                if p and ".." not in p and not p.startswith("/") and isinstance(content, str) and p != "SKILL.md":
                    files[p] = content
            out.append((sk.name, body, files))
        return out

    def ensure_process(self, c: Cfg, row_info: dict) -> None:
        """Start (or restart, when model/tools changed) `goose acp`, then create or resume the Goose session."""
        key = (row_info["model"], tuple(row_info["mcp"]), tuple(row_info["skills"]))
        with self.lock:
            if self.peer is not None and not self.peer.closed and self.proc_key == key:
                return
            self.drop_process()
            rt = runtime(self.st, c)
            self.ensure_container(c)
            self.proc_model, self.proc_key = row_info["model"], key
            self.start_gateway(c, row_info["model"])
            self.write_goose_files(c, rt, row_info["model"], row_info["context"], row_info["skill_files"])
            env = {"HOME": f"{WORK_ROOT}/.home", "GOOSE_PATH_ROOT": GOOSE_ROOT, "GOOSE_PROVIDER": "semechka", "GOOSE_MODEL": row_info["model"],
                   "GOOSE_MODE": AUTONOMY_MODE[row_info["autonomy"]], "GOOSE_MAX_TURNS": str(c.max_turns), "GOOSE_DISABLE_KEYRING": "1",
                   "GOOSE_TELEMETRY_ENABLED": "false", "SEMECHKA_LLM_KEY": "not-a-secret", "GOOSE_DISABLE_SESSION_NAMING": "false"}
            argv, denv = rt.goose_argv(self.name, env)
            self.set_state("starting")
            try:
                self.peer = AcpPeer(argv, env=denv, on_notification=self._on_notification, on_request=self._on_request,
                                    on_stderr=lambda l: log.debug("goose[%s]: %s", self.wid, l[:300]), on_close=self._on_close)
                init = self.peer.request("initialize", {"protocolVersion": 1, "clientCapabilities": {}, "clientInfo": {"name": "semechka", "version": "work-goose"}},
                                         timeout=60)
            except (AcpError, OSError) as e:
                self.drop_process()
                raise GooseError("goose_start_failed", f"Goose could not be started: {str(e)[:160]}", 503) from None
            mcp = [{"name": i, "command": c.mcp[i]["command"], "args": c.mcp[i].get("args", []),
                    "env": [{"name": k, "value": v} for k, v in c.mcp[i].get("env", {}).items()]} for i in row_info["mcp"] if i in c.mcp]
            can_load = bool(((init or {}).get("agentCapabilities") or {}).get("loadSession"))
            resumed = False
            if self.gid and can_load:
                self.loading_history = True
                try:
                    self.peer.request("session/load", {"sessionId": self.gid, "cwd": WORK_ROOT, "mcpServers": mcp}, timeout=120)
                    resumed = True
                except AcpError as e:
                    log.info("goose session %s could not be loaded (%s); starting a new one", self.gid, e)
                finally:
                    self.loading_history = False
            if not resumed:
                new = self.peer.request("session/new", {"cwd": WORK_ROOT, "mcpServers": mcp}, timeout=60)
                self.gid = new["sessionId"]
                with self.st.db.session(write=True) as s:
                    r = s.get(GooseSession, self.wid)
                    r.goose_session_id = self.gid
                    if self.gid and row_info.get("had_gid"):
                        pass
            else:
                self.emit("notice", {"code": "resumed", "message": "Resumed the earlier Goose session."})
            want = AUTONOMY_MODE[row_info["autonomy"]]
            self.mode = want
            try:
                self.peer.request("session/set_mode", {"sessionId": self.gid, "modeId": want}, timeout=30)
            except AcpError:
                pass

    def apply_mode(self, autonomy: str) -> None:
        if self.peer is not None and not self.peer.closed and self.gid:
            try:
                self.peer.request("session/set_mode", {"sessionId": self.gid, "modeId": AUTONOMY_MODE[autonomy]}, timeout=15)
            except (AcpError, AcpClosed):
                pass

    def drop_process(self) -> None:
        p, self.peer = self.peer, None
        if p is not None:
            try:
                p.close(grace=2.0)
            except Exception:  # noqa: BLE001
                pass
        self.proc_key = ()

    def shutdown(self, *, reason: str, stop_container: bool) -> None:
        with self.lock:
            self.cancel_all_approvals("cancel")
            self.drop_process()
            if self.gw is not None:
                self.gw.close()
                self.gw = None
            if stop_container:
                try:
                    with self.st.db.session() as s:
                        c = cfg(self.st, s)
                    rt = runtime(self.st, c)
                    rt.kill_goose(self.name)
                    rt.stop(self.name)
                    self.container_state = "stopped"
                except (RuntimeError_, OSError):
                    pass
            if reason == "idle":
                try:
                    self.emit("notice", {"code": "idle_stopped", "message": "The session was stopped after being idle. Your files and history are kept."})
                except Exception:  # noqa: BLE001
                    pass
            self.state = "idle"
        self.mgr.live.pop(self.wid, None) if reason in ("idle", "stop") else None

    def _on_close(self, code) -> None:
        if self.turn is not None and not self.cancelled:
            self.emit("error", {"code": "goose_exited", "message": f"Goose stopped unexpectedly (exit {code}).", "retryable": True})

    # ---- ACP: updates -------------------------------------------------------------------------------------------------------------
    def _on_notification(self, method: str, p: dict) -> None:
        if method != "session/update" or self.loading_history:
            return
        u = p.get("update") or {}
        k = u.get("sessionUpdate")
        try:
            if k == "agent_message_chunk":
                c = u.get("content") or {}
                if c.get("type") == "text":
                    self.buffer("assistant_delta", u.get("messageId") or "m", c.get("text", ""))
            elif k == "agent_thought_chunk":
                c = u.get("content") or {}
                if c.get("type") == "text":
                    self.buffer("reasoning_delta", u.get("messageId") or "r", c.get("text", ""))
            elif k == "tool_call":
                self._tool_start(u)
            elif k == "tool_call_update":
                self._tool_update(u)
            elif k == "usage_update":
                self.emit("usage", {"used": u.get("used"), "size": u.get("size")})
            elif k == "session_info_update" and u.get("title"):
                with self.st.db.session(write=True) as s:
                    r = s.get(GooseSession, self.wid)
                    if r is not None:
                        r.title_enc = enc(self.st, self.org_id, str(u["title"])[:200])
                self.emit("title", {"title": str(u["title"])[:200]})
        except Exception:  # noqa: BLE001 - one bad update must not stop the stream
            log.exception("goose update %s", k)

    @staticmethod
    def _trim_input(v):
        if isinstance(v, dict):
            return {a: (b[:INPUT_VALUE_MAX] + "…" if isinstance(b, str) and len(b) > INPUT_VALUE_MAX else b) for a, b in v.items()}
        return v

    def _tool_start(self, u: dict) -> None:
        tid = u.get("toolCallId") or secrets.token_hex(4)
        g = ((u.get("_meta") or {}).get("goose") or {}).get("toolCall") or {}
        tool, ext = g.get("toolName") or (u.get("title") or "tool").split(" ")[0], g.get("extensionName") or ""
        inp = u.get("rawInput")
        self.tools[tid] = {"tool": tool, "extension": ext, "title": u.get("title") or tool, "input": inp}
        self.emit("tool_start", {"tool_call_id": tid, "tool": tool, "extension": ext, "title": u.get("title") or tool, "input": self._trim_input(inp),
                                 "kind": u.get("kind") or KIND.get(tool, "other")})

    def _tool_update(self, u: dict) -> None:
        tid = u.get("toolCallId")
        meta = (u.get("_meta") or {}).get("toolNotification") or {}
        if meta.get("type") == "live_output":
            for ch in (meta.get("params") or {}).get("chunks", []):
                self.buffer("tool_output", tid, ch.get("output", ""), {"stream": ch.get("stream", "stdout")})
        status = u.get("status")
        if status in ("completed", "failed", "cancelled"):
            text = "".join(((x.get("content") or {}).get("text") or "") for x in (u.get("content") or []) if x.get("type") == "content")
            raw = u.get("rawOutput") or {}
            info = self.tools.get(tid, {})
            fields = {"tool_call_id": tid, "status": status, "output": text[-OUTPUT_MAX:]}
            if isinstance(raw, dict) and raw.get("exit_code") is not None:
                fields["exit_code"] = raw["exit_code"]
            if u.get("locations"):
                fields["locations"] = u["locations"]
            inp = info.get("input") if isinstance(info.get("input"), dict) else {}
            if status == "completed" and info.get("tool") == "edit" and "before" in inp and "after" in inp and inp.get("path"):
                fields["diff"] = {"path": inp["path"], "unified": "".join(difflib.unified_diff(
                    str(inp["before"]).splitlines(True), str(inp["after"]).splitlines(True), f"a/{inp['path']}", f"b/{inp['path']}"))[:65536]}
            self.emit("tool_end", fields)
            if self.state == "awaiting_approval" and not self.approvals_open():
                self.state = "running"

    # ---- ACP: permission requests ----------------------------------------------------------------------------------------------------
    def _on_request(self, method: str, p: dict):
        if method != "session/request_permission":
            raise AcpError(f"method not supported: {method}", -32601)
        tc = p.get("toolCall") or {}
        tid = tc.get("toolCallId", "")
        info = self.tools.get(tid, {})
        with self.st.db.session() as s:
            timeout = cfg(self.st, s).approval_s
        ap = Approval(tid, info.get("tool") or (tc.get("title") or "tool").split(" ")[0], info.get("extension", ""), tc.get("title") or "", self._trim_input(tc.get("rawInput")),
                      p.get("options") or [], timeout)
        self.approvals[ap.id] = ap
        self.emit("approval_request", {"approval": ap.public()})
        self.set_state("awaiting_approval")
        if not ap.done.wait(timeout):
            self.resolve(ap, "timeout", "timeout")
        if self.state == "awaiting_approval" and not self.approvals_open():
            self.state = "running"
            self.emit("state", {"state": "running"})
        if ap.decision in DECISIONS:
            kind = DECISIONS[ap.decision]
            opt = next((o for o in ap.acp_options if o.get("kind") == kind), None)
            if opt:
                return {"outcome": {"outcome": "selected", "optionId": opt["optionId"]}}
        return {"outcome": {"outcome": "cancelled"}}

    def resolve(self, ap: Approval, decision: str, by: str) -> bool:
        with self.lock:
            if ap.done.is_set():
                return False
            ap.decision, ap.by = decision, by
            ap.done.set()
        self.emit("approval_resolved", {"approval_id": ap.id, "decision": decision, "by": by})
        return True

    def cancel_all_approvals(self, by: str) -> None:
        for ap in list(self.approvals.values()):
            if not ap.done.is_set():
                self.resolve(ap, "cancelled", by)

    # ---- turns -------------------------------------------------------------------------------------------------------------------------
    def start_turn(self, text: str, c: Cfg, row_info: dict) -> str:
        with self.lock:
            if self.turn is not None:
                raise GooseError("turn_running", "Goose is still working on the previous prompt.", 409)
            if self.approvals_open():
                raise GooseError("awaiting_approval", "Answer or cancel the pending approval first.", 409)
            if self.mgr.running_count() >= c.max_running and (self.peer is None or self.peer.closed):
                raise GooseError("limit_sessions", f"The server is running its maximum of {c.max_running} Work sessions; try again in a few minutes.", 429)
            tid = "t_" + secrets.token_hex(6)
            self.turn = {"id": tid, "started": time.time()}
            self.cancelled = False
            self.final_text = {}
            self.last_active = time.time()
        with self.st.db.session(write=True) as s:
            r = s.get(GooseSession, self.wid)
            r.current_turn_id = tid
            r.last_error = ""
            r.last_active_at = now()
            r.updated_at = now()
        seq = self.emit("turn_start", {"text": text})
        threading.Thread(target=self._run_turn, args=(tid, text, c, row_info), name=f"goose-turn-{self.wid}", daemon=True).start()
        self.flusher = threading.Thread(target=self._flush_loop, name="goose-flush", daemon=True)
        self.flusher.start()
        self._seq_at_accept = seq
        return tid

    def _load_model(self, c: Cfg, model: str) -> None:
        states = router_models(c)
        st = states.get(model)
        if st is None:
            raise GooseError("model_load_failed", f"The router does not serve '{model}'.", 503)
        self.model_state = st
        if st == "loaded":
            return
        self.set_state("loading_model")
        self.emit("model_loading", {"model": model, "phase": "loading"})
        if st == "unloaded":
            try:
                r = httpx.post(c.router_url + "/models/load", json={"model": model}, headers={"Authorization": f"Bearer {_key(c)}"}, timeout=30, trust_env=False)
                if r.status_code >= 400:
                    raise GooseError("model_load_failed", f"The router refused to load the model (HTTP {r.status_code}).", 503)
            except httpx.HTTPError as e:
                raise GooseError("model_load_failed", f"The router could not be asked to load the model ({type(e).__name__}).", 503) from None
        deadline = time.time() + c.load_s
        while time.time() < deadline:
            if self.cancelled:
                return
            time.sleep(2)
            now_state = router_models(c).get(model)
            if now_state == "loaded":
                self.model_state = "loaded"
                self.emit("model_loading", {"model": model, "phase": "ready"})
                return
            if now_state == "unloaded" and time.time() - deadline < -c.load_s + 20:
                continue
        self.emit("model_loading", {"model": model, "phase": "failed", "message": "Timed out waiting for the model."})
        raise GooseError("model_load_failed", "The model did not finish loading in time.", 503)

    def _checkpoint(self, c: Cfg, label: str) -> str | None:
        rt = runtime(self.st, c)
        sid = f"{label}-{int(time.time() * 1000)}"
        try:
            code, out, _err = rt.run_in(self.name, ["/usr/local/bin/python3", "-I", "/opt/semechka/ckpt.py", "snap", sid], timeout=120)
            return sid if code == 0 else None
        except (RuntimeError_, OSError):
            return None

    def _diff(self, c: Cfg, a: str, b: str) -> dict | None:
        rt = runtime(self.st, c)
        try:
            code, out, _e = rt.run_in(self.name, ["/usr/local/bin/python3", "-I", "/opt/semechka/ckpt.py", "diff", a, b], timeout=120)
            return json.loads(out) if code == 0 and out.strip() else None
        except (RuntimeError_, OSError, ValueError):
            return None

    def _run_turn(self, tid: str, text: str, c: Cfg, info: dict) -> None:
        stop = "end_turn"
        usage = None
        snap_a = snap_b = None
        try:
            try:
                self._load_model(c, info["model"])
                self.set_state("starting")
                self.ensure_process(c, info)
                snap_a = self._checkpoint(c, "a")
                self.set_state("running")
                timer = threading.Timer(c.turn_s, self._timeout_turn)
                timer.daemon = True
                timer.start()
                try:
                    peer = self.peer
                    if peer is None:
                        raise AcpClosed("The agent process is not running.")
                    r = peer.request("session/prompt", {"sessionId": self.gid, "prompt": [{"type": "text", "text": text}]}, timeout=None)
                finally:
                    timer.cancel()
                stop = (r or {}).get("stopReason", "end_turn")
                u = (r or {}).get("usage") or {}
                usage = {"input": u.get("inputTokens"), "output": u.get("outputTokens"), "total": u.get("totalTokens")}
            except GooseError as e:
                stop = "error"
                self.emit("error", {"code": e.code, "message": e.message, "retryable": e.code != "model_not_allowed"})
                self._record_error(e.message)
            except AcpClosed:
                stop = "cancelled" if self.cancelled else "error"
                if not self.cancelled:
                    self.emit("error", {"code": "goose_exited", "message": "Goose stopped unexpectedly. Send your message again to resume.", "retryable": True})
                    self._record_error("Goose stopped unexpectedly.")
            except AcpError as e:
                stop = "error"
                self.emit("error", {"code": "protocol", "message": f"Goose reported an error: {str(e)[:200]}", "retryable": True})
                self._record_error(str(e)[:200])
            self.flush_buffers()
            if self.cancelled and stop not in ("error",):
                stop = "cancelled"
            self.cancel_all_approvals("cancel")
            if self.container_state == "running":
                snap_b = self._checkpoint(c, "b")
                if snap_a and snap_b:
                    d = self._diff(c, snap_a, snap_b)
                    if d and d.get("files"):
                        self._save_changes(tid, d)
                        self.emit("files_changed", {"turn_id": tid, "files": d["files"], "truncated": d.get("truncated", False)})
            self._mirror(text, tid)
        finally:
            self.flush_buffers()
            fields = {"stop_reason": stop}
            if usage:
                fields["usage"] = usage
            self.emit("turn_end", fields)
            with self.lock:
                self.turn = None
                self.state = "idle"
                self.last_active = time.time()
            self.emit("state", {"state": "idle"})
            with self.st.db.session(write=True) as s:
                r = s.get(GooseSession, self.wid)
                if r is not None:
                    r.current_turn_id = None
                    r.last_active_at = now()

    def _timeout_turn(self) -> None:
        if self.turn is not None:
            self.emit("error", {"code": "turn_timeout", "message": "The prompt took too long and was cancelled.", "retryable": True})
            self.cancel()

    def _record_error(self, msg: str) -> None:
        with self.st.db.session(write=True) as s:
            r = s.get(GooseSession, self.wid)
            if r is not None:
                r.last_error = msg[:300]

    def _save_changes(self, tid: str, d: dict) -> None:
        self.changes = getattr(self, "changes", {})
        self.changes[tid] = d
        p = Path(self.st.cfg.data_dir) / "work_goose" / self.wid
        try:
            p.mkdir(parents=True, exist_ok=True)
            (p / f"{tid}.enc").write_text(enc(self.st, self.org_id, json.dumps(d)))
            os.chmod(p / f"{tid}.enc", 0o600)
        except OSError:
            log.warning("could not store the change set of %s", tid)

    def cancel(self) -> None:
        with self.lock:
            if self.turn is None:
                return
            self.cancelled = True
            self.set_state("cancelling")
        self.cancel_all_approvals("cancel")
        if self.peer is not None and not self.peer.closed and self.gid:
            try:
                self.peer.notify("session/cancel", {"sessionId": self.gid})
            except AcpClosed:
                pass

        def hard() -> None:
            time.sleep(10)
            if self.turn is not None and self.cancelled:
                log.warning("goose turn %s did not stop after cancel; killing the process", self.wid)
                self.drop_process()
        threading.Thread(target=hard, daemon=True).start()

    def _mirror(self, text: str, tid: str) -> None:
        """Add the prompt and Goose's answer to the conversation as ordinary messages (part of the contract)."""
        try:
            with self.st.db.session(write=True) as s:
                if not cfg(self.st, s).mirror:
                    return
                conv = s.get(Conversation, self.cid)
                if conv is None:
                    return
                answer = "".join(self.final_text.values()).strip()
                um = Message(conversation_id=conv.id, parent_id=conv.active_leaf, role="user", content_enc=self.st.vault.encrypt(text, "content"),
                             meta={"work": {"session": self.wid, "turn": tid}})
                s.add(um)
                s.flush()
                leaf = um.id
                if answer:
                    am = Message(conversation_id=conv.id, parent_id=um.id, role="assistant", content_enc=self.st.vault.encrypt(answer, "content"),
                                 status="complete" if not self.cancelled else "stopped", meta={"work": {"session": self.wid, "turn": tid}})
                    s.add(am)
                    s.flush()
                    leaf = am.id
                conv.active_leaf = leaf
                conv.updated_at = now()
        except Exception:  # noqa: BLE001
            log.exception("goose mirror")


def _audit(st, user_id: str, action: str, target: str, detail: dict, ip: str, org_id: str) -> None:
    """Audit in its own short transaction: the handlers that relay to a running turn have released the request's database lock."""
    with st.db.session(write=True) as s2:
        audit.record(s2, user_id, action, target, detail, ip, org_id)


# ---- service entry points used by the router ---------------------------------------------------------------------------------
def row_info(st, s, row: GooseSession, c: Cfg, live: Live) -> dict:
    ctx_limit = int((verified(c).get(row.model) or {}).get("context_limit") or 65536)
    return {"model": row.model, "autonomy": row.autonomy, "mcp": list(row.mcp or []), "skills": list(row.skills or []), "context": ctx_limit,
            "skill_files": live.collect_skills(s, row), "had_gid": bool(row.goose_session_id)}


def prompt(st, s, user, principal, row: GooseSession, text: str, ip: str = "") -> dict:
    c = gate(st, s, principal)
    text = (text or "").strip()
    if not text:
        raise GooseError("empty", "Write a message first.", 422)
    if len(text) > MAX_PROMPT:
        raise GooseError("too_long", f"Messages can be at most {MAX_PROMPT} characters.", 413)
    live = get_live(st, row)
    _mgr(st).start_reaper()
    ok, why = runtime(st, c).image_ready(c.image)
    if not ok:
        raise GooseError("unavailable", why, 503)
    info = row_info(st, s, row, c, live)
    live.model_state = "unknown"
    s.commit()  # release the request's database lock: the turn thread writes events while this request is still open
    _audit(st, user.id, "work.goose.prompt", row.id, {"conversation": row.conversation_id, "model": row.model, "chars": len(text)}, ip, row.org_id)
    tid = live.start_turn(text, c, info)
    return {"turn_id": tid, "seq": getattr(live, "_seq_at_accept", 0)}


def cancel(st, s, user, row: GooseSession, ip: str = "") -> None:
    live = _mgr(st).live.get(row.id)
    if live:
        live.cancel()
        _audit(st, user.id, "work.goose.cancel", row.id, {}, ip, row.org_id)


def approve(st, s, user, row: GooseSession, approval_id: str, decision: str, ip: str = "") -> None:
    live = _mgr(st).live.get(row.id)
    ap = live.approvals.get(approval_id) if live else None
    if ap is None:
        raise GooseError("not_found", "No such approval.", 404)
    if decision not in DECISIONS or decision not in ap.kinds():
        raise GooseError("bad_decision", "That choice is not offered for this approval.", 422)
    if not live.resolve(ap, decision, "user"):
        raise GooseError("already_resolved", "This approval was already answered.", 409)
    _audit(st, user.id, "work.goose.approval", row.id, {"approval": approval_id, "decision": decision, "tool": ap.tool, "extension": ap.extension}, ip, row.org_id)


def stop(st, s, user, row: GooseSession, ip: str = "") -> None:
    live = _mgr(st).live.get(row.id)
    if live:
        if live.turn is not None:
            live.cancel()
            for _ in range(60):
                if live.turn is None:
                    break
                time.sleep(0.25)
        live.shutdown(reason="stop", stop_container=True)
    _audit(st, user.id, "work.goose.session.stop", row.id, {}, ip, row.org_id)


def delete(st, s, user, row: GooseSession, ip: str = "") -> None:
    c = cfg(st, s)
    rid, cid, org = row.id, row.conversation_id, row.org_id
    live = _mgr(st).live.get(rid)
    if live:
        live.cancel()
        for _ in range(40):
            if live.turn is None:
                break
            time.sleep(0.25)
        live.shutdown(reason="stop", stop_container=False)
    try:
        runtime(st, c).remove(goose_runtime.container_name(rid))
    except RuntimeError_ as e:
        log.warning("could not remove the container of %s: %s", rid, e)
    with st.db.session(write=True) as s2:
        s2.query(GooseEvent).filter(GooseEvent.session_id == rid).delete()
        r2 = s2.get(GooseSession, rid)
        r2.deleted_at, r2.goose_session_id, r2.current_turn_id = now(), "", None
    p = Path(st.cfg.data_dir) / "work_goose" / rid
    if p.exists():
        for f in p.glob("*.enc"):
            f.unlink(missing_ok=True)
    _audit(st, user.id, "work.goose.session.delete", rid, {"conversation": cid}, ip, org)


def read_changes(st, row: GooseSession, tid: str) -> dict | None:
    live = _mgr(st).live.get(row.id)
    d = getattr(live, "changes", {}).get(tid) if live else None
    if d is None:
        p = Path(st.cfg.data_dir) / "work_goose" / row.id / f"{tid}.enc"
        if p.is_file() and tid.replace("_", "").isalnum():
            try:
                d = json.loads(dec(st, row.org_id, p.read_text()))
            except (OSError, ValueError):
                d = None
    return d


def status(st, s, user, principal) -> dict:
    c = gate(st, s, principal)
    problems = []
    rt = runtime(st, c)
    ok, why = rt.image_ready(c.image)
    if not ok:
        problems.append(why)
    models, states = [], None
    try:
        states = router_models(c)
        models = allowed_models(st, s, principal, c, states)
        if not models:
            problems.append("No coding-verified model is available to you.")
    except GooseError as e:
        problems.append(e.message)
    from . import work_skills
    skills = [work_skills.summary(sk) for sk in work_skills.list_for(st, s, user, principal, include_disabled=False)]
    return {"enabled": True, "ready": not problems, "problems": problems, "goose_version": "1.58.1", "models": models,
            "autonomy": [{"id": a, "label": {"ask": "Ask before every action", "smart": "Ask only for sensitive actions", "auto": "Never ask"}[a],
                          **({"default": True} if a == (c.autonomy_default if c.autonomy_default in c.autonomy_choices else c.autonomy_choices[0]) else {})}
                         for a in c.autonomy_choices],
            "extensions": [{"id": "developer", "label": "Files and shell", "builtin": True, "required": True}],
            "mcp_servers": [{"id": k, "label": v.get("label", k), "description": v.get("description", "")} for k, v in c.mcp.items() if v.get("enabled", True)],
            "skills": [{"id": x["id"], "name": x["name"], "title": x["title"], "description": x["description"], "scope": x["scope"]} for x in skills],
            "limits": {"memory_mb": c.memory_mb, "cpus": c.cpus, "workspace_max_mb": c.workspace_mb, "approval_timeout_s": c.approval_s,
                       "idle_stop_minutes": c.idle_min, "upload_max_mb": c.upload_mb}}
