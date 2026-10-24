"""Permission registry and the single, deterministic evaluator.

Rules (restrictions first):
  1. A permission is allowed iff at least one source grants it AND no source denies it.
  2. Grants/denies may use a trailing wildcard ("tech.*"). The bare "*" grant covers every permission
     that is not marked `sensitive` (content access, raw logs): those must always be granted by name.
  3. Sources: role grants/denies (every role the user holds), team grants/denies, direct user override.
  4. Suspended or non-active users hold nothing.
The same evaluator backs the HTTP dependencies, background work and the "Why was access denied?" explainer.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Perm:
    id: str
    label: str
    group: str
    description: str = ""
    sensitive: bool = False


def _p(id, label, group, description="", sensitive=False):
    return Perm(id, label, group, description, sensitive)


REGISTRY: list[Perm] = [
    # --- modes (use) -------------------------------------------------------------------------
    _p("mode.chat", "Use chat", "Modes"),
    _p("mode.code", "Use code workspace", "Modes"),
    _p("mode.image", "Generate images", "Modes"),
    _p("mode.video", "Generate video", "Modes"),
    _p("mode.audio", "Use audio (speech)", "Modes"),
    _p("mode.knowledge", "Use knowledge search", "Modes"),
    _p("mode.agents", "Use assistants (no tool execution)", "Modes"),
    _p("chat.ephemeral", "Start no-save conversations", "Chat"),
    _p("chat.attach", "Attach files to chat", "Chat"),
    _p("chat.export", "Export own conversations", "Chat"),
    # --- reading technical information (never operating, never content) -----------------------
    _p("tech.model.id", "See real model ID", "Technical visibility"),
    _p("tech.model.family", "See model family / architecture", "Technical visibility"),
    _p("tech.model.params", "See parameter count", "Technical visibility"),
    _p("tech.model.quant", "See quantisation and file format", "Technical visibility"),
    _p("tech.model.context", "See context window", "Technical visibility"),
    _p("tech.model.license", "See model licence", "Technical visibility"),
    _p("tech.runtime.name", "See runtime / backend name", "Technical visibility"),
    _p("tech.runtime.version", "See runtime version", "Technical visibility"),
    _p("tech.gen.params", "See effective generation settings", "Technical visibility"),
    _p("tech.usage.tokens", "See token counts", "Technical visibility"),
    _p("tech.perf.ttft", "See time to first token", "Technical visibility"),
    _p("tech.perf.tps", "See tokens per second", "Technical visibility"),
    _p("tech.perf.duration", "See request duration", "Technical visibility"),
    _p("tech.queue.position", "See queue position and state", "Technical visibility"),
    _p("tech.node.name", "See which node served the request", "Technical visibility"),
    _p("tech.resources.gpu", "See GPU load, VRAM and temperature", "Technical visibility"),
    _p("tech.resources.host", "See CPU, memory and storage", "Technical visibility"),
    _p("tech.job.trace", "See trace identifiers", "Technical visibility"),
    # --- operating (separate from reading) -----------------------------------------------------
    _p("ops.gen.edit_params", "Change generation parameters", "Operations"),
    _p("ops.model.select_actual", "Choose the underlying model/node", "Operations"),
    _p("ops.model.load", "Load / unload models", "Operations"),
    _p("ops.model.install", "Install / remove models", "Operations"),
    _p("ops.jobs.cancel_own", "Cancel own jobs", "Operations"),
    _p("ops.jobs.cancel_others", "Cancel other people's jobs", "Operations"),
    _p("ops.jobs.reprioritise", "Change queue priority", "Operations"),
    _p("ops.node.manage", "Manage nodes", "Operations"),
    # --- administration -----------------------------------------------------------------------
    _p("admin.overview", "Open the admin console", "Administration"),
    _p("admin.branding", "Edit identity and branding", "Administration"),
    _p("admin.navigation", "Edit layout and navigation", "Administration"),
    _p("admin.users.view", "View people", "Administration"),
    _p("admin.users.manage", "Create, suspend and edit people", "Administration"),
    _p("admin.users.invite", "Create invitations", "Administration"),
    _p("admin.teams.manage", "Manage teams", "Administration"),
    _p("admin.roles.manage", "Edit roles and permissions", "Administration"),
    _p("admin.models.manage", "Edit model catalogue, aliases and access", "Administration"),
    _p("admin.backends.manage", "Manage inference backends", "Administration"),
    _p("admin.policy.edit", "Edit privacy, retention and policy settings", "Administration"),
    _p("admin.signup.edit", "Edit registration and sign-in settings", "Administration"),
    _p("admin.quotas.edit", "Edit quotas", "Administration"),
    _p("admin.settings.edit", "Edit any other setting", "Administration"),
    _p("admin.settings.view", "View settings and history", "Administration"),
    _p("admin.audit.view", "View audit log", "Administration"),
    _p("admin.system.view", "View system and node health", "Administration"),
    _p("admin.backup.manage", "Manage backups", "Administration"),
    _p("admin.preview_role", "Preview the product as another role", "Administration"),
    _p("admin.sso.manage", "Configure single sign-on providers", "Administration"),  # SSO
]

BY_ID: dict[str, Perm] = {p.id: p for p in REGISTRY}
TECH_PERMS = [p.id for p in REGISTRY if p.id.startswith("tech.")]


def valid_pattern(pattern: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return any(p.id.startswith(pattern[:-1]) for p in REGISTRY)
    return pattern in BY_ID


def _matches(pattern: str, perm: str, *, for_grant: bool) -> bool:
    if pattern == "*":
        return not (for_grant and BY_ID[perm].sensitive) if perm in BY_ID else False
    if pattern.endswith(".*"):
        return perm.startswith(pattern[:-1])
    return pattern == perm


# --- display presets (editable conveniences; stored in settings as `tech.presets`) --------------
TECH_PRESET_DEFAULTS: dict[str, list[str]] = {
    "simple": [],
    "informed": ["tech.model.family", "tech.perf.tps", "tech.model.context", "tech.queue.position"],
    "technical": [
        "tech.model.id", "tech.model.family", "tech.model.params", "tech.model.quant", "tech.model.context",
        "tech.runtime.name", "tech.gen.params", "tech.usage.tokens", "tech.perf.ttft", "tech.perf.tps",
        "tech.perf.duration", "tech.queue.position",
    ],
    "expert": ["tech.*"],
}

# --- role templates (starting points only; all editable after install) ---------------------------
ROLE_TEMPLATES: list[dict] = [
    dict(id="owner", label="Owner", rank=0, system=True,
         description="Full administration. Does not include content access; that must be granted by name.",
         grants=["*"], denies=[]),
    dict(id="platform_admin", label="Platform Admin", rank=10,
         description="Runs the platform; no access to user content.",
         grants=["mode.chat", "mode.code", "mode.knowledge", "mode.agents", "chat.ephemeral", "chat.attach", "chat.export", "admin.*", "tech.*", "ops.*"],
         denies=[]),
    dict(id="org_admin", label="Organisation Admin", rank=20,
         description="People, branding and policy for the organisation.",
         grants=["mode.chat", "mode.code", "mode.knowledge", "mode.agents", "chat.ephemeral", "chat.attach", "chat.export", "ops.jobs.cancel_own", "admin.overview", "admin.branding",
                 "admin.navigation", "admin.users.view", "admin.users.manage", "admin.users.invite",
                 "admin.teams.manage", "admin.policy.edit", "admin.signup.edit", "admin.quotas.edit",
                 "admin.settings.view", "admin.audit.view", "admin.preview_role"],
         denies=[]),
    dict(id="model_operator", label="Model Operator", rank=30,
         description="Loads and manages models and backends. Cannot read chats.",
         grants=["mode.chat", "admin.overview", "admin.models.manage", "admin.backends.manage",
                 "admin.system.view", "tech.*", "ops.model.load", "ops.model.install", "ops.model.select_actual"],
         denies=[]),
    dict(id="queue_operator", label="Queue Operator", rank=30,
         description="Watches and steers the job queue.",
         grants=["mode.chat", "admin.overview", "admin.system.view", "tech.queue.position", "tech.perf.*",
                 "ops.jobs.cancel_own", "ops.jobs.cancel_others", "ops.jobs.reprioritise"],
         denies=[]),
    dict(id="compliance_reviewer", label="Compliance Reviewer", rank=40,
         description="Reads the audit log and settings history. Content review is not available in this release.",
         grants=["mode.chat", "admin.overview", "admin.audit.view", "admin.settings.view"],
         denies=[]),
    dict(id="team_manager", label="Team Manager", rank=50,
         description="Invites members and manages their team.",
         grants=["mode.chat", "mode.code", "mode.knowledge", "mode.agents", "chat.ephemeral", "chat.attach", "chat.export", "ops.jobs.cancel_own", "admin.overview",
                 "admin.users.view", "admin.users.invite", "admin.teams.manage"],
         denies=[]),
    dict(id="member", label="Member", rank=100, system=True,
         description="Everyday use. The default role for new accounts.",
         grants=["mode.chat", "mode.code", "mode.knowledge", "mode.agents", "chat.attach", "chat.export", "ops.jobs.cancel_own"], denies=[]),
    dict(id="guest", label="Guest", rank=200,
         description="Chat only, with no export or attachments.",
         grants=["mode.chat"], denies=[]),
]


@dataclass
class Source:
    kind: str  # role | team | user
    id: str
    label: str
    grants: list[str]
    denies: list[str]


@dataclass
class Decision:
    allowed: bool
    perm: str
    reason: str
    matched_denies: list[dict] = field(default_factory=list)
    matched_grants: list[dict] = field(default_factory=list)


class Principal:
    """A resolved user: identity + ordered permission sources."""

    def __init__(self, user_id: str, status: str, sources: list[Source], role_ids: list[str],
                 team_ids: list[str], email: str = "", display_name: str = ""):
        self.user_id, self.status, self.sources = user_id, status, sources
        self.role_ids, self.team_ids = role_ids, team_ids
        self.email, self.display_name = email, display_name
        self._cache: dict[str, Decision] = {}

    def decide(self, perm: str) -> Decision:
        if perm in self._cache:
            return self._cache[perm]
        d = self._decide(perm)
        self._cache[perm] = d
        return d

    def _decide(self, perm: str) -> Decision:
        if perm not in BY_ID:
            return Decision(False, perm, f"Unknown permission '{perm}'.")
        if self.status != "active":
            return Decision(False, perm, f"Account status is '{self.status}'.")
        denies, grants = [], []
        for s in self.sources:
            for pat in s.denies:
                if _matches(pat, perm, for_grant=False):
                    denies.append({"source": s.kind, "id": s.id, "label": s.label, "rule": pat})
            for pat in s.grants:
                if _matches(pat, perm, for_grant=True):
                    grants.append({"source": s.kind, "id": s.id, "label": s.label, "rule": pat})
        if denies:
            first = denies[0]
            return Decision(False, perm,
                            f"Denied by {first['source']} '{first['label']}' (rule {first['rule']}), "
                            "which overrides any grant.", denies, grants)
        if grants:
            first = grants[0]
            return Decision(True, perm, f"Granted by {first['source']} '{first['label']}' (rule {first['rule']}).",
                            denies, grants)
        return Decision(False, perm, "No role, team or user rule grants this permission.", denies, grants)

    def can(self, perm: str) -> bool:
        return self.decide(perm).allowed

    def all_allowed(self) -> list[str]:
        return [p.id for p in REGISTRY if self.can(p.id)]


def build_principal(user, roles_by_id: dict, teams_by_id: dict, override) -> Principal:
    sources: list[Source] = []
    role_ids, team_ids = [], []
    for ur in sorted(user.roles, key=lambda r: (roles_by_id[r.role_id].rank if r.role_id in roles_by_id else 999)):
        r = roles_by_id.get(ur.role_id)
        if r:
            role_ids.append(r.id)
            sources.append(Source("role", r.id, r.label, list(r.grants or []), list(r.denies or [])))
    for tm in user.teams:
        t = teams_by_id.get(tm.team_id)
        if t:
            team_ids.append(t.id)
            sources.append(Source("team", t.id, t.name, list(t.grants or []), list(t.denies or [])))
    if override:
        sources.append(Source("user", user.id, "direct override", list(override.grants or []),
                              list(override.denies or [])))
    return Principal(user.id, user.status, sources, role_ids, team_ids, user.email, user.display_name)
