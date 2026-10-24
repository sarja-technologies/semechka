"""Deployment presets. They only populate starting values; every value stays editable afterwards."""
from __future__ import annotations

PRESETS: dict[str, dict] = {
    "personal": {
        "label": "Personal / family",
        "description": "One person or a household. Accounts are created by the owner; everything stays on this machine.",
        "settings": {"signup.mode": "admin", "privacy.mode": "user_controlled", "privacy.external_egress": False,
                     "access.exposure": "loopback", "quota.daily_messages": 0},
        "role_grants": {},
    },
    "business": {
        "label": "Business",
        "description": "A team with invitations, 30-day history and a technical role for IT.",
        "settings": {"signup.mode": "invite", "privacy.mode": "standard", "privacy.chat_retention_days": 30,
                     "auth.mfa_required_admins": False},
        "role_grants": {},
    },
    "private_enterprise": {
        "label": "Private enterprise",
        "description": "Closed registration, no external inference, no no-save chats, 30-day history.",
        "settings": {"signup.mode": "admin", "privacy.mode": "enterprise", "privacy.chat_retention_days": 30,
                     "privacy.external_egress": False,
                     "privacy.ephemeral_allowed": False},
        "role_grants": {},
    },
    "managed": {
        "label": "Managed (dedicated to a client)",
        "description": "A provider runs a single-tenant server for one client.",
        "settings": {"signup.mode": "invite", "privacy.mode": "enterprise", "privacy.chat_retention_days": 30},
        "role_grants": {},
    },
    "dedicated_provider": {
        "label": "Dedicated provider",
        "description": "Provider-operated single-tenant hosting with usage quotas. Multi-tenant hosting is not offered.",
        "settings": {"signup.mode": "invite", "quota.daily_messages": 200, "privacy.mode": "standard"},
        "role_grants": {},
    },
    "studio": {
        "label": "Studio",
        "description": "Creative team: image and video modes on, chat optional. Media modes need compatible backends.",
        "settings": {"signup.mode": "invite", "modes.image.enabled": True, "modes.video.enabled": True,
                     "nav.home": "image"},
        "role_grants": {"member": ["mode.image", "mode.video"]},
    },
    "research": {
        "label": "Research lab",
        "description": "Domain sign-up with approval; researchers see technical fields and generation settings.",
        "settings": {"signup.mode": "domain", "signup.require_approval": True},
        "role_grants": {"member": ["tech.model.id", "tech.model.family", "tech.model.params", "tech.model.quant",
                                    "tech.model.context", "tech.runtime.name", "tech.gen.params",
                                    "tech.usage.tokens", "tech.perf.*", "ops.gen.edit_params"]},
    },
    "classroom": {
        "label": "Classroom",
        "description": "Admin-created accounts, simple interface, per-person daily limit, no no-save chats.",
        "settings": {"signup.mode": "admin", "quota.daily_messages": 50, "privacy.ephemeral_allowed": False,
                     "privacy.chat_retention_days": 90, "chat.attachments": False},
        "role_grants": {},
    },
    "public_demo": {
        "label": "Public demo",
        "description": "Open sign-up with approval, a small daily limit, history off by default. Public hosting is "
                       "not private hosting; the notice says so.",
        "settings": {"signup.mode": "public", "signup.require_approval": True, "quota.daily_messages": 20,
                     "privacy.chat_retention_days": 7, "chat.attachments": False,
                     "privacy.notice": "This is a public service. Do not enter anything you would not want a "
                                       "server operator to be able to read."},
        "role_grants": {},
    },
}
