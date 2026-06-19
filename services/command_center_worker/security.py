"""Internal auth helpers for Command Center worker endpoints."""

from __future__ import annotations

import functools
import hmac
import os
from typing import Callable, TypeVar

from flask import jsonify, request


F = TypeVar("F", bound=Callable)


def _configured_token() -> str:
    return os.getenv("COMMAND_CENTER_INTERNAL_TOKEN", "").strip()


def internal_auth_configured() -> bool:
    return bool(_configured_token())


def verify_bearer_token(auth_header: str) -> tuple[bool, str]:
    configured = _configured_token()
    if not configured:
        return False, "internal_auth_not_configured"
    if not auth_header.startswith("Bearer "):
        return False, "missing_bearer_token"
    provided = auth_header.removeprefix("Bearer ").strip()
    if not provided:
        return False, "missing_bearer_token"
    if not hmac.compare_digest(provided, configured):
        return False, "invalid_bearer_token"
    return True, "authorized"


def require_internal_auth(func: F) -> F:
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        ok, reason = verify_bearer_token(request.headers.get("Authorization", ""))
        if ok:
            return func(*args, **kwargs)
        status_code = 503 if reason == "internal_auth_not_configured" else 401
        if reason == "invalid_bearer_token":
            status_code = 403
        return jsonify({"accepted": False, "error": reason}), status_code

    return wrapper  # type: ignore[return-value]
