"""Business OS — Performance: framework-agnostic controller (Stage 6).

bot.py owns the raw request, auth (session/token -> user_id) and CSRF; it turns the
returned ``(status, body)`` tuple into a Flask response. All summary logic lives here so it
is unit-testable without Flask.

Contract (mirrors the attribution / recommendations / merchant-automation /
creator-commerce / governed-UNDX / localization controllers exactly):

  * every handler returns ``(int status, dict body)``; ``body`` always has an ``ok`` bool;
  * the whole surface is DARK when ``BUSINESS_OS_PERFORMANCE`` is off — every handler
    returns 404;
  * informational only: nothing here renders, alerts, pages, or scales. A summary is a
    reporting label;
  * only curated error codes are surfaced — never an internal exception string.
"""

from __future__ import annotations

import os
from typing import Any

from services.business_os.performance import schema as _schema
from services.business_os.performance import engine as _engine


FLAG_ENV = "BUSINESS_OS_PERFORMANCE"


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
def record_sample(payload: Any) -> tuple:
    """Record a metric sample (feed/agent entry point). Records a measurement — nothing is
    rendered."""
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    org_id = payload.get("org_id")
    metric_key = payload.get("metric_key")
    value = payload.get("value")
    if org_id is None or metric_key is None or value is None:
        return _bad("missing_fields", "org_id, metric_key and value are required.")
    ensure_ready()
    try:
        result = _engine.record_sample(
            org_id, metric_key, value,
            window=(payload.get("window") if payload.get("window") is not None else ""),
            unit=payload.get("unit"), captured_at=payload.get("captured_at"),
            source=(payload.get("source") or "manual"),
            external_ref=payload.get("external_ref"), meta=payload.get("meta"))
    except _engine.PerformanceError as e:
        return _bad("invalid_sample", str(e))
    return (200, {"ok": True, "result": result})


def record_target(payload: Any) -> tuple:
    """Declare a target for a metric (operator/org entry point)."""
    if not is_enabled():
        return _dark()
    if not isinstance(payload, dict):
        return _bad("missing_payload", "Expected a JSON body.")
    org_id = payload.get("org_id")
    metric_key = payload.get("metric_key")
    if org_id is None or metric_key is None:
        return _bad("missing_fields", "org_id and metric_key are required.")
    ensure_ready()
    try:
        result = _engine.record_target(
            org_id, metric_key,
            direction=(payload.get("direction") or "lower_is_better"),
            compare_stat=(payload.get("compare_stat") or "mean"),
            warn_threshold=payload.get("warn_threshold"),
            breach_threshold=payload.get("breach_threshold"),
            active=(payload.get("active") if payload.get("active") is not None else True),
            source=(payload.get("source") or "manual"),
            external_ref=payload.get("external_ref"), meta=payload.get("meta"))
    except _engine.PerformanceError as e:
        return _bad("invalid_target", str(e))
    return (200, {"ok": True, "result": result})


# ---------------------------------------------------------------------------
# summary + reporting
# ---------------------------------------------------------------------------
def summaries_report(org_id: str, limit: int = 500) -> tuple:
    """The metric summaries for an org. Computes on demand if the projection is empty so a
    first-time caller gets a result. Read-only; nothing is rendered."""
    if not is_enabled():
        return _dark()
    org_id = str(org_id or "").strip()
    if not org_id:
        return _bad("missing_fields", "org_id is required.")
    ensure_ready()
    from services import db
    conn = db.connect()
    try:
        rows = _engine.get_summaries(org_id, limit=int(limit or 500), conn=conn)
        status_rollup = None
        if not rows:
            res = _engine.summarize_org(org_id, conn=conn)
            conn.commit()
            rows = _engine.get_summaries(org_id, limit=int(limit or 500), conn=conn)
            status_rollup = res.get("status_rollup")
        if status_rollup is None:
            status_rollup = _engine._status_rollup(rows)
    finally:
        conn.close()
    return (200, {"ok": True, "result": {"org_id": org_id, "summaries": rows,
                                         "status_rollup": status_rollup}})


def targets_report(org_id: str, limit: int = 200) -> tuple:
    """The declared targets for an org."""
    if not is_enabled():
        return _dark()
    org_id = str(org_id or "").strip()
    if not org_id:
        return _bad("missing_fields", "org_id is required.")
    ensure_ready()
    return (200, {"ok": True, "result": {"org_id": org_id,
                                         "targets": _engine.list_targets(
                                             org_id, limit=int(limit or 200))}})


def samples_report(org_id: str, limit: int = 1000) -> tuple:
    """The recorded metric samples for an org."""
    if not is_enabled():
        return _dark()
    org_id = str(org_id or "").strip()
    if not org_id:
        return _bad("missing_fields", "org_id is required.")
    ensure_ready()
    return (200, {"ok": True, "result": {"org_id": org_id,
                                         "samples": _engine.list_samples(
                                             org_id, limit=int(limit or 1000))}})


def run_summarize(org_id: str) -> tuple:
    """Operator/cron entry point: recompute an org's metric summaries from the samples and
    rebuild the summary projection. Nothing is rendered — summaries are labels."""
    if not is_enabled():
        return _dark()
    org_id = str(org_id or "").strip()
    if not org_id:
        return _bad("missing_fields", "org_id is required.")
    ensure_ready()
    try:
        result = _engine.summarize_org(org_id)
    except _engine.PerformanceError as e:
        return _bad("invalid_request", str(e))
    return (200, {"ok": True, "result": result})
