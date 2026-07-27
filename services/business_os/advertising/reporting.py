"""Business OS — Advertising: authoritative advertiser reporting (read-only).

Turns the immutable delivery + event + billing records into the numbers an advertiser
sees, with three hard rules from the reporting contract (spec §2):

  * **Every metric is labelled.** ``Confirmed`` = counted directly from immutable
    events / ledger. ``Estimated`` = derived with a stated modelling assumption.
    ``Modeled`` = a forecast/extrapolation. A consumer never has to guess how solid a
    number is.
  * **Never fabricate, never show unavailable as zero.** MVP has no video-playback
    event stream, so video views / engagement are reported as ``available: false`` with
    a ``null`` value — NOT 0, which would read as "we measured zero plays". The same
    holds for any ratio whose denominator is 0 (CTR with no impressions is ``null``,
    not 0).
  * **Grounded in real events only.** impressions/reach/frequency come from the
    immutable impression log; clicks from the click log; spend from the ledger-backed
    billing events. No auction, no projection leaks into a Confirmed figure.

Reads only. No money, no mutation. Filters: date range (``start``/``end`` ISO,
half-open ``[start, end)``), placement, campaign. Breakdowns paginate
(``limit``/``offset``). ``to_csv`` renders any list of flat dict rows for export.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Optional

from services import db
from services.business_os.advertising import service as _svc


CONFIRMED = "Confirmed"
ESTIMATED = "Estimated"
MODELED = "Modeled"

_IMPR = "business_os_ad_impression_events"
_CLICK = "business_os_ad_click_events"
_BILLING = "business_os_ad_billing_events"


def _norm_currency(currency: Any) -> str:
    return str(currency or "usd").strip().lower()


def _ratio(numer: int, denom: int) -> Optional[float]:
    """Safe ratio: ``None`` (not 0) when the denominator is 0, so an unmeasurable
    rate is never misreported as a real zero."""
    if not denom:
        return None
    return numer / denom


def _time_clause(col: str, start: Optional[str], end: Optional[str],
                 params: list) -> str:
    """Half-open [start, end) window on an ISO timestamp column."""
    parts = []
    if start:
        parts.append(f"{col} >= ?"); params.append(str(start))
    if end:
        parts.append(f"{col} < ?"); params.append(str(end))
    return (" AND " + " AND ".join(parts)) if parts else ""


def _count(conn, table: str, campaign_id: str, *, start=None, end=None,
           placement=None, distinct_col=None) -> int:
    params: list = [campaign_id]
    select = f"COUNT(DISTINCT {distinct_col})" if distinct_col else "COUNT(*)"
    sql = f"SELECT {select} FROM {table} WHERE campaign_id = ?"
    if placement:
        sql += " AND placement = ?"; params.append(placement)
    sql += _time_clause("event_at", start, end, params)
    row = conn.execute(sql, tuple(params)).fetchone()
    return int((row[0] if row else 0) or 0)


# A processed billing event is attributed to its SOURCE event's activity time and
# placement (not the ledger-post time), so "spend on day D / placement P" lines up
# with "impressions/clicks on day D / placement P".
_BILLING_SPEND_JOIN = (
    f"FROM {_BILLING} b "
    f"LEFT JOIN {_IMPR} i ON b.source_event_type = 'impression' "
    "AND b.source_event_id = i.event_id "
    f"LEFT JOIN {_CLICK} c ON b.source_event_type = 'click' "
    "AND b.source_event_id = c.event_id "
    "WHERE b.campaign_id = ? AND b.currency = ? AND b.billing_status = 'processed'")
_SRC_EVENT_AT = "COALESCE(i.event_at, c.event_at)"
_SRC_PLACEMENT = "COALESCE(i.placement, c.placement)"


def _spend_cents(conn, campaign_id: str, currency: str, *, start=None, end=None,
                 placement=None) -> int:
    """Whole cents actually charged (processed billing events) in the window,
    attributed to the source event's activity time / placement."""
    params: list = [campaign_id, currency]
    sql = f"SELECT COALESCE(SUM(b.total_amount_cents), 0) " + _BILLING_SPEND_JOIN
    sql += _time_clause(_SRC_EVENT_AT, start, end, params)
    if placement:
        sql += f" AND {_SRC_PLACEMENT} = ?"; params.append(placement)
    row = conn.execute(sql, tuple(params)).fetchone()
    return int((row[0] if row else 0) or 0)


def _core_metrics(conn, campaign_id: str, currency: str, *, start, end,
                  placement) -> dict:
    impressions = _count(conn, _IMPR, campaign_id, start=start, end=end,
                         placement=placement)
    clicks = _count(conn, _CLICK, campaign_id, start=start, end=end,
                    placement=placement)
    reach = _count(conn, _IMPR, campaign_id, start=start, end=end,
                   placement=placement, distinct_col="subject_ref")
    spend = _spend_cents(conn, campaign_id, currency, start=start, end=end,
                         placement=placement)

    frequency = _ratio(impressions, reach)
    ctr = _ratio(clicks, impressions)
    cost_per_click = _ratio(spend, clicks)
    effective_cpm = None
    if impressions:
        effective_cpm = spend / impressions * 1000.0

    return {
        # Confirmed — counted from immutable records.
        "impressions": impressions,
        "clicks": clicks,
        "reach": reach,
        "frequency": None if frequency is None else round(frequency, 4),
        "ctr": None if ctr is None else round(ctr, 6),
        "spend_cents": spend,
        "cost_per_click_cents": None if cost_per_click is None else round(cost_per_click, 2),
        "effective_cpm_cents": None if effective_cpm is None else round(effective_cpm, 2),
        # Unavailable in MVP — reported as null + available:false, never 0.
        "video_views": None,
        "video_view_rate": None,
        "video_engagements": None,
    }


def _metric_meta() -> dict:
    """Per-metric confidence + availability so no number is consumed unlabelled."""
    return {
        "impressions": {"confidence": CONFIRMED, "available": True},
        "clicks": {"confidence": CONFIRMED, "available": True},
        "reach": {"confidence": CONFIRMED, "available": True},
        "frequency": {"confidence": CONFIRMED, "available": True},
        "ctr": {"confidence": CONFIRMED, "available": True},
        "spend_cents": {"confidence": CONFIRMED, "available": True},
        "cost_per_click_cents": {"confidence": CONFIRMED, "available": True},
        "effective_cpm_cents": {"confidence": CONFIRMED, "available": True},
        # No video-playback event stream exists yet: unavailable, not zero.
        "video_views": {"confidence": None, "available": False,
                        "reason": "no_video_event_source"},
        "video_view_rate": {"confidence": None, "available": False,
                            "reason": "no_video_event_source"},
        "video_engagements": {"confidence": None, "available": False,
                              "reason": "no_video_event_source"},
    }


# --- public: campaign summary -----------------------------------------------
def campaign_report(campaign_id: Any, *, currency: Any = "usd",
                    start: Optional[str] = None, end: Optional[str] = None,
                    placement: Optional[str] = None, conn=None) -> dict:
    """Authoritative summary for one campaign over an optional window/placement."""
    _svc._require_enabled()
    campaign_id = _svc._sid(campaign_id)
    cur = _norm_currency(currency)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        metrics = _core_metrics(conn, campaign_id, cur, start=start, end=end,
                                placement=placement)
        return {
            "campaign_id": campaign_id,
            "currency": cur,
            "time_range": {"start": start, "end": end},
            "placement": placement,
            "metrics": metrics,
            "metric_meta": _metric_meta(),
        }
    finally:
        if owned:
            conn.close()


# --- public: placement breakdown --------------------------------------------
def placement_breakdown(campaign_id: Any, *, currency: Any = "usd",
                        start: Optional[str] = None, end: Optional[str] = None,
                        limit: int = 50, offset: int = 0, conn=None) -> dict:
    """Per-placement metric rows (paginated). Placements come from the impression log
    so an advertiser sees exactly where their ad was actually shown."""
    _svc._require_enabled()
    campaign_id = _svc._sid(campaign_id)
    cur = _norm_currency(currency)
    lim, off = _page(limit, offset)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        prows = conn.execute(
            f"SELECT DISTINCT placement FROM {_IMPR} WHERE campaign_id = ?"
            + _time_clause_simple("event_at", start, end),
            _params(campaign_id, start, end)).fetchall()
        placements = sorted((r[0] if not hasattr(r, "keys") else r["placement"])
                            for r in prows)
        total = len(placements)
        page = placements[off:off + lim]
        rows = []
        for pl in page:
            m = _core_metrics(conn, campaign_id, cur, start=start, end=end,
                              placement=pl)
            m_row = {"placement": pl}
            m_row.update(m)
            rows.append(m_row)
        return {
            "campaign_id": campaign_id, "currency": cur,
            "time_range": {"start": start, "end": end},
            "rows": rows, "total": total, "limit": lim, "offset": off,
            "metric_meta": _metric_meta(),
        }
    finally:
        if owned:
            conn.close()


# --- public: creative breakdown ---------------------------------------------
def creative_breakdown(campaign_id: Any, *, currency: Any = "usd",
                       start: Optional[str] = None, end: Optional[str] = None,
                       limit: int = 50, offset: int = 0, conn=None) -> dict:
    """Per-(creative_id, creative_version) metric rows (paginated)."""
    _svc._require_enabled()
    campaign_id = _svc._sid(campaign_id)
    cur = _norm_currency(currency)
    lim, off = _page(limit, offset)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        crows = conn.execute(
            f"SELECT DISTINCT creative_id, creative_version FROM {_IMPR} "
            "WHERE campaign_id = ?" + _time_clause_simple("event_at", start, end)
            + " ORDER BY creative_id, creative_version",
            _params(campaign_id, start, end)).fetchall()
        creatives = [(_svc._row_to_dict(r)) for r in crows]
        total = len(creatives)
        page = creatives[off:off + lim]
        rows = []
        for c in page:
            cid = c.get("creative_id"); cver = c.get("creative_version")
            m = _creative_metrics(conn, campaign_id, cur, cid, cver,
                                  start=start, end=end)
            row = {"creative_id": cid, "creative_version": cver}
            row.update(m)
            rows.append(row)
        return {
            "campaign_id": campaign_id, "currency": cur,
            "time_range": {"start": start, "end": end},
            "rows": rows, "total": total, "limit": lim, "offset": off,
            "metric_meta": _metric_meta(),
        }
    finally:
        if owned:
            conn.close()


def _creative_metrics(conn, campaign_id, currency, creative_id, creative_version,
                      *, start, end) -> dict:
    def _c(table):
        params: list = [campaign_id, creative_id, creative_version]
        sql = (f"SELECT COUNT(*) FROM {table} WHERE campaign_id = ? "
               "AND creative_id = ? AND creative_version = ?")
        sql += _time_clause("event_at", start, end, params)
        return int((conn.execute(sql, tuple(params)).fetchone()[0]) or 0)

    def _reach():
        params: list = [campaign_id, creative_id, creative_version]
        sql = (f"SELECT COUNT(DISTINCT subject_ref) FROM {_IMPR} "
               "WHERE campaign_id = ? AND creative_id = ? AND creative_version = ?")
        sql += _time_clause("event_at", start, end, params)
        return int((conn.execute(sql, tuple(params)).fetchone()[0]) or 0)

    impressions = _c(_IMPR)
    clicks = _c(_CLICK)
    reach = _reach()
    ctr = _ratio(clicks, impressions)
    return {
        "impressions": impressions,
        "clicks": clicks,
        "reach": reach,
        "ctr": None if ctr is None else round(ctr, 6),
        "frequency": None if not reach else round(impressions / reach, 4),
    }


# --- public: time-series ----------------------------------------------------
def time_series(campaign_id: Any, *, currency: Any = "usd",
                start: Optional[str] = None, end: Optional[str] = None,
                granularity: str = "day", placement: Optional[str] = None,
                conn=None) -> dict:
    """Bucketed impressions/clicks/spend over time (Confirmed). Granularity
    ``day`` (default) or ``hour``; buckets derive from the ISO timestamp prefix."""
    _svc._require_enabled()
    campaign_id = _svc._sid(campaign_id)
    cur = _norm_currency(currency)
    g = str(granularity or "day").strip().lower()
    prefix = 13 if g == "hour" else 10  # 'YYYY-MM-DDTHH' vs 'YYYY-MM-DD'
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        buckets: dict = {}

        def _accumulate(table, key):
            params: list = [campaign_id]
            sql = f"SELECT event_at FROM {table} WHERE campaign_id = ?"
            if placement:
                sql += " AND placement = ?"; params.append(placement)
            sql += _time_clause("event_at", start, end, params)
            for r in conn.execute(sql, tuple(params)).fetchall():
                ts = (r[0] if not hasattr(r, "keys") else r["event_at"]) or ""
                b = ts[:prefix]
                if not b:
                    continue
                buckets.setdefault(b, {"impressions": 0, "clicks": 0,
                                       "spend_cents": 0})[key] += 1

        _accumulate(_IMPR, "impressions")
        _accumulate(_CLICK, "clicks")

        # Spend per bucket from processed billing events, attributed to the SOURCE
        # event's activity time (so spend lines up with impressions/clicks).
        params: list = [campaign_id, cur]
        sql = (f"SELECT {_SRC_EVENT_AT} AS ev_at, b.total_amount_cents AS amt "
               + _BILLING_SPEND_JOIN)
        sql += _time_clause(_SRC_EVENT_AT, start, end, params)
        if placement:
            sql += f" AND {_SRC_PLACEMENT} = ?"; params.append(placement)
        for r in conn.execute(sql, tuple(params)).fetchall():
            d = _svc._row_to_dict(r)
            b = (d.get("ev_at") or "")[:prefix]
            if not b:
                continue
            buckets.setdefault(b, {"impressions": 0, "clicks": 0,
                                   "spend_cents": 0})["spend_cents"] += \
                int(d.get("amt") or 0)

        series = [dict(bucket=b, **vals) for b, vals in sorted(buckets.items())]
        return {
            "campaign_id": campaign_id, "currency": cur,
            "granularity": g, "placement": placement,
            "time_range": {"start": start, "end": end},
            "series": series,
            "confidence": CONFIRMED,
        }
    finally:
        if owned:
            conn.close()


# --- helpers ----------------------------------------------------------------
def _page(limit, offset):
    try:
        lim = max(1, min(int(limit), 500))
    except Exception:
        lim = 50
    try:
        off = max(0, int(offset))
    except Exception:
        off = 0
    return lim, off


def _time_clause_simple(col, start, end):
    parts = []
    if start:
        parts.append(f"{col} >= ?")
    if end:
        parts.append(f"{col} < ?")
    return (" AND " + " AND ".join(parts)) if parts else ""


def _params(campaign_id, start, end):
    p = [campaign_id]
    if start:
        p.append(str(start))
    if end:
        p.append(str(end))
    return tuple(p)


def to_csv(rows: list) -> str:
    """Render a list of flat dict rows to CSV text for export. Empty -> empty string.
    Null values render as empty cells (never a fabricated 0)."""
    if not rows:
        return ""
    # Union of keys, preserving first-seen order.
    fields: list = []
    for r in rows:
        for k in r.keys():
            if k not in fields:
                fields.append(k)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})
    return buf.getvalue()
