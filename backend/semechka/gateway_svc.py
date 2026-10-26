"""Model gateway: which models may this person use, for this purpose, in this workspace?

One answer for agents, the workspace UI and the API. It starts from the platform's existing model access (grants to
people, roles and teams, availability, external-egress policy) and narrows it with the organisation's allow-list
(`Org.policy["models"]`) and the workspace's (`Workspace.policy["models"]`). A list that is absent means "no extra
restriction"; an empty list means "none". Capabilities come from the model registry, never from names.
"""
from __future__ import annotations

from sqlalchemy import select

from . import catalog_svc, services
from .models import Model
from .models_tenancy import Org, Workspace


def _allowed(policy: dict | None, model_id: str) -> bool:
    lst = (policy or {}).get("models")
    return lst is None or model_id in lst


def models_for(st, s, principal, org: Org | None, ws: Workspace | None, mode: str = "chat") -> list[dict]:
    out = []
    for m, b in services.accessible_models(s, principal, st, mode, include_hidden=False):
        if not _allowed(org.policy if org else None, m.id) or not _allowed(ws.policy if ws else None, m.id):
            continue
        meta = m.meta or {}
        out.append({"id": m.id, "alias": m.alias, "capabilities": catalog_svc.capabilities(m), "external": services.is_external(m, b),
                    "loaded": meta.get("loaded"), "backend": b.name, "restricted": services.is_restricted(s, principal, m, mode,
                    catalog_svc.grants_by_model(s).get(m.id, []))})
    return out


def check(st, s, principal, org: Org | None, ws: Workspace | None, model_id: str, mode: str = "chat") -> str | None:
    """None when allowed, else the reason (written for the person)."""
    if org is not None and not _allowed(org.policy, model_id):
        return "Your organisation does not allow that model."
    if ws is not None and not _allowed(ws.policy, model_id):
        return "This workspace does not allow that model."
    if not any(m.id == model_id for m, _ in services.accessible_models(s, principal, st, mode)):
        return "You are not allowed to use that model."
    return None


def validate_policy_models(s, lst) -> list[str]:
    """A models allow-list must name models that exist."""
    if lst is None:
        return None
    if not isinstance(lst, list) or not all(isinstance(x, str) for x in lst):
        raise ValueError("models must be a list of model ids.")
    known = {m for (m,) in s.execute(select(Model.id).where(Model.id.in_(lst or [""])))}
    missing = [x for x in lst if x not in known]
    if missing:
        raise ValueError(f"Unknown model: {missing[0]}")
    return list(dict.fromkeys(lst))
