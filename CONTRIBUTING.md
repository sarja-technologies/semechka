# Contributing

- Backend: `python -m venv .venv && .venv/bin/pip install -e "backend[dev]"`, `pytest backend/tests`. Real-backend tests are opt-in via the `SEMECHKA_REAL_*` variables (see `tests/test_real_backends.py`).
- Web: `cd web && pnpm install && pnpm check && pnpm build`. Run `scripts/design-lint.sh`; read DESIGN.md first. No purple, gradients, emoji icons, fake numbers.
- A new setting is a `Def` in `settings_reg.py` (with a validator and edit permission) and a test. A new permission goes in `perms.REGISTRY`. Re-run `scripts/gen_docs.py`.
- Never claim a feature works without a test or a recorded manual check in `docs/REQUIREMENTS_TRACEABILITY.md`. Never add a control the server cannot honour.
- Security issues: private advisory, not a public issue.
