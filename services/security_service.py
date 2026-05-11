"""Security helpers for private SaaS surfaces."""

import hashlib
import secrets


def secure_token(length=32):
    return secrets.token_urlsafe(length)


def hash_token(token):
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def mask_phone(phone):
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) < 4:
        return "Not set"
    return f"***-***-{digits[-4:]}"

