"""Business OS — Advertising slice 7 delivery-instance service.

The orchestration seam that turns a placement REQUEST into a server-authorized
delivery INSTANCE (spec §1, §3). Flow:

    request_placement(viewer, placement, request)
        -> normalize non-PII request context + derive subject_ref
        -> basic per-viewer request rate limit (spec §8)
        -> candidate selection (spec §4, behind the strategy interface)
        -> if a winner: persist ONE immutable delivery instance binding the exact
           campaign/ad-set/creative/creative_version/placement/destination that was
           eligible at decision time, mint an impression_token, snapshot eligibility
        -> project a CLIENT-SAFE sponsored payload (or a no-placement result)

Hard guarantees:
  * The delivery instance is the single source of truth for what may be shown. The
    authoritative creative/version/destination are ALWAYS read back from this row,
    so a client can never substitute a different creative while reusing a delivery
    id (spec §3).
  * The sponsored payload NEVER exposes internal ledger ids, private targeting
    rules, admin notes, or reviewer identity (spec §3, §9).
  * Nothing here spends, reserves, releases, or prices anything (spec §10).
"""

from __future__ import annotations

import json
import time as _time
from datetime import timedelta
from typing import Any, Optional

from services import db
from services.business_os.advertising import service as _svc
from services.business_os.advertising import creatives as _creative
from services.business_os.advertising.service import AdvertisingError
from . import selection as _selection
from . import eligibility as _elig
from . import delivery_common as _c


SPONSORED_LABEL = "Sponsored"


# --- first-touch schema guarantee -------------------------------------------
# The Business OS schema bootstrap (services/business_os/schema_bootstrap.py) is
# driven by a before_request hook that only fires for paths under
# ``/api/business-os`` or ``/business-os``. Delivery, however, is entered from
# the Pulse surface -- ``GET /api/pulse/ads/placements`` -- which never matches
# that prefix. On a production database where nobody had loaded a Business OS
# page in that worker's lifetime, the advertising tables were therefore never
# created, and the very first thing request_placement does (the per-viewer rate
# limit) SELECTed from a table that did not exist:
#
#   UndefinedTable: relation "business_os_ad_delivery_instances" does not exist
#
# The caller swallows it, so the ad request degrades to no-fill and the
# server-authoritative frequency cap silently stops capping. Ensuring the
# subsystem's own schema at its own entry point removes the dependency on which
# URL happened to be requested first. Idempotent (CREATE TABLE IF NOT EXISTS)
# and latched, so this costs one guarded call per process, not per request.
_SCHEMA_READY = False


def _ensure_schema_once(conn) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    try:
        _svc.ensure_schema(conn)
        _SCHEMA_READY = True
    except Exception:
        # Never convert a schema hiccup into a failed ad request: leave the latch
        # down so a later request retries, and let the original query surface its
        # own error exactly as it does today.
        import logging

        logging.exception("AD_DELIVERY_SCHEMA_ENSURE_FAILED (non-fatal)")


# --- passive measurement hook (ads intelligence, Stage 1) --------------------
def _record_opportunity(placement, subject, ctx, result, winner, latency_ms,
                        *, forced_reason=None):
    """Record this ad opportunity for measurement. Observer only.

    Stage 1 of the advertising-intelligence rollout is measurement with NO
    delivery change, and this function is where that promise is kept:

    * It is called *after* selection has already decided, so it cannot influence
      the outcome even by accident.
    * It runs on its OWN connection rather than the caller's. Sharing the
      delivery transaction would mean a failed CREATE TABLE or a constraint
      error inside measurement aborts the surrounding transaction on
      PostgreSQL — turning a lost log line into a failed ad request.
    * Every failure is swallowed and logged by ``record_safely``.
    * It is off unless the measurement flag is set, so the default posture is
      byte-for-byte the current behaviour.

    Returns the decision id, or None when disabled or on any failure.
    """
    try:
        from services.business_os import ads_intelligence as _ai  # noqa: PLC0415
        if not _ai.measurement_enabled():
            return None
        from services.business_os.ads_intelligence import decisions as _dec  # noqa: PLC0415
        return _dec.record_safely(
            placement_key=placement,
            surface=(ctx or {}).get("surface") or placement,
            platform=(ctx or {}).get("platform"),
            subject_ref=subject,
            session_ref=(ctx or {}).get("session_ref"),
            selection=result,
            winner=winner,
            latency_ms=latency_ms,
            ranking_mode="legacy",
            forced_no_fill_reason=forced_reason,
        )
    except Exception:
        # Import-time or flag-check failure must not touch delivery either.
        return None


# --- request rate limit (basic abuse control, spec §8) ----------------------
def _request_rate_exceeded(conn, subject_ref: str) -> bool:
    if _c.REQUEST_RATE_MAX <= 0:
        return False
    start = _c.iso(_c.now_utc() - timedelta(seconds=_c.REQUEST_RATE_WINDOW_SECONDS))
    cur = conn.execute(
        "SELECT COUNT(*) AS n FROM business_os_ad_delivery_instances "
        "WHERE subject_ref = ? AND created_at >= ?",
        (_c.sid(subject_ref), start),
    )
    row = cur.fetchone()
    try:
        n = int(row["n"])
    except Exception:
        n = int(row[0]) if row else 0
    return n >= _c.REQUEST_RATE_MAX


# --- projections ------------------------------------------------------------
def _advertiser_identity(conn, advertiser_uid: Any) -> dict:
    """Public advertiser display identity for the 'Sponsored by' line. Reads the
    canonical users table defensively; never exposes private/account internals."""
    ident = {"advertiser_ref": _c.sid(advertiser_uid), "display_name": None,
             "username": None}
    # The canonical users table is keyed by `user_id` (bot.init_db), NOT `id`.
    # This previously selected on `id`, which raises UndefinedColumn on
    # PostgreSQL — swallowed by the except below, so every served ad rendered a
    # blank "Sponsored by" line in production instead of failing loudly.
    try:
        row = _svc._row_to_dict(conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (advertiser_uid,)).fetchone())
    except Exception:
        row = None
    if row:
        for key in ("display_name", "full_name", "name"):
            if row.get(key):
                ident["display_name"] = row.get(key)
                break
        for key in ("username", "handle"):
            if row.get(key):
                ident["username"] = row.get(key)
                break
    return ident


_SAFE_MEDIA_KEYS = (
    "media_type", "width", "height", "duration_ms", "duration",
    "aspect_ratio", "url", "cdn_url", "public_url", "thumbnail_url",
)


def _media_metadata(conn, creative_row: dict) -> dict:
    """Client-safe media metadata for rendering. Exposes the canonical media id and
    a bounded allowlist of presentational fields — never a raw filesystem path."""
    meta = {"media_asset_id": creative_row.get("media_asset_id"),
            "thumbnail_asset_id": creative_row.get("thumbnail_asset_id")}
    media_id = creative_row.get("media_asset_id")
    if media_id:
        try:
            asset = _svc._row_to_dict(conn.execute(
                "SELECT * FROM pulse_media_assets WHERE id = ?", (media_id,)).fetchone())
        except Exception:
            asset = None
        if asset:
            for k in _SAFE_MEDIA_KEYS:
                if asset.get(k) not in (None, ""):
                    meta[k] = asset.get(k)
    return meta


def _destination_public(creative_row: dict) -> dict:
    """The normalized destination as the client may render/deep-link it. The click
    is still SERVER-RESOLVED from this same bound row, so this is display-only."""
    return {
        "type": creative_row.get("destination_type"),
        "ref": creative_row.get("destination_ref"),
    }


def _sponsored_payload(conn, delivery_row: dict, creative_row: dict) -> dict:
    """Project the client-safe sponsored placement (spec §3). Reads creative content
    from the authoritative creative row bound to this delivery."""
    return {
        "delivery_id": delivery_row.get("delivery_id"),
        "placement": delivery_row.get("placement"),
        "sponsored": True,
        "sponsored_label": SPONSORED_LABEL,
        "advertiser": _advertiser_identity(conn, delivery_row.get("advertiser_user_id")),
        "creative_type": creative_row.get("creative_type"),
        "creative_version": delivery_row.get("creative_version"),
        "media": _media_metadata(conn, creative_row),
        "headline": creative_row.get("headline"),
        "body": creative_row.get("body"),
        "call_to_action": creative_row.get("call_to_action"),
        "destination": _destination_public(creative_row),
        "accessibility_text": creative_row.get("accessibility_text"),
        "impression_token": delivery_row.get("impression_token"),
        "expires_at": delivery_row.get("expires_at"),
        "disclosure": {
            "sponsored": True,
            "label": SPONSORED_LABEL,
            "kind": "paid_advertisement",
        },
    }


def _no_placement(placement: str, reason: str, detail: Any = None) -> dict:
    out = {"placement": placement, "sponsored": None, "no_placement_reason": reason}
    if detail is not None:
        out["detail"] = detail
    return out


# --- request placement (the orchestrator) -----------------------------------
def request_placement(viewer_user_id: Any, placement: Any, *,
                      request: Optional[dict] = None,
                      context: Optional[dict] = None,
                      strategy=None, conn=None) -> dict:
    """Return ONE eligible sponsored placement, or a structured no-placement result.

    Server-authoritative end to end: the viewer only supplies non-PII request
    signals; advertiser/campaign/audience/price are never trusted from the client.
    """
    _svc._require_enabled()
    placement = (placement or "").strip().lower() if isinstance(placement, str) else ""
    if placement not in _c.PLACEMENTS_SUPPORTED:
        raise AdvertisingError(
            f"Unsupported placement: {placement!r}.", 400, "bad_placement")
    ctx = _elig.normalize_request_context(request)
    subject = _c.subject_ref(viewer_user_id)
    request_ref = None
    if isinstance(request, dict):
        rid = request.get("request_id")
        request_ref = str(rid)[:120] if rid not in (None, "") else None

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        _ensure_schema_once(conn)
        if _request_rate_exceeded(conn, subject):
            raise AdvertisingError(
                "Too many delivery requests.", 429, "rate_limited")

        _select_started = _time.monotonic()
        result = _selection.select_candidate(
            conn, placement=placement, subject_ref=subject,
            request_ctx=ctx, strategy=strategy)
        _latency_ms = int((_time.monotonic() - _select_started) * 1000)
        winner = result.get("winner")
        if winner is None:
            _record_opportunity(placement, subject, ctx, result, None,
                                _latency_ms)
            return _no_placement(placement, "no_eligible_candidate",
                                 {"candidate_count": result.get("candidate_count"),
                                  "eligible_count": result.get("eligible_count")})

        # Re-load the authoritative creative row to bind exact content/version.
        creative_row = _creative._get_row(
            conn, winner.get("creative_id"), requester_user_id=None)
        if creative_row is None:
            # A winner that cannot be loaded is an unfilled opportunity with a
            # cause of its own — recording it as a generic no-candidate would
            # hide a real data-integrity problem.
            _record_opportunity(placement, subject, ctx, result, None,
                                _latency_ms, forced_reason="CREATIVE_UNAVAILABLE")
            return _no_placement(placement, "no_eligible_candidate")

        _svc._begin(conn)
        delivery_id = _c.new_id("adlv")
        now_dt = _c.now_utc()
        now = _c.iso(now_dt)
        token = _c.make_impression_token(delivery_id)
        snapshot = json.dumps({
            "reasons_cleared": True,
            "readiness_snapshot": winner.get("readiness_snapshot"),
            "frequency_state": winner.get("frequency_state"),
            "request_ctx": ctx,
            "strategy": result.get("strategy"),
            "candidate_count": result.get("candidate_count"),
            "eligible_count": result.get("eligible_count"),
        }, sort_keys=True)
        conn.execute(
            "INSERT INTO business_os_ad_delivery_instances "
            "(delivery_id, advertiser_user_id, campaign_id, ad_set_id, creative_id, "
            "creative_version, placement, subject_ref, destination_type, "
            "destination_ref, status, decision_at, expires_at, request_ref, "
            "impression_token, eligibility_snapshot_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)",
            (
                delivery_id,
                _c.sid(creative_row.get("advertiser_user_id")),
                creative_row.get("campaign_id"),
                creative_row.get("ad_set_id"),
                creative_row.get("creative_id"),
                int(creative_row.get("version") or 1),
                placement,
                subject,
                creative_row.get("destination_type"),
                creative_row.get("destination_ref"),
                now,
                _c.expires_at_from(now_dt),
                request_ref,
                token,
                snapshot,
                now,
            ),
        )
        _svc._commit(conn)

        # Recorded only after the delivery has actually committed, so the
        # measurement log can never claim a fill that was rolled back.
        decision_id = _record_opportunity(
            placement, subject, ctx, result,
            {"campaign_id": creative_row.get("campaign_id"),
             "creative_id": creative_row.get("creative_id")},
            _latency_ms)

        delivery_row = load_delivery_row(conn, delivery_id)
        out = {
            "placement": placement,
            "sponsored": _sponsored_payload(conn, delivery_row, creative_row),
        }
        # Sibling of `sponsored`, deliberately NOT inside it: the sponsored
        # payload is a strict allowlist of client-safe creative fields, and the
        # decision id is correlation metadata rather than ad content. Clients
        # echo it back on ad events so an event can be joined to the decision
        # that produced it.
        if decision_id:
            out["decision_id"] = decision_id
        return out
    except AdvertisingError:
        _svc._rollback(conn)
        raise
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


# --- internal reads (used by the event service) -----------------------------
def load_delivery_row(conn, delivery_id: str) -> Optional[dict]:
    return _svc._row_to_dict(conn.execute(
        "SELECT * FROM business_os_ad_delivery_instances WHERE delivery_id = ?",
        (delivery_id,)).fetchone())


def is_expired(delivery_row: dict, *, now_dt=None) -> bool:
    now_dt = now_dt or _c.now_utc()
    exp = _c.parse_iso(delivery_row.get("expires_at"))
    return exp is not None and now_dt > exp


# --- admin read-only visibility (spec §13) ----------------------------------
def _delivery_admin_view(row: dict) -> dict:
    """Admin projection of a delivery instance. Parses the eligibility snapshot but
    keeps the impression_token OUT of the admin listing (it is a client secret)."""
    if row is None:
        return None
    snap = None
    raw = row.get("eligibility_snapshot_json")
    if raw:
        try:
            snap = json.loads(raw)
        except Exception:
            snap = None
    return {
        "delivery_id": row.get("delivery_id"),
        "advertiser_user_id": row.get("advertiser_user_id"),
        "campaign_id": row.get("campaign_id"),
        "ad_set_id": row.get("ad_set_id"),
        "creative_id": row.get("creative_id"),
        "creative_version": row.get("creative_version"),
        "placement": row.get("placement"),
        "subject_ref": row.get("subject_ref"),
        "destination_type": row.get("destination_type"),
        "destination_ref": row.get("destination_ref"),
        "status": row.get("status"),
        "decision_at": row.get("decision_at"),
        "expires_at": row.get("expires_at"),
        "request_ref": row.get("request_ref"),
        "eligibility_snapshot": snap,
        "created_at": row.get("created_at"),
    }


def admin_search_deliveries(*, advertiser_user_id: Optional[Any] = None,
                            campaign_id: Optional[str] = None,
                            placement: Optional[str] = None,
                            since: Optional[str] = None,
                            until: Optional[str] = None,
                            limit: int = 100, conn=None) -> list:
    """Read-only admin search over delivery instances with optional filters. Admins
    can never fabricate or mutate a delivery here — this is strictly a read."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        clauses, params = [], []
        if advertiser_user_id is not None:
            clauses.append("advertiser_user_id = ?")
            params.append(_c.sid(advertiser_user_id))
        if campaign_id is not None:
            clauses.append("campaign_id = ?")
            params.append(campaign_id)
        if placement is not None:
            clauses.append("placement = ?")
            params.append(placement)
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since)
        if until is not None:
            clauses.append("created_at <= ?")
            params.append(until)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        try:
            lim = max(1, min(int(limit), 500))
        except Exception:
            lim = 100
        cur = conn.execute(
            "SELECT * FROM business_os_ad_delivery_instances" + where +
            " ORDER BY created_at DESC, delivery_id DESC LIMIT ?",
            tuple(params + [lim]))
        return [_delivery_admin_view(_svc._row_to_dict(r)) for r in cur.fetchall()]
    finally:
        if owned:
            conn.close()


def admin_get_delivery(delivery_id: str, *, conn=None) -> dict:
    """Admin inspect one delivery + its bound hierarchy context (read-only)."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = load_delivery_row(conn, delivery_id)
        if row is None:
            raise AdvertisingError("Delivery not found.", 404, "not_found")
        view = _delivery_admin_view(row)
        creative = _creative._get_row(conn, row.get("creative_id"), requester_user_id=None)
        view["creative"] = None if creative is None else {
            "creative_id": creative.get("creative_id"),
            "creative_type": creative.get("creative_type"),
            "status": creative.get("status"),
            "version": creative.get("version"),
        }
        campaign = _svc.get_campaign(row.get("campaign_id"), conn=conn)
        view["campaign"] = None if campaign is None else {
            "campaign_id": campaign.get("campaign_id"),
            "name": campaign.get("name"),
            "status": campaign.get("status"),
        }
        return view
    finally:
        if owned:
            conn.close()
