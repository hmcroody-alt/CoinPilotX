"""Attribution engine — multi-touch credit over the append-only logs (Stage 6 Part 2).

Records touchpoints and conversions (idempotently), then computes a fractional,
remainder-safe **credit** split of each conversion's integer-cent value across the
eligible touchpoints in the converting user's path, under a named model:

  * ``last_touch``      — 100% to the final touchpoint before the conversion.
  * ``first_touch``     — 100% to the first touchpoint in the window.
  * ``linear``          — equal share to every touchpoint.
  * ``position_based``  — 40% first, 40% last, remaining 20% split among the middle
                          (U-shaped); degrades to 100% (n=1) and 50/50 (n=2).

Money discipline: value is integer cents; the split uses the largest-remainder
method so the per-touchpoint credits sum back to the conversion value **exactly** —
never over- or under-crediting by a penny. Credit is a *projection*: recomputing a
conversion under a model is deterministic and idempotent (it replaces that
conversion/model's credit rows, and the UNIQUE key guarantees exactly-one row per
touchpoint).

Nothing here moves money. Credit is a reporting quantity, not a payout instruction.
"""

from __future__ import annotations

from decimal import Decimal, getcontext
from typing import Any, Optional

from services import db
from services.business_os.attribution import schema as _schema


getcontext().prec = 40  # ample headroom for cents * fraction arithmetic

VALID_MODELS = ("last_touch", "first_touch", "linear", "position_based")
_TOUCH_TYPES = ("impression", "click", "engagement", "visit")


class AttributionError(ValueError):
    """Curated, user-safe validation error (never leaks internals)."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return _schema.utc_now_iso()


def _norm_ts(value: Any) -> str:
    """Accept an ISO string or None; default to now. Stored/compared as text — the
    canonical ISO form sorts lexicographically in chronological order."""
    if value in (None, ""):
        return _now()
    return str(value)


def _dict(row) -> Optional[dict]:
    return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# ingest (append-only, idempotent)
# ---------------------------------------------------------------------------
def record_touchpoint(user_id: str, channel: str, touch_type: str, *,
                      campaign_ref: Optional[str] = None, occurred_at: Any = None,
                      source: str = "manual", external_ref: Optional[str] = None,
                      meta: Any = None, conn=None) -> dict:
    """Append one touchpoint. Replaying the same ``(source, external_ref)`` is a
    no-op that returns the existing row (``deduped=True``)."""
    if not user_id:
        raise AttributionError("user_id is required")
    channel = (str(channel or "").strip() or "")
    if not channel:
        raise AttributionError("channel is required")
    if touch_type not in _TOUCH_TYPES:
        raise AttributionError(f"unknown touch_type: {touch_type!r}")

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if external_ref is not None:
            existing = conn.execute(
                "SELECT * FROM business_os_attr_touchpoints "
                "WHERE source = ? AND external_ref = ?",
                (source, external_ref)).fetchone()
            if existing is not None:
                return {"touchpoint_id": existing["touchpoint_id"],
                        "recorded": False, "deduped": True}
        tp_id = _schema.new_id()
        now = _now()
        meta_json = None
        if meta not in (None, ""):
            import json
            try:
                meta_json = json.dumps(meta, sort_keys=True)[:4000]
            except Exception:
                meta_json = None
        conn.execute(
            "INSERT INTO business_os_attr_touchpoints "
            "(touchpoint_id,user_id,channel,touch_type,campaign_ref,occurred_at,"
            "source,external_ref,meta_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (tp_id, str(user_id), channel, touch_type, campaign_ref,
             _norm_ts(occurred_at), source, external_ref, meta_json, now))
        if owned:
            conn.commit()
        return {"touchpoint_id": tp_id, "recorded": True, "deduped": False}
    finally:
        if owned:
            conn.close()


def record_conversion(user_id: str, conversion_type: str, value_cents: Any, *,
                      currency: str = "usd", occurred_at: Any = None,
                      lookback_days: Any = 30, source: str = "manual",
                      external_ref: Optional[str] = None,
                      related_object: Optional[str] = None, meta: Any = None,
                      conn=None) -> dict:
    """Append one conversion. Idempotent on ``(source, external_ref)``."""
    if not user_id:
        raise AttributionError("user_id is required")
    if not conversion_type:
        raise AttributionError("conversion_type is required")
    try:
        value_cents = int(value_cents)
    except (TypeError, ValueError):
        raise AttributionError("value_cents must be an integer number of cents")
    if value_cents < 0:
        raise AttributionError("value_cents must be non-negative")
    try:
        lookback_days = int(lookback_days)
    except (TypeError, ValueError):
        raise AttributionError("lookback_days must be an integer")
    if lookback_days <= 0:
        raise AttributionError("lookback_days must be positive")

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if external_ref is not None:
            existing = conn.execute(
                "SELECT * FROM business_os_attr_conversions "
                "WHERE source = ? AND external_ref = ?",
                (source, external_ref)).fetchone()
            if existing is not None:
                return {"conversion_id": existing["conversion_id"],
                        "recorded": False, "deduped": True}
        cv_id = _schema.new_id()
        now = _now()
        meta_json = None
        if meta not in (None, ""):
            import json
            try:
                meta_json = json.dumps(meta, sort_keys=True)[:4000]
            except Exception:
                meta_json = None
        conn.execute(
            "INSERT INTO business_os_attr_conversions "
            "(conversion_id,user_id,conversion_type,value_cents,currency,occurred_at,"
            "lookback_days,source,external_ref,related_object,meta_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (cv_id, str(user_id), str(conversion_type), value_cents, currency,
             _norm_ts(occurred_at), lookback_days, source, external_ref,
             related_object, meta_json, now))
        if owned:
            conn.commit()
        return {"conversion_id": cv_id, "recorded": True, "deduped": False}
    finally:
        if owned:
            conn.close()


# ---------------------------------------------------------------------------
# model math
# ---------------------------------------------------------------------------
def _weights(model: str, n: int) -> list:
    """Fractional weights (Decimal) for ``n`` touchpoints under ``model``. Returned
    weights are proportional; the cents split normalizes and is remainder-safe, so
    the weights need not be exactly summable in floating terms."""
    if n <= 0:
        return []
    D = Decimal
    if model == "last_touch":
        w = [D(0)] * n
        w[-1] = D(1)
        return w
    if model == "first_touch":
        w = [D(0)] * n
        w[0] = D(1)
        return w
    if model == "linear":
        return [D(1) / D(n)] * n
    if model == "position_based":
        if n == 1:
            return [D(1)]
        if n == 2:
            return [D(1) / D(2), D(1) / D(2)]
        first = D(40) / D(100)
        last = D(40) / D(100)
        mid_each = (D(20) / D(100)) / D(n - 2)
        w = [mid_each] * n
        w[0] = first
        w[-1] = last
        return w
    raise AttributionError(f"unknown model: {model!r}")


def _split_cents(total: int, weights: list) -> list:
    """Split ``total`` cents across ``weights`` so the result is a list of
    non-negative integers summing to EXACTLY ``total`` (largest-remainder method,
    deterministic tie-break by index)."""
    n = len(weights)
    if n == 0:
        return []
    if total == 0:
        return [0] * n
    wsum = sum(weights)
    if wsum <= 0:
        # degenerate: hand the whole value to the last touch (last-touch fallback)
        out = [0] * n
        out[-1] = total
        return out
    raw = [(Decimal(total) * w / wsum) for w in weights]
    floors = [int(r) for r in raw]  # floor for non-negative Decimals
    allocated = sum(floors)
    remainder = total - allocated  # guaranteed 0 <= remainder < n
    order = sorted(range(n), key=lambda i: (raw[i] - floors[i], -i), reverse=True)
    for k in range(int(remainder)):
        floors[order[k]] += 1
    return floors


# ---------------------------------------------------------------------------
# eligibility + credit computation
# ---------------------------------------------------------------------------
def _lookback_floor(occurred_at: str, lookback_days: int) -> str:
    """Earliest eligible touchpoint timestamp = conversion time minus the window."""
    from datetime import datetime, timedelta, timezone
    fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
    try:
        dt = datetime.strptime(occurred_at, fmt).replace(tzinfo=timezone.utc)
    except Exception:
        # Be permissive: if the stored form is a bare ISO without microseconds,
        # fall back to a very old floor so nothing is wrongly excluded.
        return "0000-01-01T00:00:00.000000Z"
    return (dt - timedelta(days=lookback_days)).strftime(fmt)


def eligible_touchpoints(conn, user_id: str, occurred_at: str,
                         lookback_days: int) -> list:
    """The converting user's touchpoints inside the lookback window, at or before the
    conversion, ordered oldest-first (deterministic tie-break by created_at then id)."""
    floor = _lookback_floor(occurred_at, lookback_days)
    rows = conn.execute(
        "SELECT * FROM business_os_attr_touchpoints "
        "WHERE user_id = ? AND occurred_at <= ? AND occurred_at >= ? "
        "ORDER BY occurred_at ASC, created_at ASC, touchpoint_id ASC",
        (str(user_id), occurred_at, floor)).fetchall()
    return [dict(r) for r in rows]


def compute_credits(conversion_id: str, model: str = "last_touch", *,
                    conn=None) -> dict:
    """Compute (and persist) the credit split for one conversion under ``model``.
    Idempotent: it replaces any prior credit rows for this ``(conversion, model)``.
    Returns a summary including the per-touchpoint credit list."""
    if model not in VALID_MODELS:
        raise AttributionError(f"unknown model: {model!r}")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conv = conn.execute(
            "SELECT * FROM business_os_attr_conversions WHERE conversion_id = ?",
            (conversion_id,)).fetchone()
        if conv is None:
            raise AttributionError("no such conversion")
        conv = dict(conv)
        touches = eligible_touchpoints(
            conn, conv["user_id"], conv["occurred_at"], int(conv["lookback_days"]))

        # Recompute is a replace: clear prior projection for this conversion/model.
        conn.execute(
            "DELETE FROM business_os_attr_credits "
            "WHERE conversion_id = ? AND model = ?", (conversion_id, model))

        now = _now()
        value_cents = int(conv["value_cents"])
        if not touches:
            if owned:
                conn.commit()
            return {"conversion_id": conversion_id, "model": model,
                    "attributed": False, "reason": "no_eligible_touchpoints",
                    "value_cents": value_cents, "total_credit_cents": 0,
                    "touchpoints": []}

        weights = _weights(model, len(touches))
        cents = _split_cents(value_cents, weights)
        wsum = sum(weights)
        out = []
        for i, tp in enumerate(touches):
            frac = (weights[i] / wsum) if wsum > 0 else Decimal(0)
            conn.execute(
                "INSERT INTO business_os_attr_credits "
                "(credit_id,conversion_id,touchpoint_id,model,user_id,channel,"
                "campaign_ref,credit_cents,credit_fraction,position,computed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (_schema.new_id(), conversion_id, tp["touchpoint_id"], model,
                 conv["user_id"], tp.get("channel"), tp.get("campaign_ref"),
                 int(cents[i]), format(frac, "f"), i, now))
            out.append({"touchpoint_id": tp["touchpoint_id"],
                        "channel": tp.get("channel"),
                        "campaign_ref": tp.get("campaign_ref"),
                        "credit_cents": int(cents[i]),
                        "credit_fraction": format(frac, "f"),
                        "position": i})
        if owned:
            conn.commit()
        return {"conversion_id": conversion_id, "model": model, "attributed": True,
                "value_cents": value_cents, "total_credit_cents": sum(cents),
                "touchpoints": out}
    finally:
        if owned:
            conn.close()


def recompute_conversion(conversion_id: str, models=None, *, conn=None) -> dict:
    """Recompute one conversion under several models (default: all)."""
    models = list(models or VALID_MODELS)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        results = {}
        for m in models:
            results[m] = compute_credits(conversion_id, m, conn=conn)
        if owned:
            conn.commit()
        return {"conversion_id": conversion_id, "models": results}
    finally:
        if owned:
            conn.close()


# ---------------------------------------------------------------------------
# reporting (read-only)
# ---------------------------------------------------------------------------
def conversion_credits(conversion_id: str, model: str, *, conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM business_os_attr_credits "
            "WHERE conversion_id = ? AND model = ? ORDER BY position ASC",
            (conversion_id, model)).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owned:
            conn.close()


def campaign_report(model: str, *, conn=None) -> dict:
    """Aggregate credited cents by campaign_ref under ``model``."""
    if model not in VALID_MODELS:
        raise AttributionError(f"unknown model: {model!r}")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT campaign_ref, COUNT(*) AS credits, "
            "COALESCE(SUM(credit_cents),0) AS credit_cents "
            "FROM business_os_attr_credits WHERE model = ? "
            "GROUP BY campaign_ref ORDER BY credit_cents DESC", (model,)).fetchall()
        return {"model": model,
                "rows": [{"campaign_ref": r["campaign_ref"],
                          "credits": int(r["credits"]),
                          "credit_cents": int(r["credit_cents"])} for r in rows]}
    finally:
        if owned:
            conn.close()


def channel_report(model: str, *, conn=None) -> dict:
    """Aggregate credited cents by channel under ``model``."""
    if model not in VALID_MODELS:
        raise AttributionError(f"unknown model: {model!r}")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT channel, COUNT(*) AS credits, "
            "COALESCE(SUM(credit_cents),0) AS credit_cents "
            "FROM business_os_attr_credits WHERE model = ? "
            "GROUP BY channel ORDER BY credit_cents DESC", (model,)).fetchall()
        return {"model": model,
                "rows": [{"channel": r["channel"], "credits": int(r["credits"]),
                          "credit_cents": int(r["credit_cents"])} for r in rows]}
    finally:
        if owned:
            conn.close()


def user_path(user_id: str, *, limit: int = 200, conn=None) -> list:
    """The ordered touchpoint path for a user (oldest-first) — for path analysis."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT touchpoint_id,channel,touch_type,campaign_ref,occurred_at "
            "FROM business_os_attr_touchpoints WHERE user_id = ? "
            "ORDER BY occurred_at ASC, created_at ASC LIMIT ?",
            (str(user_id), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owned:
            conn.close()
