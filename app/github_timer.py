"""Lets a GitHub Actions workflow in this app's own repository run the bot timer - no secret to copy.

The workflow asks GitHub for a short-lived OpenID Connect token (a JWT signed by GitHub) and sends it with
the timer request. We check GitHub's signature, that it's fresh, that it was minted for TrendBot, and that
it came from a scheduled/manual run in the repository this site is deployed from.
"""
import base64
import json
import os
import time

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ISSUER = "https://token.actions.githubusercontent.com"
AUDIENCE = "trendbot-timer"
ALLOWED_EVENTS = {"schedule", "workflow_dispatch"}
_jwks: tuple[float, dict] = (0.0, {})


def allowed_repo() -> str:
    """owner/repo whose workflows may run the timer: set explicitly, or the repo Vercel deployed from."""
    explicit = os.environ.get("TRENDBOT_GITHUB_REPO", "").strip()
    if explicit:
        return explicit.lower()
    owner, slug = os.environ.get("VERCEL_GIT_REPO_OWNER", ""), os.environ.get("VERCEL_GIT_REPO_SLUG", "")
    return f"{owner}/{slug}".lower() if owner and slug else ""


def _b64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _keys() -> dict:
    global _jwks
    fetched, keys = _jwks
    if time.time() - fetched > 3600 or not keys:
        data = requests.get(ISSUER + "/.well-known/jwks", timeout=10).json()
        keys = {k["kid"]: k for k in data.get("keys", []) if k.get("kty") == "RSA"}
        _jwks = (time.time(), keys)
    return keys


def verify(token: str) -> bool:
    """True if `token` is a valid GitHub Actions OIDC token from the allowed repository."""
    repo = allowed_repo()
    if not repo or token.count(".") != 2:
        return False
    try:
        head_b, body_b, sig_b = token.split(".")
        header, claims = json.loads(_b64(head_b)), json.loads(_b64(body_b))
        if header.get("alg") != "RS256":
            return False
        key = _keys().get(header.get("kid"))
        if not key:
            return False
        public = rsa.RSAPublicNumbers(int.from_bytes(_b64(key["e"]), "big"),
                                      int.from_bytes(_b64(key["n"]), "big")).public_key()
        public.verify(_b64(sig_b), f"{head_b}.{body_b}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except (ValueError, KeyError, InvalidSignature, requests.RequestException):
        return False
    now = time.time()
    aud = claims.get("aud")
    return (claims.get("iss") == ISSUER
            and (aud == AUDIENCE or (isinstance(aud, list) and AUDIENCE in aud))
            and claims.get("nbf", 0) - 60 <= now < claims.get("exp", 0)
            and str(claims.get("repository", "")).lower() == repo
            and claims.get("event_name") in ALLOWED_EVENTS)
