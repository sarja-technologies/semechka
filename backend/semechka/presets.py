"""Deployment presets: exactly three starting points, plus Custom.

A preset only fills in starting values; every value stays editable in the wizard and afterwards. Custom applies no
values at all: the owner makes each choice in the wizard's later steps. The choice is recorded as the setting
`deployment.preset` so administrators can see later what the server started from.
"""
from __future__ import annotations

PRESETS: dict[str, dict] = {
    "personal": {
        "label": "Personal",
        "description": "For yourself or your household. You create the accounts, history is kept until deleted, "
                       "and nothing is sent to outside services.",
        "settings": {"signup.mode": "admin", "privacy.mode": "user_controlled", "privacy.external_egress": False,
                     "privacy.chat_retention_days": -1, "quota.daily_messages": 0, "auth.mfa_policy": "optional"},
        "role_grants": {},
    },
    "business": {
        "label": "Business",
        "description": "For a team or company. People join by invitation, history is kept for 30 days, and "
                       "administrators must use a second factor.",
        "settings": {"signup.mode": "invite", "privacy.mode": "standard", "privacy.chat_retention_days": 30,
                     "privacy.external_egress": False, "auth.mfa_policy": "admins"},
        "role_grants": {},
    },
    "managed": {
        "label": "Managed Private Hosting",
        "description": "A server you run for one client organisation. Invitation only, organisation-managed "
                       "privacy, 30-day history, a daily limit per person and a second factor for administrators.",
        "settings": {"signup.mode": "invite", "privacy.mode": "enterprise", "privacy.chat_retention_days": 30,
                     "privacy.external_egress": False, "quota.daily_messages": 200, "auth.mfa_policy": "admins"},
        "role_grants": {},
    },
}

CUSTOM = {
    "label": "Custom",
    "description": "Start from nothing preset and make each choice yourself in the next steps.",
    "settings": {},
    "role_grants": {},
}

CHOICES = {**PRESETS, "custom": CUSTOM}
