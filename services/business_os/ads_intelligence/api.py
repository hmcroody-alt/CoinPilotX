"""Ads intelligence — framework-agnostic HTTP controller.

Same contract as the advertising slice's ``api.py``, deliberately: ``bot.py``
owns authentication, CSRF and RBAC, then calls these pure functions with an
already-authenticated identity and parsed input, and turns the returned
``(status_code, body)`` tuple into a Flask JSON response. Nothing here imports
Flask, so every branch is unit-testable in the hermetic sandbox.

Rules this surface enforces:

* **Dark when disabled.** Every handler 404s unless the flag is on, so no
  partial canonical path is ever exposed.
* **Identity comes from the session, never the body.** ``viewer_user_id`` is
  passed in by ``bot.py``. A client cannot attribute events to another account,
  because it never gets to name an account at all — the id is pseudonymised into
  a ``subject_ref`` here, on the server.
* **A client may not assert revenue.** Ingest is always called with
  ``ingest_source='client'``. There is no parameter, header, or body field a
  caller can set to change that; server-derived conversions use the service
  layer directly and never travel through this module.
* **Advertiser reads are ownership-scoped.** A campaign report is refused unless
  the caller owns the campaign, and refusal is a 404 so campaign existence is
  not leaked.
"""

from __future__ import annotations

from typing import Any, Optional

from services import db

from . import decisions as _dec
from . import events as _events
from . import is_enabled, measurement_enabled

_DARK = (404, {"ok": False, "error": "Not found."})

#: Fields a client may supply per event. Anything else is dropped rather than
#: rejected: a newer app version sending an extra field must not fail ingest for
#: everything else in the batch, but it also must not be able to set a column
#: this surface does not intend to expose (validity, billable, quality_status).
CLIENT_EVENT_FIELDS = {
    "event_name", "dedup_key", "occurred_at", "decision_id", "campaign_id",
    "creative_id", "placement_key", "platform", "app_version", "surface",
    "percent_visible", "duration_ms", "is_video", "foreground", "session_id",
    "meta",
}


def _filtered(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in CLIENT_EVENT_FIELDS}


def ingest_events(*, viewer_user_id: Any, payload: Optional[dict]) -> tuple:
    """``POST /api/business-os/ads-intel/events`` — batched client ingest.

    Returns per-batch counts. A partially-invalid batch is a 200 with the
    rejection reasons included, not a 400: the valid events in it are real and
    dropping them would punish every user of a client release that has one bad
    event type.
    """
    if not measurement_enabled():
        return _DARK
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "Invalid payload."}

    raw_events = payload.get("events")
    if not isinstance(raw_events, list):
        return 400, {"ok": False, "error": "events must be a list."}
    if len(raw_events) > _events.MAX_BATCH_EVENTS:
        return 413, {"ok": False,
                     "error": f"Batch exceeds {_events.MAX_BATCH_EVENTS} events."}

    cleaned = []
    for raw in raw_events:
        item = _filtered(raw)
        if not item:
            continue
        # Server-assigned identity. The client never names a subject.
        item["user_id"] = viewer_user_id
        cleaned.append(item)

    result = _events.ingest_batch(
        {"batch_key": str(payload.get("batch_key") or "").strip() or None,
         "events": cleaned},
        ingest_source="client")

    if not result.get("ok"):
        return 400, {"ok": False, "error": result.get("error") or "Invalid batch."}
    return 200, {
        "ok": True,
        "received": result.get("received", 0),
        "accepted": result.get("accepted", 0),
        "duplicate": result.get("duplicate", 0),
        "rejected": result.get("rejected", 0),
        "reject_reasons": result.get("reject_reasons") or {},
        "replayed": bool(result.get("replayed")),
    }


def delivery_health(*, placement_key: Optional[str] = None,
                    since: Optional[str] = None) -> tuple:
    """``GET /admin/business-os/ads-intel/delivery-health`` — admin only.

    Fill rate and the ranked causes of no-fill. This is the report that makes
    "we showed nothing" answerable; ``bot.py`` gates it behind admin RBAC.
    """
    if not measurement_enabled():
        return _DARK
    conn = db.connect()
    try:
        report = _dec.no_fill_breakdown(
            conn, since=since, placement_key=placement_key)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return 200, {"ok": True, "report": report}


def campaign_delivery_diagnosis(*, owner_user_id: Any, campaign_id: Any) -> tuple:
    """``GET /api/business-os/ads-intel/campaigns/<id>/delivery`` — advertiser.

    Why this specific campaign is or is not delivering. Ownership is verified
    against the canonical advertising tables — this module does not keep its own
    idea of who owns a campaign, because a second ownership model is exactly how
    two systems end up disagreeing about who may read what.
    """
    if not measurement_enabled():
        return _DARK
    campaign_id = str(campaign_id or "").strip()
    if not campaign_id:
        return 404, {"ok": False, "error": "Not found."}

    conn = db.connect()
    try:
        try:
            owned = conn.execute(
                "SELECT 1 FROM business_os_ad_campaigns "
                "WHERE campaign_id = ? AND advertiser_user_id = ?",
                (campaign_id, str(owner_user_id))).fetchone()
        except Exception:
            # Ownership that cannot be verified is ownership denied. If the
            # advertising tables are absent or unreadable, answering "not found"
            # is the only safe response — surfacing the error would both leak
            # internals and turn an unverifiable read into a 500.
            owned = None
        if not owned:
            # 404 rather than 403: existence is not leaked to a non-owner.
            return 404, {"ok": False, "error": "Not found."}

        row = conn.execute(
            "SELECT COUNT(*), SUM(filled) FROM ads_intel_delivery_decisions "
            "WHERE campaign_id = ?", (campaign_id,)).fetchone()
        served = int((row or [0, 0])[1] or 0)

        # Opportunities this campaign WON. Opportunities it merely competed in
        # are not attributable to it from this table, and inventing a number
        # here would be worse than reporting none.
        return 200, {"ok": True, "campaign_id": campaign_id,
                     "served": served,
                     "delivering": served > 0}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def status() -> tuple:
    """``GET /admin/business-os/ads-intel/status`` — rollout visibility."""
    if not measurement_enabled():
        return _DARK
    return 200, {"ok": True,
                 "measurement_enabled": True,
                 "fully_enabled": is_enabled()}
