"""Sign in with Google, limited to Ole Miss Google accounts (go.olemiss.edu).

Standard OpenID Connect "authorization code" flow. The ID token comes straight from
Google's token endpoint over HTTPS using our client secret, so per the OIDC spec we
check its claims (issuer, audience, expiry, nonce, hosted domain) without fetching
Google's signing keys.

Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to turn it on.
"""

import base64
import json
import os
import secrets
import time
from urllib.parse import urlencode

import logging

import requests

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


class GoogleAuthError(Exception):
    """Shown to the user, so messages should be friendly."""


def client_id() -> str | None:
    return os.environ.get("GOOGLE_CLIENT_ID")


def configured() -> bool:
    return bool(client_id() and os.environ.get("GOOGLE_CLIENT_SECRET"))


def authorization_url(redirect_uri: str, hosted_domain: str) -> tuple[str, str, str]:
    """Returns (url, state, nonce). Save state and nonce in the session."""
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    params = {
        "client_id": client_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email",
        "state": state,
        "nonce": nonce,
        "hd": hosted_domain,          # only a hint to Google's account picker; enforced below
        "prompt": "select_account",
    }
    return f"{AUTH_URL}?{urlencode(params)}", state, nonce


def _decode_jwt_payload(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        raise GoogleAuthError("Google sent back something unexpected. Please try again.")


def verified_email(code: str, redirect_uri: str, nonce: str, allowed_domains: list[str]) -> str:
    """Trade the one-time code for an ID token and return the verified Ole Miss email."""
    r = requests.post(TOKEN_URL, data={
        "code": code,
        "client_id": client_id(),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }, timeout=15)
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200 or "id_token" not in body:
        # e.g. invalid_client (wrong ID/secret) or redirect_uri_mismatch
        log.error("Google token exchange failed (%s): %s", r.status_code, r.text[:300])
        raise GoogleAuthError("Google sign-in didn't go through. Please try again.")
    claims = _decode_jwt_payload(body["id_token"])

    if claims.get("iss") not in ISSUERS or claims.get("aud") != client_id():
        log.error("Google ID token rejected: iss=%r aud=%r (expected aud %r)",
                  claims.get("iss"), claims.get("aud"), client_id())
        raise GoogleAuthError("Google sign-in didn't go through. Please try again.")
    if claims.get("exp", 0) < time.time():
        raise GoogleAuthError("That sign-in expired. Please try again.")
    if claims.get("nonce") != nonce:
        log.error("Google ID token rejected: nonce mismatch")
        raise GoogleAuthError("Google sign-in didn't go through. Please try again.")

    email = str(claims.get("email", "")).lower()
    domain = email.rpartition("@")[2]
    # `hd` is set only for Google Workspace accounts, so a personal Gmail can't pass.
    if not claims.get("email_verified") or (claims.get("hd") or "").lower() not in allowed_domains \
            or domain not in allowed_domains:
        raise GoogleAuthError("Please choose your Ole Miss Google account "
                              f"({' or '.join('@' + d for d in allowed_domains)}).")
    return email
