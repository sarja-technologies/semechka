from __future__ import annotations

import json
import time
import uuid
from typing import Any

from sqlalchemy import delete, select

from . import audit
from .models import Role, SettingRevision, SettingValue, Team
from .settings_reg import BY_ID, DEFS, SCOPE_ORDER, Def, SettingError, coerce


class SettingsService:
    def __init__(self, db, vault):
        self.db, self.vault = db, vault

    # ---- capabilities (what the installed deployment can honour) ------------------------------
    def capabilities(self, s) -> dict[str, dict]:
        smtp_ok = bool(self._raw(s, "smtp.host", "deployment", ""))
        return {
            "smtp": {"ok": smtp_ok, "why": "Configure an SMTP host under Email first." if not smtp_ok else ""},
            "telemetry_sender": {"ok": False, "why": "Semechka contains no telemetry sender."},
        }

    def _raw(self, s, key, scope_type, scope_id):
        row = s.get(SettingValue, (key, scope_type, scope_id))
        if row is None:
            return BY_ID[key].default
        if "enc" in row.value:
            return self.vault.decrypt(row.value["enc"], "setting")
        return row.value["v"]

    # ---- reads -----------------------------------------------------------------------------------
    def deployment(self, s, key: str):
        return self._raw(s, key, "deployment", "")

    def public_values(self, s) -> dict:
        return {d.id: self.deployment(s, d.id) for d in DEFS if d.public and not d.secret}

    def effective(self, s, key: str, *, role_ids=(), team_ids=(), user_id: str | None = None) -> dict:
        """Resolve a value. Returns {value, source:{scope_type,scope_id}|None, layers:[...], rule}"""
        d = BY_ID[key]
        rows = s.execute(select(SettingValue).where(SettingValue.key == key)).scalars().all()
        layers = []
        by = {(r.scope_type, r.scope_id): r for r in rows}

        def val(r):
            return self.vault.decrypt(r.value["enc"], "setting") if "enc" in r.value else r.value["v"]

        roles_ranked = sorted(role_ids, key=lambda rid: (s.get(Role, rid).rank if s.get(Role, rid) else 999, rid))
        applicable: list[tuple[str, str]] = [("deployment", "")]
        applicable += [("role", r) for r in reversed(roles_ranked)]  # lower rank (stronger) applied later
        applicable += [("team", t) for t in sorted(team_ids)]
        if user_id:
            applicable.append(("user", user_id))
        for st, sid in applicable:
            if st in d.scopes and (st, sid) in by:
                layers.append({"scope_type": st, "scope_id": sid, "value": val(by[(st, sid)])})
        values = [l["value"] for l in layers]
        if not values:
            return {"value": d.default, "source": None, "layers": [], "rule": "default"}
        m = d.merge
        if m == "specific":
            win = layers[-1]
            return {"value": win["value"], "source": {k: win[k] for k in ("scope_type", "scope_id")},
                    "layers": layers, "rule": "most specific scope wins"}
        # restrictive merges consider the default as a layer too, so a lone permissive value cannot exceed it
        if m == "false_wins":
            if self._raw(s, key, "deployment", "") is False:  # deployment switch is a hard ceiling
                return {"value": False, "source": {"scope_type": "deployment", "scope_id": ""}, "layers": layers,
                        "rule": "disabled at deployment level"}
            win = next((l for l in layers if l["value"] is False), None)
            if win is not None:
                return {"value": False, "source": {k: win[k] for k in ("scope_type", "scope_id")}, "layers": layers,
                        "rule": "any 'off' wins"}
            return {"value": True, "source": {k: layers[-1][k] for k in ("scope_type", "scope_id")},
                    "layers": layers, "rule": "any 'off' wins"}
        if m == "min":
            win = min(layers, key=lambda l: l["value"])
            return {"value": win["value"], "source": {k: win[k] for k in ("scope_type", "scope_id")},
                    "layers": layers, "rule": "lowest limit wins"}
        if m == "min_nonzero":  # 0 = unlimited
            lim = [l for l in layers if l["value"] > 0]
            if not lim:
                return {"value": 0, "source": None, "layers": layers, "rule": "lowest non-zero limit wins"}
            win = min(lim, key=lambda l: l["value"])
            return {"value": win["value"], "source": {k: win[k] for k in ("scope_type", "scope_id")},
                    "layers": layers, "rule": "lowest non-zero limit wins"}
        if m == "min_nonneg":  # -1 = unlimited / forever
            lim = [l for l in layers if l["value"] >= 0]
            if not lim:
                return {"value": -1, "source": None, "layers": layers, "rule": "shortest retention wins"}
            win = min(lim, key=lambda l: l["value"])
            return {"value": win["value"], "source": {k: win[k] for k in ("scope_type", "scope_id")},
                    "layers": layers, "rule": "shortest retention wins"}
        raise AssertionError(m)

    def value_for(self, s, key: str, principal) -> Any:
        return self.effective(s, key, role_ids=principal.role_ids, team_ids=principal.team_ids,
                              user_id=principal.user_id)["value"]

    # ---- writes ----------------------------------------------------------------------------------
    def validate(self, s, key: str, value: Any, scope_type: str = "deployment", scope_id: str = "") -> Any:
        d = BY_ID.get(key)
        if d is None:
            raise SettingError(f"Unknown setting '{key}'.")
        if scope_type not in d.scopes:
            raise SettingError(f"'{d.label}' cannot be set at {scope_type} scope.")
        if scope_type == "role" and s.get(Role, scope_id) is None:
            raise SettingError("Unknown role.")
        if scope_type == "team" and s.get(Team, scope_id) is None:
            raise SettingError("Unknown team.")
        value = coerce(d, value)
        if d.requires and value not in (False, "", None, []):
            cap = self.capabilities(s).get(d.requires)
            if not cap or not cap["ok"]:
                raise SettingError(cap["why"] if cap else f"Prerequisite '{d.requires}' unavailable.")
        if key == "signup.default_role":
            r = s.get(Role, value)
            if r is None:
                raise SettingError("Unknown role.")
            if any(g == "*" or g.startswith("admin") for g in (r.grants or [])):
                raise SettingError("New accounts cannot default to a role that carries administrator permissions.")
        return value

    def impact(self, s, key: str, value: Any, scope_type="deployment", scope_id="") -> dict:
        d = BY_ID[key]
        old = self._raw(s, key, scope_type, scope_id)
        weakens = bool(d.lowers_privacy and d.lowers_privacy(old, value))
        return {"key": key, "old": None if d.secret else old, "new": None if d.secret else value,
                "weakens_protection": weakens, "effect": d.effect, "apply": d.apply}

    def set(self, s, actor: str, key: str, value: Any, scope_type="deployment", scope_id="", *, batch="",
            ip="", confirm_weaker=False) -> dict:
        d = BY_ID[key]
        value = self.validate(s, key, value, scope_type, scope_id)
        imp = self.impact(s, key, value, scope_type, scope_id)
        if imp["weakens_protection"] and not confirm_weaker:
            raise SettingError("This change reduces protection. Review the impact and confirm to apply it.")
        row = s.get(SettingValue, (key, scope_type, scope_id))
        old_stored = row.value if row else None
        stored = {"enc": self.vault.encrypt(value, "setting")} if d.secret else {"v": value}
        if row is None:
            row = SettingValue(key=key, scope_type=scope_type, scope_id=scope_id, value=stored)
            s.add(row)
        else:
            row.value = stored
        row.updated_at, row.updated_by = time.time(), actor
        s.add(SettingRevision(actor=actor, key=key, scope_type=scope_type, scope_id=scope_id,
                              old=self._redact(d, old_stored), new=self._redact(d, stored), batch=batch))
        audit.record(s, actor, "setting.change", key, {"scope": scope_type, "scope_id": scope_id,
                     "weakened": imp["weakens_protection"], "batch": batch,
                     **({} if d.secret else {"old": imp["old"], "new": value})}, ip)
        s.flush()
        return imp

    def reset(self, s, actor: str, key: str, scope_type: str, scope_id: str = "", ip="") -> None:
        if scope_type == "deployment":
            raise SettingError("Reset deployment values by setting them; only narrower scopes can inherit.")
        row = s.get(SettingValue, (key, scope_type, scope_id))
        if row is None:
            return
        d = BY_ID[key]
        s.add(SettingRevision(actor=actor, key=key, scope_type=scope_type, scope_id=scope_id,
                              old=self._redact(d, row.value), new=None))
        s.delete(row)
        audit.record(s, actor, "setting.reset", key, {"scope": scope_type, "scope_id": scope_id}, ip)

    @staticmethod
    def _redact(d: Def, stored):
        if stored is None:
            return None
        return {"v": "<secret>"} if d.secret else stored

    def history(self, s, key: str | None = None, limit=100) -> list[dict]:
        q = select(SettingRevision).order_by(SettingRevision.id.desc()).limit(limit)
        if key:
            q = q.where(SettingRevision.key == key)
        return [{"id": r.id, "ts": r.ts, "actor": r.actor, "key": r.key, "scope_type": r.scope_type,
                 "scope_id": r.scope_id, "old": r.old, "new": r.new, "batch": r.batch}
                for r in s.execute(q).scalars()]

    def rollback(self, s, actor: str, revision_id: int, ip="") -> dict:
        rev = s.get(SettingRevision, revision_id)
        if rev is None:
            raise SettingError("Unknown revision.")
        d = BY_ID.get(rev.key)
        if d is None:
            raise SettingError("Setting no longer exists.")
        if d.secret:
            raise SettingError("Secrets cannot be rolled back; set a new value.")
        if rev.old is None:
            self.reset_force(s, actor, rev.key, rev.scope_type, rev.scope_id, ip)
            return {"restored": None}
        self.set(s, actor, rev.key, rev.old["v"], rev.scope_type, rev.scope_id, batch=f"rollback:{rev.id}",
                 ip=ip, confirm_weaker=True)
        return {"restored": rev.old["v"]}

    def reset_force(self, s, actor, key, scope_type, scope_id, ip=""):
        row = s.get(SettingValue, (key, scope_type, scope_id))
        if row is not None:
            s.add(SettingRevision(actor=actor, key=key, scope_type=scope_type, scope_id=scope_id,
                                  old=self._redact(BY_ID[key], row.value), new=None, batch="rollback"))
            s.delete(row)
            audit.record(s, actor, "setting.rollback_to_inherited", key, {"scope": scope_type}, ip)

    # ---- import / export (secrets never included) ----------------------------------------------
    def export_profile(self, s, categories: list[str] | None = None) -> dict:
        out = []
        for r in s.execute(select(SettingValue).order_by(SettingValue.key)).scalars():
            d = BY_ID.get(r.key)
            if d is None or d.secret or (categories and d.category not in categories):
                continue
            if r.scope_type != "deployment":
                continue  # role/team/user ids are deployment-specific
            out.append({"key": r.key, "value": r.value["v"]})
        return {"schema": "semechka.settings/1", "settings": out}

    def import_profile(self, s, actor: str, profile: dict, *, confirm_weaker: bool, dry_run=False, ip="") -> dict:
        if profile.get("schema") != "semechka.settings/1":
            raise SettingError("Unrecognised profile schema.")
        batch = uuid.uuid4().hex[:12]
        plan, errors = [], []
        for item in profile.get("settings", []):
            try:
                key = item["key"]
                d = BY_ID[key]
                if d.secret:
                    raise SettingError("Profiles cannot carry secrets.")
                v = self.validate(s, key, item["value"])
                plan.append(self.impact(s, key, v))
                plan[-1]["_v"] = v
            except (SettingError, KeyError) as e:
                errors.append({"key": item.get("key"), "error": str(e)})
        if errors:
            return {"applied": False, "errors": errors, "plan": [{k: v for k, v in p.items() if k != "_v"} for p in plan]}
        weaker = [p["key"] for p in plan if p["weakens_protection"]]
        if weaker and not confirm_weaker and not dry_run:
            return {"applied": False, "errors": [{"key": k, "error": "Reduces protection; confirm required."} for k in weaker],
                    "plan": [{k: v for k, v in p.items() if k != "_v"} for p in plan]}
        if not dry_run:
            for p in plan:
                self.set(s, actor, p["key"], p["_v"], batch=batch, ip=ip, confirm_weaker=True)
        return {"applied": not dry_run, "batch": batch, "errors": [],
                "plan": [{k: v for k, v in p.items() if k != "_v"} for p in plan]}
