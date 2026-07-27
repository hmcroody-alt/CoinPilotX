"""Business OS — Advertising slice 7 advertising-event service.

Immutable, append-only impression and click logs (spec §6, §7). Both are written
ONLY from the server-authoritative delivery instance — the client never supplies
campaign, advertiser, price, billable amount, or destination. A delivery
authorizes ONE display, so a repeat submission on the same delivery is served
idempotently (UNIQUE ``dedup_key``) and never creates a second row.

Acceptance gates:
  impression — delivery exists, token valid, not expired, placement matches the
               bound row, not a duplicate.
  click      — delivery exists, an accepted impression exists (policy), the
               destination is SERVER-RESOLVED from the bound creative version, not
               a duplicate.

Spend boundary (spec §10): each event carries the canonical references the NEXT
billing slice needs plus a DERIVED ``billing_eligible`` flag and
``billing_processed=false``. No money is read, deducted, reserved, or released
here. ``fraud_status`` records the basic MVP signals (self/advertiser-owned view,
etc.); a non-clean status is recorded but marked NOT billing-eligible.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from services import db
from services.business_os.advertising import service as _svc
from services.business_os.advertising.service import AdvertisingError
from . import delivery as _delivery
from . import delivery_common as _c


def _dumps_meta(request_meta: Any) -> Optional[str]:
    """Serialize privacy-allowed request metadata. Never stores raw PII; the caller
    is responsible for passing only non-sensitive signals."""
    if request_meta in (None, ""):
        return None
    try:
        blob = json.dumps(request_meta, sort_keys=True)
    except Exception:
        return None
    return blob[:4000]


def _self_view_status(delivery_row: dict, viewer_user_id: Any) -> str:
    """Basic self/advertiser-owned-view signal (spec §8). Returns 'self_view' when
    the viewer IS the advertiser that owns the ad, else 'clean'."""
    if viewer_user_id is None:
        return "clean"
    if _c.sid(viewer_user_id) == _c.sid(delivery_row.get("advertiser_user_id")):
        return "self_view"
    return "clean"


def _find_by_dedup(conn, table: str, dedup_key: str) -> Optional[dict]:
    return _svc._row_to_dict(conn.execute(
        f"SELECT * FROM {table} WHERE dedup_key = ?", (dedup_key,)).fetchone())


def _is_integrity_error(exc: Exception) -> bool:
    return "IntegrityError" in type(exc).__name__ or "UNIQUE" in str(exc)


# --- impression -------------------------------------------------------------
def record_impression(delivery_id: Any, token: Any, *,
                      placement: Optional[str] = None,
                      idempotency_key: Optional[str] = None,
                      viewer_user_id: Optional[Any] = None,
                      request_meta: Optional[dict] = None,
                      conn=None) -> dict:
    """Record ONE impression for a delivery, idempotently.

    The event's hierarchy fields are copied from the authoritative delivery row —
    never from the client. A duplicate submission returns the existing event with
    ``duplicate=True`` and creates no second row.
    """
    _svc._require_enabled()
    delivery_id = _c.sid(delivery_id)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = _delivery.load_delivery_row(conn, delivery_id)
        if row is None:
            raise AdvertisingError("Delivery not found.", 404, "not_found")
        if not _c.verify_impression_token(delivery_id, token):
            raise AdvertisingError("Invalid delivery token.", 403, "bad_token")
        if _delivery.is_expired(row):
            raise AdvertisingError("Delivery has expired.", 409, "expired")
        if placement is not None and _c.sid(placement).lower() != _c.sid(row.get("placement")).lower():
            raise AdvertisingError("Placement mismatch.", 409, "placement_mismatch")

        # ONE impression per delivery — dedup strictly on the delivery id.
        dedup_key = f"impr:{delivery_id}"
        existing = _find_by_dedup(conn, "business_os_ad_impression_events", dedup_key)
        if existing is not None:
            return _impression_public(existing, duplicate=True)

        fraud_status = _self_view_status(row, viewer_user_id)
        billing_eligible = 1 if fraud_status == "clean" else 0
        meta_blob = _dumps_meta(request_meta)
        if idempotency_key:
            meta_blob = _dumps_meta({
                "idempotency_key": str(idempotency_key)[:120],
                **(request_meta or {})})

        _svc._begin(conn)
        # Re-check inside the write lock (race backstop).
        existing = _find_by_dedup(conn, "business_os_ad_impression_events", dedup_key)
        if existing is not None:
            _svc._commit(conn)
            return _impression_public(existing, duplicate=True)
        event_id = _c.new_id("adimp")
        now = _c.now_iso()
        try:
            conn.execute(
                "INSERT INTO business_os_ad_impression_events "
                "(event_id, delivery_id, campaign_id, ad_set_id, creative_id, "
                "creative_version, placement, subject_ref, advertiser_user_id, "
                "event_at, dedup_key, request_meta_json, fraud_status, "
                "billing_eligible, billing_processed, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (
                    event_id, delivery_id, row.get("campaign_id"),
                    row.get("ad_set_id"), row.get("creative_id"),
                    int(row.get("creative_version") or 1), row.get("placement"),
                    row.get("subject_ref"), row.get("advertiser_user_id"),
                    now, dedup_key, meta_blob, fraud_status,
                    billing_eligible, now,
                ),
            )
        except Exception as exc:
            _svc._rollback(conn)
            if _is_integrity_error(exc):
                again = _find_by_dedup(conn, "business_os_ad_impression_events", dedup_key)
                if again is not None:
                    return _impression_public(again, duplicate=True)
            raise
        _svc._commit(conn)
        return _impression_public(
            _find_by_dedup(conn, "business_os_ad_impression_events", dedup_key),
            duplicate=False)
    except AdvertisingError:
        _svc._rollback(conn)
        raise
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


def _impression_public(row: dict, *, duplicate: bool) -> dict:
    if row is None:
        return None
    return {
        "event_id": row.get("event_id"),
        "delivery_id": row.get("delivery_id"),
        "campaign_id": row.get("campaign_id"),
        "ad_set_id": row.get("ad_set_id"),
        "creative_id": row.get("creative_id"),
        "creative_version": row.get("creative_version"),
        "placement": row.get("placement"),
        "event_at": row.get("event_at"),
        "fraud_status": row.get("fraud_status"),
        "billing_eligible": bool(row.get("billing_eligible")),
        "billing_processed": bool(row.get("billing_processed")),
        "duplicate": bool(duplicate),
    }


# --- click ------------------------------------------------------------------
def _latest_impression(conn, delivery_id: str) -> Optional[dict]:
    return _svc._row_to_dict(conn.execute(
        "SELECT * FROM business_os_ad_impression_events WHERE delivery_id = ? "
        "ORDER BY event_at ASC LIMIT 1", (delivery_id,)).fetchone())


def record_click(delivery_id: Any, *,
                 idempotency_key: Optional[str] = None,
                 require_impression: bool = True,
                 viewer_user_id: Optional[Any] = None,
                 request_meta: Optional[dict] = None,
                 conn=None) -> dict:
    """Record ONE click for a delivery, idempotently.

    The destination is SERVER-RESOLVED from the bound creative version on the
    delivery row and returned to the caller — the client can never supply an
    arbitrary destination. Requires (by policy) an accepted impression on the same
    delivery. A duplicate click returns the existing event with ``duplicate=True``.
    """
    _svc._require_enabled()
    delivery_id = _c.sid(delivery_id)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = _delivery.load_delivery_row(conn, delivery_id)
        if row is None:
            raise AdvertisingError("Delivery not found.", 404, "not_found")

        impression = _latest_impression(conn, delivery_id)
        if require_impression and impression is None:
            raise AdvertisingError(
                "A recorded impression is required before a click.",
                409, "impression_required")

        dedup_key = f"click:{delivery_id}"
        existing = _find_by_dedup(conn, "business_os_ad_click_events", dedup_key)
        if existing is not None:
            return _click_public(existing, duplicate=True)

        fraud_status = _self_view_status(row, viewer_user_id)
        billing_eligible = 1 if fraud_status == "clean" else 0
        meta_blob = _dumps_meta(request_meta)
        if idempotency_key:
            meta_blob = _dumps_meta({
                "idempotency_key": str(idempotency_key)[:120],
                **(request_meta or {})})

        _svc._begin(conn)
        existing = _find_by_dedup(conn, "business_os_ad_click_events", dedup_key)
        if existing is not None:
            _svc._commit(conn)
            return _click_public(existing, duplicate=True)
        event_id = _c.new_id("adclk")
        now = _c.now_iso()
        try:
            conn.execute(
                "INSERT INTO business_os_ad_click_events "
                "(event_id, delivery_id, impression_event_id, campaign_id, ad_set_id, "
                "creative_id, creative_version, placement, subject_ref, "
                "advertiser_user_id, destination_type, destination_ref, event_at, "
                "dedup_key, request_meta_json, fraud_status, billing_eligible, "
                "billing_processed, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (
                    event_id, delivery_id,
                    (impression or {}).get("event_id"),
                    row.get("campaign_id"), row.get("ad_set_id"),
                    row.get("creative_id"), int(row.get("creative_version") or 1),
                    row.get("placement"), row.get("subject_ref"),
                    row.get("advertiser_user_id"),
                    row.get("destination_type"), row.get("destination_ref"),
                    now, dedup_key, meta_blob, fraud_status,
                    billing_eligible, now,
                ),
            )
        except Exception as exc:
            _svc._rollback(conn)
            if _is_integrity_error(exc):
                again = _find_by_dedup(conn, "business_os_ad_click_events", dedup_key)
                if again is not None:
                    return _click_public(again, duplicate=True)
            raise
        _svc._commit(conn)
        return _click_public(
            _find_by_dedup(conn, "business_os_ad_click_events", dedup_key),
            duplicate=False)
    except AdvertisingError:
        _svc._rollback(conn)
        raise
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


def _click_public(row: dict, *, duplicate: bool) -> dict:
    if row is None:
        return None
    return {
        "event_id": row.get("event_id"),
        "delivery_id": row.get("delivery_id"),
        "impression_event_id": row.get("impression_event_id"),
        "campaign_id": row.get("campaign_id"),
        "ad_set_id": row.get("ad_set_id"),
        "creative_id": row.get("creative_id"),
        "creative_version": row.get("creative_version"),
        "placement": row.get("placement"),
        "event_at": row.get("event_at"),
        # SERVER-RESOLVED destination (authoritative), returned to the client.
        "destination": {
            "type": row.get("destination_type"),
            "ref": row.get("destination_ref"),
        },
        "fraud_status": row.get("fraud_status"),
        "billing_eligible": bool(row.get("billing_eligible")),
        "billing_processed": bool(row.get("billing_processed")),
        "duplicate": bool(duplicate),
    }


# --- admin read-only event search (spec §13) --------------------------------
def admin_search_impressions(*, delivery_id: Optional[str] = None,
                             campaign_id: Optional[str] = None,
                             advertiser_user_id: Optional[Any] = None,
                             fraud_status: Optional[str] = None,
                             since: Optional[str] = None,
                             until: Optional[str] = None,
                             limit: int = 100, conn=None) -> list:
    return _admin_search_events(
        "business_os_ad_impression_events", _impression_public,
        delivery_id=delivery_id, campaign_id=campaign_id,
        advertiser_user_id=advertiser_user_id, fraud_status=fraud_status,
        since=since, until=until, limit=limit, conn=conn)


def admin_search_clicks(*, delivery_id: Optional[str] = None,
                        campaign_id: Optional[str] = None,
                        advertiser_user_id: Optional[Any] = None,
                        fraud_status: Optional[str] = None,
                        since: Optional[str] = None,
                        until: Optional[str] = None,
                        limit: int = 100, conn=None) -> list:
    return _admin_search_events(
        "business_os_ad_click_events", _click_public,
        delivery_id=delivery_id, campaign_id=campaign_id,
        advertiser_user_id=advertiser_user_id, fraud_status=fraud_status,
        since=since, until=until, limit=limit, conn=conn)


def _admin_search_events(table, projector, *, delivery_id, campaign_id,
                         advertiser_user_id, fraud_status, since, until,
                         limit, conn) -> list:
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        clauses, params = [], []
        if delivery_id is not None:
            clauses.append("delivery_id = ?"); params.append(delivery_id)
        if campaign_id is not None:
            clauses.append("campaign_id = ?"); params.append(campaign_id)
        if advertiser_user_id is not None:
            clauses.append("advertiser_user_id = ?"); params.append(_c.sid(advertiser_user_id))
        if fraud_status is not None:
            clauses.append("fraud_status = ?"); params.append(fraud_status)
        if since is not None:
            clauses.append("event_at >= ?"); params.append(since)
        if until is not None:
            clauses.append("event_at <= ?"); params.append(until)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        try:
            lim = max(1, min(int(limit), 500))
        except Exception:
            lim = 100
        cur = conn.execute(
            f"SELECT * FROM {table}" + where +
            " ORDER BY event_at DESC, event_id DESC LIMIT ?",
            tuple(params + [lim]))
        return [projector(_svc._row_to_dict(r), duplicate=False) for r in cur.fetchall()]
    finally:
        if owned:
            conn.close()
