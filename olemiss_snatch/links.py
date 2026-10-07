"""Public URLs and signed one-click unsubscribe links (shared by the website and emails)."""

import os

from itsdangerous import BadSignature, URLSafeSerializer


def base_url() -> str:
    return os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")


def secret_key() -> str:
    return os.environ.get("SECRET_KEY", "dev-only-change-me")


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(secret_key(), salt="unsubscribe")


def unsubscribe_url(email: str, term: str, crn: str) -> str:
    token = _serializer().dumps([email.lower(), term, crn])
    return f"{base_url()}/unsubscribe/{token}"


def read_unsubscribe_token(token: str) -> tuple[str, str, str] | None:
    try:
        email, term, crn = _serializer().loads(token)
    except (BadSignature, ValueError, TypeError):
        return None
    return email, term, crn
