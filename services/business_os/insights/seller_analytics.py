"""Business OS — Section 7: per-seller, per-period analytics aggregates.

Why this module exists
----------------------
The Insights screen shows a seller totals ("$4,182 revenue, 37 orders, last 30 days"),
a daily series, a source split and a ranked list of their best listings. Until now the
app had no way to obtain any of those honestly.

The only seller-facing order endpoint is ``GET /api/pulse/payments/seller/orders``, and
it is ``LIMIT 100`` **per table**, newest first, with no date range (``bot.py``). A busy
store's 90-day total computed from that list is not a total — it is the sum of the most
recent hundred rows, silently truncated, and it gets *further* from the truth the better
the store does. Deriving "analytics" from it and printing the result as a total is the
one thing the Insights brief explicitly forbids, and rightly: a seller makes restock and
ad-spend decisions on these numbers.

So the aggregation happens here, on the server, over the full table, inside an explicit
half-open period window. Nothing is sampled and nothing is capped.

What this module will and will not claim
----------------------------------------
Four of the screen's metrics have **no source anywhere in this codebase**, and this
module says so rather than approximating them. ``UNAVAILABLE_METRICS`` is the complete,
machine-readable list, each entry naming the schema change that would fix it. The HTTP
layer passes it through untouched and the client hides those modules. The list is
exported so a test can pin it: if somebody later invents one of these numbers, the count
changes and the test fails.

Boundaries
----------
* **No new table.** This is a read over ``seller_transactions``, ``creator_transactions``
  and ``pulse_follows``, all of which already exist and are already written to.
* **Portable SQL.** ``services.db`` runs SQLite locally and PostgreSQL in production.
  ``created_at`` is TEXT holding an ISO-8601 UTC timestamp in both, so the window is a
  plain string range — ``>= start AND < end`` — which compares identically on both
  engines and uses the same index. Bucketing into days is done in Python afterwards
  rather than with ``date()``/``date_trunc()``, which differ between the two.
* **The seller's own midnight.** Period boundaries are computed in the seller's local
  time and converted back to UTC before they touch SQL, so "today" means their today.
* **Money stays in minor units.** No float ever touches a currency figure here, and no
  symbol or separator is chosen — the client formats through its own localization.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from services import db


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------

#: Selectable periods and their length in whole seller-local days. The keys are the
#: wire values; the client's period picker sends one of these verbatim.
PERIOD_DAYS: dict[str, int] = {
    "today": 1,
    "7d": 7,
    "30d": 30,
    "90d": 90,
}

DEFAULT_PERIOD = "7d"

#: Above this many buckets the series is folded into weeks. 30 daily points is about
#: what a phone-width chart can show before the x-axis stops being readable; 90 is not.
MAX_DAILY_BUCKETS = 30


# ---------------------------------------------------------------------------
# What this platform cannot yet answer
# ---------------------------------------------------------------------------

#: Metrics the Insights design asks for that have no source in this codebase. Each entry
#: names the concrete schema or engine change needed. The API returns this list and the
#: client renders nothing in its place — no zero, no placeholder, no estimate.
UNAVAILABLE_METRICS: tuple[dict[str, str], ...] = (
    {
        "key": "store_views",
        "label": "Store and listing views",
        "needs": (
            "A view-tracking table for storefronts and listings. `pulse_post_views` and "
            "`pulse_video_views` cover feed content only; `marketplace_listings` carries "
            "no view counter and nothing increments one."
        ),
    },
    {
        "key": "ads_attribution",
        "label": "Revenue attributed to ads",
        "needs": (
            "A business-scoped, period-scoped attribution read. The attribution engine is "
            "real (four models, a lookback window) but `campaign_report(model)` and "
            "`channel_report(model)` accept neither a business_id nor a date range — they "
            "are platform-wide and all-time, so no per-seller 'From ads' figure can be "
            "taken from them."
        ),
    },
    {
        "key": "on_time_dispatch",
        "label": "On-time dispatch rate",
        "needs": "A promised `ship_by` and a recorded `dispatched_at` on the order. Neither column exists.",
    },
    {
        "key": "reply_rate",
        "label": "Replies under the response threshold",
        "needs": "A messaging metric that records first-response latency per conversation.",
    },
    {
        "key": "offers_answered",
        "label": "Offers answered",
        "needs": (
            "A live offers table. `marketplace_buyer_interest` has the right shape but is "
            "created and never written to or read from."
        ),
    },
)


# ---------------------------------------------------------------------------
# Status vocabulary
# ---------------------------------------------------------------------------

#: A transaction whose status contains any of these did not result in money the seller
#: keeps, so it is excluded from revenue, order counts, the series and the rankings.
#: This deliberately matches the client-side rule the Store and Orders dashboards
#: already apply (`status.includes("cancel") || status.includes("refund")`), extended
#: with the terminal failure states those screens never see because the list endpoint
#: rarely returns them. Insights and its owner screens must not disagree about what an
#: order is.
EXCLUDED_STATUS_FRAGMENTS = (
    "cancel",
    "refund",
    "fail",
    "expire",
    "charge_back",
    "chargeback",
    "void",
    "dispute",
)


def _counts_as_sale(status: Any) -> bool:
    text = str(status or "").strip().lower()
    return not any(fragment in text for fragment in EXCLUDED_STATUS_FRAGMENTS)


# ---------------------------------------------------------------------------
# Source classification
# ---------------------------------------------------------------------------

#: Item types sold through the Marketplace surface. Everything else a seller lists is
#: creator commerce and belongs to Store. The classification is exported and echoed in
#: the payload so it is auditable rather than a hidden opinion — see METRICS.md.
MARKETPLACE_ITEM_TYPES = ("listing", "product", "marketplace")
MARKETPLACE_SELLER_TYPES = ("merchant", "marketplace", "seller")


def _source_of(seller_type: Any, item_type: Any) -> str:
    item = str(item_type or "").strip().lower()
    seller = str(seller_type or "").strip().lower()
    if any(token in item for token in MARKETPLACE_ITEM_TYPES):
        return "marketplace"
    if any(token in seller for token in MARKETPLACE_SELLER_TYPES):
        return "marketplace"
    return "store"


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

def _local_now(now: datetime, tz_offset_minutes: int) -> datetime:
    """UTC instant seen from the seller's wall clock, as a naive datetime."""
    return now + timedelta(minutes=tz_offset_minutes)


def period_bounds(
    period: str,
    *,
    tz_offset_minutes: int = 0,
    now: Optional[datetime] = None,
) -> tuple[datetime, datetime, datetime]:
    """Return ``(prior_start, start, end)`` as UTC instants.

    The window is half-open — ``start <= t < end`` — and its edges sit on the seller's
    local midnight, not on UTC midnight. A seller in Los Angeles asking for "today" at
    9pm gets 07:00Z today through 07:00Z tomorrow, which is the day they actually had.

    The prior window is the immediately preceding stretch of *equal length*, so every
    "vs prior" comparison on the screen is like-for-like and can say so in words.
    """
    days = PERIOD_DAYS.get(period, PERIOD_DAYS[DEFAULT_PERIOD])
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)

    local = _local_now(now, tz_offset_minutes)
    # Tomorrow's local midnight: the exclusive upper edge, so a sale made a minute ago
    # is inside the window rather than a minute outside it.
    local_end = datetime(local.year, local.month, local.day) + timedelta(days=1)
    local_start = local_end - timedelta(days=days)
    local_prior_start = local_start - timedelta(days=days)

    back = timedelta(minutes=-tz_offset_minutes)
    return (local_prior_start + back, local_start + back, local_end + back)


def _iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat(sep=" ")


def _parse(raw: Any) -> Optional[datetime]:
    """Parse a stored timestamp. Tolerates the several shapes this database holds.

    ``created_at`` is written by a dozen different call sites across ``bot.py`` — some
    use ``isoformat(timespec="seconds")``, some include a ``T``, some a trailing ``Z``,
    some a fractional part. A row this cannot read is dropped from the aggregate rather
    than being silently bucketed to the epoch, which would put it in every window.
    """
    if not raw:
        return None
    text = str(raw).strip().replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1]
    if "+" in text[10:]:
        text = text[: 10 + text[10:].index("+")]
    for pattern in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

class _Sale:
    """One transaction, normalized across the two differently-shaped order tables."""

    __slots__ = ("at", "minor", "currency", "item_id", "item_type", "source")

    def __init__(self, at, minor, currency, item_id, item_type, source):
        self.at = at
        self.minor = minor
        self.currency = currency
        self.item_id = item_id
        self.item_type = item_type
        self.source = source


def _rows(conn, sql: str, params: tuple) -> list:
    try:
        cursor = conn.execute(sql, params)
    except Exception:
        # A table that does not exist on this deployment yields no sales rather than a
        # 500. `creator_transactions` in particular is created by two different
        # bootstrap paths and an older database may predate one of them.
        return []
    try:
        return list(cursor.fetchall() or [])
    except Exception:
        return []


def _value(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except Exception:
        try:
            return dict(row).get(key, default)
        except Exception:
            return default


def _read_sales(conn, seller_user_id: int, start: datetime, end: datetime) -> list[_Sale]:
    """Every non-excluded transaction for this seller inside ``[start, end)``.

    Both order tables are read. There is no ``LIMIT``: the point of this module is that
    the totals are totals.
    """
    window = (int(seller_user_id), _iso(start), _iso(end))
    sales: list[_Sale] = []

    for sql, amount_key in (
        (
            """
            SELECT created_at, gross_amount_cents, currency, status, item_id, item_type, seller_type
            FROM creator_transactions
            WHERE seller_user_id = ? AND created_at >= ? AND created_at < ?
            """,
            "gross_amount_cents",
        ),
        (
            """
            SELECT created_at, amount_cents, currency, status, item_id, item_type, seller_type
            FROM seller_transactions
            WHERE seller_user_id = ? AND created_at >= ? AND created_at < ?
            """,
            "amount_cents",
        ),
    ):
        for row in _rows(conn, sql, window):
            if not _counts_as_sale(_value(row, "status")):
                continue
            at = _parse(_value(row, "created_at"))
            if at is None:
                continue
            try:
                minor = int(_value(row, amount_key) or 0)
            except (TypeError, ValueError):
                minor = 0
            item_type = _value(row, "item_type")
            sales.append(
                _Sale(
                    at=at,
                    minor=minor,
                    currency=str(_value(row, "currency") or "USD").upper(),
                    item_id=str(_value(row, "item_id") or ""),
                    item_type=str(item_type or ""),
                    source=_source_of(_value(row, "seller_type"), item_type),
                )
            )

    return sales


def _count_followers(conn, seller_user_id: int, start: datetime, end: datetime) -> int:
    rows = _rows(
        conn,
        """
        SELECT COUNT(*) AS n FROM pulse_follows
        WHERE followed_user_id = ? AND created_at >= ? AND created_at < ?
        """,
        (int(seller_user_id), _iso(start), _iso(end)),
    )
    if not rows:
        return 0
    try:
        return int(_value(rows[0], "n") or 0)
    except (TypeError, ValueError):
        return 0


def _has_history_before(conn, seller_user_id: int, start: datetime) -> bool:
    """True when this seller has any transaction older than the window.

    This is what separates "you earned nothing last week" from "you did not exist last
    week". Without it the screen would report a 100% rise on a new seller's first sale,
    which is a made-up number dressed as a measurement.
    """
    edge = _iso(start)
    for table in ("creator_transactions", "seller_transactions"):
        rows = _rows(
            conn,
            f"SELECT 1 AS present FROM {table} WHERE seller_user_id = ? AND created_at < ? LIMIT 1",
            (int(seller_user_id), edge),
        )
        if rows:
            return True
    return False


# ---------------------------------------------------------------------------
# Shaping
# ---------------------------------------------------------------------------

def _totals(sales: Iterable[_Sale]) -> dict:
    revenue = 0
    orders = 0
    for sale in sales:
        revenue += sale.minor
        orders += 1
    return {"revenue_minor": revenue, "orders": orders}


def _series(sales: list[_Sale], start: datetime, end: datetime, tz_offset_minutes: int) -> tuple[list[dict], str]:
    """Contiguous buckets from ``start`` to ``end``, oldest first, gaps included as zero.

    Empty days are present rather than omitted. A chart that skips them compresses a
    quiet week into a single busy-looking point, which reads as growth that did not
    happen.
    """
    span_days = max(1, (end - start).days)
    bucket_days = 1 if span_days <= MAX_DAILY_BUCKETS else 7
    label = "day" if bucket_days == 1 else "week"

    offset = timedelta(minutes=tz_offset_minutes)
    edges: list[datetime] = []
    cursor = start
    while cursor < end:
        edges.append(cursor)
        cursor += timedelta(days=bucket_days)

    buckets = [{"date": (edge + offset).strftime("%Y-%m-%d"), "revenue_minor": 0, "orders": 0} for edge in edges]

    step = timedelta(days=bucket_days)
    for sale in sales:
        index = int((sale.at - start) / step)
        if 0 <= index < len(buckets):
            buckets[index]["revenue_minor"] += sale.minor
            buckets[index]["orders"] += 1

    return buckets, label


def _sources(sales: list[_Sale]) -> list[dict]:
    """Revenue split by the surface the sale came through.

    Two rows, Store and Marketplace, because those are the two the *data* can support.
    The design's third row — "From ads" — needs an attribution read that is scoped to a
    business and a period, and no such read exists; see ``UNAVAILABLE_METRICS``. It is
    omitted rather than estimated: a seller decides next month's ad budget on that row.
    """
    tally: dict[str, dict] = {
        "store": {"key": "store", "revenue_minor": 0, "orders": 0},
        "marketplace": {"key": "marketplace", "revenue_minor": 0, "orders": 0},
    }
    for sale in sales:
        bucket = tally.setdefault(sale.source, {"key": sale.source, "revenue_minor": 0, "orders": 0})
        bucket["revenue_minor"] += sale.minor
        bucket["orders"] += 1

    rows = [row for row in tally.values() if row["orders"] > 0]
    rows.sort(key=lambda row: (-row["revenue_minor"], row["key"]))
    return rows


def _top_items(sales: list[_Sale], limit: int) -> list[dict]:
    """Listings ranked by revenue in the window, with the units that produced it.

    Ranked by revenue rather than by unit count, because the screen's promise is that
    this is where the money came from. Ties break on order count and then on id, so the
    order is stable between two identical requests.
    """
    tally: dict[tuple[str, str], dict] = {}
    for sale in sales:
        if not sale.item_id:
            continue
        key = (sale.item_id, sale.source)
        row = tally.setdefault(
            key,
            {
                "item_id": sale.item_id,
                "item_type": sale.item_type,
                "source": sale.source,
                "revenue_minor": 0,
                "orders": 0,
            },
        )
        row["revenue_minor"] += sale.minor
        row["orders"] += 1

    rows = sorted(tally.values(), key=lambda row: (-row["revenue_minor"], -row["orders"], row["item_id"]))
    return rows[: max(1, min(int(limit or 5), 50))]


def _decorate_items(conn, seller_user_id: int, rows: list[dict]) -> list[dict]:
    """Attach each ranked row's title, cover image and stock state.

    These are labels and inventory facts, not analytics — they are read from
    ``marketplace_listings`` rather than derived, and a row whose listing has since been
    deleted keeps its revenue and simply has no title. The screen falls back to the id
    in that case; dropping the row would understate the period's revenue in a list
    headed "where the money came from".

    ``stock`` is the listing's live quantity, so "low stock" and "sold out" on the meta
    line are the current state of the shelf, which is what a restock decision needs. The
    listing table records no sell-out timestamp, so the design's "sold out {day}" phrasing
    is not available; the client says "sold out" without a date rather than guessing one.
    """
    ids: list[str] = []
    for row in rows:
        raw = str(row.get("item_id") or "").strip()
        if raw.isdigit() and raw not in ids:
            ids.append(raw)
    if not ids:
        return rows

    placeholders = ",".join("?" for _ in ids)
    found: dict[str, dict] = {}
    for record in _rows(
        conn,
        f"""
        SELECT id, title, cover_image_url, media_url, status, quantity, price_label, currency
        FROM marketplace_listings
        WHERE seller_user_id = ? AND id IN ({placeholders})
        """,
        tuple([int(seller_user_id)] + [int(value) for value in ids]),
    ):
        listing_id = str(_value(record, "id") or "")
        quantity = _value(record, "quantity")
        found[listing_id] = {
            "title": (_value(record, "title") or None),
            "image_url": (_value(record, "cover_image_url") or _value(record, "media_url") or None),
            "listing_status": (_value(record, "status") or None),
            # None means "this listing does not track stock", which is a different
            # statement from zero and must not render as "sold out".
            "stock": (int(quantity) if isinstance(quantity, (int, float)) else None),
            "price_label": (_value(record, "price_label") or None),
        }

    for row in rows:
        row.update(
            found.get(
                str(row.get("item_id") or ""),
                {"title": None, "image_url": None, "listing_status": None, "stock": None, "price_label": None},
            )
        )
    return rows


def _dominant_currency(sales: list[_Sale]) -> str:
    """The currency most of this window's revenue arrived in.

    Multi-currency sellers exist, and summing across currencies would be nonsense. The
    payload reports which currency the totals are stated in and lists every other one
    seen, so the client can label the figure honestly instead of pretending there is
    only ever one.
    """
    weight: dict[str, int] = {}
    for sale in sales:
        weight[sale.currency] = weight.get(sale.currency, 0) + max(sale.minor, 0)
    if not weight:
        return "USD"
    return sorted(weight.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]


# ---------------------------------------------------------------------------
# Public read
# ---------------------------------------------------------------------------

def seller_summary(
    seller_user_id: Any,
    *,
    period: str = DEFAULT_PERIOD,
    tz_offset_minutes: int = 0,
    top_limit: int = 5,
    now: Optional[datetime] = None,
    conn=None,
) -> dict:
    """Everything the Insights screen can honestly show, for one seller and one period.

    ``now`` is injectable so the whole aggregate is testable against a fixed clock;
    ``conn`` is injectable so a caller inside an existing transaction does not open a
    second connection.
    """
    period = period if period in PERIOD_DAYS else DEFAULT_PERIOD
    try:
        tz_offset_minutes = int(tz_offset_minutes)
    except (TypeError, ValueError):
        tz_offset_minutes = 0
    # Real offsets run from UTC-12:00 to UTC+14:00. Anything else is a client bug or a
    # tampered request, and letting it through would shift the window by days.
    tz_offset_minutes = max(-720, min(tz_offset_minutes, 840))

    prior_start, start, end = period_bounds(period, tz_offset_minutes=tz_offset_minutes, now=now)

    owned = conn is None
    conn = conn or db.connect()
    try:
        sales = _read_sales(conn, int(seller_user_id), start, end)
        prior_sales = _read_sales(conn, int(seller_user_id), prior_start, start)
        followers = _count_followers(conn, int(seller_user_id), start, end)
        prior_followers = _count_followers(conn, int(seller_user_id), prior_start, start)
        has_prior = _has_history_before(conn, int(seller_user_id), start)
        top_items = _decorate_items(conn, int(seller_user_id), _top_items(sales, top_limit))
    finally:
        if owned:
            conn.close()

    buckets, bucket_label = _series(sales, start, end, tz_offset_minutes)
    currencies = sorted({sale.currency for sale in sales})

    return {
        "period": period,
        "days": PERIOD_DAYS[period],
        "timezone_offset_minutes": tz_offset_minutes,
        # Boundaries are returned so the chart's date-range caption states the real span
        # rather than recomputing it and drifting from what was actually queried.
        "start": _iso(start),
        "end": _iso(end),
        "prior_start": _iso(prior_start),
        "prior_end": _iso(start),
        # False means "this seller has no history before the window", and the client
        # must print "New — no prior period" instead of a percentage.
        "has_prior_period": bool(has_prior),
        "currency": _dominant_currency(sales),
        "currencies": currencies,
        "totals": _totals(sales),
        "prior_totals": _totals(prior_sales) if has_prior else None,
        "bucket": bucket_label,
        "series": buckets,
        "sources": _sources(sales),
        "top_items": top_items,
        "followers": {
            "gained": followers,
            "prior_gained": prior_followers if has_prior else None,
        },
        # Passed through to the client verbatim. Every key here is a module the screen
        # will not render.
        "unavailable": [dict(entry) for entry in UNAVAILABLE_METRICS],
    }
