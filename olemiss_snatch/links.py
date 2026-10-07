"""Public URLs and signed one-click unsubscribe links (shared by the website and emails)."""

import os

from itsdangerous import BadSignature, URLSafeSerializer, URLSafeTimedSerializer


def base_url() -> str:
    return os.environ.get("BASE_URL", "http://127.0.0.1:5000").rstrip("/")


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


def make_alert_email_token(email: str, alert_email: str) -> str:
    return URLSafeTimedSerializer(secret_key(), salt="alert-email").dumps([email.lower(), alert_email.lower()])


def read_alert_email_token(token: str, max_age: int = 24 * 3600) -> tuple[str, str] | None:
    try:
        email, alert_email = URLSafeTimedSerializer(secret_key(), salt="alert-email").loads(
            token, max_age=max_age)
    except (BadSignature, ValueError, TypeError):
        return None
    return email, alert_email
