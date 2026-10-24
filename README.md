# semechka.

A configurable, private AI platform for your own infrastructure. By Sarja Technologies. Apache-2.0.

Semechka is a web application you install on a Linux server. It sits in front of the inference engine you already run (llama.cpp, vLLM, Ollama, or Sarness Engine) and adds accounts, roles, per-model access, branding, retention and privacy controls, and a chat interface. Everyone sees a simple chat by default; administrators can reveal as much technical detail as they choose to selected people, **without** giving them any control over infrastructure.

> **Status: 0.2.0, early.** Single organisation, single node. Chat, image and video generation (Sarness Engine), keyword-search knowledge, assistants, a code chat workspace, a generation queue and OIDC single sign-on are built. Audio, autonomous agents with tools, code execution, clustering, SAML/SCIM and a content-policy engine are **not built**. See [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) and [docs/REQUIREMENTS_TRACEABILITY.md](docs/REQUIREMENTS_TRACEABILITY.md). It has had no independent security review.

## Quick start
```
./scripts/package.sh                    # builds dist/semechka-0.2.0.tar.gz (+ SHA-256)
cd dist && sha256sum -c semechka-0.2.0.tar.gz.sha256 && tar xzf semechka-0.2.0.tar.gz && cd semechka-0.2.0
./install.sh                            # private venv + systemd user service, loopback only
```
Open the printed URL, enter the one-time setup token, and follow the wizard. Details: [docs/INSTALL.md](docs/INSTALL.md).

## What works (tested)
First-run wizard with one-time owner token · owner/member accounts · five registration modes (public, domain, invite, admin-created; SSO not built) with approval, throttling, no account enumeration · email verification/reset over SMTP · TOTP MFA · roles/teams/per-person overrides with deterministic deny-wins evaluation and an explainer · independent technical-visibility fields, enforced on every read · per-model grants · streaming chat with stop, edit/regenerate branches, search, export · no-save conversations · retention expiry · encrypted-at-rest history · quotas · settings registry with scopes, impact preview, history/rollback, import/export · branding that changes the real product · hash-chained audit log · encrypted backup/restore · adapters for llama.cpp, Ollama and Sarness Engine.

## Docs
[Architecture](docs/ARCHITECTURE.md) · [Decisions](docs/DECISIONS.md) · [Security & threat model](docs/SECURITY.md) · [Privacy disclosure](docs/PRIVACY.md) · [Permissions](docs/PERMISSIONS.md) · [Configuration](docs/CONFIGURATION.md) · [Adapters](docs/ADAPTERS.md) · [Design contract](DESIGN.md) · [Design review](docs/DESIGN_REVIEW.md) · API reference: start with `SEMECHKA_API_DOCS=1` and open `/api/docs` (off by default because it lists every route).
