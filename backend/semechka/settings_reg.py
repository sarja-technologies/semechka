"""The typed settings registry: every product-choice setting has one contract here.

Each definition carries id, type/widget, default, valid scopes, merge rule, the permission needed to edit,
whether lowering it reduces privacy/security (confirmation + impact diff), whether it applies live or on
restart, an optional capability prerequisite, and a validator. The admin GUI, API, CLI and import/export
are all generated from this list, so a control can never exist without a server-side definition.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

SCOPE_ORDER = ["deployment", "role", "team", "user"]  # least -> most specific
ALL_SCOPES = ["deployment", "role", "team", "user"]


class SettingError(ValueError):
    pass


@dataclass
class Def:
    id: str
    category: str
    label: str
    description: str
    type: str  # bool int float string text enum list color secret json
    default: Any
    widget: str = ""
    choices: list[str] | None = None
    min: float | None = None
    max: float | None = None
    scopes: list[str] = field(default_factory=lambda: ["deployment"])
    merge: str = "specific"  # specific | min | max | false_wins | true_wins
    edit_perm: str = "admin.settings.edit"
    lowers_privacy: Callable[[Any, Any], bool] | None = None  # (old,new) -> True if it weakens protection
    apply: str = "live"  # live | restart
    requires: str | None = None  # capability key checked against runtime capabilities
    aliases: list[str] = field(default_factory=list)
    public: bool = False  # readable by signed-out visitors (branding, signup state)
    secret: bool = False
    validator: Callable[[Any], Any] | None = None
    effect: str = ""  # one-line security / behaviour consequence

    def to_public(self) -> dict:
        return {
            "id": self.id, "category": self.category, "label": self.label, "description": self.description,
            "type": self.type, "widget": self.widget or self.type, "default": None if self.secret else self.default,
            "choices": self.choices, "min": self.min, "max": self.max, "scopes": self.scopes, "merge": self.merge,
            "edit_perm": self.edit_perm, "apply": self.apply, "requires": self.requires, "aliases": self.aliases,
            "secret": self.secret, "effect": self.effect, "sensitive_change": self.lowers_privacy is not None,
        }


# ---- validators ------------------------------------------------------------------------------------
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _lum(hexcol: str) -> float:
    def ch(i):
        c = int(hexcol[1 + 2 * i:3 + 2 * i], 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(0) + 0.7152 * ch(1) + 0.0722 * ch(2)


def contrast(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _accent(*surfaces: str):
    """An accent is used as text and as a fill, so it must reach 4.5:1 on every surface it can sit on in those themes."""
    def v(value):
        if not isinstance(value, str) or not _HEX.match(value):
            raise SettingError("Use a #RRGGBB colour.")
        worst = min(surfaces, key=lambda sfc: contrast(value, sfc))
        ratio = contrast(value, worst)
        if ratio < 4.5:
            raise SettingError(f"Contrast against {worst} is {ratio:.2f}:1; WCAG AA needs 4.5:1 on every surface the theme uses.")
        return value.upper()
    return v


def _domains(value):
    out = []
    for d in value:
        d = str(d).strip().lower().lstrip("@")
        if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", d):
            raise SettingError(f"'{d}' is not a valid domain.")
        out.append(d)
    return sorted(set(out))


def _url_or_blank(value):
    if value in ("", None):
        return ""
    if not re.match(r"^(https?://|mailto:)[^\s]+$", value):
        raise SettingError("Must start with https://, http:// or mailto:")
    return value


def _asset_id(value):
    if value in ("", None):
        return ""
    if not re.fullmatch(r"[0-9a-f]{24}\.(png|webp|jpg|svg)", str(value)):
        raise SettingError("Upload the image through the branding page; this is not a valid asset id.")
    return value


def _nav_items(value):
    allowed = {"chat", "code", "image", "video", "audio", "knowledge", "agents", "usage", "help"}
    seen = set()
    out = []
    for it in value:
        if not isinstance(it, dict) or it.get("id") not in allowed or it["id"] in seen:
            raise SettingError("Each navigation item needs a unique, known id.")
        seen.add(it["id"])
        label = str(it.get("label", "")).strip()[:40]
        out.append({"id": it["id"], "label": label, "enabled": bool(it.get("enabled", True))})
    return out


def _prompts(value):
    out = [str(x).strip()[:200] for x in value if str(x).strip()]
    if len(out) > 8:
        raise SettingError("At most 8 suggested prompts.")
    return out


def _lower_if_bool_true_to_false(old, new):
    return bool(old) and not bool(new)


def _raise_if_bool_false_to_true(old, new):
    return (not bool(old)) and bool(new)


def _retention_longer(old, new):
    # -1 = keep forever; 0 = do not keep. Longer retention weakens privacy.
    def rank(v):
        return 10**9 if v == -1 else v
    return rank(new) > rank(old)


_PRIVACY_ORDER = {"no_retention": 0, "standard": 1, "user_controlled": 1, "enterprise": 1}

C = "Identity & Branding"
N = "Layout & Navigation"
M = "Modes"
S_ = "Registration & Sign-in"
P = "Privacy & Retention"
Q = "Quotas & Scheduling"
T = "Technical Visibility"
SYS = "System"
E = "Email"
U = "Chat & Composer"

DEFS: list[Def] = [
    # ---- identity & branding ----
    Def("brand.org_name", C, "Organisation name", "Shown in legal footer, emails and the privacy notice.",
        "string", "My Organisation", scopes=["deployment"], edit_perm="admin.branding", public=True,
        aliases=["company", "organization"], validator=lambda v: _strlen(v, 1, 80)),
    Def("brand.app_name", C, "Application name", "The product name users see in the header and sign-in screen.",
        "string", "Semechka", scopes=["deployment"], edit_perm="admin.branding", public=True,
        aliases=["rename platform", "product name"], validator=lambda v: _strlen(v, 1, 60)),
    Def("brand.short_name", C, "Short name", "Used on narrow screens and as the wordmark fallback.",
        "string", "Semechka", scopes=["deployment"], edit_perm="admin.branding", public=True,
        validator=lambda v: _strlen(v, 1, 20)),
    Def("brand.tab_title", C, "Browser tab title", "The text on the browser tab.", "string", "Semechka",
        scopes=["deployment"], edit_perm="admin.branding", public=True, aliases=["title"],
        validator=lambda v: _strlen(v, 1, 60)),
    Def("brand.logo_asset", C, "Logo", "Uploaded logo asset id (SVG, PNG or WebP).", "string", "",
        scopes=["deployment"], edit_perm="admin.branding", public=True, widget="asset", validator=_asset_id),
    Def("brand.favicon_asset", C, "Favicon", "Uploaded favicon asset id.", "string", "",
        scopes=["deployment"], edit_perm="admin.branding", public=True, widget="asset", validator=_asset_id),
    Def("brand.theme_default", C, "Default theme", "Paper (light), Carbon (dark) or Graphite (dark grey).",
        "enum", "paper", choices=["paper", "carbon", "graphite", "system"], scopes=["deployment", "user"],
        edit_perm="admin.branding", public=True),
    Def("brand.accent_light", C, "Accent colour (light themes)",
        "Primary action colour. Must reach 4.5:1 contrast on white and on the light-grey Paper surfaces.", "color", "#087771",
        scopes=["deployment"], edit_perm="admin.branding", public=True, validator=_accent("#FFFFFF", "#F7F7F5", "#F0F1EE")),
    Def("brand.accent_dark", C, "Accent colour (dark themes)",
        "Primary action colour. Must reach 4.5:1 contrast on the Carbon and Graphite surfaces.", "color", "#64BEB5",
        scopes=["deployment"], edit_perm="admin.branding", public=True, validator=_accent("#1A1E1F", "#222728", "#303637", "#394041")),
    Def("brand.radius", C, "Corner radius", "Control and card corner radius in pixels.", "int", 8,
        min=0, max=16, scopes=["deployment"], edit_perm="admin.branding", public=True),
    Def("brand.density", C, "Density", "Row height and spacing.", "enum", "comfortable",
        choices=["comfortable", "compact"], scopes=["deployment", "role", "user"], edit_perm="admin.branding",
        public=True),
    Def("brand.footer_text", C, "Footer text", "Legal or support text in the page footer.", "text", "",
        scopes=["deployment"], edit_perm="admin.branding", public=True, validator=lambda v: _strlen(v, 0, 400)),
    Def("brand.support_url", C, "Support link", "Where the Help link points.", "string", "",
        scopes=["deployment"], edit_perm="admin.branding", public=True, validator=_url_or_blank),
    Def("brand.powered_by", C, "Show 'Powered by Semechka'", "Optional attribution in the footer.", "bool", False,
        scopes=["deployment"], edit_perm="admin.branding", public=True),
    Def("brand.signin_message", C, "Sign-in message", "Short text under the sign-in form.", "text", "",
        scopes=["deployment"], edit_perm="admin.branding", public=True, validator=lambda v: _strlen(v, 0, 300)),
    # ---- navigation ----
    Def("nav.home", N, "Start page", "Where users land after signing in.", "enum", "chat",
        choices=["chat", "code", "image", "video", "audio", "knowledge", "usage"],
        scopes=["deployment", "role", "team", "user"], edit_perm="admin.navigation"),
    Def("nav.sidebar_collapsed", N, "Collapse sidebar by default", "", "bool", False,
        scopes=["deployment", "role", "team", "user"], edit_perm="admin.navigation"),
    Def("nav.items", N, "Navigation items", "Order, labels and visibility. Hiding an item never removes the "
        "permission check behind it.", "json", [
            {"id": i, "label": "", "enabled": True} for i in
            ["chat", "code", "image", "video", "audio", "knowledge", "agents", "usage", "help"]],
        scopes=["deployment", "role", "team"], edit_perm="admin.navigation", widget="nav-order",
        validator=_nav_items),
    Def("nav.suggested_prompts", N, "Suggested prompts", "Shown on an empty conversation.", "list", [],
        scopes=["deployment", "role", "team"], edit_perm="admin.navigation", validator=_prompts),
    # ---- modes ----
    *[Def(f"modes.{m}.enabled", M, f"Enable {lbl}", f"Turns {lbl} on or off. When off, navigation and the API "
          "both reject it.", "bool", m == "chat", scopes=["deployment", "role", "team", "user"],
          merge="false_wins", edit_perm="admin.settings.edit", aliases=[f"disable {m}"])
      for m, lbl in [("chat", "chat"), ("code", "code workspace"), ("image", "image generation"),
                     ("video", "video generation"), ("audio", "audio"), ("knowledge", "knowledge search"),
                     ("agents", "assistants (saved prompts; no tools)")]],
    # ---- composer ----
    Def("chat.attachments", U, "Allow text-file attachments", "Text files are read in the browser and included in the message (no upload storage). Images are not supported in this release.", "bool", True,
        scopes=["deployment", "role", "team"], merge="false_wins"),
    Def("chat.branching", U, "Message editing and branching", "Edit a message or regenerate to create branches.",
        "bool", True, scopes=["deployment", "role", "team"], merge="false_wins"),
    Def("chat.export", U, "Allow export", "Users can export their own conversations.", "bool", True,
        scopes=["deployment", "role", "team"], merge="false_wins"),
    Def("chat.max_input_chars", U, "Maximum message length", "Characters per message.", "int", 32000,
        min=100, max=1000000, scopes=["deployment", "role", "team"], merge="min"),
    # ---- registration & sign-in ----
    Def("signup.mode", S_, "Registration mode", "How people get an account.", "enum", "admin",
        choices=["public", "domain", "invite", "admin", "sso"], scopes=["deployment"], edit_perm="admin.signup.edit",
        public=True, aliases=["registration", "turn registration off", "signup"],
        effect="Changing this immediately shows or hides the sign-up form and enables or rejects the "
               "account-creation endpoint. Existing sessions are not touched.",
        lowers_privacy=lambda o, n: n == "public" and o != "public"),
    Def("signup.default_role", S_, "Role for new accounts", "Lowest approved role. Never an administrator role.",
        "string", "member", scopes=["deployment"], edit_perm="admin.signup.edit",
        effect="New accounts receive this role only. Roles with admin permissions are rejected."),
    Def("signup.require_approval", S_, "Require admin approval", "New self-registered accounts wait for approval.",
        "bool", True, scopes=["deployment"], edit_perm="admin.signup.edit", public=True,
        lowers_privacy=_lower_if_bool_true_to_false),
    Def("signup.require_email_verification", S_, "Require verified email",
        "Self-registered accounts must confirm their address. Needs working email delivery.", "bool", False,
        scopes=["deployment"], edit_perm="admin.signup.edit", requires="smtp", public=True,
        lowers_privacy=_lower_if_bool_true_to_false),
    Def("signup.allowed_domains", S_, "Allowed email domains", "Domain mode: only these may register.", "list", [],
        scopes=["deployment"], edit_perm="admin.signup.edit", validator=_domains),
    Def("signup.blocked_domains", S_, "Blocked email domains", "Never allowed to register.", "list", [],
        scopes=["deployment"], edit_perm="admin.signup.edit", validator=_domains),
    Def("signup.rate_per_hour", S_, "Registrations per hour (per address)", "Abuse throttle for the sign-up "
        "endpoint.", "int", 10, min=1, max=1000, scopes=["deployment"], edit_perm="admin.signup.edit"),
    Def("signup.invite_expiry_hours", S_, "Invitation lifetime (hours)", "", "int", 72, min=1, max=24 * 90,
        scopes=["deployment"], edit_perm="admin.signup.edit"),
    Def("auth.password_min_length", S_, "Minimum password length", "", "int", 12, min=8, max=128,
        scopes=["deployment"], edit_perm="admin.signup.edit",
        lowers_privacy=lambda o, n: n < o),
    Def("auth.session_hours", S_, "Session lifetime (hours)", "", "int", 168, min=1, max=24 * 90,
        scopes=["deployment"], edit_perm="admin.signup.edit", lowers_privacy=lambda o, n: n > o),
    Def("auth.idle_minutes", S_, "Idle timeout (minutes)", "0 disables idle timeout.", "int", 0, min=0, max=1440,
        scopes=["deployment"], edit_perm="admin.signup.edit",
        lowers_privacy=lambda o, n: (n == 0 and o != 0) or (o != 0 and n > o)),
    Def("auth.mfa_required_admins", S_, "Require MFA for administrators",
        "Anyone holding an admin permission must have TOTP set up before using admin features.", "bool", False,
        scopes=["deployment"], edit_perm="admin.signup.edit", lowers_privacy=_lower_if_bool_true_to_false),
    Def("auth.mfa_required_all", S_, "Require MFA for everyone", "", "bool", False, scopes=["deployment"],
        edit_perm="admin.signup.edit", lowers_privacy=_lower_if_bool_true_to_false),
    Def("auth.allow_user_delete", S_, "Let people delete their own account", "", "bool", True,
        scopes=["deployment"], edit_perm="admin.signup.edit"),
    # ---- single sign-on (SSO slice) ----
    Def("sso.local_login", S_, "Allow password sign-in", "When off and a single sign-on provider is enabled, only owners can "
        "still sign in with a password (the break-glass path). Everyone else must use single sign-on.", "bool", True,
        scopes=["deployment"], edit_perm="admin.sso.manage",
        effect="Turning this off sends everyone except owners to the identity provider."),
    # ---- privacy & retention ----
    Def("privacy.mode", P, "Privacy mode", "Standard private (encrypted history, governed access), user-"
        "controlled history, or enterprise-managed. No-save conversations are a separate permission.", "enum",
        "standard", choices=["standard", "user_controlled", "enterprise"], scopes=["deployment"],
        edit_perm="admin.policy.edit", effect="Controls what the 'How is this chat protected?' panel states."),
    Def("privacy.chat_retention_days", P, "Chat history retention (days)",
        "-1 keeps history until the user deletes it. 0 stores nothing. Applies to conversations created after the "
        "change; existing ones keep the expiry they were created with.", "int", -1, min=-1, max=3650,
        scopes=["deployment", "role", "team"], merge="min_nonneg", edit_perm="admin.policy.edit",
        lowers_privacy=_retention_longer, aliases=["limit retention", "7-day history"]),
    Def("privacy.ephemeral_allowed", P, "Allow no-save conversations", "Users with the permission may start a "
        "conversation that is never written to the chat database.", "bool", True,
        scopes=["deployment", "role", "team"], merge="false_wins", edit_perm="admin.policy.edit"),
    Def("privacy.external_egress", P, "Allow external inference backends",
        "When off, requests are never sent to a backend marked external.", "bool", False, scopes=["deployment"],
        edit_perm="admin.policy.edit", lowers_privacy=_raise_if_bool_false_to_true,
        effect="Enforced in the request path, not only in the interface."),
    Def("privacy.notice", P, "Privacy notice text", "Extra text shown in the protection panel.", "text", "",
        scopes=["deployment"], edit_perm="admin.policy.edit", public=True, validator=lambda v: _strlen(v, 0, 2000)),
    Def("privacy.usage_retention_days", P, "Usage and metadata retention (days)", "", "int", 365, min=1, max=3650,
        scopes=["deployment"], edit_perm="admin.policy.edit", lowers_privacy=_retention_longer),
    # ---- quotas & scheduling ----
    Def("quota.daily_messages", Q, "Messages per day (per person)", "0 means unlimited.", "int", 0, min=0,
        max=100000, scopes=["deployment", "role", "team", "user"], merge="min_nonzero",
        edit_perm="admin.quotas.edit", aliases=["rate limit", "daily limit"]),
    Def("quota.concurrent_requests", Q, "Concurrent generations (per person)", "", "int", 2, min=1, max=64,
        scopes=["deployment", "role", "team", "user"], merge="min", edit_perm="admin.quotas.edit"),
    Def("quota.max_tokens", Q, "Maximum output tokens per reply", "", "int", 4096, min=16, max=131072,
        scopes=["deployment", "role", "team", "user"], merge="min", edit_perm="admin.quotas.edit"),
    # ---- technical visibility ----
    Def("tech.presets", T, "Technical visibility presets",
        "Which fields each preset grants. Presets are shortcuts, never fixed tiers.", "json",
        None, scopes=["deployment"], edit_perm="admin.roles.manage", widget="tech-presets"),
    Def("tech.drawer_enabled", T, "Offer the technical details drawer",
        "People who hold at least one tech.* permission see a toggle. It never grants more fields.", "bool", True,
        scopes=["deployment", "role", "team"], merge="false_wins", edit_perm="admin.settings.edit"),
    # ---- email ----
    Def("smtp.host", E, "SMTP host", "Email is sent only when this is configured.", "string", "",
        scopes=["deployment"], edit_perm="admin.settings.edit", public=False),
    Def("smtp.port", E, "SMTP port", "", "int", 587, min=1, max=65535, scopes=["deployment"],
        edit_perm="admin.settings.edit"),
    Def("smtp.security", E, "SMTP security", "", "enum", "starttls", choices=["starttls", "ssl", "none"],
        scopes=["deployment"], edit_perm="admin.settings.edit"),
    Def("smtp.username", E, "SMTP username", "", "string", "", scopes=["deployment"],
        edit_perm="admin.settings.edit"),
    Def("smtp.password", E, "SMTP password", "Write-only.", "secret", "", scopes=["deployment"],
        edit_perm="admin.settings.edit", secret=True),
    Def("smtp.from_address", E, "Sender address", "", "string", "", scopes=["deployment"],
        edit_perm="admin.settings.edit"),
    # ---- system ----
    Def("access.exposure", SYS, "Network exposure", "loopback: this machine only. lan: other devices on your "
        "network. The service binds to this on next restart; nothing is exposed until then.", "enum", "loopback",
        choices=["loopback", "lan"], scopes=["deployment"], apply="restart", edit_perm="admin.settings.edit",
        lowers_privacy=lambda o, n: n == "lan" and o != "lan"),
    Def("access.public_url", SYS, "Public URL", "Used in emails and invitation links.", "string", "",
        scopes=["deployment"], edit_perm="admin.settings.edit", validator=_url_or_blank),
    Def("system.telemetry_opt_in", SYS, "Anonymous usage telemetry", "Off. Semechka has no telemetry sender; this "
        "setting records intent only and is shown as 'not implemented'.", "bool", False, scopes=["deployment"],
        edit_perm="admin.settings.edit", requires="telemetry_sender"),
    # ---- media (image and video generation) ----
    Def("media.max_concurrent_jobs", Q, "Concurrent image/video jobs (per person)",
        "How many generations one person may have queued or running at once.", "int", 2, min=1, max=16,
        scopes=["deployment", "role", "team", "user"], merge="min", edit_perm="admin.quotas.edit"),
    Def("media.daily_generations", Q, "Image/video generations per day (per person)", "0 means unlimited.", "int", 0,
        min=0, max=10000, scopes=["deployment", "role", "team", "user"], merge="min_nonzero",
        edit_perm="admin.quotas.edit"),
    Def("media.max_assets", Q, "Saved images and videos (per person)",
        "Maximum items in one person's gallery. New generations are refused when it is full. 0 means unlimited.",
        "int", 200, min=0, max=100000, scopes=["deployment", "role", "team", "user"], merge="min_nonzero",
        edit_perm="admin.quotas.edit"),
    Def("media.default_image_size", Q, "Default image size (pixels)",
        "Starting width and height in the image form, rounded and limited to what each model supports. 0 uses each "
        "model's own default.", "int", 0, min=0, max=2048, scopes=["deployment", "role", "team"],
        edit_perm="admin.settings.edit"),
    Def("media.video_max_seconds", Q, "Longest video clip (seconds)",
        "Requests whose frames divided by frame rate exceed this are refused.", "int", 8, min=1, max=60,
        scopes=["deployment", "role", "team"], merge="min", edit_perm="admin.quotas.edit"),
    # ---- knowledge fork: generation queue, knowledge limits, code workspace ----
    Def("queue.backend_concurrency", Q, "Simultaneous generations per backend",
        "How many replies one backend works on at once; the rest wait in the queue. A backend's own "
        "'max_concurrency' option overrides this. Match it to what the backend really supports "
        "(for llama.cpp: its parallel slots).", "int", 2, min=1, max=64, edit_perm="admin.quotas.edit",
        effect="Lower values make people wait longer but protect the backend from overload."),
    Def("queue.user_slots_per_backend", Q, "Simultaneous generations per person on one backend",
        "Applies only while other people are waiting, so a single user can still use an idle backend.",
        "int", 1, min=1, max=64, edit_perm="admin.quotas.edit"),
    Def("queue.wait_timeout_seconds", Q, "Longest wait in the queue (seconds)",
        "A request that has not started by then is cancelled with a clear message. 0 means wait indefinitely.",
        "int", 300, min=0, max=3600, edit_perm="admin.quotas.edit"),
    Def("queue.max_depth", Q, "Maximum waiting requests per backend",
        "Further requests are refused immediately instead of queuing.", "int", 50, min=1, max=1000,
        edit_perm="admin.quotas.edit"),
    Def("knowledge.max_collections", M, "Knowledge collections per person", "Personal collections each person may own.",
        "int", 20, min=1, max=1000, scopes=["deployment", "role", "team", "user"], merge="min",
        edit_perm="admin.quotas.edit"),
    Def("knowledge.max_docs_per_collection", M, "Documents per collection", "", "int", 200, min=1, max=10000,
        edit_perm="admin.quotas.edit"),
    Def("knowledge.max_doc_kb", M, "Largest document (KB)", "Upload size limit for one document.", "int", 2048,
        min=16, max=65536, edit_perm="admin.quotas.edit"),
    Def("knowledge.max_chunks_per_collection", M, "Passages per collection",
        "Documents are split into passages of about 1,200 characters. Searching decrypts a collection's passages into "
        "memory, so this bounds memory use.", "int", 20000, min=100, max=200000, edit_perm="admin.quotas.edit"),
    Def("knowledge.top_k", M, "Passages given to the model per question",
        "Keyword search picks the best-matching passages; this many are added to the prompt.", "int", 5, min=1,
        max=20, scopes=["deployment", "role", "team"], merge="min"),
    Def("knowledge.context_chars", M, "Maximum characters of passages per question",
        "Caps how much retrieved text is added to the prompt.", "int", 6000, min=500, max=100000,
        scopes=["deployment", "role", "team"], merge="min"),
    Def("code.system_prompt", M, "Code workspace instructions",
        "Added to the model's instructions in the code workspace. Nothing is executed: the workspace has no "
        "sandbox or tools.", "text", "You are a careful programming assistant. Prefer correct, readable code. "
        "Put code in fenced blocks with the language named. State assumptions and say when you are unsure. "
        "You cannot run code or access files or the internet; do not claim to have tested anything.",
        edit_perm="admin.settings.edit", validator=lambda v: _strlen(v, 0, 4000)),
]

BY_ID: dict[str, Def] = {d.id: d for d in DEFS}


def _strlen(v, lo, hi):
    if not isinstance(v, str):
        raise SettingError("Must be text.")
    v = v.strip()
    if not lo <= len(v) <= hi:
        raise SettingError(f"Must be {lo}–{hi} characters.")
    return v


def coerce(d: Def, value: Any) -> Any:
    t = d.type
    if t == "bool":
        if not isinstance(value, bool):
            raise SettingError("Must be true or false.")
    elif t == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingError("Must be a whole number.")
        if d.min is not None and value < d.min or d.max is not None and value > d.max:
            raise SettingError(f"Must be between {d.min} and {d.max}.")
    elif t == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingError("Must be a number.")
        if d.min is not None and value < d.min or d.max is not None and value > d.max:
            raise SettingError(f"Must be between {d.min} and {d.max}.")
    elif t in ("string", "text", "color", "secret"):
        if not isinstance(value, str):
            raise SettingError("Must be text.")
    elif t == "enum":
        if value not in (d.choices or []):
            raise SettingError(f"Must be one of: {', '.join(d.choices or [])}.")
    elif t == "list":
        if not isinstance(value, list):
            raise SettingError("Must be a list.")
    elif t == "json":
        pass
    if d.validator:
        value = d.validator(value)
    return value
