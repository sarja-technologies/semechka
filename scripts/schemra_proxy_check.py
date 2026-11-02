#!/usr/bin/env python3
"""Check a deployment's /schemra/ proxy from the outside, the way a browser would use it.

    SEMECHKA_CHECK_EMAIL=you@example.org SEMECHKA_CHECK_PASSWORD=... \\
        python3 scripts/schemra_proxy_check.py https://semechka.example.org --expect-role admin

Credentials come from the environment only (never the command line, so they stay out of `ps` and shell history):
  SEMECHKA_CHECK_EMAIL, SEMECHKA_CHECK_PASSWORD, and SEMECHKA_CHECK_TOTP when the account has a second factor.

It signs in, asks Schemra who it thinks you are (`GET /schemra/api/lab/whoami`) and compares the role with the one you
expect, then checks that a signed-out request is refused and that nothing from the lab leaks into the answer (a cookie,
the lab's own port). It prints no cookie, token or password. Exit status: 0 all good, 1 a check failed, 2 usage or
connection problem.

Needs Python 3.9+ and httpx (already a Semechka dependency).
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx

ROLES = ("viewer", "operator", "admin")


def error_code(r: httpx.Response) -> str:
    try:
        return str(r.json()["detail"]["code"])
    except (ValueError, KeyError, TypeError):
        return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify Semechka's /schemra/ proxy and the role Schemra receives.")
    ap.add_argument("base_url", help="Semechka's address, e.g. https://semechka.example.org or http://127.0.0.1:8780")
    ap.add_argument("--expect-role", choices=ROLES, help="the Schemra role this account should arrive as")
    ap.add_argument("--lab-port", default="8820", help="Schemra's own port, which must never show up in answers (default 8820)")
    ap.add_argument("--insecure", action="store_true", help="do not verify the TLS certificate (a test deployment only)")
    ap.add_argument("--timeout", type=float, default=15.0)
    a = ap.parse_args(argv)

    email, password = os.environ.get("SEMECHKA_CHECK_EMAIL", ""), os.environ.get("SEMECHKA_CHECK_PASSWORD", "")
    if not email or not password:
        print("Set SEMECHKA_CHECK_EMAIL and SEMECHKA_CHECK_PASSWORD (and SEMECHKA_CHECK_TOTP if the account uses a second factor).",
              file=sys.stderr)
        return 2
    base = a.base_url.rstrip("/")
    failures: list[str] = []

    def report(ok: bool, what: str, detail: str = "") -> None:
        print(f"[{'ok' if ok else 'FAIL'}] {what}" + (f": {detail}" if detail else ""))
        if not ok:
            failures.append(what)

    try:
        with httpx.Client(base_url=base, timeout=a.timeout, verify=not a.insecure, follow_redirects=False) as anon:
            r = anon.get("/schemra/api/lab/whoami")
            if r.status_code == 404:
                report(False, "the proxy is switched on", "answered 404: schemra.enabled is off, or this build has no proxy")
                return 1
            report(r.status_code == 401, "signed-out request is refused", f"HTTP {r.status_code}")

        with httpx.Client(base_url=base, timeout=a.timeout, verify=not a.insecure, follow_redirects=False) as c:
            body = {"email": email, "password": password}
            if os.environ.get("SEMECHKA_CHECK_TOTP"):
                body["totp"] = os.environ["SEMECHKA_CHECK_TOTP"]
            r = c.post("/api/auth/login", json=body)
            if r.status_code != 200:
                print(f"[FAIL] sign-in: HTTP {r.status_code} {error_code(r)}".rstrip(), file=sys.stderr)
                return 2
            report(True, "signed in")
            r = c.get("/schemra/api/lab/whoami")
            leaks = [h for h in ("set-cookie", "via", "x-powered-by") if h in r.headers]
            if r.status_code == 403:
                report(False, "this account may open Schemra", "HTTP 403: it holds none of schemra.view / .operate / .admin")
                return 1
            if r.status_code in (502, 503, 504):
                report(False, "Schemra answers through the proxy", f"HTTP {r.status_code} {error_code(r)}: see docs/SCHEMRA_INTEGRATION.md, failure modes")
                return 1
            if r.status_code != 200:
                report(False, "whoami answers", f"HTTP {r.status_code}")
                return 1
            who = r.json()
            report(who.get("via") == "proxy", "Schemra took the identity from the proxy", f"via={who.get('via')!r}")
            role = who.get("role")
            if a.expect_role:
                report(role == a.expect_role, "role Schemra assigned", f"got {role!r}, expected {a.expect_role!r}")
            else:
                print(f"[info] role Schemra assigned: {role!r}")
            report(str(who.get("user", "")).lower() == email.lower(), "user Schemra saw is this account",
                   "matches" if str(who.get("user", "")).lower() == email.lower() else "different name (is the e-mail address unusual?)")
            print(f"[info] permissions: {', '.join(who.get('permissions', []))}")
            report(not leaks, "no cookie or proxy header leaks from the lab", ", ".join(leaks))
            everything = str(r.headers.multi_items()) + r.text
            report(f":{a.lab_port}" not in everything, "the lab's own port is not advertised")
            r = c.get("/schemra/", headers={"accept": "text/html"})
            report(r.status_code in (200, 304), "the Schemra page loads", f"HTTP {r.status_code}")
            report("set-cookie" not in r.headers, "the page sets no cookie")
            cs = r.headers.get_list("content-security-policy")
            report(bool(cs) and r.headers.get("x-content-type-options") == "nosniff", "security headers present", f"{len(cs)} CSP header(s)")
            r = c.post("/schemra/api/lab/boards", content=b"{}", headers={"content-type": "application/json", "origin": "https://evil.example"})
            report(r.status_code == 403, "a cross-site POST is refused", f"HTTP {r.status_code}")
            c.post("/api/auth/logout", headers={"x-csrf-token": c.get("/api/me").json().get("csrf", "")})
    except httpx.HTTPError as e:
        print(f"Could not reach {base}: {type(e).__name__}", file=sys.stderr)
        return 2
    print("ALL CHECKS PASSED" if not failures else f"{len(failures)} CHECK(S) FAILED")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
