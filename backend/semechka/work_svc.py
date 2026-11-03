"""Work Mode: sessions, the run extension, containers' lifecycle, the event timeline, artifacts and recovery.

Read docs/WORK_MODE.md first (design, contract) and its "Implementation notes" (the seams later phases build on).

How it fits together
  * A session is a `WorkSession` row plus ONE container and ONE named volume (`work_runtime`). A user message becomes an
    `AgentRun` (agent = the hidden per-org Work agent, `Agent.kind == 'work'`, `AgentRun.session_id`) executed by the
    EXISTING run loop in agent_svc as a job of kind "work" (same handler, own concurrency limit). Nothing here forks the loop;
    `WorkExtension` plugs into its hooks (`agent_svc.RunExtension`).
  * Tools are ordinary entries of `agent_svc.TOOLS` (see work_tools.py). They run on the host, validate their arguments, and
    reach the container only through `work_runtime`.
  * Everything that happens is also written to `work_events` (the timeline the UI polls) inside the same transaction as the
    step it describes.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass

from sqlalchemy import func, select

from . import agent_svc, audit, authz, gateway_svc, jobs_svc, services, work_runtime
from .models_agents import Agent, AgentRun, AgentStep
from .models_files import Job
from .models_tenancy import Org, OrgMember, Workspace
from .models_work import WorkArtifact, WorkEvent, WorkMessage, WorkSession
from .tenancy_svc import TenancyError

log = logging.getLogger(__name__)

AUTONOMY = ("contained", "ask")
NETWORKS = ("none", "internet")
ACTIVE_JOB = ("queued", "running", "awaiting_approval")
MAX_MESSAGE_CHARS = 20000
STATE_BLOCK_CHARS = 3000
ACTIVE_STATUSES = ("running", "awaiting_approval", "paused")


class WorkError(TenancyError):
    """A refusal with a message for the person (and for the model, when it comes out of a tool)."""


# ---- plumbing ----------------------------------------------------------------------------------------------
def enc(st, org_id: str, text: str) -> str:
    return st.vault.encrypt(text, f"org:{org_id}:content")


def dec(st, org_id: str, blob: str) -> str:
    return st.vault.decrypt(blob, f"org:{org_id}:content") if blob else ""


def instance_id(st) -> str:
    """Names this install on a container daemon that several installs share (reconcile only ever touches its own)."""
    return st.vault.mac("work-instance", "work")[:12]


def runtime(st) -> work_runtime.WorkRuntime:
    """The container runtime. Production: Docker through the sandbox socket. Tests assign a double to `st.work_runtime`;
    nothing in settings or the environment can select the double."""
    rt = st.__dict__.get("work_runtime")
    if rt is not None:
        return rt
    with st.db.session() as s:
        host = st.settings.deployment(s, "sandbox.docker_host")
    return work_runtime.DockerRuntime(host)


def _lock(st, sid: str) -> threading.RLock:
    locks = st.__dict__.setdefault("work_locks", {})
    return locks.setdefault(sid, threading.RLock())


@dataclass
class Cfg:
    enabled: bool
    image: str
    per_user: int
    total: int
    stored: int
    memory_mb: int
    cpus: float
    pids: int
    workspace_mb: int
    tool_timeout_s: int
    output_chars: int
    idle_stop_min: int
    max_steps: int
    run_timeout_s: int
    artifact_mb: int
    upload_mb: int
    network_allowed: bool
    network_name: str


def cfg(st, s) -> Cfg:
    d = st.settings.deployment
    return Cfg(bool(d(s, "work.enabled")), d(s, "work.image"), int(d(s, "work.max_sessions_per_user")), int(d(s, "work.max_running_total")),
               int(d(s, "work.max_sessions_stored")), int(d(s, "work.memory_mb")), float(d(s, "work.cpus")), int(d(s, "work.pids")),
               int(d(s, "work.workspace_max_mb")), int(d(s, "work.tool_timeout_s")), int(d(s, "work.output_max_chars")),
               int(d(s, "work.idle_stop_minutes")), int(d(s, "work.max_steps")), int(d(s, "work.run_timeout_s")),
               int(d(s, "work.artifact_max_mb")), int(d(s, "work.upload_max_mb")), bool(d(s, "work.network_allowed")),
               str(d(s, "work.network_name")))


LIMIT_KEYS = {"memory_mb": (256, "memory_mb", int), "cpus": (0.25, "cpus", float), "workspace_mb": (64, "workspace_mb", int),
              "tool_timeout_s": (5, "tool_timeout_s", int), "max_steps": (1, "max_steps", int)}


def clamp_limits(c: Cfg, asked: dict | None) -> dict:
    """What a person may lower for one session; never above the platform ceiling, never below a floor."""
    out = {}
    for k, v in (asked or {}).items():
        if k not in LIMIT_KEYS or v in (None, ""):
            continue
        floor, ceil_attr, cast = LIMIT_KEYS[k]
        try:
            out[k] = max(floor, min(cast(v), cast(getattr(c, ceil_attr))))
        except (TypeError, ValueError):
            raise WorkError("invalid", f"{k} must be a number.") from None
    return out


def effective(c: Cfg, sess: WorkSession) -> dict:
    lim = clamp_limits(c, sess.limits)  # re-clamped on every read: a lowered ceiling applies to existing sessions at once
    return {"memory_mb": lim.get("memory_mb", c.memory_mb), "cpus": lim.get("cpus", c.cpus), "pids": c.pids,
            "workspace_mb": lim.get("workspace_mb", c.workspace_mb), "tool_timeout_s": lim.get("tool_timeout_s", c.tool_timeout_s),
            "max_steps": lim.get("max_steps", c.max_steps), "output_chars": c.output_chars, "run_timeout_s": c.run_timeout_s,
            "artifact_mb": c.artifact_mb}


def container_spec(st, s, sess: WorkSession) -> work_runtime.ContainerSpec:
    c = cfg(st, s)
    lim = effective(c, sess)
    return work_runtime.ContainerSpec(session_id=sess.id, image=c.image, memory_mb=lim["memory_mb"], cpus=lim["cpus"], pids=lim["pids"],
                                      network=sess.network, network_name=c.network_name, instance=instance_id(st))


# ---- sessions -----------------------------------------------------------------------------------------------
def user_org(s, user_id: str) -> str | None:
    m = s.execute(select(OrgMember).where(OrgMember.user_id == user_id, OrgMember.status == "active")
                  .order_by(OrgMember.created_at)).scalars().first()
    return m.org_id if m else None


def get_session(s, user_id: str, sid: str) -> WorkSession:
    """Owner-only: someone else's session, a deleted one and a missing one are all the same 404."""
    sess = s.get(WorkSession, sid)
    if sess is None or sess.user_id != user_id or sess.deleted_at:
        raise WorkError("not_found", "No such session.", 404)
    return sess


def ensure_agent(st, s, org_id: str) -> Agent:
    """The built-in hidden Work agent of an organisation (and the internal workspace its runs belong to)."""
    a = s.execute(select(Agent).where(Agent.org_id == org_id, Agent.kind == "work")).scalars().first()
    if a is not None:
        if a.tools != list(work_tools.WORK_TOOLS) or a.archived:
            a.tools, a.archived = list(work_tools.WORK_TOOLS), False
        return a
    ws = Workspace(org_id=org_id, name="Work Mode (internal)", description="Home of the Work Mode agent. Not listed.",
                   visibility="shared", policy={"system": "work"}, created_by="")
    s.add(ws)
    s.flush()
    a = Agent(org_id=org_id, workspace_id=ws.id, name="Work agent", description="Built-in Work Mode agent (hidden).",
              kind="work", tools=list(work_tools.WORK_TOOLS), approval="never", context_ids=[], memory={}, limits={}, network={},
              instructions_enc="", created_by="")
    s.add(a)
    s.flush()
    return a


def check_model(st, s, principal, org: Org | None, ws: Workspace | None, model_id: str) -> None:
    why = gateway_svc.check(st, s, principal, org, ws, model_id, "chat")
    if why:
        raise WorkError("model", why, 403)


def _check_network(st, s, principal, network: str) -> None:
    if network not in NETWORKS:
        raise WorkError("invalid", "network must be 'none' or 'internet'.")
    if network == "internet":
        if not principal.can("work.network"):
            raise WorkError("forbidden", "You do not have permission to give a session internet access.", 403)
        if not cfg(st, s).network_allowed:
            raise WorkError("network_off", "An administrator has not allowed internet access for Work Mode sessions.", 403)


def create_session(st, s, user, principal, *, title: str = "", objective: str = "", model_id: str | None = None, skill_ids=None,
                   network: str = "none", autonomy: str = "contained", limits: dict | None = None, ip: str = "") -> WorkSession:
    c = cfg(st, s)
    org_id = user_org(s, user.id)
    if org_id is None:
        raise WorkError("no_org", "Work Mode needs an organisation membership; you are not an active member of one.", 409)
    if autonomy not in AUTONOMY:
        raise WorkError("invalid", "autonomy must be 'contained' or 'ask'.")
    _check_network(st, s, principal, network)
    n = s.execute(select(func.count()).select_from(WorkSession).where(WorkSession.user_id == user.id,
                                                                      WorkSession.deleted_at.is_(None))).scalar() or 0
    if n >= c.stored:
        raise WorkError("quota", f"You already have {n} sessions (limit {c.stored}). Delete one first.", 429)
    org = s.get(Org, org_id)
    agent = ensure_agent(st, s, org_id)
    ws = s.get(Workspace, agent.workspace_id)
    if model_id:
        check_model(st, s, principal, org, ws, model_id)
    else:  # the first chat model this person may use, chosen now so the session keeps one model until it is changed
        for m in gateway_svc.models_for(st, s, principal, org, ws, "chat", only=agent_svc.agent_models(st, s) or None):
            model_id = m["id"]
            break
        if not model_id:
            raise WorkError("model", "No chat model is available to you.", 409)
    title = (title or "").strip()[:200]
    objective = (objective or "").strip()[:MAX_MESSAGE_CHARS]
    sess = WorkSession(user_id=user.id, org_id=org_id, title=title or (objective[:60] if objective else "New session"),
                       objective_enc=enc(st, org_id, objective), model_id=model_id, agent_id=agent.id, status="idle",
                       network=network, autonomy=autonomy, skill_ids=[str(x) for x in (skill_ids or [])][:20],
                       limits=clamp_limits(c, limits))
    s.add(sess)
    s.flush()
    sess.container_name = sess.volume_name = work_runtime.container_name(sess.id)
    emit(st, s, sess, "state", "Session created", {"model_id": model_id, "network": network, "autonomy": autonomy})
    audit.record(s, user.id, "work.session.create", sess.id, {"network": network, "autonomy": autonomy, "model": model_id}, ip, org_id=org_id)
    if network == "internet":
        audit.record(s, user.id, "work.network.grant", sess.id, {"network": network}, ip, org_id=org_id)
    return sess


def update_session(st, s, user, principal, sess: WorkSession, body: dict, ip: str = "") -> WorkSession:
    c = cfg(st, s)
    busy = sess.status in ACTIVE_STATUSES
    if "title" in body and body["title"] is not None:
        sess.title = str(body["title"]).strip()[:200] or sess.title
    if "objective" in body and body["objective"] is not None:
        sess.objective_enc = enc(st, sess.org_id, str(body["objective"]).strip()[:MAX_MESSAGE_CHARS])
    if "autonomy" in body and body["autonomy"] is not None:
        if body["autonomy"] not in AUTONOMY:
            raise WorkError("invalid", "autonomy must be 'contained' or 'ask'.")
        sess.autonomy = body["autonomy"]
    if "model_id" in body and body["model_id"]:
        org = s.get(Org, sess.org_id)
        check_model(st, s, principal, org, s.get(Workspace, s.get(Agent, sess.agent_id).workspace_id), body["model_id"])
        sess.model_id = body["model_id"]
    if "skill_ids" in body and body["skill_ids"] is not None:
        sess.skill_ids = [str(x) for x in body["skill_ids"]][:20]
    recreate = False
    if "network" in body and body["network"] is not None and body["network"] != sess.network:
        if busy:
            raise WorkError("busy", "Change the network while the session is not working (pause or wait for the run to end).", 409)
        _check_network(st, s, principal, body["network"])
        sess.network = body["network"]
        recreate = True
        audit.record(s, user.id, "work.network.grant" if sess.network == "internet" else "work.network.revoke", sess.id,
                     {"network": sess.network}, ip, org_id=sess.org_id)
    if "limits" in body and body["limits"] is not None:
        if busy:
            raise WorkError("busy", "Change limits while the session is not working.", 409)
        sess.limits = clamp_limits(c, body["limits"])
        recreate = True
    sess.updated_at = time.time()
    if recreate:
        _drop_container(st, sess, keep_volume=True)
        sess.container_state = "absent"
        emit(st, s, sess, "state", "Container will be recreated with the new settings; the workspace is kept.")
    return sess


def _drop_container(st, sess: WorkSession, *, keep_volume: bool) -> None:
    rt = runtime(st)
    try:
        with _lock(st, sess.id):
            rt.remove(sess.container_name, volume=not keep_volume)
    except work_runtime.RuntimeError_ as e:
        log.warning("work: could not remove container of %s: %s", sess.id, e)
        if not keep_volume:
            raise WorkError("runtime", "The container runtime could not remove the session's container; it will be cleaned up later.", 502) from None


def delete_session(st, s, user, sess: WorkSession, ip: str = "", *, remove_now: bool = True) -> str:
    """Stops any run, removes the stored artifacts and every row of the session and (remove_now) its container AND volume.
    With remove_now=False the caller commits first and then calls `remove_container(st, name)`, so the database write lock is
    not held while the container runtime works. -> the container name."""
    sid, org_id, uid = sess.id, sess.org_id, sess.user_id
    sess.deleted_at = time.time()
    run_ids = [r for (r,) in s.execute(select(AgentRun.id).where(AgentRun.session_id == sid))]
    for jid in [j for (j,) in s.execute(select(AgentRun.job_id).where(AgentRun.session_id == sid))]:
        j = s.get(Job, jid)
        if j and j.status in ACTIVE_JOB:
            j.status, j.error, j.finished_at, j.cancel_requested = "cancelled", "The session was deleted.", time.time(), True
    s.flush()
    name = sess.container_name
    purge_artifact_files(st, uid, [a for (a,) in s.execute(select(WorkArtifact.id).where(WorkArtifact.session_id == sid))])
    if run_ids:
        s.query(AgentStep).filter(AgentStep.run_id.in_(run_ids)).delete(synchronize_session=False)
        s.query(AgentRun).filter(AgentRun.id.in_(run_ids)).delete(synchronize_session=False)
    s.query(WorkEvent).filter(WorkEvent.session_id == sid).delete(synchronize_session=False)
    s.query(WorkMessage).filter(WorkMessage.session_id == sid).delete(synchronize_session=False)
    s.query(WorkArtifact).filter(WorkArtifact.session_id == sid).delete(synchronize_session=False)
    s.expunge(sess)
    s.query(WorkSession).filter(WorkSession.id == sid).delete(synchronize_session=False)
    audit.record(s, user.id if user else uid, "work.session.delete", sid, {}, ip, org_id=org_id)
    if remove_now:
        remove_container(st, sid, name)
    return name


def remove_container(st, sid: str, name: str) -> bool:
    """Kill what is running and remove the container and its volume. A failure is left to the reconciler, which removes
    containers of this install that no session owns."""
    rt = runtime(st)
    try:
        rt.interrupt(name)
        with _lock(st, sid):
            rt.remove(name, volume=True)
        return True
    except work_runtime.RuntimeError_ as e:
        log.warning("work: container/volume %s left for the reconciler: %s", name, e)
        return False


def purge_sessions(st, s, *, user_id: str | None = None, org_id: str | None = None) -> int:
    """A person or organisation is being removed: take their sessions (containers, volumes, artifacts, rows) with them."""
    q = select(WorkSession)
    if user_id:
        q = q.where(WorkSession.user_id == user_id)
    if org_id:
        q = q.where(WorkSession.org_id == org_id)
    n = 0
    for sess in s.execute(q).scalars().all():
        delete_session(st, s, None, sess)
        n += 1
    return n


def purge_user(st, s, user_id: str) -> int:
    return purge_sessions(st, s, user_id=user_id)


# ---- events ------------------------------------------------------------------------------------------------
def emit(st, s, sess: WorkSession, kind: str, summary: str, detail: dict | None = None, *, run_id: str | None = None,
         step_n: int | None = None) -> WorkEvent:
    """Append to the session timeline. `summary` is short and never carries content; `detail` is encrypted."""
    ev = WorkEvent(session_id=sess.id, kind=kind, run_id=run_id, step_n=step_n, summary=(summary or "")[:300],
                   payload_enc=enc(st, sess.org_id, json.dumps(detail, default=str)) if detail else "")
    s.add(ev)
    s.flush()
    sess.updated_at = ev.created_at
    return ev


def event_out(st, sess: WorkSession, ev: WorkEvent) -> dict:
    detail = {}
    if ev.payload_enc:
        try:
            detail = json.loads(dec(st, sess.org_id, ev.payload_enc))
        except ValueError:
            detail = {}
    return {"id": ev.id, "kind": ev.kind, "run_id": ev.run_id, "step": ev.step_n, "summary": ev.summary, "detail": detail,
            "at": ev.created_at}


def events_since(st, s, sess: WorkSession, since: int, limit: int = 200) -> list[dict]:
    rows = s.execute(select(WorkEvent).where(WorkEvent.session_id == sess.id, WorkEvent.id > since).order_by(WorkEvent.id).limit(limit)).scalars()
    return [event_out(st, sess, ev) for ev in rows]


# ---- durable "session state" block ---------------------------------------------------------------------------
def load_state(st, sess: WorkSession) -> dict:
    try:
        d = json.loads(dec(st, sess.org_id, sess.state_enc)) if sess.state_enc else {}
    except ValueError:
        d = {}
    return {"notes": d.get("notes", []), "files": d.get("files", []), "results": d.get("results", []), "last_answer": d.get("last_answer", "")}


def save_state(st, sess: WorkSession, state: dict) -> None:
    state = {"notes": state["notes"][-12:], "files": state["files"][-25:], "results": state["results"][-6:],
             "last_answer": state.get("last_answer", "")[:600]}
    sess.state_enc = enc(st, sess.org_id, json.dumps(state))


def render_state(st, sess: WorkSession, state: dict) -> str:
    """The bounded block that rides in front of every model turn, so a long session survives the context window. When it is
    too big the OLDEST notes, results and files go first: the newest, and the objective, are what the model needs."""
    objective = dec(st, sess.org_id, sess.objective_enc)[:800]
    notes, results, files = [n[:300] for n in state["notes"]], [r[:220] for r in state["results"]], list(state["files"])
    last = state["last_answer"][:400]

    def build() -> str:
        parts = []
        if objective:
            parts.append("Objective: " + objective)
        if notes:
            parts.append("Your notes:\n" + "\n".join("- " + n for n in notes))
        if files:
            parts.append("Files of note: " + ", ".join(files))
        if results:
            parts.append("Recent results:\n" + "\n".join("- " + r for r in results))
        if last:
            parts.append("Your last answer to the person: " + last)
        return "\n".join(parts) or "(nothing recorded yet)"

    out = build()
    for pool in (results, notes, files):
        while len(out) > STATE_BLOCK_CHARS and len(pool) > 1:
            pool.pop(0)
            out = build()
    if len(out) > STATE_BLOCK_CHARS:
        last = last[:120]
        out = build()
    return out[:STATE_BLOCK_CHARS]


def note_file(state: dict, path: str) -> None:
    path = path.strip("/")[:200]
    if path and path not in state["files"]:
        state["files"].append(path)
    elif path in state["files"]:  # most recent last
        state["files"].remove(path)
        state["files"].append(path)


# ---- containers ---------------------------------------------------------------------------------------------
def ensure_running(st, sid: str, *, create: bool = True) -> str | None:
    """Make sure the session's container is running (create it lazily on first use, start it when idle-stopped). -> name."""
    with _lock(st, sid):
        with st.db.session() as s:
            sess = s.get(WorkSession, sid)
            if sess is None or sess.deleted_at:
                raise WorkError("not_found", "No such session.", 404)
            spec = container_spec(st, s, sess)
        rt = runtime(st)
        state = rt.state(spec.name)
        started = False
        if state == "stopped":
            if not rt.container_matches(spec):
                rt.remove(spec.name, volume=False)
                state = "absent"
            else:
                rt.start(spec.name)
                started = True
        if state == "absent":
            if not create:
                return None
            ok, why = rt.image_ready(spec.image)
            if not ok:
                raise WorkError("image", f"The Work image '{spec.image}' is not ready: {why}", 503)
            rt.create(spec)
            started = True
        elif state == "error":
            raise WorkError("runtime", "The session container is in an unusable state.", 502)
        if started or state == "absent":
            with st.db.session(write=True) as s:
                sess = s.get(WorkSession, sid)
                if sess is not None:
                    sess.container_state, sess.has_workspace = "running", True
                    sess.last_active_at = time.time()
                    emit(st, s, sess, "state", "Container started" if state != "absent" else "Container created")
        else:
            touch(st, sid)
        return spec.name


def touch(st, sid: str) -> None:
    """Activity resets the idle clock. Best effort and quick: it must never fail a tool call."""
    now = time.time()
    last = st.__dict__.setdefault("work_touched", {})
    if now - last.get(sid, 0) < 5:
        return
    last[sid] = now
    try:
        with st.db.session(write=True) as s:
            sess = s.get(WorkSession, sid)
            if sess is not None:
                sess.last_active_at = now
                if sess.container_state != "running":
                    sess.container_state = "running"
    except Exception:  # noqa: BLE001
        log.debug("work: touch failed", exc_info=True)


def stop_idle(st) -> int:
    """Stop containers of sessions that have been quiet for `work.idle_stop_minutes` (the volume stays)."""
    with st.db.session() as s:
        c = cfg(st, s)
        cutoff = time.time() - c.idle_stop_min * 60
        due = [(x.id, x.container_name) for x in s.execute(select(WorkSession).where(
            WorkSession.container_state == "running", WorkSession.deleted_at.is_(None), WorkSession.last_active_at < cutoff,
            WorkSession.status.notin_(("running", "awaiting_approval")))).scalars()]
    n = 0
    for sid, name in due:
        try:
            with _lock(st, sid):
                runtime(st).stop(name)
        except work_runtime.RuntimeError_ as e:
            log.warning("work: could not stop idle container %s: %s", name, e)
            continue
        with st.db.session(write=True) as s:
            sess = s.get(WorkSession, sid)
            if sess is not None and sess.status not in ("running", "awaiting_approval") and sess.last_active_at < cutoff:
                sess.container_state = "stopped"
                emit(st, s, sess, "state", f"Container stopped after {c.idle_stop_min} idle minutes; the workspace is kept.")
                n += 1
    return n


def reconcile(st) -> dict:
    """Adopt what exists, fix the database's belief about containers, remove containers/volumes of this install that no
    session owns. Never touches anything without this install's label."""
    rt = runtime(st)
    with st.db.session() as s:
        known = {sess.id: sess.container_name for sess in s.execute(select(WorkSession).where(WorkSession.deleted_at.is_(None))).scalars()}
    try:
        rep = rt.reconcile(instance_id(st), known)
    except work_runtime.RuntimeError_ as e:
        log.warning("work: reconcile skipped: %s", e)
        return {"error": str(e)}
    with st.db.session(write=True) as s:
        for sid, state in rep["states"].items():
            sess = s.get(WorkSession, sid)
            if sess and sess.container_state != state:
                sess.container_state = state
        for sid in rep["absent"]:
            sess = s.get(WorkSession, sid)
            if sess and sess.container_state in ("running", "stopped"):
                sess.container_state = "absent"
                emit(st, s, sess, "error", "The session's container was removed outside Semechka; a new one is created on next use.")
    return rep


# ---- artifacts ------------------------------------------------------------------------------------------------
def artifact_dir(st, user_id: str):
    return st.cfg.data_dir / "work_artifacts" / user_id


def purge_artifact_files(st, user_id: str, ids: list[str]) -> None:
    for aid in ids:
        (artifact_dir(st, user_id) / f"{aid}.bin").unlink(missing_ok=True)


def store_artifact_bytes(st, user_id: str, aid: str, data: bytes) -> str:
    d = artifact_dir(st, user_id)
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    p = d / f"{aid}.bin"
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(st.vault.encrypt_bytes(data, "work-artifact", aid))
    tmp.chmod(0o600)
    tmp.replace(p)
    return f"{user_id}/{aid}.bin"


def read_artifact(st, art: WorkArtifact, user_id: str) -> bytes:
    p = artifact_dir(st, user_id) / f"{art.id}.bin"
    return st.vault.decrypt_bytes(p.read_bytes(), "work-artifact", art.id)


def artifact_out(a: WorkArtifact) -> dict:
    return {"id": a.id, "session_id": a.session_id, "path": a.path, "name": a.name, "mime": a.mime, "size": a.size, "sha256": a.sha256,
            "created_at": a.created_at, "updated_at": a.updated_at, "step": a.created_by_step, "saved_to_library": a.saved_to_library,
            "content_url": f"/api/work/artifacts/{a.id}/content"}


# ---- messages and runs --------------------------------------------------------------------------------------
def _next_seq(s, sid: str) -> int:
    return (s.execute(select(func.max(WorkMessage.seq)).where(WorkMessage.session_id == sid)).scalar() or 0) + 1


def active_run(s, sess: WorkSession) -> tuple[AgentRun, Job] | None:
    if not sess.active_run_id:
        return None
    run = s.get(AgentRun, sess.active_run_id)
    job = s.get(Job, run.job_id) if run else None
    if run is None or job is None or job.status not in ACTIVE_JOB:
        return None
    return run, job


def running_counts(s, user_id: str) -> tuple[int, int]:
    """(this person's, everyone's) runs that are queued, running or waiting: the concurrency limits count these."""
    base = s.query(Job).filter(Job.kind == "work", Job.status.in_(ACTIVE_JOB))
    return base.filter(Job.created_by == user_id).count(), base.count()


def _start_run(st, s, sess: WorkSession, content: str, task_seq: int) -> AgentRun:
    agent = s.get(Agent, sess.agent_id)
    job = jobs_svc.enqueue(s, "work", org_id=sess.org_id, ws_id=agent.workspace_id, user_id=sess.user_id,
                           payload={"agent_id": agent.id}, priority=150)
    run = AgentRun(job_id=job.id, agent_id=agent.id, agent_version=agent.version, org_id=sess.org_id, workspace_id=agent.workspace_id,
                   user_id=sess.user_id, task_enc=enc(st, sess.org_id, content), session_id=sess.id,
                   usage={"model": sess.model_id, "model_calls": 0, "tool_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                          "task_seq": task_seq, "seen_seq": task_seq})
    s.add(run)
    s.flush()
    job.payload = {"agent_id": agent.id, "run_id": run.id}
    sess.active_run_id = run.id
    sess.status = "running"
    sess.last_active_at = time.time()
    return run


def post_message(st, s, user, principal, sess: WorkSession, content: str, ip: str = "") -> dict:
    content = (content or "").strip()
    if not content or len(content) > MAX_MESSAGE_CHARS:
        raise WorkError("invalid", f"Write a message of up to {MAX_MESSAGE_CHARS:,} characters.")
    c = cfg(st, s)
    org = s.get(Org, sess.org_id)
    agent = ensure_agent(st, s, sess.org_id)
    if sess.model_id:
        check_model(st, s, principal, org, s.get(Workspace, agent.workspace_id), sess.model_id)  # refuse early, with the reason
    cur = active_run(s, sess)
    seq = _next_seq(s, sess.id)
    if cur is not None:  # the active run reads it at its next model turn
        run, _job = cur
        s.add(WorkMessage(session_id=sess.id, seq=seq, role="user", content_enc=enc(st, sess.org_id, content), run_id=run.id))
        emit(st, s, sess, "message", "You", {"role": "user", "content": content, "seq": seq}, run_id=run.id)
        return {"run_id": run.id, "seq": seq, "appended": True, "queued": False}
    mine, total = running_counts(s, user.id)
    if mine >= c.per_user:
        raise WorkError("busy", f"You already have {mine} session(s) working (limit {c.per_user}). Wait for one to finish.", 429)
    cap = (authz.Authz(s, user.id).org(sess.org_id).quotas or {}).get("agent_runs_per_day")
    if cap:
        from . import usage_svc
        if usage_svc.runs_today(s, sess.org_id) >= int(cap):
            raise WorkError("quota", f"Your organisation's limit of {cap} agent runs per day has been reached.", 429)
    run = _start_run(st, s, sess, content, seq)
    s.add(WorkMessage(session_id=sess.id, seq=seq, role="user", content_enc=enc(st, sess.org_id, content), run_id=run.id))
    emit(st, s, sess, "message", "You", {"role": "user", "content": content, "seq": seq}, run_id=run.id)
    emit(st, s, sess, "state", "Run started" + (" (waiting for a free slot)" if total >= c.total else ""), {"status": "running"}, run_id=run.id)
    audit.record(s, user.id, "work.run.start", sess.id, {"run": run.id}, ip, org_id=sess.org_id)
    return {"run_id": run.id, "seq": seq, "appended": False, "queued": total >= c.total}


def pause(st, s, user, sess: WorkSession) -> WorkSession:
    cur = active_run(s, sess)
    if cur is None:
        raise WorkError("idle", "Nothing is running in this session.", 409)
    if sess.status != "paused":
        sess.status = "paused"
        emit(st, s, sess, "state", "Paused: the current step finishes, then the run waits", {"status": "paused"}, run_id=cur[0].id)
        audit.record(s, user.id, "work.run.pause", sess.id, {"run": cur[0].id}, org_id=sess.org_id)
    return sess


def resume(st, s, user, sess: WorkSession) -> WorkSession:
    cur = active_run(s, sess)
    if cur is None or sess.status != "paused":
        raise WorkError("idle", "This session is not paused.", 409)
    run, job = cur
    waiting = s.execute(select(AgentStep).where(AgentStep.run_id == run.id, AgentStep.status == "awaiting_approval")).first()
    sess.status = "awaiting_approval" if waiting else "running"
    if job.status == "awaiting_approval" and not waiting:
        job.status, job.lease_until, job.progress_text = "queued", 0, ""
    emit(st, s, sess, "state", "Resumed", {"status": sess.status}, run_id=run.id)
    audit.record(s, user.id, "work.run.resume", sess.id, {"run": run.id}, org_id=sess.org_id)
    return sess


def cancel(st, s, user, sess: WorkSession) -> WorkSession:
    cur = active_run(s, sess)
    if cur is None:
        raise WorkError("idle", "Nothing is running in this session.", 409)
    run, job = cur
    if job.status in ("queued", "awaiting_approval"):
        job.status, job.error, job.finished_at = "cancelled", "Cancelled.", time.time()
        job.cancel_requested = True
        _close_run(st, s, sess, run, "cancelled", "Cancelled by you.")
    else:
        job.cancel_requested = True
        emit(st, s, sess, "state", "Cancelling: running commands are being stopped", {"status": "cancelling"}, run_id=run.id)
    audit.record(s, user.id, "work.run.cancel", sess.id, {"run": run.id}, org_id=sess.org_id)
    return sess


def interrupt_commands(st, sess: WorkSession) -> None:
    """Called after the cancel committed: kill whatever the container is executing so the run notices at once."""
    try:
        runtime(st).interrupt(sess.container_name)
    except Exception:  # noqa: BLE001
        log.debug("work: interrupt failed", exc_info=True)


def decide_approval(st, s, user, sess: WorkSession, step_n: int, approve: bool) -> AgentStep:
    cur = active_run(s, sess)
    if cur is None:
        raise WorkError("not_found", "That step is not waiting for approval.", 404)
    run, job = cur
    stp = s.execute(select(AgentStep).where(AgentStep.run_id == run.id, AgentStep.n == step_n)).scalar_one_or_none()
    if stp is None or stp.status != "awaiting_approval":
        raise WorkError("not_found", "That step is not waiting for approval.", 404)
    stp.status = "approved" if approve else "denied"
    stp.error = "" if approve else "A person declined to approve this action."
    stp.meta = {**(stp.meta or {}), "decided_by": user.id, "decided_at": time.time()}
    if job.status == "awaiting_approval" and sess.status != "paused":
        job.status, job.lease_until = "queued", 0
    sess.status = "paused" if sess.status == "paused" else "running"
    emit(st, s, sess, "approval", ("Approved: " if approve else "Declined: ") + stp.tool, {"step": step_n, "approved": approve, "tool": stp.tool},
         run_id=run.id, step_n=step_n)
    audit.record(s, user.id, "work.approval", sess.id, {"step": step_n, "approved": approve, "tool": stp.tool}, org_id=sess.org_id)
    return stp


def pending_approvals(st, s, sess: WorkSession) -> list[dict]:
    cur = active_run(s, sess)
    if cur is None:
        return []
    out = []
    for stp in s.execute(select(AgentStep).where(AgentStep.run_id == cur[0].id, AgentStep.status == "awaiting_approval")).scalars():
        try:
            args = json.loads(dec(st, sess.org_id, stp.args_enc) or "{}")
        except ValueError:
            args = {}
        out.append({"step": stp.n, "tool": stp.tool, "reason": (stp.meta or {}).get("approval_reason", ""), "args": args})
    return out


def _close_run(st, s, sess: WorkSession, run: AgentRun, outcome: str, message: str) -> None:
    """The run is over (any way): the session is free again; messages written while it ended start a follow-up run."""
    sess.active_run_id = None
    if outcome == "succeeded":
        sess.status = "idle"
    elif outcome == "cancelled":
        sess.status = "cancelled"
        s.add(WorkMessage(session_id=sess.id, seq=_next_seq(s, sess.id), role="system", content_enc=enc(st, sess.org_id, message), run_id=run.id))
        emit(st, s, sess, "state", message or "Cancelled", {"status": "cancelled"}, run_id=run.id)
    else:
        sess.status = "failed"
        s.add(WorkMessage(session_id=sess.id, seq=_next_seq(s, sess.id), role="system",
                          content_enc=enc(st, sess.org_id, "The run stopped before finishing: " + message), run_id=run.id))
        emit(st, s, sess, "error", "The run stopped: " + message[:200], {"message": message}, run_id=run.id)
    sess.last_active_at = time.time()
    if outcome == "succeeded":
        seen = int((run.usage or {}).get("seen_seq", 0))
        late = s.execute(select(WorkMessage).where(WorkMessage.session_id == sess.id, WorkMessage.role == "user", WorkMessage.seq > seen,
                                                   WorkMessage.run_id == run.id).order_by(WorkMessage.seq)).scalars().all()
        if late:
            text = "\n\n".join(dec(st, sess.org_id, m.content_enc) for m in late)
            new = _start_run(st, s, sess, text, late[0].seq)
            for m in late:
                m.run_id = new.id
            emit(st, s, sess, "state", "Run started for a message sent while the last one was finishing", {"status": "running"}, run_id=new.id)


def settle(st) -> int:
    """After a restart (or any crash): a session whose run's job is already over must not stay 'running' forever."""
    n = 0
    with st.db.session(write=True) as s:
        for sess in s.execute(select(WorkSession).where(WorkSession.deleted_at.is_(None), WorkSession.active_run_id.is_not(None))).scalars().all():
            run = s.get(AgentRun, sess.active_run_id)
            job = s.get(Job, run.job_id) if run else None
            if run is None or job is None:
                sess.active_run_id, sess.status = None, "failed"
                n += 1
            elif job.status in ("succeeded", "failed", "cancelled"):
                _close_run(st, s, sess, run, job.status, job.error or ("Interrupted: the server restarted before the run finished."
                                                                        if job.status == "failed" else "Cancelled."))
                n += 1
    return n


def startup(st) -> dict:
    """Run once when the application starts (after jobs were requeued): adopt containers, settle sessions."""
    out = {"settled": settle(st)}
    out["reconcile"] = reconcile(st)
    return out


async def sweeper(st) -> None:
    import asyncio
    tick = 0
    while True:
        await asyncio.sleep(60)
        tick += 1
        try:
            await asyncio.to_thread(stop_idle, st)
            if tick % 5 == 0:
                await asyncio.to_thread(settle, st)
                await asyncio.to_thread(reconcile, st)
        except Exception:  # noqa: BLE001
            log.exception("work sweep failed")


# ---- state endpoint ---------------------------------------------------------------------------------------------
def session_out(st, s, sess: WorkSession, c: Cfg | None = None) -> dict:
    c = c or cfg(st, s)
    agent = s.get(Agent, sess.agent_id) if sess.agent_id else None
    return {"id": sess.id, "title": sess.title, "objective": dec(st, sess.org_id, sess.objective_enc), "model_id": sess.model_id,
            "status": sess.status, "container_state": sess.container_state, "network": sess.network, "autonomy": sess.autonomy,
            "skill_ids": sess.skill_ids or [], "limits": effective(c, sess), "active_run_id": sess.active_run_id,
            "created_at": sess.created_at, "updated_at": sess.updated_at, "last_active_at": sess.last_active_at,
            "tools": list(agent.tools) if agent else []}


def state_of(st, s, sess: WorkSession, *, live: bool = False) -> dict:
    c = cfg(st, s)
    lim = effective(c, sess)
    cur = active_run(s, sess)
    run_out = None
    if cur is not None:
        run, job = cur
        steps = s.query(AgentStep).filter(AgentStep.run_id == run.id)
        run_out = {"id": run.id, "job_status": job.status, "model_calls": (run.usage or {}).get("model_calls", 0),
                   "tool_calls": (run.usage or {}).get("tool_calls", 0), "max_steps": lim["max_steps"],
                   "steps": steps.count(), "started_at": job.started_at}
    used = st.__dict__.get("work_usage", {}).get(sess.id)
    usage = {"workspace_bytes": used[1] if used else None, "workspace_limit_bytes": lim["workspace_mb"] * 1048576,
             "memory_limit_mb": lim["memory_mb"], "cpus": lim["cpus"]}
    if live and sess.container_state == "running":
        usage["live"] = runtime(st).live_usage(sess.container_name)
    return {"session": session_out(st, s, sess), "status": sess.status,
            "container": {"name": sess.container_name, "state": sess.container_state, "network": sess.network,
                          "image": c.image},
            "active_run": run_out, "pending_approvals": pending_approvals(st, s, sess),
            "engine_jobs": [],  # phase B: generation jobs started by engine tools, with progress
            "artifacts": s.query(WorkArtifact).filter(WorkArtifact.session_id == sess.id).count(),
            "last_event_id": s.execute(select(func.max(WorkEvent.id)).where(WorkEvent.session_id == sess.id)).scalar() or 0,
            "usage": usage}


# ---- library ------------------------------------------------------------------------------------------------------
def save_to_library(st, s, user, principal, art: WorkArtifact) -> dict:
    """Copy an image or video artifact into the person's media library (the gallery's asset store). Other kinds have no
    library to go to yet: they stay downloadable from the session."""
    import uuid

    from . import media_svc
    from .media_models import MediaAsset, MediaJob
    kind = "image" if art.mime.startswith("image/") else ("video" if art.mime.startswith("video/") else "")
    if not kind or art.mime not in media_svc.SAFE_MIME:
        raise WorkError("unsupported", "Only PNG, JPEG and WebP images and MP4 or WebM videos can be saved to the library.", 422)
    if art.saved_to_library and art.library_asset_id:
        return {"asset_id": art.library_asset_id, "already": True}
    state = services.mode_state(s, principal, st, kind)
    if not state["enabled"]:
        raise WorkError("unavailable", f"The {kind} library is not available to you: {state['reason']}", 409)
    data = read_artifact(st, art, user.id)
    days = st.settings.value_for(s, "privacy.chat_retention_days", principal)
    now = time.time()
    expires = now + days * 86400 if days and days > 0 else None
    w = h = None
    if kind == "image":
        try:
            import io

            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                w, h = im.size
        except Exception:  # noqa: BLE001
            w = h = None
    job = MediaJob(user_id=user.id, kind=kind, model_id="work-mode", backend_id="work-mode", status="completed", save=True,
                   prompt_enc=st.vault.encrypt(f"Work Mode artifact: {art.name}", "media"), params_enc="", progress=1.0,
                   progress_text="Saved from a Work Mode session", created_at=now, started_at=now, finished_at=now, expires_at=expires)
    s.add(job)
    s.flush()
    aid = "ma_" + uuid.uuid4().hex[:24]
    media_svc.write_asset(st, user.id, aid, data)
    s.add(MediaAsset(id=aid, user_id=user.id, job_id=job.id, kind=kind, model_id="work-mode", mime=art.mime, size=len(data), width=w, height=h,
                     prompt_enc=st.vault.encrypt(f"Work Mode artifact: {art.name}", "media"), params_enc="", expires_at=expires))
    art.saved_to_library, art.library_asset_id = True, aid
    audit.record(s, user.id, "work.artifact.save", art.id, {"asset": aid, "kind": kind})
    return {"asset_id": aid, "job_id": job.id, "kind": kind, "already": False}


# ---- the run extension -----------------------------------------------------------------------------------------
NETWORK_RE = re.compile(r"\b(curl|wget|ssh|scp|rsync|nc|ncat|telnet|ftp|pip3?\s+(install|download)|npm\s+(install|i|ci)|yarn|apt(-get)?\s|"
                        r"git\s+(clone|fetch|pull|push|remote)|requests\.|urllib|http\.client|socket\.|aiohttp|https?://)", re.I)
DESTRUCTIVE_RE = re.compile(r"(\brm\s+(-[a-zA-Z]*[rRf][a-zA-Z]*\s+)+(/work/?\*?|/work/\.\*|\.\.?/?\*?|\*|~/?\*?)(\s|$|;|&|\|)|"
                            r"\bfind\s+(/work|\.)\s.*-delete|\bshutil\.rmtree\(\s*['\"](/work|\.)/?['\"]|\bmkfs\b|\bdd\s+if=.*of=/)", re.I)
GIT_NETWORK = {"clone", "fetch", "pull", "push", "remote"}


def short(text: str, n: int = 80) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:n]


class WorkExtension(agent_svc.RunExtension):
    # ---- gating ---------------------------------------------------------------------------------------------
    def _sess(self, s, run) -> WorkSession | None:
        sess = s.get(WorkSession, run.session_id) if run.session_id else None
        return None if sess is None or sess.deleted_at else sess

    def disabled_reason(self, st, s, run) -> str | None:
        sess = self._sess(s, run)
        if sess is None:
            return "The session no longer exists."
        from .models import User
        user = s.get(User, run.user_id)
        state = services.mode_state(s, services.load_principal(s, user), st, "work")
        if not state["enabled"]:
            return "Work Mode is not available to you: " + state["reason"]
        return None

    def before_turn(self, st, s, run, job) -> None:
        sess = self._sess(s, run)
        if sess is not None and sess.status == "paused":
            job.status, job.progress_text = "awaiting_approval", "Paused"
            raise jobs_svc.JobPaused()
        if sess is not None:
            usage = dict(run.usage or {})
            seen = s.execute(select(func.max(WorkMessage.seq)).where(WorkMessage.session_id == sess.id)).scalar() or 0
            if usage.get("seen_seq") != seen:
                run.usage = {**usage, "seen_seq": seen}

    def on_interrupted(self, st, s, run, steps) -> None:
        sess = self._sess(s, run)
        if sess is not None:
            emit(st, s, sess, "state", "A command was interrupted by a restart; the run continues and will check its state",
                 {"steps": [x.n for x in steps]}, run_id=run.id)

    def limits(self, st, s, run) -> dict:
        sess = self._sess(s, run)
        if sess is None:
            return {}
        lim = effective(cfg(st, s), sess)
        return {"max_steps": lim["max_steps"], "timeout_s": lim["run_timeout_s"], "max_observation_chars": lim["output_chars"]}

    def resolve_model(self, st, s, agent, run, principal) -> str | None:
        sess = self._sess(s, run)
        if sess is None or not sess.model_id:
            return None
        check_model(st, s, principal, s.get(Org, run.org_id), s.get(Workspace, run.workspace_id), sess.model_id)
        return sess.model_id

    # ---- the prompt -------------------------------------------------------------------------------------------
    def messages(self, st, s, agent, run, ws, person, steps, lim) -> list[dict]:
        from . import work_prompt
        sess = self._sess(s, run)
        eff = effective(cfg(st, s), sess)
        state = load_state(st, sess)
        usage = run.usage or {}
        task_seq = int(usage.get("task_seq", 0))
        msgs = [{"role": "system", "content": work_prompt.system_prompt(st, s, sess, agent, person, eff, render_state(st, sess, state))}]
        hist = work_prompt.history(st, s, sess, run, task_seq)
        task = agent_svc._dec(st, run.org_id, run.task_enc)
        if hist and hist[-1]["role"] == "user":  # alternate roles: fold a dangling earlier message into this one
            task = hist.pop()["content"] + "\n\n" + task
        msgs += hist
        msgs.append({"role": "user", "content": task})
        extra = s.execute(select(WorkMessage).where(WorkMessage.session_id == sess.id, WorkMessage.run_id == run.id, WorkMessage.role == "user",
                                                    WorkMessage.seq > task_seq).order_by(WorkMessage.seq)).scalars().all()
        # a message sent mid-run is placed where it happened: before the first step recorded after it
        cuts, rest = [], list(extra)
        for i, stp in enumerate(steps):
            while rest and rest[0].created_at <= stp.started_at:
                cuts.append((i, rest.pop(0)))
        cuts += [(len(steps), m) for m in rest]
        pos = 0
        for i, m in cuts:
            msgs += agent_svc.step_messages(st, run, steps[pos:i], lim)
            msgs.append({"role": "user", "content": "The person sent another message while you were working:\n" + dec(st, sess.org_id, m.content_enc)})
            pos = i
        msgs += agent_svc.step_messages(st, run, steps[pos:], lim)
        return msgs

    # ---- approvals --------------------------------------------------------------------------------------------
    def needs_approval(self, st, s, run, agent, spec, args) -> str | None:
        sess = self._sess(s, run)
        if sess is None:
            return None
        if spec.sensitive:
            return f"{spec.name} is marked sensitive."
        if sess.autonomy == "ask" and spec.mutating:
            return "This session asks before every command, edit and commit."
        a = args or {}
        gargs = a.get("args")
        if isinstance(gargs, str):
            import shlex
            try:
                gargs = shlex.split(gargs)
            except ValueError:
                gargs = [gargs]
        gargs = [str(x) for x in gargs] if isinstance(gargs, list) else []
        text = str(a.get("cmd") or a.get("code") or " ".join(gargs))
        if spec.name == "work_git" and sess.network == "internet" and gargs and gargs[0] in GIT_NETWORK:
            return f"git {gargs[0]} talks to a remote."
        if spec.name in ("work_shell", "work_python", "work_git") and sess.network == "internet" and NETWORK_RE.search(text):
            return "This command looks like it uses the network."
        if spec.name in ("work_shell", "work_python") and DESTRUCTIVE_RE.search(text):
            return "This command deletes the workspace or a large part of it."
        return None

    # ---- timeline ---------------------------------------------------------------------------------------------
    def on_model(self, st, s, run, n, text, action, parse_error) -> None:
        sess = self._sess(s, run)
        if sess is not None and parse_error:
            emit(st, s, sess, "error", "The model's reply was not a valid action", {"why": parse_error, "reply": text[:1500]}, run_id=run.id, step_n=n)

    def on_tool_requested(self, st, s, run, step) -> None:
        sess = self._sess(s, run)
        if sess is None:
            return
        try:
            args = json.loads(dec(st, run.org_id, step.args_enc) or "{}")
        except ValueError:
            args = {}
        if step.status == "error":
            emit(st, s, sess, "error", step.error[:200], {"tool": step.tool}, run_id=run.id, step_n=step.n)
            return
        label = step.tool + (" " + short(str(args.get("path") or ""), 60) if args.get("path") else "")
        emit(st, s, sess, "tool_call", label.strip(), {"tool": step.tool, "args": args}, run_id=run.id, step_n=step.n)
        if step.status == "awaiting_approval":
            sess.status = "awaiting_approval"
            emit(st, s, sess, "approval", "Waiting for your approval: " + step.tool,
                 {"step": step.n, "tool": step.tool, "reason": (step.meta or {}).get("approval_reason", ""), "args": args},
                 run_id=run.id, step_n=step.n)

    def before_tool(self, tc, spec, args) -> None:
        sess = self._sess(tc.s, tc.run)
        if sess is None:
            raise agent_svc.ToolError("The session no longer exists.")
        tc.work_session = sess
        if sess.network == "internet":
            from .models import User
            p = services.load_principal(tc.s, tc.s.get(User, tc.run.user_id))
            if not p.can("work.network") or not cfg(tc.st, tc.s).network_allowed:
                raise agent_svc.ToolError("This session's internet access is no longer permitted; change the session to 'no network'.")

    def on_tool_done(self, tc, step, status, err, text, dur) -> None:
        sess = self._sess(tc.s, tc.run)
        if sess is None:
            return
        st, s, run = tc.st, tc.s, tc.run
        if sess.status == "awaiting_approval":
            sess.status = "running"
        res = getattr(tc, "work_result", {}) or {}
        detail = {"tool": step.tool, "status": status, "seconds": round(dur, 2), "text": text[:20000], "error": err, **res}
        label = f"{step.tool} {status}" + (f" (exit {res['exit']})" if "exit" in res else "")
        emit(st, s, sess, "tool_result", label, detail, run_id=run.id, step_n=step.n)
        state = load_state(st, sess)
        for p in res.get("files", []):
            note_file(state, p)
        first = short((text or err).splitlines()[0] if (text or err) else "", 140)
        state["results"].append(f"{step.tool} {status}" + (f" exit {res['exit']}" if "exit" in res else "") + (f": {first}" if first else ""))
        if step.tool == "work_note" and res.get("note"):
            state["notes"].append(res["note"])
        save_state(st, sess, state)
        sess.last_active_at = time.time()
        if step.tool in ("work_shell", "work_python", "work_git", "work_write_file", "work_edit_file", "work_publish_artifact"):
            audit.record(s, run.user_id, "work.tool", sess.id,
                         {"tool": step.tool, "status": status, "run": run.id, "step": step.n, "seconds": round(dur, 2),
                          **({"exit": res["exit"]} if "exit" in res else {}), **({"bytes": res["bytes"]} if "bytes" in res else {})},
                         org_id=run.org_id)

    def on_final(self, st, s, run, final) -> None:
        sess = self._sess(s, run)
        if sess is None:
            return
        s.add(WorkMessage(session_id=sess.id, seq=_next_seq(s, sess.id), role="assistant", content_enc=enc(st, sess.org_id, final), run_id=run.id))
        emit(st, s, sess, "message", "Answer", {"role": "assistant", "content": final, "artifacts": run.artifacts}, run_id=run.id)
        state = load_state(st, sess)
        state["last_answer"] = final
        save_state(st, sess, state)

    def on_end(self, st, s, run, outcome, message) -> None:
        sess = self._sess(s, run)
        if sess is None or sess.active_run_id != run.id:
            return
        _close_run(st, s, sess, run, outcome, message)


# ---- registration -----------------------------------------------------------------------------------------------
agent_svc.EXTENSIONS["work"] = WorkExtension()
jobs_svc.HANDLERS["work"] = agent_svc.handle_agent
jobs_svc.CONCURRENCY["work"] = lambda st: _max_running(st)


def _max_running(st) -> int:
    try:
        with st.db.session() as s:
            return int(st.settings.deployment(s, "work.max_running_total"))
    except Exception:  # noqa: BLE001
        return 2


from . import work_tools  # noqa: E402,F401  (registers the tools in agent_svc.TOOLS)
