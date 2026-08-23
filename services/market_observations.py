"""Sampled market observations for windowed premium alert conditions.

The existing ``price_history`` table is not trustworthy for intraday windows,
so the alert engine records the quotes it ALREADY fetches (one fetch, no second
poller) into ``market_observations`` and evaluates windowed conditions against
those real samples only.

Honesty rules (do not weaken):
- An observation is only recorded when a real price was fetched. Nothing is
  interpolated, extrapolated or back-filled.
- ``window_start_observation`` returns the nearest real sample to a window
  start ONLY when it lies within +/-20% of the window length from the target
  instant. Otherwise it returns ``None`` and the caller must record
  ``insufficient_data`` and skip — never guess.

Import safety: stdlib + ``services.db`` only; no flask or network access.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta

from . import db as db_service

_log = logging.getLogger("market_observations")

#: Keep roughly one week of samples; windowed conditions max out at 1440
#: minutes (24h) so seven days leaves ample slack for the +/-20% tolerance.
RETENTION_DAYS = 7

#: Fraction of the window length within which a "window start" sample is
#: considered valid. Outside it the lookup refuses rather than approximates.
WINDOW_TOLERANCE_FRACTION = 0.20

#: Cheapest possible prune scheduling: at most once per interval per process.
PRUNE_MIN_INTERVAL_SECONDS = 3600

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_LAST_PRUNE_AT = 0.0


def _connect():
    """Indirection so tests can point the module at a scratch database."""
    return db_service.connect()


def _utcnow():
    return datetime.utcnow()


def _iso(value):
    """Normalize a datetime/ISO-string to a second-resolution ISO string."""
    parsed = _parse_ts(value)
    if parsed is None:
        return None
    return parsed.isoformat(timespec="seconds")


def _parse_ts(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=None, microsecond=0)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(
            tzinfo=None, microsecond=0
        )
    except Exception:
        return None


def _normalize_asset_id(asset_id):
    return str(asset_id or "").strip().upper()[:60]


def _to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_to_dict(row):
    return dict(row) if row else None


def ensure_observation_schema(conn=None):
    """Create the ``market_observations`` table idempotently (shared pattern
    with ``alert_engine.ensure_alert_schema``)."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return {"ok": True}
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return {"ok": True}
        owns_connection = conn is None
        connection = conn or _connect()
        try:
            cur = connection.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS market_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_id TEXT,
                    observed_at TEXT,
                    price REAL,
                    volume_24h REAL,
                    market_cap REAL,
                    source TEXT,
                    created_at TEXT
                )
                """
            )
            try:
                cur.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_market_observations_asset_time "
                    "ON market_observations (asset_id, observed_at)"
                )
            except Exception:
                _log.warning(
                    "market_observations unique index could not be created.",
                    exc_info=True,
                )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_market_observations_lookup "
                "ON market_observations (asset_id, observed_at)"
            )
            connection.commit()
            _SCHEMA_READY = True
        finally:
            if owns_connection:
                connection.close()
        return {"ok": True}


def record_observation(
    asset_id,
    price=None,
    volume_24h=None,
    market_cap=None,
    source="",
    observed_at=None,
):
    """Record one real market sample. Deduped on (asset_id, observed_at).

    ``price`` is mandatory: a sample without a price is not a market
    observation and must not be written. Returns ``{"ok": bool, ...}`` and
    never raises — recording is best-effort and may not break evaluation.
    """
    asset = _normalize_asset_id(asset_id)
    price_value = _to_float(price)
    if not asset or price_value is None:
        return {"ok": False, "recorded": False, "message": "asset_id and price are required."}
    when = _iso(observed_at) or _utcnow().isoformat(timespec="seconds")
    try:
        ensure_observation_schema()
        conn = _connect()
        try:
            cur = conn.cursor()
            # INSERT OR IGNORE dedupes on the unique (asset_id, observed_at)
            # index; services.db translates it to ON CONFLICT DO NOTHING on
            # PostgreSQL.
            cur.execute(
                """
                INSERT OR IGNORE INTO market_observations
                (asset_id, observed_at, price, volume_24h, market_cap, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset,
                    when,
                    price_value,
                    _to_float(volume_24h),
                    _to_float(market_cap),
                    str(source or "")[:80],
                    _utcnow().isoformat(timespec="seconds"),
                ),
            )
            recorded = int(getattr(cur, "rowcount", 0) or 0) > 0
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "recorded": recorded, "asset_id": asset, "observed_at": when}
    except Exception as exc:
        _log.info("market observation not recorded for %s: %s", asset, exc)
        return {"ok": False, "recorded": False, "message": str(exc)}


def record_quote(quote):
    """Record the asset row of a ``live_market_service.get_crypto_quote``
    payload, if (and only if) it carries a real price."""
    if not isinstance(quote, dict) or not quote.get("ok"):
        return {"ok": False, "recorded": False, "message": "No live quote to record."}
    asset = quote.get("asset") or {}
    return record_observation(
        asset.get("symbol") or asset.get("id"),
        price=asset.get("price"),
        volume_24h=asset.get("volume_24h"),
        market_cap=asset.get("market_cap"),
        source=str(quote.get("source") or "live_market"),
        observed_at=quote.get("updated_at"),
    )


def get_observations(asset_id, start=None, end=None, limit=None):
    """Real samples for one asset, oldest first.

    ``start``/``end`` (datetime or ISO string) bound ``observed_at``
    inclusively. ``limit`` keeps the MOST RECENT ``limit`` samples of the
    range. Returns ``[]`` on any failure — this reader never raises.
    """
    asset = _normalize_asset_id(asset_id)
    if not asset:
        return []
    try:
        ensure_observation_schema()
        clauses = ["asset_id=?"]
        params = [asset]
        start_iso = _iso(start)
        end_iso = _iso(end)
        if start_iso:
            clauses.append("observed_at>=?")
            params.append(start_iso)
        if end_iso:
            clauses.append("observed_at<=?")
            params.append(end_iso)
        sql = (
            "SELECT asset_id, observed_at, price, volume_24h, market_cap, source "
            "FROM market_observations WHERE " + " AND ".join(clauses) + " ORDER BY observed_at DESC"
        )
        capped = None
        if limit is not None:
            capped = max(1, min(int(limit), 5000))
            sql += " LIMIT ?"
            params.append(capped)
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            rows = [_row_to_dict(row) for row in cur.fetchall()]
        finally:
            conn.close()
        rows.reverse()  # oldest first
        for row in rows:
            row["symbol"] = row.get("asset_id")
        return rows
    except Exception as exc:
        _log.info("market observation read failed for %s: %s", asset, exc)
        return []


def window_start_observation(asset_id, window_minutes, now=None):
    """The real sample nearest to ``now - window_minutes``, or ``None``.

    Valid only when the nearest sample lies within +/-20% of the window length
    from the target instant. ``None`` means the caller must record
    ``insufficient_data`` and skip — it must never substitute a guess.
    """
    try:
        window = float(window_minutes)
    except (TypeError, ValueError):
        return None
    if window <= 0:
        return None
    reference = _parse_ts(now) or _utcnow()
    target = reference - timedelta(minutes=window)
    tolerance = timedelta(minutes=window * WINDOW_TOLERANCE_FRACTION)
    rows = get_observations(asset_id, start=target - tolerance, end=target + tolerance)
    best = None
    best_offset = None
    for row in rows:
        ts = _parse_ts(row.get("observed_at"))
        if ts is None or row.get("price") is None:
            continue
        offset = abs((ts - target).total_seconds())
        if best_offset is None or offset < best_offset:
            best_offset = offset
            best = row
    if best is None or best_offset is None:
        return None
    if best_offset > tolerance.total_seconds():
        return None
    return best


def prune_observations(max_age_days=RETENTION_DAYS, now=None):
    """Delete samples older than ``max_age_days``. Cheap and idempotent."""
    reference = _parse_ts(now) or _utcnow()
    cutoff = (reference - timedelta(days=max(1, int(max_age_days)))).isoformat(
        timespec="seconds"
    )
    try:
        ensure_observation_schema()
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM market_observations WHERE observed_at < ?", (cutoff,))
            deleted = int(getattr(cur, "rowcount", 0) or 0)
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "deleted": deleted, "cutoff": cutoff}
    except Exception as exc:
        _log.info("market observation prune failed: %s", exc)
        return {"ok": False, "deleted": 0, "message": str(exc)}


def maybe_prune_observations(now=None):
    """Prune at most once per :data:`PRUNE_MIN_INTERVAL_SECONDS` per process.

    Called from the alert worker's evaluation cycle so retention needs no
    second worker or scheduler.
    """
    global _LAST_PRUNE_AT
    current = time.time()
    if current - _LAST_PRUNE_AT < PRUNE_MIN_INTERVAL_SECONDS:
        return {"ok": True, "deleted": 0, "skipped": True}
    _LAST_PRUNE_AT = current
    return prune_observations(now=now)
