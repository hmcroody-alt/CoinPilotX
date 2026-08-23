"""A real sampled market observation series.

Time-window alert conditions — "price fell 5% in the last hour" — need something
the rest of the app does not have: a record of what the market *was*, sampled at
a known cadence. Every other crypto surface here fetches an instantaneous quote
and discards it, so a window computed from those surfaces would be a guess
wearing a timestamp.

Why not ``price_history``
-------------------------
That table already exists and is the trap this module exists to avoid. It is
written only by legacy Telegram paths, at no fixed cadence, and read as "the last
N rows". Rows arrive when somebody happens to ask a bot for a price, so "the row
before this one" can be four seconds or four days old and the table cannot tell
you which. A window built on it would answer confidently and be wrong by an
unbounded amount. It is left alone rather than extended.

One writer, one cadence
-----------------------
:func:`record_board` is called from ``alert_worker`` once per cycle and nowhere
else. Sampling opportunistically from web requests would reproduce exactly the
``price_history`` defect: a series whose density tracks user traffic rather than
time, dense at peak hours and empty overnight, with no way to tell the two apart
after the fact.

Two timestamps, and why
-----------------------
``observed_at`` is our own UTC clock at the moment the sample was written. It is
what every window is measured against, and it has to come from the same clock as
the boundary arithmetic. The provider's stamp cannot serve that purpose:
``market_data.live_market_board`` writes it with ``time.strftime`` and no time
argument, which is *local* time — identical to UTC on the deployment container
and several hours away from it on a developer's machine. Silently mixing the two
would shift every window by the local offset.

``provider_updated_at`` is that provider stamp, kept for one job: identity. The
market board is cached for 45s, so a worker cycle landing on a cache hit is
re-reading a market it has already recorded. ``(symbol, provider_updated_at)`` is
unique and the insert is written to be a no-op, so the series counts the number
of times the market actually moved on from us, not the number of times we asked.
The cost is that ``observed_at`` trails the true read by up to the cache TTL —
the same order as the sampling interval itself, and bounded, which is the part
that matters.

What a window may and may not claim
-----------------------------------
A window is answered from the newest observation at or before the boundary — the
reading that was current when the window opened. If no such observation exists
the answer is *undecidable*, never "use the oldest row we happen to have". A rule
asking about one hour must not be silently answered with twenty minutes because
the worker only started twenty minutes ago; that is a different question with a
different answer, and the member has no way to see the substitution.
:data:`MAX_BASELINE_DRIFT_SECONDS` applies the same rule to holes in the middle
of the series.

Every reading carries the age of the baseline it actually used, so the copy the
member reads can name the two moments that were compared instead of implying the
requested window was measured exactly.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Optional

from . import crypto_alert_conditions as conditions
from . import user_context

#: The window vocabulary has exactly one owner, and it is the rule validator —
#: it is pure, it is what rejects a window at creation time, and a series that
#: offered a window the validator refuses (or refused one the series can answer)
#: would be a split-brain the member experiences as an alert that never fires.
WINDOW_CHOICES = conditions.WINDOW_CHOICES
WINDOWABLE_METRICS = conditions.WINDOWABLE_METRICS
normalize_window = conditions.normalize_window


#: Nominal seconds between samples. This is ``ALERT_WORKER_INTERVAL_SECONDS``'
#: default and the market board's cache TTL — the two are equal by design, so a
#: cycle normally corresponds to one genuine provider read.
SAMPLE_INTERVAL_SECONDS = 45

#: How far past the requested boundary a baseline may sit before the window is
#: refused. Sized as several sample intervals: a cycle or two can be lost to a
#: worker restart or a provider timeout without invalidating the window, but a
#: real hole in the series is reported rather than papered over.
MAX_BASELINE_DRIFT_SECONDS = 6 * SAMPLE_INTERVAL_SECONDS

#: How stale the newest sample may be before readings are refused outright. If
#: the sampler has been down this long we are not observing the market, and a
#: "current" value from twenty minutes ago compared against an hour-old baseline
#: is not the window anyone asked for.
MAX_LATEST_AGE_SECONDS = 8 * SAMPLE_INTERVAL_SECONDS

#: Kept comfortably longer than the longest offered window so a rule at the
#: boundary still has a baseline after a prune.
RETENTION_HOURS = 72

#: Columns sampled per symbol. The two provider-supplied 24h deltas are recorded
#: for context but are not windowable — a percent change of a percent change is
#: not a quantity anybody means to ask about.
SAMPLED_METRICS = ("price", "change_24h", "volume_24h", "market_cap")

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()

_PRUNE_LOCK = threading.Lock()
_LAST_PRUNE_AT: list = [None]
PRUNE_INTERVAL_SECONDS = 3600


def _utcnow() -> datetime:
    return datetime.utcnow()


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _number(raw: Any) -> Optional[float]:
    """A float, or ``None`` for anything that is not an observation.

    Booleans and NaN are rejected rather than coerced, for the same reason the
    condition library rejects them: a missing metric must stay missing all the
    way to the member, not arrive as a number that happens to compare cleanly.
    """
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value == value and value not in (float("inf"), float("-inf")) else None


def ensure_schema() -> dict:
    """Create the table on first use.

    Takes no caller connection on purpose. DDL issued on a borrowed connection
    is not committed here, and on Postgres the uncommitted catalog lock then
    blocks the next connection that touches the table — a hang, not an error.
    Owning the connection keeps the commit and the DDL together.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return {"ok": True}
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return {"ok": True}
        result = _ensure_schema_impl()
        _SCHEMA_READY = True
        return result


def _ensure_schema_impl() -> dict:
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS market_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                observed_at TEXT,
                provider_updated_at TEXT,
                price REAL,
                change_24h REAL,
                volume_24h REAL,
                market_cap REAL,
                source TEXT
            )
            """
        )
        # The dedupe that keeps the series honest about how often the market
        # actually moved on from us. Without it a cached board re-recorded every
        # cycle would inflate the sample count and make a sparse series look
        # dense.
        try:
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_market_observations_provider "
                "ON market_observations (symbol, provider_updated_at)"
            )
        except Exception:
            logging.warning(
                "market_observations unique index could not be created; "
                "INSERT OR IGNORE will no longer suppress re-reads of a cached board.",
                exc_info=True,
            )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_market_observations_symbol_at "
            "ON market_observations (symbol, observed_at)"
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


def _normalize_symbol(symbol: Any) -> str:
    return str(symbol or "").strip().upper()


def record_board(board: Optional[dict], now: Optional[datetime] = None) -> dict:
    """Persist one sample per symbol from a market board payload.

    Called once per ``alert_worker`` cycle. Returns counts rather than raising:
    a sampling failure must not stop the cycle from evaluating alerts, because
    every rule that does not use a window works perfectly well without the
    series.
    """
    markets = (board or {}).get("markets") or []
    if not markets:
        return {"ok": False, "recorded": 0, "skipped": 0, "reason": "empty_board"}

    # Identity only — never parsed, never compared against our clock. See the
    # module docstring on why the provider stamp cannot measure a window.
    provider_updated_at = str((board or {}).get("updated_at") or "").strip()
    if not provider_updated_at:
        # With no way to tell one board read from the next, every cycle would
        # record a fresh sample of a possibly-cached market and the series would
        # overstate how often we looked.
        return {"ok": False, "recorded": 0, "skipped": 0, "reason": "no_provider_timestamp"}

    source = str((board or {}).get("source") or "").strip()
    ensure_schema()
    observed_at = _iso(now or _utcnow())
    recorded = 0
    skipped = 0
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        for item in markets:
            symbol = _normalize_symbol(item.get("symbol"))
            price = _number(item.get("price"))
            if not symbol or price is None:
                # A row with no price is not an observation of anything a window
                # can be computed over.
                skipped += 1
                continue
            cur.execute(
                """
                INSERT OR IGNORE INTO market_observations
                    (symbol, observed_at, provider_updated_at, price, change_24h,
                     volume_24h, market_cap, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (symbol, observed_at, provider_updated_at, price,
                 _number(item.get("change_24h")), _number(item.get("volume_24h")),
                 _number(item.get("market_cap")), source),
            )
            recorded += 1
        conn.commit()
    except Exception as exc:
        logging.exception("Market observation sampling failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            logging.debug("Rollback after failed sampling was itself unavailable.", exc_info=True)
        return {"ok": False, "recorded": 0, "skipped": skipped, "reason": str(exc)}
    finally:
        conn.close()

    prune_if_due(now)
    return {"ok": True, "recorded": recorded, "skipped": skipped,
            "observed_at": observed_at, "provider_updated_at": provider_updated_at,
            "source": source}


def prune_if_due(now: Optional[datetime] = None) -> dict:
    """Drop samples past :data:`RETENTION_HOURS`, at most hourly."""
    moment = now or _utcnow()
    with _PRUNE_LOCK:
        last = _LAST_PRUNE_AT[0]
        if last is not None and (moment - last).total_seconds() < PRUNE_INTERVAL_SECONDS:
            return {"ok": True, "pruned": 0, "skipped": True}
        _LAST_PRUNE_AT[0] = moment
    cutoff = _iso(moment - timedelta(hours=RETENTION_HOURS))
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM market_observations WHERE observed_at < ?", (cutoff,))
        pruned = max(0, int(getattr(cur, "rowcount", 0) or 0))
        conn.commit()
        return {"ok": True, "pruned": pruned, "cutoff": cutoff}
    except Exception as exc:
        logging.warning("Market observation prune failed: %s", exc)
        return {"ok": False, "pruned": 0, "reason": str(exc)}
    finally:
        conn.close()


def _latest_row(cur, symbol: str) -> dict:
    cur.execute(
        "SELECT * FROM market_observations WHERE symbol=? "
        "ORDER BY observed_at DESC LIMIT 1",
        (symbol,),
    )
    return user_context.row_to_dict(cur.fetchone()) or {}


def _baseline_row(cur, symbol: str, boundary: str) -> dict:
    """The newest sample at or before the window boundary.

    Deliberately not "the oldest sample we have": that would answer a different
    window than the one asked for whenever the series is younger than the window.
    """
    cur.execute(
        "SELECT * FROM market_observations WHERE symbol=? AND observed_at <= ? "
        "ORDER BY observed_at DESC LIMIT 1",
        (symbol, boundary),
    )
    return user_context.row_to_dict(cur.fetchone()) or {}


def _sample_count(cur, symbol: str, since: str) -> int:
    cur.execute(
        "SELECT COUNT(*) FROM market_observations WHERE symbol=? AND observed_at >= ?",
        (symbol, since),
    )
    row = cur.fetchone()
    return int((row or [0])[0] or 0)


def _unavailable(symbol: str, metric: str, minutes: int, reason: str, message: str) -> dict:
    return {"ok": False, "symbol": symbol, "metric": metric, "window_minutes": minutes,
            "change_percent": None, "latest": None, "baseline": None,
            "baseline_at": "", "baseline_age_seconds": None, "latest_at": "",
            "sample_count": 0, "reason": reason, "message": message}


def window_reading(symbol: Any, metric: Any, minutes: Any,
                   now: Optional[datetime] = None) -> dict:
    """Percent change in ``metric`` for ``symbol`` over the last ``minutes``.

    ``ok`` False means undecidable, and callers must treat it exactly as they
    treat a metric the provider omitted — never as "it did not move".

    ``baseline_age_seconds`` is the age of the reading actually compared
    against, which is the number the copy should quote. The requested window is
    what the member asked for; this is what could be measured.
    """
    symbol = _normalize_symbol(symbol)
    metric = str(metric or "price").strip().lower()
    if metric not in WINDOWABLE_METRICS:
        return _unavailable(symbol, metric, 0, "unsupported_metric",
                            f"{metric} is not measured over a window.")
    try:
        minutes = normalize_window(minutes)
    except ValueError as exc:
        return _unavailable(symbol, metric, 0, "unsupported_window", str(exc))

    moment = now or _utcnow()
    boundary = moment - timedelta(minutes=minutes)
    ensure_schema()
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        latest = _latest_row(cur, symbol)
        if not latest:
            return _unavailable(symbol, metric, minutes, "no_series",
                                f"No recorded readings for {symbol} yet.")
        latest_at = _parse(latest.get("observed_at"))
        latest_age = (moment - latest_at).total_seconds() if latest_at else None
        if latest_age is None or latest_age > MAX_LATEST_AGE_SECONDS:
            return _unavailable(symbol, metric, minutes, "series_stale",
                                f"{symbol} readings have stopped arriving.")

        baseline = _baseline_row(cur, symbol, _iso(boundary))
        if not baseline:
            return _unavailable(
                symbol, metric, minutes, "window_not_covered",
                f"{symbol} has not been watched for a full {minutes} minutes yet.")
        baseline_at = _parse(baseline.get("observed_at"))
        baseline_age = (moment - baseline_at).total_seconds() if baseline_at else None
        if baseline_age is None or baseline_age - minutes * 60 > MAX_BASELINE_DRIFT_SECONDS:
            return _unavailable(
                symbol, metric, minutes, "window_gap",
                f"{symbol} readings are missing around the {minutes}-minute mark.")

        latest_value = _number(latest.get(metric))
        baseline_value = _number(baseline.get(metric))
        sample_count = _sample_count(cur, symbol, _iso(baseline_at))
    finally:
        conn.close()

    if latest_value is None or baseline_value is None:
        return _unavailable(
            symbol, metric, minutes, "metric_unavailable",
            f"{symbol} {metric} was not published for the whole window.")
    if baseline_value == 0:
        # A percent change from zero has no value, and reporting one would be
        # inventing a number rather than reading one.
        return _unavailable(symbol, metric, minutes, "baseline_zero",
                            f"{symbol} {metric} was zero at the start of the window.")

    change = (latest_value - baseline_value) / abs(baseline_value) * 100.0
    return {
        "ok": True,
        "symbol": symbol,
        "metric": metric,
        "window_minutes": minutes,
        "change_percent": change,
        "latest": latest_value,
        "latest_at": _iso(latest_at),
        "baseline": baseline_value,
        "baseline_at": _iso(baseline_at),
        "baseline_age_seconds": int(baseline_age),
        "sample_count": sample_count,
        "reason": "",
        "message": "",
    }


def coverage(symbol: Any, now: Optional[datetime] = None) -> dict:
    """How far back the series actually reaches for one symbol.

    The UI offers only the windows this reports, so a member never creates a
    rule that is undecidable from the moment it is saved.
    """
    symbol = _normalize_symbol(symbol)
    moment = now or _utcnow()
    ensure_schema()
    conn = user_context.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT MIN(observed_at), MAX(observed_at), COUNT(*) "
            "FROM market_observations WHERE symbol=?",
            (symbol,),
        )
        row = cur.fetchone() or (None, None, 0)
    finally:
        conn.close()
    oldest, newest, count = _parse(row[0]), _parse(row[1]), int(row[2] or 0)
    if not oldest or not newest:
        return {"ok": True, "symbol": symbol, "sample_count": 0, "span_minutes": 0,
                "available_windows": [], "oldest_at": "", "newest_at": "", "stale": True}
    span_minutes = int((moment - oldest).total_seconds() // 60)
    stale = (moment - newest).total_seconds() > MAX_LATEST_AGE_SECONDS
    return {
        "ok": True,
        "symbol": symbol,
        "sample_count": count,
        "span_minutes": span_minutes,
        "oldest_at": _iso(oldest),
        "newest_at": _iso(newest),
        "stale": stale,
        "available_windows": [] if stale else [w for w in WINDOW_CHOICES if w <= span_minutes],
    }
