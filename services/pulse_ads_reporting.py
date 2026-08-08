"""Advertiser reporting + stored purchase attribution (slice 3).

Every number in a report is derived from a real table:

* impressions / clicks   pulse_ad_impressions / pulse_ad_clicks
* reach                  COUNT(DISTINCT COALESCE(viewer_user_id, session_id));
                         flagged ``reach_estimated`` when a meaningful share of
                         impressions carry no viewer id (anonymous sessions)
* spend                  pulse_ad_wallet_transactions rows with
                         transaction_type='spend' AND status='posted', joined by
                         campaign_id and bucketed by created_at day; the
                         placement is recovered from the description prefix
                         written by pulse_ad_payments.record_spend_event
* results                objective-aware (see RESULTS section below)
* purchases / revenue    stored rows in pulse_ad_attributions (see below)

ATTRIBUTION MODEL — last-click, 7-day post-click window
-------------------------------------------------------
A marketplace purchase is attributed to an ad click when:

1. the click has a real ``viewer_user_id`` (anonymous clicks are never
   attributed),
2. the clicked creative promotes a marketplace listing
   (``content_ref_type='listing'`` with a positive ``content_ref_id``) — this
   covers both marketplace_sales campaigns and any other campaign whose
   creative destination points at a listing,
3. the same user later bought that exact listing: a ``seller_transactions``
   row (the canonical purchase table, the same one the "previous customers"
   engagement preset reads) with ``buyer_user_id = viewer_user_id``,
   ``item_type='marketplace_product'``, ``item_id = content_ref_id`` and a
   paid-like status,
4. the purchase happened within 7 days after the click.

When several clicks qualify for one purchase, the LAST (most recent) click
before the purchase wins. Each purchase is attributed at most once, ever:
rows are stored idempotently in ``pulse_ad_attributions`` with
``UNIQUE(click_id, order_ref)`` plus an order_ref-level guard, so recomputing
never duplicates or reassigns an existing attribution. ROAS is only reported
when spend > 0; nothing is ever fabricated.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services import pulse_ads_service, pulse_advertiser_portal
from services.pulse_ads_service import (
    PulseAdsError,
    canonical_objective,
    clean_text,
    now_iso,
    row_to_dict,
    safe_int,
)

PAID_TRANSACTION_STATUSES = ("paid", "completed", "succeeded", "settled", "released", "delivered")
SPEND_DESCRIPTION_PREFIX = "Ad delivery spend for "
ATTRIBUTION_WINDOW_DAYS = 7
ATTRIBUTION_MODEL = "last_click_7d"
DEFAULT_RANGE_DAYS = 14

REPORT_BREAKDOWNS = {"campaign", "adset", "ad", "creative", "placement", "date", "objective", "audience"}

# Objectives whose honest result metric is a click on the ad.
TRAFFIC_LIKE_OBJECTIVES = {
    "engagement",
    "website_traffic",
    "app_activity",
    "lead_generation",
    "event_promotion",
    "profile_growth",
    "live_promotion",
}
# Share of impressions without a viewer_user_id above which reach is labelled
# an estimate (session ids stand in for people and can overcount).
REACH_ESTIMATED_ANON_SHARE = 0.10


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def ensure_schema(conn) -> None:
    """Create the stored-attribution table. Idempotent, engine-tolerant."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pulse_ad_attributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                creative_id INTEGER,
                click_id INTEGER NOT NULL,
                order_ref TEXT NOT NULL,
                buyer_user_id INTEGER,
                revenue_cents INTEGER DEFAULT 0,
                attributed_at TEXT,
                created_at TEXT,
                UNIQUE(click_id, order_ref)
            )
            """
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_pulse_ad_attributions_campaign ON pulse_ad_attributions(campaign_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_pulse_ad_attributions_order ON pulse_ad_attributions(order_ref)",
    ):
        try:
            cur.execute(statement)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Attribution (compute-on-read, stored idempotently)
# ---------------------------------------------------------------------------

def _parse_ts(value):
    text = clean_text(value, 40)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def attribute_purchases(conn, account_id=0, campaign_id=0) -> dict:
    """Compute and store last-click 7-day purchase attributions.

    Idempotent: an order already attributed (any click) is never re-attributed
    or duplicated, so calling this on every report read is safe. Returns how
    many attributions now exist for the scope and how many this call added.
    """
    ensure_schema(conn)
    account_id = safe_int(account_id, 0)
    campaign_id = safe_int(campaign_id, 0)
    cur = conn.cursor()
    marks = ",".join("?" for _ in PAID_TRANSACTION_STATUSES)
    scope_clause = ""
    scope_params: list = []
    if campaign_id:
        scope_clause = " AND k.campaign_id=?"
        scope_params.append(campaign_id)
    elif account_id:
        scope_clause = " AND c.ad_account_id=?"
        scope_params.append(account_id)
    try:
        cur.execute(
            f"""
            SELECT k.id AS click_id, k.campaign_id, k.creative_id,
                   k.created_at AS clicked_at, k.viewer_user_id,
                   st.id AS txn_id, st.amount_cents, st.created_at AS purchased_at
            FROM pulse_ad_clicks k
            JOIN pulse_ad_campaigns c ON c.id=k.campaign_id
            JOIN pulse_ad_creatives cr ON cr.id=k.creative_id
                 AND cr.content_ref_type='listing' AND COALESCE(cr.content_ref_id, 0) > 0
            JOIN seller_transactions st ON st.buyer_user_id=k.viewer_user_id
                 AND st.item_type='marketplace_product'
                 AND CAST(st.item_id AS INTEGER)=cr.content_ref_id
                 AND LOWER(COALESCE(st.status,'')) IN ({marks})
            WHERE k.viewer_user_id IS NOT NULL {scope_clause}
            ORDER BY k.created_at DESC, k.id DESC
            """,
            (*PAID_TRANSACTION_STATUSES, *scope_params),
        )
        rows = [row_to_dict(row) for row in cur.fetchall()]
    except Exception:
        # seller_transactions may not exist on a deployment without the
        # marketplace; a report with zero purchases is the honest answer.
        return {"attributed": 0, "new": 0, "model": ATTRIBUTION_MODEL}

    inserted = 0
    seen_orders: set = set()
    for row in rows:
        order_ref = str(safe_int(row.get("txn_id")))
        if order_ref in seen_orders:
            continue
        clicked = _parse_ts(row.get("clicked_at"))
        purchased = _parse_ts(row.get("purchased_at"))
        if not clicked or not purchased:
            continue
        if purchased < clicked or purchased > clicked + timedelta(days=ATTRIBUTION_WINDOW_DAYS):
            continue
        # Clicks are iterated most-recent-first, so the first qualifying click
        # for an order IS the last click before (or at) the purchase.
        seen_orders.add(order_ref)
        cur.execute("SELECT id FROM pulse_ad_attributions WHERE order_ref=?", (order_ref,))
        if cur.fetchone():
            continue
        try:
            cur.execute(
                """
                INSERT INTO pulse_ad_attributions
                (campaign_id, creative_id, click_id, order_ref, buyer_user_id,
                 revenue_cents, attributed_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    safe_int(row.get("campaign_id")),
                    safe_int(row.get("creative_id")) or None,
                    safe_int(row.get("click_id")),
                    order_ref,
                    safe_int(row.get("viewer_user_id")) or None,
                    safe_int(row.get("amount_cents"), 0),
                    clean_text(row.get("purchased_at"), 40),
                    now_iso(),
                ),
            )
            inserted += 1
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
    if inserted:
        conn.commit()

    count_clause = ""
    count_params: list = []
    if campaign_id:
        count_clause = "WHERE a.campaign_id=?"
        count_params.append(campaign_id)
    elif account_id:
        count_clause = "JOIN pulse_ad_campaigns c ON c.id=a.campaign_id WHERE c.ad_account_id=?"
        count_params.append(account_id)
    cur.execute(f"SELECT COUNT(*) AS n FROM pulse_ad_attributions a {count_clause}", tuple(count_params))
    total = safe_int(row_to_dict(cur.fetchone()).get("n"), 0)
    return {"attributed": total, "new": inserted, "model": ATTRIBUTION_MODEL}


def _attribution_rows(conn, account_id, start, end, campaign_id=0) -> list[dict]:
    """Stored attributions joined back to their click for placement/day drill.

    Purchases are bucketed on the CLICK day — the day the ad did its work —
    which keeps a purchase and the click that earned it in the same report row.
    """
    attribute_purchases(conn, account_id=account_id, campaign_id=campaign_id)
    cur = conn.cursor()
    clause = ""
    params: list = [account_id]
    if campaign_id:
        clause += " AND a.campaign_id=?"
        params.append(campaign_id)
    if start:
        clause += " AND substr(k.created_at, 1, 10) >= ?"
        params.append(start)
    if end:
        clause += " AND substr(k.created_at, 1, 10) <= ?"
        params.append(end)
    try:
        cur.execute(
            f"""
            SELECT a.campaign_id, a.creative_id, a.revenue_cents,
                   k.placement_key, substr(k.created_at, 1, 10) AS day
            FROM pulse_ad_attributions a
            JOIN pulse_ad_clicks k ON k.id=a.click_id
            JOIN pulse_ad_campaigns c ON c.id=a.campaign_id AND c.ad_account_id=? {clause}
            """,
            tuple(params),
        )
    except Exception:
        return []
    return [
        {
            "campaign_id": safe_int(item.get("campaign_id")),
            "creative_id": safe_int(item.get("creative_id")),
            "placement_key": item.get("placement_key") or "",
            "date": item.get("day") or "",
            "revenue_cents": safe_int(item.get("revenue_cents"), 0),
        }
        for item in (row_to_dict(row) for row in cur.fetchall())
    ]


def attribution_status(conn, user_id, campaign_id) -> dict:
    """Attribution summary for one campaign (lazy compute-on-read)."""
    campaign_id = safe_int(campaign_id, minimum=1)
    account_id = pulse_advertiser_portal._campaign_account_id(conn, campaign_id)
    pulse_advertiser_portal._require_account_role(
        conn, user_id, account_id, pulse_advertiser_portal.ANALYTICS_ROLES
    )
    computed = attribute_purchases(conn, account_id=account_id, campaign_id=campaign_id)
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(revenue_cents), 0) AS revenue FROM pulse_ad_attributions WHERE campaign_id=?",
        (campaign_id,),
    )
    totals = row_to_dict(cur.fetchone())
    cur.execute(
        """
        SELECT COALESCE(SUM(amount_cents), 0) AS spend
        FROM pulse_ad_wallet_transactions
        WHERE campaign_id=? AND transaction_type='spend' AND status='posted'
        """,
        (campaign_id,),
    )
    spend_cents = safe_int(row_to_dict(cur.fetchone()).get("spend"), 0)
    revenue_cents = safe_int(totals.get("revenue"), 0)
    return {
        "campaign_id": campaign_id,
        "model": ATTRIBUTION_MODEL,
        "window_days": ATTRIBUTION_WINDOW_DAYS,
        "purchases": safe_int(totals.get("n"), 0),
        "revenue_cents": revenue_cents,
        "spend_cents": spend_cents,
        "roas": round(revenue_cents / spend_cents, 2) if spend_cents > 0 else None,
        "newly_attributed": safe_int(computed.get("new"), 0),
        "computed_at": now_iso(),
    }


# ---------------------------------------------------------------------------
# Report engine
# ---------------------------------------------------------------------------

def _date_range(params: dict) -> tuple[str, str]:
    start = clean_text(params.get("start") or params.get("from"), 10)
    end = clean_text(params.get("end") or params.get("to"), 10)
    if not start and not end:
        today = datetime.now(timezone.utc).date()
        end = today.isoformat()
        start = (today - timedelta(days=DEFAULT_RANGE_DAYS - 1)).isoformat()
    return start, end


def _date_clause(alias: str, start: str, end: str, extra_campaign: int = 0) -> tuple[str, list]:
    clause = ""
    params: list = []
    if extra_campaign:
        clause += f" AND {alias}.campaign_id=?"
        params.append(extra_campaign)
    if start:
        clause += f" AND substr({alias}.created_at, 1, 10) >= ?"
        params.append(start)
    if end:
        clause += f" AND substr({alias}.created_at, 1, 10) <= ?"
        params.append(end)
    return clause, params


def _campaign_meta(conn, account_id) -> dict:
    cur = conn.cursor()
    cur.execute(
        "SELECT id, campaign_name, objective FROM pulse_ad_campaigns WHERE ad_account_id=?",
        (account_id,),
    )
    meta = {}
    for row in cur.fetchall():
        item = row_to_dict(row)
        meta[safe_int(item.get("id"))] = {
            "name": item.get("campaign_name") or f"Campaign {item.get('id')}",
            "objective": canonical_objective(item.get("objective")),
        }
    return meta


def _creative_maps(conn, account_id) -> tuple[dict, dict]:
    """(creative_id -> label, creative_id -> {adset_id, campaign_id})."""
    cur = conn.cursor()
    labels: dict = {}
    adsets: dict = {}
    try:
        cur.execute(
            "SELECT id, title, campaign_id, adset_id FROM pulse_ad_creatives WHERE ad_account_id=?",
            (account_id,),
        )
        rows = [row_to_dict(row) for row in cur.fetchall()]
    except Exception:
        cur.execute("SELECT id, title, campaign_id FROM pulse_ad_creatives WHERE ad_account_id=?", (account_id,))
        rows = [row_to_dict(row) for row in cur.fetchall()]
    for item in rows:
        creative_id = safe_int(item.get("id"))
        labels[creative_id] = item.get("title") or f"Creative {creative_id}"
        adsets[creative_id] = {
            "adset_id": safe_int(item.get("adset_id"), 0),
            "campaign_id": safe_int(item.get("campaign_id"), 0),
        }
    return labels, adsets


def _adset_labels(conn, account_id) -> dict:
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, name FROM pulse_ad_adsets WHERE ad_account_id=?", (account_id,))
        return {safe_int(row_to_dict(row).get("id")): row_to_dict(row).get("name") or "" for row in cur.fetchall()}
    except Exception:
        return {}


def _audience_modes(conn, account_id) -> dict:
    """campaign_id -> audience_mode (default 'everyone')."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT t.campaign_id, t.audience_mode FROM pulse_ad_targeting t
            JOIN pulse_ad_campaigns c ON c.id=t.campaign_id WHERE c.ad_account_id=?
            """,
            (account_id,),
        )
        return {
            safe_int(item.get("campaign_id")): clean_text(item.get("audience_mode"), 20).lower() or "everyone"
            for item in (row_to_dict(row) for row in cur.fetchall())
        }
    except Exception:
        return {}


def _spend_rows(conn, account_id, start, end, campaign_id=0) -> list[dict]:
    cur = conn.cursor()
    clause, params = _date_clause("t", start, end, campaign_id)
    cur.execute(
        f"""
        SELECT t.campaign_id, t.creative_id, t.amount_cents, t.description,
               substr(t.created_at, 1, 10) AS day
        FROM pulse_ad_wallet_transactions t
        WHERE t.account_id=? AND t.transaction_type='spend' AND t.status='posted' {clause}
        """,
        (account_id, *params),
    )
    rows = []
    for raw in cur.fetchall():
        row = row_to_dict(raw)
        description = clean_text(row.get("description"), 240)
        placement = ""
        if description.startswith(SPEND_DESCRIPTION_PREFIX):
            placement = description[len(SPEND_DESCRIPTION_PREFIX):]
        rows.append({
            "campaign_id": safe_int(row.get("campaign_id")),
            "creative_id": safe_int(row.get("creative_id")),
            "placement_key": placement,
            "date": row.get("day") or "",
            "amount_cents": safe_int(row.get("amount_cents"), 0),
        })
    return rows


def _delivery_aggregates(conn, account_id, table: str, start, end, campaign_id=0) -> list[dict]:
    """Per (campaign, creative, placement, day) counts + anonymous share."""
    cur = conn.cursor()
    clause, params = _date_clause("i", start, end, campaign_id)
    cur.execute(
        f"""
        SELECT i.campaign_id, i.creative_id, i.placement_key,
               substr(i.created_at, 1, 10) AS day, COUNT(*) AS n,
               SUM(CASE WHEN i.viewer_user_id IS NULL THEN 1 ELSE 0 END) AS anon
        FROM {table} i
        JOIN pulse_ad_campaigns c ON c.id=i.campaign_id AND c.ad_account_id=? {clause}
        GROUP BY i.campaign_id, i.creative_id, i.placement_key, day
        """,
        (account_id, *params),
    )
    return [row_to_dict(row) for row in cur.fetchall()]


def _reach_by_expr(conn, account_id, key_expr: str, start, end, campaign_id=0, join_creatives=False) -> dict:
    cur = conn.cursor()
    clause, params = _date_clause("i", start, end, campaign_id)
    creative_join = "LEFT JOIN pulse_ad_creatives cr ON cr.id=i.creative_id" if join_creatives else ""
    try:
        cur.execute(
            f"""
            SELECT {key_expr} AS k,
                   COUNT(DISTINCT COALESCE(i.viewer_user_id, i.session_id)) AS reach
            FROM pulse_ad_impressions i
            JOIN pulse_ad_campaigns c ON c.id=i.campaign_id AND c.ad_account_id=?
            {creative_join}
            WHERE 1=1 {clause}
            GROUP BY k
            """,
            (account_id, *params),
        )
        return {row_to_dict(row).get("k"): safe_int(row_to_dict(row).get("reach"), 0) for row in cur.fetchall()}
    except Exception:
        return {}


def _total_reach(conn, account_id, start, end, campaign_id=0) -> int:
    cur = conn.cursor()
    clause, params = _date_clause("i", start, end, campaign_id)
    try:
        cur.execute(
            f"""
            SELECT COUNT(DISTINCT COALESCE(i.viewer_user_id, i.session_id)) AS reach
            FROM pulse_ad_impressions i
            JOIN pulse_ad_campaigns c ON c.id=i.campaign_id AND c.ad_account_id=?
            WHERE 1=1 {clause}
            """,
            (account_id, *params),
        )
        return safe_int(row_to_dict(cur.fetchone()).get("reach"), 0)
    except Exception:
        return 0


def _event_counts(conn, account_id, event_types: tuple, start, end, campaign_id=0) -> list[dict]:
    """Per (campaign, creative, day) counts of recorded ad events.

    pulse_ad_events carries only campaign_id, creative_id, event_type,
    metadata_json and created_at — no placement or viewer dimension — so
    event-based results can be broken down by campaign/ad-set/creative/date
    but honestly cannot be split by placement.
    """
    cur = conn.cursor()
    clause, params = _date_clause("e", start, end, campaign_id)
    marks = ",".join("?" for _ in event_types)
    try:
        cur.execute(
            f"""
            SELECT e.campaign_id, e.creative_id, substr(e.created_at, 1, 10) AS day, COUNT(*) AS n
            FROM pulse_ad_events e
            JOIN pulse_ad_campaigns c ON c.id=e.campaign_id AND c.ad_account_id=?
            WHERE e.event_type IN ({marks}) {clause}
            GROUP BY e.campaign_id, e.creative_id, day
            """,
            (account_id, *event_types, *params),
        )
        return [row_to_dict(row) for row in cur.fetchall()]
    except Exception:
        return []


def _results_rule(objective: str) -> tuple[str, bool]:
    """(metric_name, available) for a canonical objective."""
    if objective in TRAFFIC_LIKE_OBJECTIVES:
        return "clicks", True
    if objective == "video_views":
        return "video_start_events", True
    if objective == "marketplace_sales":
        return "purchases", True
    if objective == "messages":
        return "message_events", True
    # awareness (and anything unmapped): impressions are reported as their own
    # column already; inventing a "result" for them would be double-counting.
    return "", False


def _zero_row(key, label) -> dict:
    return {
        "key": key,
        "label": label,
        "campaign_id": None,
        "adset_id": None,
        "creative_id": None,
        "placement_key": None,
        "spend_cents": 0,
        "impressions": 0,
        "reach": 0,
        "reach_estimated": False,
        "frequency": 0,
        "clicks": 0,
        "ctr": 0,
        "cpc_cents": 0,
        "results": 0,
        "results_metric": "",
        "results_available": False,
        "cost_per_result_cents": 0,
        "purchases": 0,
        "revenue_cents": 0,
        "roas": 0,
    }


def build_report(conn, user_id, account_id, params: dict = None) -> dict:
    """Flat, CSV-able performance report over real delivery tables.

    ``params``: start/end (ISO dates, default trailing 14 days; ``from``/``to``
    accepted as aliases), breakdown (campaign | adset | ad | creative |
    placement | date | objective | audience), optional campaign_id filter.
    """
    params = params or {}
    account_id = safe_int(account_id, minimum=1)
    pulse_advertiser_portal._require_account_role(
        conn, user_id, account_id, pulse_advertiser_portal.ANALYTICS_ROLES
    )
    breakdown = clean_text(params.get("breakdown") or "campaign", 20).lower()
    if breakdown not in REPORT_BREAKDOWNS:
        raise PulseAdsError(
            "breakdown must be one of campaign, adset, ad, creative, placement, date, objective, audience."
        )
    if breakdown == "ad":
        breakdown = "creative"
    start, end = _date_range(params)
    campaign_filter = safe_int(params.get("campaign_id"), 0)
    if campaign_filter:
        if pulse_advertiser_portal._campaign_account_id(conn, campaign_filter) != account_id:
            raise PulseAdsError("Campaign not found.", 404)

    ensure_schema(conn)
    meta = _campaign_meta(conn, account_id)
    creative_labels, creative_adsets = _creative_maps(conn, account_id)
    adset_labels = _adset_labels(conn, account_id)
    audience_modes = _audience_modes(conn, account_id)

    impressions = _delivery_aggregates(conn, account_id, "pulse_ad_impressions", start, end, campaign_filter)
    clicks = _delivery_aggregates(conn, account_id, "pulse_ad_clicks", start, end, campaign_filter)
    spend = _spend_rows(conn, account_id, start, end, campaign_filter)
    purchases = _attribution_rows(conn, account_id, start, end, campaign_filter)
    video_events = _event_counts(conn, account_id, ("video_start",), start, end, campaign_filter)
    message_events = _event_counts(
        conn, account_id, ("message", "message_started", "messaging_conversation_started"),
        start, end, campaign_filter,
    )
    campaigns_with_message_events = {safe_int(item.get("campaign_id")) for item in message_events}

    def adset_key_for(creative_id, campaign_id):
        info = creative_adsets.get(safe_int(creative_id)) or {}
        adset_id = safe_int(info.get("adset_id"), 0)
        return adset_id if adset_id else f"default:{safe_int(campaign_id)}"

    def report_key(campaign_id, creative_id, placement_key, day):
        campaign_id = safe_int(campaign_id)
        if breakdown == "campaign":
            return campaign_id
        if breakdown == "creative":
            return safe_int(creative_id)
        if breakdown == "adset":
            return adset_key_for(creative_id, campaign_id)
        if breakdown == "placement":
            return placement_key or "unknown"
        if breakdown == "date":
            return day or "unknown"
        if breakdown == "audience":
            return audience_modes.get(campaign_id, "everyone")
        return (meta.get(campaign_id) or {}).get("objective", "awareness")

    rows: dict = {}
    row_objectives: dict = {}
    row_anon: dict = {}

    def row_for(key):
        if key not in rows:
            row = _zero_row(key, str(key))
            if breakdown == "campaign":
                row["label"] = (meta.get(key) or {}).get("name", f"Campaign {key}")
                row["campaign_id"] = safe_int(key)
            elif breakdown == "creative":
                row["label"] = creative_labels.get(key, f"Creative {key}")
                row["creative_id"] = safe_int(key)
            elif breakdown == "adset":
                if isinstance(key, str) and key.startswith("default:"):
                    campaign_id = safe_int(key.split(":", 1)[1])
                    name = (meta.get(campaign_id) or {}).get("name", f"Campaign {campaign_id}")
                    row["label"] = f"Default ad set — {name}"
                    row["campaign_id"] = campaign_id
                else:
                    row["label"] = adset_labels.get(safe_int(key)) or f"Ad set {key}"
                    row["adset_id"] = safe_int(key)
            elif breakdown == "placement":
                row["placement_key"] = key if key != "unknown" else None
            rows[key] = row
            row_objectives[key] = set()
            row_anon[key] = 0
        return rows[key]

    def note_objective(key, campaign_id):
        row_objectives[key].add((meta.get(safe_int(campaign_id)) or {}).get("objective", "awareness"))

    for agg in impressions:
        campaign_id = safe_int(agg.get("campaign_id"))
        key = report_key(campaign_id, agg.get("creative_id"), agg.get("placement_key") or "", agg.get("day") or "")
        row = row_for(key)
        row["impressions"] += safe_int(agg.get("n"), 0)
        row_anon[key] += safe_int(agg.get("anon"), 0)
        note_objective(key, campaign_id)

    for agg in clicks:
        campaign_id = safe_int(agg.get("campaign_id"))
        key = report_key(campaign_id, agg.get("creative_id"), agg.get("placement_key") or "", agg.get("day") or "")
        row = row_for(key)
        count = safe_int(agg.get("n"), 0)
        row["clicks"] += count
        note_objective(key, campaign_id)
        objective = (meta.get(campaign_id) or {}).get("objective", "awareness")
        if objective in TRAFFIC_LIKE_OBJECTIVES:
            row["results"] += count

    for item in video_events:
        campaign_id = safe_int(item.get("campaign_id"))
        objective = (meta.get(campaign_id) or {}).get("objective", "awareness")
        if objective != "video_views":
            continue
        if breakdown == "placement":
            continue  # events carry no placement dimension; never invent one
        key = report_key(campaign_id, item.get("creative_id"), "", item.get("day") or "")
        row_for(key)["results"] += safe_int(item.get("n"), 0)
        note_objective(key, campaign_id)

    for item in message_events:
        campaign_id = safe_int(item.get("campaign_id"))
        objective = (meta.get(campaign_id) or {}).get("objective", "awareness")
        if objective != "messages" or breakdown == "placement":
            continue
        key = report_key(campaign_id, item.get("creative_id"), "", item.get("day") or "")
        row_for(key)["results"] += safe_int(item.get("n"), 0)
        note_objective(key, campaign_id)

    for item in spend:
        key = report_key(item["campaign_id"], item["creative_id"], item["placement_key"], item["date"])
        row = row_for(key)
        row["spend_cents"] += item["amount_cents"]
        note_objective(key, item["campaign_id"])

    for item in purchases:
        key = report_key(item["campaign_id"], item["creative_id"], item["placement_key"], item["date"])
        row = row_for(key)
        row["purchases"] += 1
        row["revenue_cents"] += item["revenue_cents"]
        note_objective(key, item["campaign_id"])
        objective = (meta.get(item["campaign_id"]) or {}).get("objective", "awareness")
        if objective == "marketplace_sales":
            row["results"] += 1

    # Reach per key straight from the impressions table (never summed guesses,
    # except objective/audience keys which aggregate per-campaign distincts —
    # noted in metadata).
    key_exprs = {
        "campaign": ("i.campaign_id", False),
        "creative": ("i.creative_id", False),
        "adset": ("COALESCE(cr.adset_id, -i.campaign_id)", True),
        "placement": ("i.placement_key", False),
        "date": ("substr(i.created_at, 1, 10)", False),
        "objective": ("i.campaign_id", False),
        "audience": ("i.campaign_id", False),
    }
    expr, join_creatives = key_exprs[breakdown]
    raw_reach = _reach_by_expr(conn, account_id, expr, start, end, campaign_filter, join_creatives)
    reach_by_key: dict = {}
    if breakdown in {"objective", "audience"}:
        for campaign_id, value in raw_reach.items():
            campaign_id = safe_int(campaign_id)
            if breakdown == "objective":
                bucket = (meta.get(campaign_id) or {}).get("objective", "awareness")
            else:
                bucket = audience_modes.get(campaign_id, "everyone")
            reach_by_key[bucket] = reach_by_key.get(bucket, 0) + value
    elif breakdown == "adset":
        for raw_key, value in raw_reach.items():
            numeric = safe_int(raw_key)
            key = numeric if numeric > 0 else f"default:{-numeric}"
            reach_by_key[key] = reach_by_key.get(key, 0) + value
    else:
        reach_by_key = dict(raw_reach)

    output = []
    for key in sorted(rows, key=lambda value: str(value)):
        row = rows[key]
        objectives = row_objectives.get(key) or set()
        metrics = {_results_rule(objective) for objective in objectives}
        available = any(flag for _, flag in metrics)
        names = sorted({name for name, flag in metrics if flag})
        row["results_available"] = available
        row["results_metric"] = names[0] if len(names) == 1 else ("mixed" if names else "")
        row["reach"] = safe_int(reach_by_key.get(key), 0)
        if row["impressions"]:
            row["reach_estimated"] = row_anon.get(key, 0) >= row["impressions"] * REACH_ESTIMATED_ANON_SHARE and row_anon.get(key, 0) > 0
        if row["reach"]:
            row["frequency"] = round(row["impressions"] / row["reach"], 2)
        if row["impressions"]:
            row["ctr"] = round(row["clicks"] / row["impressions"], 4)
        if row["clicks"] and row["spend_cents"]:
            row["cpc_cents"] = row["spend_cents"] // row["clicks"]
        if row["results"]:
            row["cost_per_result_cents"] = row["spend_cents"] // row["results"]
        if row["spend_cents"] > 0:
            row["roas"] = round(row["revenue_cents"] / row["spend_cents"], 2)
        output.append(row)

    totals = _zero_row("totals", "Totals")
    totals.pop("campaign_id", None), totals.pop("adset_id", None)
    totals.pop("creative_id", None), totals.pop("placement_key", None)
    anon_total = 0
    for row in output:
        for field in ("spend_cents", "impressions", "clicks", "results", "purchases", "revenue_cents"):
            totals[field] += row[field]
    for key in rows:
        anon_total += row_anon.get(key, 0)
    totals["results_available"] = any(row["results_available"] for row in output)
    totals["results_metric"] = "mixed" if len({row["results_metric"] for row in output if row["results_metric"]}) > 1 else next(
        (row["results_metric"] for row in output if row["results_metric"]), ""
    )
    totals["reach"] = _total_reach(conn, account_id, start, end, campaign_filter)
    if totals["impressions"]:
        totals["reach_estimated"] = anon_total >= totals["impressions"] * REACH_ESTIMATED_ANON_SHARE and anon_total > 0
    if totals["reach"]:
        totals["frequency"] = round(totals["impressions"] / totals["reach"], 2)
    if totals["impressions"]:
        totals["ctr"] = round(totals["clicks"] / totals["impressions"], 4)
    if totals["clicks"] and totals["spend_cents"]:
        totals["cpc_cents"] = totals["spend_cents"] // totals["clicks"]
    if totals["results"]:
        totals["cost_per_result_cents"] = totals["spend_cents"] // totals["results"]
    if totals["spend_cents"] > 0:
        totals["roas"] = round(totals["revenue_cents"] / totals["spend_cents"], 2)

    return {
        "rows": output,
        "totals": totals,
        "breakdown": breakdown,
        "start": start,
        "end": end,
        "metadata": {
            "start": start,
            "end": end,
            "breakdown": breakdown,
            "campaign_id": campaign_filter or None,
            "attribution": {
                "model": ATTRIBUTION_MODEL,
                "window_days": ATTRIBUTION_WINDOW_DAYS,
                "note": (
                    "Purchases are attributed to the last qualifying ad click within "
                    "7 days, stored idempotently, and bucketed on the click day. "
                    "ROAS is reported only where spend > 0."
                ),
            },
            "notes": [
                "reach counts distinct viewers (session ids stand in for anonymous viewers; "
                "rows are flagged reach_estimated when anonymous impressions are significant).",
                "objective/audience reach sums per-campaign distinct counts and can overcount "
                "a viewer who saw multiple campaigns in the same bucket.",
                "event-based results (video/messages) carry no placement dimension and are "
                "omitted from the placement breakdown rather than invented.",
            ],
            "generated_at": now_iso(),
        },
    }
