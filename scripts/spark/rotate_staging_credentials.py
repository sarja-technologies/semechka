#!/usr/bin/env python3
"""Rotate every password in the staging credentials file through Semechka's own API. Runs ON the host that runs staging.

New passwords are generated here, written to the 0600 file and never printed. Each account signs in and changes its own
password (organisation members are not visible to a platform admin), and the file is rewritten after every change, so a crash
never leaves it out of step with the instance.

    python3 rotate_staging_credentials.py --base http://192.168.20.193:8790 --credentials ~/semechka-deploy/secrets/staging-credentials.txt
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from provision_salted import Api, load_credentials, new_password, save_credentials  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--credentials", required=True)
    a = ap.parse_args()
    path = Path(a.credentials).expanduser()
    creds = load_credentials(path)
    done = 0
    for email in list(creds):
        api = Api(a.base)
        api.login(email, creds[email])  # each person changes their own password: organisation members are not visible to a platform admin
        pw = new_password()
        api.call("POST", "/api/auth/password", {"current": creds[email], "new": pw})
        creds[email] = pw
        save_credentials(path, creds, a.base)  # after each change, so the file never lags the instance
        done += 1
    # prove the new passwords work (names only, never values)
    ok = 0
    for email, pw in creds.items():
        try:
            Api(a.base).login(email, pw)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"login check failed for {email}: {str(exc)[:80]}")
    print(f"rotated {done} password(s); {ok}/{len(creds)} accounts sign in with the new ones; file {path}")
    return 0 if ok == len(creds) else 1


if __name__ == "__main__":
    raise SystemExit(main())
