"""Business OS — Localization: framework-agnostic controller (Stage 6).

bot.py owns the raw request, auth (session/token -> user_id) and CSRF; it turns the
returned ``(status, body)`` tuple into a Flask response. All resolution logic lives here
so it is unit-testable without Flask.

Contract (mirrors the attribution / recommendations / merchant-automation /
creator-commerce / governed-UNDX controllers exactly):

  * every handler returns ``(int status, dict body)``; ``body`` always has an ``ok`` bool;
  * the whole surface is DARK when ``BUSINESS_OS_LOCALIZATION`` is off — every handler
    returns 404;
  * informational only: nothing here renders or ships a string. A resolution is a
    reporting label;
  * only curated error codes are surfaced — never an internal exception string.
"""

from __future__ import annotations

import os
from typing import Any

from services.business_os.localization import schema as _schema
from services.business_os.localization import engine as _engine


FLAG_ENV = "BUSINESS_OS_LOCALIZATION"


def is_enabled() -> bool:
    raw = (os.getenv(FLAG_ENV, "") or "").strip().lower()
    return raw in ("1", "true", "on", "yes", "enabled", "canonical")


def _dark():
    return (404, {"ok": False, "error": "Not found."})


def _bad(code: str, msg: str, status: int = 400):
    return (status, {"ok": False, "code": code, "error": msg})


def ensure_ready() -> None:
    """Idempotent schema bootstrap; cheap to call on each request path."""
    _schema.ensure_schema()


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------
def record_locale(payload: Any) -> tuple:
    """Declare a locale (operator/org entry point)."""
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    org_id = payload.get("org_id")
    locale = payload.get("locale")
    if org_id is None or locale is None:
        return _bad("missing_fields", "org_id and locale are required.")
    ensure_ready()
    try:
        result = _engine.record_locale(
            org_id, locale,
            is_default=(payload.get("is_default") if payload.get("is_default")
                        is not None else False),
            fallback_locale=payload.get("fallback_locale"),
            active=(payload.get("active") if payload.get("active") is not None else True),
            source=(payload.get("source") or "manual"),
            external_ref=payload.get("external_ref"), meta=payload.get("meta"))
    except _engine.LocalizationError as e:
        return _bad("invalid_locale", str(e))
    return (200, {"ok": True, "result": result})


def record_string(payload: Any) -> tuple:
    """Append a translation fact (feed/import entry point). Records an assertion — nothing
    is rendered."""
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    org_id = payload.get("org_id")
    string_key = payload.get("string_key")
    locale = payload.get("locale")
    value = payload.get("value")
    if org_id is None or string_key is None or locale is None or value is None:
        return _bad("missing_fields",
                    "org_id, string_key, locale and value are required.")
    ensure_ready()
    try:
        result = _engine.record_string(
            org_id, string_key, locale, value,
            context=payload.get("context"),
            source=(payload.get("source") or "manual"),
            external_ref=payload.get("external_ref"), meta=payload.get("meta"))
    except _engine.LocalizationError as e:
        return _bad("invalid_string", str(e))
    return (200, {"ok": True, "result": result})


# ---------------------------------------------------------------------------
# resolution + reporting
# ---------------------------------------------------------------------------
def resolutions_report(org_id: str, limit: int = 500) -> tuple:
    """The string resolutions for an org. Computes on demand if the projection is empty so
    a first-time caller gets a result. Read-only; nothing is rendered."""
    if not is_enabled():
        return _dark()
    org_id = str(org_id or "").strip()
    if not org_id:
        return _bad("missing_fields", "org_id is required.")
    ensure_ready()
    from services import db
    conn = db.connect()
    try:
        rows = _engine.get_resolutions(org_id, limit=int(limit or 500), conn=conn)
        coverage = None
        if not rows:
            res = _engine.resolve_org(org_id, conn=conn)
            conn.commit()
            rows = _engine.get_resolutions(org_id, limit=int(limit or 500), conn=conn)
            coverage = res.get("coverage")
        if coverage is None:
            coverage = _engine._coverage(rows)
    finally:
        conn.close()
    return (200, {"ok": True, "result": {"org_id": org_id, "resolutions": rows,
                                         "coverage": coverage}})


def locales_report(org_id: str, limit: int = 200) -> tuple:
    """The declared locales for an org."""
    if not is_enabled():
        return _dark()
    org_id = str(org_id or "").strip()
    if not org_id:
        return _bad("missing_fields", "org_id is required.")
    ensure_ready()
    return (200, {"ok": True, "result": {"org_id": org_id,
                                         "locales": _engine.list_locales(
                                             org_id, limit=int(limit or 200))}})


def strings_report(org_id: str, limit: int = 1000) -> tuple:
    """The recorded translation facts for an org."""
    if not is_enabled():
        return _dark()
    org_id = str(org_id or "").strip()
    if not org_id:
        return _bad("missing_fields", "org_id is required.")
    ensure_ready()
    return (200, {"ok": True, "result": {"org_id": org_id,
                                         "strings": _engine.list_strings(
                                             org_id, limit=int(limit or 1000))}})


def run_resolve(org_id: str) -> tuple:
    """Operator/cron entry point: re-resolve an org's strings against declared locales and
    rebuild the resolution projection. Nothing is rendered — resolutions are labels."""
    if not is_enabled():
        return _dark()
    org_id = str(org_id or "").strip()
    if not org_id:
        return _bad("missing_fields", "org_id is required.")
    ensure_ready()
    try:
        result = _engine.resolve_org(org_id)
    except _engine.LocalizationError as e:
        return _bad("invalid_request", str(e))
    return (200, {"ok": True, "result": result})
