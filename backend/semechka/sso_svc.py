"""OpenID Connect relying-party logic: SSRF-safe fetching, discovery, JWKS and ID-token verification.

Only RS256 and ES256 are accepted. `alg: none`, HMAC algorithms and anything else are rejected before a key is even
looked up, so an attacker can never turn the public key into an HMAC secret.
"""
from __future__ import annotations

import base64
import ipaddress
import json
import socket
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

MAX_BODY = 1_000_000
TIMEOUT = 5.0
CLOCK_SKEW = 60
JWKS_REFRESH_MIN = 10.0  # seconds between forced JWKS refetches on an unknown key id
CACHE_TTL = 600.0
ALLOWED_ALGS = ("RS256", "ES256")
LOOPBACK_NAMES = ("localhost",)


class SsoError(Exception):
    """`code` is a short stable identifier shown to people; `detail` is for the audit log only."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code, self.detail = code, detail


# ---- SSRF-safe fetching ---------------------------------------------------------------------------------
def _is_dev_host(host: str) -> bool:
    h = host.lower().rstrip(".")
    return h in LOOPBACK_NAMES or h.endswith(".localhost") or h.endswith(".test") or h in ("127.0.0.1", "::1")


def check_url(url: str, *, allow_private: bool = False) -> None:
    """Raise SsoError unless the URL is safe to fetch from the server. Never follow redirects around this check."""
    u = urlsplit(url)
    if u.scheme not in ("https", "http") or not u.hostname or u.username or u.password:
        raise SsoError("bad_url", "scheme or host not allowed")
    host = u.hostname
    dev = _is_dev_host(host)
    if u.scheme == "http" and not dev:
        raise SsoError("insecure_url", "https is required")
    try:
        infos = {i[4][0] for i in socket.getaddrinfo(host, u.port or (443 if u.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except socket.gaierror as e:
        raise SsoError("unresolvable", str(e)) from e
    for raw in infos:
        ip = ipaddress.ip_address(raw.split("%")[0])
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise SsoError("blocked_address", f"{ip} is never allowed")  # includes 169.254.169.254 cloud metadata
        if ip.is_loopback and not dev:
            raise SsoError("blocked_address", "loopback only for development hosts")
        if (ip.is_private and not ip.is_loopback) and not allow_private:
            raise SsoError("blocked_address", "private network address; an owner must allow this provider on private networks")
    if not infos:
        raise SsoError("unresolvable", "no address")


async def fetch_json(url: str, *, method="GET", data: dict | None = None, headers: dict | None = None,
                     auth: tuple[str, str] | None = None, allow_private=False) -> dict:
    check_url(url, allow_private=allow_private)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as c:
            async with c.stream(method, url, data=data, headers={"Accept": "application/json", **(headers or {})},
                                auth=auth) as r:
                body = b""
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_BODY:
                        raise SsoError("response_too_large")
                if r.status_code >= 300:
                    raise SsoError("provider_http_error", f"{r.status_code} from {urlsplit(url).netloc}")
    except httpx.HTTPError as e:
        raise SsoError("provider_unreachable", type(e).__name__) from e
    try:
        out = json.loads(body)
    except ValueError as e:
        raise SsoError("provider_bad_response", "not JSON") from e
    if not isinstance(out, dict):
        raise SsoError("provider_bad_response", "not an object")
    return out


# ---- discovery and keys --------------------------------------------------------------------------------
@dataclass
class Discovery:
    doc: dict
    fetched: float


_disc_cache: dict[str, Discovery] = {}
_jwks_cache: dict[str, tuple[float, float, dict]] = {}  # uri -> (fetched, last_forced_refetch, jwks)


async def discover(issuer: str, *, allow_private=False, fresh=False) -> dict:
    iss = issuer.rstrip("/")
    hit = _disc_cache.get(iss)
    if hit and not fresh and time.time() - hit.fetched < CACHE_TTL:
        return hit.doc
    doc = await fetch_json(iss + "/.well-known/openid-configuration", allow_private=allow_private)
    if str(doc.get("issuer", "")).rstrip("/") != iss:
        raise SsoError("issuer_mismatch", "discovery issuer differs from the configured issuer")
    for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not isinstance(doc.get(k), str):
            raise SsoError("provider_bad_response", f"missing {k}")
        check_url(doc[k], allow_private=allow_private)
    _disc_cache[iss] = Discovery(doc, time.time())
    return doc


async def get_jwks(uri: str, *, allow_private=False, force=False) -> dict:
    now = time.time()
    hit = _jwks_cache.get(uri)
    if hit and not force and now - hit[0] < CACHE_TTL:
        return hit[2]
    if hit and force and now - hit[1] < JWKS_REFRESH_MIN:
        return hit[2]
    jwks = await fetch_json(uri, allow_private=allow_private)
    if not isinstance(jwks.get("keys"), list):
        raise SsoError("provider_bad_response", "jwks has no keys")
    _jwks_cache[uri] = (now, now if force else (hit[1] if hit else 0.0), jwks)
    return jwks


def clear_caches():
    _disc_cache.clear()
    _jwks_cache.clear()


# ---- JWS verification ----------------------------------------------------------------------------------
def _b64(s: str) -> bytes:
    if not isinstance(s, str) or not s.replace("-", "").replace("_", "").isalnum():
        raise SsoError("bad_token", "base64url")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _uint(s: str) -> int:
    return int.from_bytes(_b64(s), "big")


def _public_key(jwk: dict, alg: str):
    kty = jwk.get("kty")
    if alg == "RS256" and kty == "RSA":
        return rsa.RSAPublicNumbers(_uint(jwk["e"]), _uint(jwk["n"])).public_key()
    if alg == "ES256" and kty == "EC" and jwk.get("crv") == "P-256":
        return ec.EllipticCurvePublicNumbers(_uint(jwk["x"]), _uint(jwk["y"]), ec.SECP256R1()).public_key()
    raise SsoError("bad_token", "key type does not match algorithm")


def split_jwt(token: str) -> tuple[dict, dict, bytes, bytes]:
    parts = token.split(".")
    if len(parts) != 3:
        raise SsoError("bad_token", "not a compact JWS")
    try:
        header, claims = json.loads(_b64(parts[0])), json.loads(_b64(parts[1]))
    except (ValueError, KeyError) as e:
        raise SsoError("bad_token", "undecodable") from e
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise SsoError("bad_token", "not objects")
    return header, claims, (parts[0] + "." + parts[1]).encode(), _b64(parts[2])


def header_checks(header: dict) -> str:
    alg = header.get("alg")
    if alg not in ALLOWED_ALGS:  # rejects "none", "HS256" and everything else up front
        raise SsoError("bad_token", f"algorithm {alg!r} not accepted")
    if "crit" in header:
        raise SsoError("bad_token", "crit header not supported")
    return alg


def verify_signature(alg: str, jwk: dict, signing_input: bytes, sig: bytes) -> None:
    if jwk.get("use") not in (None, "sig") or jwk.get("alg") not in (None, alg):
        raise SsoError("bad_token", "key not usable for this algorithm")
    key = _public_key(jwk, alg)
    try:
        if alg == "RS256":
            key.verify(sig, signing_input, padding.PKCS1v15(), hashes.SHA256())
        else:
            if len(sig) != 64:
                raise InvalidSignature()
            key.verify(encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")),
                       signing_input, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as e:
        raise SsoError("bad_signature") from e


async def verify_id_token(token: str, *, issuer: str, client_id: str, nonce: str, jwks_uri: str,
                          allow_private=False, now: float | None = None) -> dict:
    header, claims, signing_input, sig = split_jwt(token)
    alg = header_checks(header)
    kid = header.get("kid")

    def pick(jwks):
        keys = [k for k in jwks["keys"] if isinstance(k, dict) and (kid is None or k.get("kid") == kid)]
        if kid is None and len(keys) != 1:
            return None  # without a kid we only accept an unambiguous key set
        return keys[0] if keys else None

    jwk = pick(await get_jwks(jwks_uri, allow_private=allow_private))
    if jwk is None:  # key rotation: refetch once
        jwk = pick(await get_jwks(jwks_uri, allow_private=allow_private, force=True))
    if jwk is None:
        raise SsoError("bad_signature", "unknown key id")
    verify_signature(alg, jwk, signing_input, sig)

    t = time.time() if now is None else now
    if claims.get("iss") != issuer.rstrip("/") and claims.get("iss") != issuer:
        raise SsoError("bad_issuer")
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    if client_id not in auds:
        raise SsoError("bad_audience")
    if len(auds) > 1 and claims.get("azp") != client_id:
        raise SsoError("bad_audience", "azp mismatch")
    if "azp" in claims and claims["azp"] != client_id:
        raise SsoError("bad_audience", "azp mismatch")
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp < t - CLOCK_SKEW:
        raise SsoError("token_expired")
    for k in ("nbf", "iat"):
        if k in claims and (not isinstance(claims[k], (int, float)) or claims[k] > t + CLOCK_SKEW):
            raise SsoError("bad_token", f"{k} in the future")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise SsoError("bad_token", "no subject")
    if claims.get("nonce") != nonce:
        raise SsoError("bad_nonce")
    return claims


def safe_return_path(p: str | None) -> str:
    """Only same-site absolute paths: no scheme, no host, no protocol-relative or backslash tricks."""
    if not p or not p.startswith("/") or p.startswith("//") or "\\" in p or "\n" in p or "\r" in p:
        return "/"
    if urlsplit(p).scheme or urlsplit(p).netloc:
        return "/"
    return p[:300]
