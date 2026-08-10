"""Ad set layer + campaign detail for the PulseSoc Advertising OS.

An ad set groups the creatives of a campaign under a name, a status, and a
targeting snapshot. Every campaign has exactly one *default* ad set, created
lazily; a creative whose `adset_id` is NULL belongs to that default. The
delivery engine stays campaign-keyed — the only delivery-facing rule an ad set
adds is that creatives in a paused or archived ad set do not serve, which
`pulse_ads_service.select_ads` enforces with a join on this table.

Ownership follows the advertiser portal role model (owner/campaign_manager/
marketing_manager write; analyst/viewer read) via the portal's own helpers, so
this module cannot drift from the permissions the rest of the portal enforces.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from services import db, pulse_ads_os, pulse_ads_service, pulse_advertiser_portal

PulseAdsError = pulse_ads_service.PulseAdsError
now_iso = pulse_ads_service.now_iso
row_to_dict = pulse_ads_service.row_to_dict
clean_text = pulse_ads_service.clean_text
clean_json = pulse_ads_service.clean_json
safe_int = pulse_ads_service.safe_int
audit_log = pulse_ads_service.audit_log

ADSET_STATUSES = {"active", "paused", "archived"}

# Which ad set statuses each action may be applied from. Repeating an action
# that already succeeded (pause while paused, resume while active) is
# idempotent rather than an error, matching CAMPAIGN_TRANSITIONS' stance.
ADSET_TRANSITIONS = {
    "pause": {"active", "paused"},
    "resume": {"paused", "active"},
    "archive": {"active", "paused"},
}

MAX_ADSETS_PER_CAMPAIGN = 20

# A completed or archived campaign is an end state; its ad sets are a record of
# what ran, not a control surface.
LOCKED_CAMPAIGN_STATUSES = {"completed", "archived"}

TARGETING_FIELDS = (
    "countries",
    "languages",
    "min_age",
    "max_age",
    "device_type",
    "interests",
    "keywords",
    "audience_mode",
    "saved_audience_ids",
    "excluded_audience_ids",
)


# ---------------------------------------------------------------------------
# Schema (idempotent; called from bot.init_db and reusable by tests)
# ---------------------------------------------------------------------------

def _add_column_if_missing(conn, table: str, column: str, definition: str) -> None:
    cur = conn.cursor()
    try:
        # Cross-engine introspection (SQLite dev / PostgreSQL prod).
        existing = db.get_table_columns(conn, table)
        if column in existing:
            return
    except Exception:
        # Introspection failed; fall through and let the ALTER itself decide
        # (a duplicate column is swallowed below).
        try:
            conn.rollback()
        except Exception:
            pass
    try:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def _safe_execute(conn, statement: str) -> None:
    cur = conn.cursor()
    try:
        cur.execute(statement)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def ensure_schema(conn) -> None:
    """Create the ad-set table and the columns this slice adds. Idempotent."""
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_ad_adsets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            ad_account_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            targeting_json TEXT DEFAULT '{}',
            is_default INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.commit()
    # NULL adset_id on a creative means "the campaign's default ad set".
    _add_column_if_missing(conn, "pulse_ad_creatives", "adset_id", "INTEGER")
    # Client-supplied draft key: the mobile wizard's autosave retries with the
    # same key and must land on the same draft campaign, never a second one.
    _add_column_if_missing(conn, "pulse_ad_campaigns", "draft_key", "TEXT")
    _safe_execute(conn, "CREATE INDEX IF NOT EXISTS idx_pulse_ad_adsets_campaign ON pulse_ad_adsets(campaign_id, status)")
    _safe_execute(conn, "CREATE INDEX IF NOT EXISTS idx_pulse_ad_creatives_adset ON pulse_ad_creatives(adset_id)")
    # One default ad set per campaign, enforced at the storage layer so a
    # concurrent double-backfill cannot create two.
    _safe_execute(
        conn,
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_pulse_ad_adsets_default ON pulse_ad_adsets(campaign_id) WHERE is_default=1",
    )
    # Draft upsert idempotency: one draft per (account, draft_key).
    _safe_execute(
        conn,
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_pulse_ad_campaigns_draft_key "
        "ON pulse_ad_campaigns(ad_account_id, draft_key) WHERE COALESCE(draft_key,'') != ''",
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _campaign_row(conn, campaign_id) -> dict:
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_campaigns WHERE id=?", (safe_int(campaign_id, minimum=1),))
    campaign = row_to_dict(cur.fetchone())
    if not campaign:
        raise PulseAdsError("Campaign not found.", 404)
    return campaign


def _adset_row(conn, adset_id) -> dict:
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_adsets WHERE id=?", (safe_int(adset_id, minimum=1),))
    adset = row_to_dict(cur.fetchone())
    if not adset:
        raise PulseAdsError("Ad set not found.", 404)
    return adset


def _assert_campaign_editable(campaign: dict) -> None:
    status = clean_text(campaign.get("status"), 40).lower()
    if status in LOCKED_CAMPAIGN_STATUSES:
        raise PulseAdsError(f"This campaign is {status}; its ad sets can no longer be changed.", 409)


def normalize_targeting(payload: dict | None) -> dict:
    """Validate an ad-set targeting payload with the same rules as campaign
    targeting (`pulse_ads_os.put_targeting`), returning the canonical shape."""
    payload = payload or {}
    countries = pulse_ads_os._parse_list(payload.get("countries"), max_items=50, max_len=8, upper=True)
    languages = pulse_ads_os._parse_list(payload.get("languages"), max_items=50, max_len=12, lower=True)
    min_age = safe_int(payload.get("min_age"), 0, 0, 120)
    max_age = safe_int(payload.get("max_age"), 0, 0, 120)
    if min_age and min_age < 13:
        raise PulseAdsError("Ads cannot target users under 13.")
    if min_age and max_age and max_age < min_age:
        raise PulseAdsError("max_age must be greater than or equal to min_age.")
    device_type = clean_text(payload.get("device_type") or "all", 20).lower()
    if device_type not in pulse_ads_os.TARGETING_DEVICE_TYPES:
        raise PulseAdsError("device_type must be one of all, mobile, desktop.")
    audience_mode = clean_text(payload.get("audience_mode") or "everyone", 20).lower()
    if audience_mode not in pulse_ads_os.AUDIENCE_MODES:
        raise PulseAdsError("audience_mode must be one of everyone, followers, non_followers, engaged.")
    return {
        "countries": countries,
        "languages": languages,
        "min_age": min_age or None,
        "max_age": max_age or None,
        "device_type": device_type,
        "interests": pulse_ads_os._parse_list(payload.get("interests"), max_items=50, lower=True),
        "keywords": pulse_ads_os._parse_list(payload.get("keywords"), max_items=50, lower=True),
        "audience_mode": audience_mode,
        "saved_audience_ids": pulse_ads_os._parse_int_list(payload.get("saved_audience_ids")),
        "excluded_audience_ids": pulse_ads_os._parse_int_list(payload.get("excluded_audience_ids")),
    }


def _default_adset_name(targeting: dict) -> str:
    """A readable name from the targeting snapshot: "US, CA — 18-45", "US — 18+",
    or "All" when nothing narrows the audience."""
    countries = [clean_text(item, 8).upper() for item in (targeting.get("countries") or []) if clean_text(item, 8)]
    location = ", ".join(countries[:2])
    if len(countries) > 2:
        location += f" +{len(countries) - 2}"
    min_age = safe_int(targeting.get("min_age"), 0)
    max_age = safe_int(targeting.get("max_age"), 0)
    if min_age and max_age:
        ages = f"{min_age}-{max_age}"
    elif min_age:
        ages = f"{min_age}+"
    elif max_age:
        ages = f"Up to {max_age}"
    else:
        ages = ""
    if location and ages:
        return f"{location} — {ages}"
    if location:
        return location
    if ages:
        return f"All — {ages}"
    return "All"


def _adset_public(row: dict) -> dict:
    item = dict(row or {})
    try:
        targeting = json.loads(item.get("targeting_json") or "{}")
    except Exception:
        targeting = {}
    item["targeting"] = targeting if isinstance(targeting, dict) else {}
    item.pop("targeting_json", None)
    item["is_default"] = bool(safe_int(item.get("is_default"), 0))
    return item


def _empty_metrics() -> dict:
    return {"impressions": 0, "clicks": 0, "spend_cents": 0, "ctr": 0.0}


def _finish_metrics(metrics: dict) -> dict:
    for entry in metrics.values():
        entry["ctr"] = round(entry["clicks"] / entry["impressions"], 4) if entry["impressions"] else 0.0
    return metrics


# ---------------------------------------------------------------------------
# Default ad set backfill
# ---------------------------------------------------------------------------

def ensure_default_adset(conn, campaign_id) -> dict:
    """Every campaign gets one default ad set, lazily, snapshotting the
    campaign's `pulse_ad_targeting` row. Does not commit; callers do."""
    campaign_id = safe_int(campaign_id, minimum=1)
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM pulse_ad_adsets WHERE campaign_id=? AND is_default=1 ORDER BY id ASC LIMIT 1",
        (campaign_id,),
    )
    existing = row_to_dict(cur.fetchone())
    if existing:
        return existing
    campaign = _campaign_row(conn, campaign_id)
    snapshot = pulse_ads_os._targeting_public(campaign_id, pulse_ads_os._targeting_row(conn, campaign_id))
    targeting = {key: snapshot.get(key) for key in TARGETING_FIELDS}
    now = now_iso()
    try:
        cur.execute(
            """
            INSERT INTO pulse_ad_adsets
            (campaign_id, ad_account_id, name, status, targeting_json, is_default, created_at, updated_at)
            VALUES (?, ?, ?, 'active', ?, 1, ?, ?)
            """,
            (
                campaign_id,
                safe_int(campaign.get("ad_account_id")),
                _default_adset_name(targeting),
                clean_json(targeting, 6000),
                now,
                now,
            ),
        )
    except Exception:
        # A concurrent backfill won the unique-default race; read theirs below.
        pass
    cur.execute(
        "SELECT * FROM pulse_ad_adsets WHERE campaign_id=? AND is_default=1 ORDER BY id ASC LIMIT 1",
        (campaign_id,),
    )
    created = row_to_dict(cur.fetchone())
    if not created:
        raise PulseAdsError("Could not create the campaign's default ad set.", 500)
    return created


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _adset_metrics(conn, campaign_id, default_adset_id) -> dict:
    """Per-adset impressions/clicks (delivery tables joined through creatives)
    and spend (posted wallet spend transactions). NULL adset_id buckets into
    the default ad set."""
    cur = conn.cursor()
    default_id = safe_int(default_adset_id, 0)
    metrics: dict[int, dict] = {}

    def bucket(adset_id) -> dict:
        key = safe_int(adset_id, 0) or default_id
        return metrics.setdefault(key, _empty_metrics())

    for table, key in (("pulse_ad_impressions", "impressions"), ("pulse_ad_clicks", "clicks")):
        try:
            cur.execute(
                f"""
                SELECT cr.adset_id AS adset_id, COUNT(*) AS n
                FROM {table} i
                JOIN pulse_ad_creatives cr ON cr.id=i.creative_id
                WHERE i.campaign_id=?
                GROUP BY cr.adset_id
                """,
                (campaign_id,),
            )
            rows = cur.fetchall()
        except Exception:
            rows = []
        for raw in rows:
            row = row_to_dict(raw)
            bucket(row.get("adset_id"))[key] += safe_int(row.get("n"), 0)
    try:
        cur.execute(
            """
            SELECT cr.adset_id AS adset_id, SUM(t.amount_cents) AS cents
            FROM pulse_ad_wallet_transactions t
            LEFT JOIN pulse_ad_creatives cr ON cr.id=t.creative_id
            WHERE t.campaign_id=? AND t.transaction_type='spend' AND t.status='posted'
            GROUP BY cr.adset_id
            """,
            (campaign_id,),
        )
        rows = cur.fetchall()
    except Exception:
        rows = []
    for raw in rows:
        row = row_to_dict(raw)
        bucket(row.get("adset_id"))["spend_cents"] += safe_int(row.get("cents"), 0)
    return _finish_metrics(metrics)


def _creative_metrics(conn, campaign_id) -> dict:
    cur = conn.cursor()
    metrics: dict[int, dict] = {}

    def bucket(creative_id) -> dict:
        return metrics.setdefault(safe_int(creative_id, 0), _empty_metrics())

    for table, key in (("pulse_ad_impressions", "impressions"), ("pulse_ad_clicks", "clicks")):
        try:
            cur.execute(
                f"SELECT creative_id, COUNT(*) AS n FROM {table} WHERE campaign_id=? GROUP BY creative_id",
                (campaign_id,),
            )
            rows = cur.fetchall()
        except Exception:
            rows = []
        for raw in rows:
            row = row_to_dict(raw)
            bucket(row.get("creative_id"))[key] += safe_int(row.get("n"), 0)
    try:
        cur.execute(
            """
            SELECT creative_id, SUM(amount_cents) AS cents
            FROM pulse_ad_wallet_transactions
            WHERE campaign_id=? AND transaction_type='spend' AND status='posted'
            GROUP BY creative_id
            """,
            (campaign_id,),
        )
        rows = cur.fetchall()
    except Exception:
        rows = []
    for raw in rows:
        row = row_to_dict(raw)
        bucket(row.get("creative_id"))["spend_cents"] += safe_int(row.get("cents"), 0)
    return _finish_metrics(metrics)


def _daily_series(conn, campaign_id, days: int = 7) -> list[dict]:
    """Real date-bucketed impressions/clicks/spend for the last `days` days.
    `substr(created_at, 1, 10)` buckets ISO timestamps identically on SQLite
    and PostgreSQL (see pulse_ads_os report queries)."""
    days = safe_int(days, 7, 1, 31)
    today = datetime.now(timezone.utc).date()
    day_keys = [(today - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]
    start = day_keys[0]
    series = {day: {"date": day, "impressions": 0, "clicks": 0, "spend_cents": 0} for day in day_keys}
    cur = conn.cursor()
    for table, key in (("pulse_ad_impressions", "impressions"), ("pulse_ad_clicks", "clicks")):
        try:
            cur.execute(
                f"""
                SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS n
                FROM {table}
                WHERE campaign_id=? AND created_at>=?
                GROUP BY substr(created_at, 1, 10)
                """,
                (campaign_id, start),
            )
            rows = cur.fetchall()
        except Exception:
            rows = []
        for raw in rows:
            row = row_to_dict(raw)
            day = clean_text(row.get("day"), 10)
            if day in series:
                series[day][key] += safe_int(row.get("n"), 0)
    try:
        cur.execute(
            """
            SELECT substr(created_at, 1, 10) AS day, SUM(amount_cents) AS cents
            FROM pulse_ad_wallet_transactions
            WHERE campaign_id=? AND transaction_type='spend' AND status='posted' AND created_at>=?
            GROUP BY substr(created_at, 1, 10)
            """,
            (campaign_id, start),
        )
        rows = cur.fetchall()
    except Exception:
        rows = []
    for raw in rows:
        row = row_to_dict(raw)
        day = clean_text(row.get("day"), 10)
        if day in series:
            series[day]["spend_cents"] += safe_int(row.get("cents"), 0)
    return [series[day] for day in day_keys]


# ---------------------------------------------------------------------------
# Ad set CRUD
# ---------------------------------------------------------------------------

def list_adsets(conn, user_id, campaign_id) -> list[dict]:
    campaign_id = safe_int(campaign_id, minimum=1)
    account_id = pulse_advertiser_portal._campaign_account_id(conn, campaign_id)
    pulse_advertiser_portal._require_account_role(conn, user_id, account_id)
    default = ensure_default_adset(conn, campaign_id)
    conn.commit()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM pulse_ad_adsets WHERE campaign_id=? ORDER BY is_default DESC, id ASC",
        (campaign_id,),
    )
    adsets = [_adset_public(row_to_dict(row)) for row in cur.fetchall()]
    metrics = _adset_metrics(conn, campaign_id, safe_int(default.get("id")))
    for adset in adsets:
        adset["metrics"] = metrics.get(safe_int(adset.get("id")), _empty_metrics())
    return adsets


def create_adset(conn, user_id, campaign_id, payload: dict) -> dict:
    payload = payload or {}
    campaign_id = safe_int(campaign_id, minimum=1)
    account_id = pulse_advertiser_portal._campaign_account_id(conn, campaign_id)
    pulse_advertiser_portal._require_account_role(conn, user_id, account_id, pulse_advertiser_portal.WRITE_ROLES)
    campaign = _campaign_row(conn, campaign_id)
    _assert_campaign_editable(campaign)
    name = clean_text(payload.get("name"), 120)
    if not name:
        raise PulseAdsError("Ad set name is required.")
    status = clean_text(payload.get("status") or "active", 20).lower()
    if status not in {"active", "paused"}:
        raise PulseAdsError("A new ad set must start active or paused.")
    targeting = normalize_targeting(payload.get("targeting") if isinstance(payload.get("targeting"), dict) else {})
    ensure_default_adset(conn, campaign_id)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS n FROM pulse_ad_adsets WHERE campaign_id=?", (campaign_id,))
    if safe_int(row_to_dict(cur.fetchone()).get("n"), 0) >= MAX_ADSETS_PER_CAMPAIGN:
        raise PulseAdsError(f"A campaign can have at most {MAX_ADSETS_PER_CAMPAIGN} ad sets.", 409)
    now = now_iso()
    cur.execute(
        """
        INSERT INTO pulse_ad_adsets
        (campaign_id, ad_account_id, name, status, targeting_json, is_default, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (campaign_id, account_id, name, status, clean_json(targeting, 6000), now, now),
    )
    adset_id = cur.lastrowid
    audit_log(conn, user_id, "ad_adset_created", "pulse_ad_adsets", adset_id, after={"name": name, "campaign_id": campaign_id})
    conn.commit()
    adset = _adset_public(_adset_row(conn, adset_id))
    adset["metrics"] = _empty_metrics()
    return adset


def update_adset(conn, user_id, adset_id, payload: dict) -> dict:
    payload = payload or {}
    before = _adset_row(conn, adset_id)
    account_id = safe_int(before.get("ad_account_id"), minimum=1)
    pulse_advertiser_portal._require_account_role(conn, user_id, account_id, pulse_advertiser_portal.WRITE_ROLES)
    campaign = _campaign_row(conn, before.get("campaign_id"))
    _assert_campaign_editable(campaign)
    name = clean_text(payload.get("name"), 120) if "name" in payload else clean_text(before.get("name"), 120)
    if not name:
        raise PulseAdsError("Ad set name is required.")
    status = clean_text(before.get("status"), 20).lower() or "active"
    if "status" in payload:
        status = clean_text(payload.get("status"), 20).lower()
        if status not in ADSET_STATUSES:
            raise PulseAdsError("Ad set status must be active, paused, or archived.")
        if status == "archived" and safe_int(before.get("is_default"), 0):
            raise PulseAdsError("The default ad set can be paused but not archived.", 409)
    targeting_json = before.get("targeting_json") or "{}"
    if "targeting" in payload:
        targeting = normalize_targeting(payload.get("targeting") if isinstance(payload.get("targeting"), dict) else {})
        targeting_json = clean_json(targeting, 6000)
    cur = conn.cursor()
    cur.execute(
        "UPDATE pulse_ad_adsets SET name=?, status=?, targeting_json=?, updated_at=? WHERE id=?",
        (name, status, targeting_json, now_iso(), safe_int(adset_id, minimum=1)),
    )
    audit_log(
        conn, user_id, "ad_adset_updated", "pulse_ad_adsets", adset_id,
        before={"name": before.get("name"), "status": before.get("status")},
        after={"name": name, "status": status},
    )
    conn.commit()
    return _adset_public(_adset_row(conn, adset_id))


def adset_action(conn, user_id, adset_id, action: str) -> dict:
    action = clean_text(action, 20).lower()
    if action not in ADSET_TRANSITIONS:
        raise PulseAdsError("Unsupported ad set action.")
    before = _adset_row(conn, adset_id)
    account_id = safe_int(before.get("ad_account_id"), minimum=1)
    pulse_advertiser_portal._require_account_role(conn, user_id, account_id, pulse_advertiser_portal.WRITE_ROLES)
    campaign = _campaign_row(conn, before.get("campaign_id"))
    _assert_campaign_editable(campaign)
    current = clean_text(before.get("status"), 20).lower() or "active"
    if current not in ADSET_TRANSITIONS[action]:
        if current == "archived":
            raise PulseAdsError("This ad set is archived and can no longer change state.", 409)
        raise PulseAdsError(f"This ad set is {current} and can't be {action}d from there.", 409)
    if action == "archive" and safe_int(before.get("is_default"), 0):
        raise PulseAdsError("The default ad set can be paused but not archived.", 409)
    new_status = {"pause": "paused", "resume": "active", "archive": "archived"}[action]
    cur = conn.cursor()
    cur.execute(
        "UPDATE pulse_ad_adsets SET status=?, updated_at=? WHERE id=?",
        (new_status, now_iso(), safe_int(adset_id, minimum=1)),
    )
    audit_log(
        conn, user_id, f"ad_adset_{action}", "pulse_ad_adsets", adset_id,
        before={"status": current}, after={"status": new_status},
    )
    conn.commit()
    return {"adset_id": safe_int(adset_id), "status": new_status, "action": action}


def assign_creative(conn, user_id, creative_id, adset_id) -> dict:
    """Move a creative into an ad set (or back to the default with adset_id=0)."""
    creative_id = safe_int(creative_id, minimum=1)
    account_id = pulse_advertiser_portal._creative_account_id(conn, creative_id)
    pulse_advertiser_portal._require_account_role(conn, user_id, account_id, pulse_advertiser_portal.WRITE_ROLES)
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_creatives WHERE id=?", (creative_id,))
    creative = row_to_dict(cur.fetchone())
    campaign = _campaign_row(conn, creative.get("campaign_id"))
    _assert_campaign_editable(campaign)
    target_id = safe_int(adset_id, 0)
    if target_id:
        target = _adset_row(conn, target_id)
        if safe_int(target.get("campaign_id")) != safe_int(creative.get("campaign_id")):
            raise PulseAdsError("That ad set belongs to a different campaign.", 400)
        if clean_text(target.get("status"), 20).lower() == "archived":
            raise PulseAdsError("Creatives can't be assigned to an archived ad set.", 409)
        cur.execute(
            "UPDATE pulse_ad_creatives SET adset_id=?, updated_at=? WHERE id=?",
            (target_id, now_iso(), creative_id),
        )
    else:
        ensure_default_adset(conn, creative.get("campaign_id"))
        cur.execute(
            "UPDATE pulse_ad_creatives SET adset_id=NULL, updated_at=? WHERE id=?",
            (now_iso(), creative_id),
        )
    audit_log(
        conn, user_id, "ad_creative_adset_assigned", "pulse_ad_creatives", creative_id,
        before={"adset_id": creative.get("adset_id")}, after={"adset_id": target_id or None},
    )
    conn.commit()
    return {"creative_id": creative_id, "adset_id": target_id or None}


# ---------------------------------------------------------------------------
# Campaign detail
# ---------------------------------------------------------------------------

# Objectives whose "results" metric is a number this system actually records.
# Anything else has no honest results figure and the field is omitted.
RESULTS_METRIC_BY_OBJECTIVE = {
    "awareness": "impressions",
    "video_views": "impressions",
    "website_traffic": "clicks",
    "marketplace_sales": "clicks",
    "profile_growth": "clicks",
}


def campaign_detail(conn, user_id, campaign_id) -> dict:
    campaign_id = safe_int(campaign_id, minimum=1)
    account_id = pulse_advertiser_portal._campaign_account_id(conn, campaign_id)
    pulse_advertiser_portal._require_account_role(conn, user_id, account_id)
    campaign = _campaign_row(conn, campaign_id)
    status = clean_text(campaign.get("status"), 40).lower() or "draft"
    objective = pulse_ads_service.canonical_objective(campaign.get("objective"))
    budget_type = clean_text(campaign.get("budget_type"), 20).lower() or "daily"
    daily_cents = safe_int(campaign.get("daily_budget_cents"), 0)
    lifetime_cents = safe_int(campaign.get("lifetime_budget_cents"), 0)
    spent_cents = safe_int(campaign.get("spent_cents"), 0)
    budget_cents = lifetime_cents if budget_type == "lifetime" else daily_cents
    try:
        gate = pulse_advertiser_portal.activation_blocker(conn, account_id, campaign)
    except Exception:
        gate = None
    default = ensure_default_adset(conn, campaign_id)
    conn.commit()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM pulse_ad_adsets WHERE campaign_id=? ORDER BY is_default DESC, id ASC",
        (campaign_id,),
    )
    adsets = [_adset_public(row_to_dict(row)) for row in cur.fetchall()]
    adset_metrics = _adset_metrics(conn, campaign_id, safe_int(default.get("id")))
    for adset in adsets:
        adset["metrics"] = adset_metrics.get(safe_int(adset.get("id")), _empty_metrics())
    cur.execute("SELECT * FROM pulse_ad_creatives WHERE campaign_id=? ORDER BY id ASC", (campaign_id,))
    creative_rows = [row_to_dict(row) for row in cur.fetchall()]
    creative_metrics = _creative_metrics(conn, campaign_id)
    creatives = []
    for row in creative_rows:
        try:
            item = pulse_advertiser_portal._creative_public(pulse_ads_service.attach_creative_media(conn, row))
        except Exception:
            item = dict(row)
        item["metrics"] = creative_metrics.get(safe_int(row.get("id")), _empty_metrics())
        creatives.append(item)
    totals = _empty_metrics()
    for entry in creative_metrics.values():
        totals["impressions"] += entry["impressions"]
        totals["clicks"] += entry["clicks"]
        totals["spend_cents"] += entry["spend_cents"]
    totals["ctr"] = round(totals["clicks"] / totals["impressions"], 4) if totals["impressions"] else 0.0
    targeting = pulse_ads_os._targeting_public(campaign_id, pulse_ads_os._targeting_row(conn, campaign_id))
    detail = {
        "campaign": {
            "id": campaign_id,
            "ad_account_id": account_id,
            "campaign_name": clean_text(campaign.get("campaign_name"), 120),
            "status": status,
            "objective": objective,
            "objective_raw": clean_text(campaign.get("objective"), 40),
            "draft_key": clean_text(campaign.get("draft_key"), 160),
            "created_at": campaign.get("created_at") or "",
            "updated_at": campaign.get("updated_at") or "",
        },
        "lifecycle": {
            "status": status,
            "can_edit": status not in LOCKED_CAMPAIGN_STATUSES,
            "blocker": {"code": gate[0], "message": gate[1]} if gate else None,
        },
        "budget": {
            "budget_type": budget_type,
            "daily_budget_cents": daily_cents,
            "lifetime_budget_cents": lifetime_cents,
            "spent_cents": spent_cents,
            "remaining_cents": max(0, budget_cents - spent_cents) if budget_cents else 0,
        },
        "schedule": {
            "start_at": campaign.get("start_at") or "",
            "end_at": campaign.get("end_at") or "",
        },
        "targeting": targeting,
        "placements": pulse_advertiser_portal._campaign_placements(conn, campaign_id),
        "adsets": adsets,
        "creatives": creatives,
        "totals": totals,
        "daily_series": _daily_series(conn, campaign_id, days=7),
    }
    # Estimated results only where the objective maps onto a metric this system
    # genuinely records; otherwise the key is absent rather than invented.
    metric = RESULTS_METRIC_BY_OBJECTIVE.get(objective)
    if metric and totals["impressions"]:
        detail["estimated_results"] = {"metric": metric, "value": totals[metric]}
    return detail
