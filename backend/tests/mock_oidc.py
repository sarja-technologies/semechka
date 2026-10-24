"""A small but strict OpenID Connect provider for tests: discovery, JWKS, authorize and token endpoints.

It enforces what a real provider enforces (PKCE verifier, client authentication, single-use codes) and exposes knobs
to produce hostile tokens (wrong audience, tampered signature, alg none, HS256 confusion, rotated keys).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from fastapi import FastAPI, Form, Header, Request
from fastapi.responses import JSONResponse, RedirectResponse


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def jint(n: int) -> str:
    return b64(n.to_bytes((n.bit_length() + 7) // 8, "big"))


class Key:
    def __init__(self, kid: str, kind="RSA"):
        self.kid, self.kind = kid, kind
        self.priv = rsa.generate_private_key(65537, 2048) if kind == "RSA" else ec.generate_private_key(ec.SECP256R1())

    @property
    def alg(self):
        return "RS256" if self.kind == "RSA" else "ES256"

    def jwk(self) -> dict:
        pub = self.priv.public_key().public_numbers()
        if self.kind == "RSA":
            return {"kty": "RSA", "kid": self.kid, "use": "sig", "alg": "RS256", "n": jint(pub.n), "e": jint(pub.e)}
        return {"kty": "EC", "crv": "P-256", "kid": self.kid, "use": "sig", "alg": "ES256",
                "x": b64(pub.x.to_bytes(32, "big")), "y": b64(pub.y.to_bytes(32, "big"))}

    def sign(self, data: bytes) -> bytes:
        if self.kind == "RSA":
            return self.priv.sign(data, padding.PKCS1v15(), hashes.SHA256())
        r, s = decode_dss_signature(self.priv.sign(data, ec.ECDSA(hashes.SHA256())))
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")

    def public_pem(self) -> bytes:
        return self.priv.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


class Idp:
    CLIENT_ID = "semechka-test"
    CLIENT_SECRET = "s3cr3t-value-that-must-never-leak-9f2c"

    def __init__(self):
        self.base = ""  # set once the server port is known
        self.keys = [Key("k1")]
        self.claims: dict = {}  # per-test overrides; value None deletes the claim
        self.discovery: dict = {}  # per-test overrides of the discovery document
        self.hide_last = False
        self.mode = "normal"  # normal | none | hs256 | tamper | wrongkey
        self.codes: dict[str, dict] = {}
        self.last_authorize: dict = {}
        self.token_calls: list[dict] = []
        self.app = self._build()

    def reset(self):
        self.keys = [Key("k1")] if not self.keys or self.keys[0].kid != "k1" else self.keys[:1]
        self.hide_last = False
        self.claims, self.discovery, self.mode, self.codes, self.last_authorize, self.token_calls = {}, {}, "normal", {}, {}, []

    # ---- token construction -------------------------------------------------------------------------
    def make_id_token(self, nonce: str) -> str:
        now = int(time.time())
        claims = {"iss": self.base, "sub": "idp-user-1", "aud": self.CLIENT_ID, "exp": now + 300, "iat": now, "nonce": nonce,
                  "email": "sso.user@example.test", "email_verified": True, "name": "Sasha SSO", "groups": []}
        for k, v in self.claims.items():
            if v is None:
                claims.pop(k, None)
            else:
                claims[k] = v
        key = self.keys[-1]
        header = {"alg": key.alg, "kid": key.kid, "typ": "JWT"}
        if self.mode == "none":
            header["alg"] = "none"
        elif self.mode == "hs256":
            header["alg"] = "HS256"
        signing = b64(json.dumps(header).encode()) + "." + b64(json.dumps(claims).encode())
        if self.mode == "none":
            return signing + "."
        if self.mode == "hs256":  # classic confusion attack: HMAC keyed with the PUBLIC key
            return signing + "." + b64(hmac.new(key.public_pem(), signing.encode(), hashlib.sha256).digest())
        if self.mode == "wrongkey":
            return signing + "." + b64(Key(key.kid, key.kind).sign(signing.encode()))
        sig = key.sign(signing.encode())
        if self.mode == "tamper":  # keep a genuine signature, change the payload afterwards
            forged = dict(claims, email="admin@example.test", sub="someone-else")
            return b64(json.dumps(header).encode()) + "." + b64(json.dumps(forged).encode()) + "." + b64(sig)
        return signing + "." + b64(sig)

    # ---- the provider app ----------------------------------------------------------------------------
    def _build(self) -> FastAPI:
        app = FastAPI()
        idp = self

        @app.get("/.well-known/openid-configuration")
        def disc():
            doc = {"issuer": idp.base, "authorization_endpoint": idp.base + "/authorize", "token_endpoint": idp.base + "/token",
                   "jwks_uri": idp.base + "/jwks", "response_types_supported": ["code"], "code_challenge_methods_supported": ["S256"],
                   "id_token_signing_alg_values_supported": ["RS256"], "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"]}
            doc.update(idp.discovery)
            return doc

        @app.get("/jwks")
        def jwks():
            return {"keys": [k.jwk() for k in (idp.keys[:-1] if idp.hide_last else idp.keys)]}

        @app.get("/authorize")
        def authorize(request: Request):
            q = dict(request.query_params)
            idp.last_authorize = q
            assert q["response_type"] == "code" and q["code_challenge_method"] == "S256"
            code = secrets.token_urlsafe(16)
            idp.codes[code] = {"challenge": q["code_challenge"], "nonce": q["nonce"], "redirect_uri": q["redirect_uri"]}
            return RedirectResponse(q["redirect_uri"] + "?" + urlencode({"code": code, "state": q["state"]}), status_code=302)

        @app.post("/token")
        def token(code: str = Form(), code_verifier: str = Form(), redirect_uri: str = Form(), grant_type: str = Form(),
                  client_id: str = Form(default=""), client_secret: str = Form(default=""), authorization: str | None = Header(default=None)):
            if authorization and authorization.startswith("Basic "):
                cid, _, sec = base64.b64decode(authorization[6:]).decode().partition(":")
            else:
                cid, sec = client_id, client_secret
            idp.token_calls.append({"client_id": cid, "has_secret": bool(sec), "basic": bool(authorization)})
            if cid != idp.CLIENT_ID or sec != idp.CLIENT_SECRET:
                return JSONResponse({"error": "invalid_client"}, status_code=401)
            entry = idp.codes.pop(code, None)  # single use
            if entry is None or entry["redirect_uri"] != redirect_uri:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            if b64(hashlib.sha256(code_verifier.encode()).digest()) != entry["challenge"]:
                return JSONResponse({"error": "invalid_grant", "error_description": "PKCE"}, status_code=400)
            return {"access_token": "at-" + secrets.token_hex(8), "token_type": "Bearer", "id_token": idp.make_id_token(entry["nonce"])}

        return app
