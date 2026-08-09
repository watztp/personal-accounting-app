from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time


COOKIE_NAME = "accounting_demo_session"
SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE", "86400"))
_PROCESS_SECRET = secrets.token_bytes(32)


def expected_credential() -> str:
    return os.getenv("DEMO_CREDENTIAL", "demo")


def _secret() -> bytes:
    configured = os.getenv("SESSION_SECRET", "")
    return configured.encode("utf-8") if configured else _PROCESS_SECRET


def create_session(user_id: int) -> str:
    payload = f"{user_id}:{int(time.time())}".encode("ascii")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(_secret(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def read_session(token: str | None) -> int | None:
    if not token or "." not in token:
        return None
    encoded, signature = token.rsplit(".", 1)
    expected = hmac.new(_secret(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        padding = "=" * (-len(encoded) % 4)
        user_text, issued_text = base64.urlsafe_b64decode(encoded + padding).decode("ascii").split(":", 1)
        user_id = int(user_text)
        issued_at = int(issued_text)
    except (ValueError, UnicodeDecodeError):
        return None
    now = int(time.time())
    if user_id <= 0 or issued_at > now + 60 or now - issued_at > SESSION_MAX_AGE:
        return None
    return user_id
