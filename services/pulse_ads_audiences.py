"""PulseSoc Advertising OS — audience manager (backend slice 2).

Custom audiences built from REAL first-party engagement data, lookalike
audiences with an honest banded estimate, audience detail with campaign
references, and the delivery-time membership evaluator used by
pulse_ads_service.select_ads.

House rules, same as the rest of the ads stack:
- Numbers come from real rows or they are zero. Estimates are recomputed at
  read time from live queries; nothing is materialized into member tables.
- Membership that cannot be evaluated cheaply is treated as NON-matching for
  include lists and MATCHING for exclude lists (fail-safe: when in doubt,
  withhold the ad rather than violate the advertiser's constraint).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services import pulse_ads_os, pulse_ads_service
from services.pulse_ads_service import (
    PulseAdsError,
    audit_log,
    clean_json,
    clean_text,
    now_iso,
    row_to_dict,
    safe_int,
)

# Custom audience kinds. Each maps 1:1 to a real first-party data source that
# already backs the engagement presets in pulse_ads_os._engagement_presets.
# "marketplace_engagers" is an alias of "previous_customers" (same source:
# paid seller_transactions) kept because both names appear in product copy.
CUSTOM_AUDIENCE_SOURCES = {
    "engaged_with_content",
    "video_viewers",
    "marketplace_engagers",
    "previous_customers",
    "profile_engagers",
    "live_engagers",
}

DEFAULT_WINDOW_DAYS = 30
MAX_WINDOW_DAYS = 365
NARROW_THRESHOLD = 1000  # below this the audience is flagged "narrow"
BROAD_SHARE = 0.5        # above this share of the user base -> "broad"
LOOKALIKE_MIN_SEED = 100
LOOKALIKE_MIN_BREADTH = 1
LOOKALIKE_MAX_BREADTH = 20
LOOKALIKE_ROUND_TO = 100  # estimates are floored to bands of this size


def _cutoff_iso(window_days: int) -> str:
    days = safe_int(window_days, DEFAULT_WINDOW_DAYS, 1, MAX_WINDOW_DAYS)
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def validate_custom_definition(kind: str, definition: dict) -> dict:
    """Normalize a custom-audience definition to {source, window_days}."""
    definition = definition if isinstance(definition, dict) else {}
    source = clean_text(definition.get("source") or kind, 40).lower() or kind
    if source != kind:
        raise PulseAdsError("Audience definition source must match the audience kind.")
    window_days = safe_int(definition.get("window_days"), DEFAULT_WINDOW_DAYS, 1, MAX_WINDOW_DAYS)
    return {"source": source, "window_days": window_days}


def _member_sql(source: str, window_days: int) -> tuple[str, tuple]:
    """SQL selecting DISTINCT member user ids (column alias: uid) for a custom
    source, scoped to the account owner (first positional param) and a
    real-time window. Raises PulseAdsError for unknown sources.
    """
    cutoff = _cutoff_iso(window_days)
    if source == "engaged_with_content":
        return (
            """
            SELECT r.user_id AS uid FROM pulse_reactions r
            JOIN pulse_posts p ON p.id=r.post_id
            WHERE p.user_id=:owner AND COALESCE(r.created_at,'') >= :cutoff
            UNION
            SELECT cm.user_id AS uid FROM pulse_comments cm
            JOIN pulse_posts p2 ON p2.id=cm.post_id
            WHERE p2.user_id=:owner AND cm.deleted_at IS NULL AND COALESCE(cm.created_at,'') >= :cutoff
            """,
            cutoff,
        )
    if source == "video_viewers":
        return (
            """
            SELECT DISTINCT vv.viewer_user_id AS uid
            FROM pulse_video_views vv
            JOIN pulse_videos v ON v.id=vv.video_id
            WHERE v.owner_user_id=:owner AND vv.viewer_user_id IS NOT NULL
              AND COALESCE(vv.created_at,'') >= :cutoff
            """,
            cutoff,
        )
    if source == "profile_engagers":
        return (
            """
            SELECT DISTINCT follower_user_id AS uid FROM pulse_follows
            WHERE followed_user_id=:owner AND COALESCE(created_at,'') >= :cutoff
            """,
            cutoff,
        )
    if source in ("marketplace_engagers", "previous_customers"):
        statuses = ", ".join(f"'{status}'" for status in pulse_ads_os.PAID_TRANSACTION_STATUSES)
        return (
            f"""
            SELECT DISTINCT buyer_user_id AS uid FROM seller_transactions
            WHERE seller_user_id=:owner AND item_type='marketplace_product'
              AND LOWER(COALESCE(status,'')) IN ({statuses})
              AND COALESCE(created_at,'') >= :cutoff
            """,
            cutoff,
        )
    if source == "live_engagers":
        return (
            """
            SELECT DISTINCT lv.user_id AS uid
            FROM pulse_live_viewers lv
            JOIN pulse_live_streams ls ON ls.id=lv.live_id
            WHERE ls.creator_user_id=:owner AND COALESCE(lv.user_id, 0) > 0
              AND COALESCE(lv.joined_at,'') >= :cutoff
            """,
            cutoff,
        )
    raise PulseAdsError(f"Unknown custom audience source '{source}'.")


def _custom_member_count(conn, owner_user_id, source: str, window_days: int) -> int:
    """Live COUNT over the real source. Failure (missing table on legacy
    schemas) yields 0 — never a fabricated number.
    """
    try:
        sql, cutoff = _member_sql(source, window_days)
        cur = conn.cursor()
        cur.execute(
            f"SELECT COUNT(DISTINCT uid) AS n FROM ({sql})",
            {"owner": owner_user_id, "cutoff": cutoff},
        )
        return safe_int(row_to_dict(cur.fetchone()).get("n"), 0)
    except PulseAdsError:
        raise
    except Exception:
        return 0


def _custom_member_ids(conn, owner_user_id, source: str, window_days: int) -> list[int]:
    try:
        sql, cutoff = _member_sql(source, window_days)
        cur = conn.cursor()
        cur.execute(
            f"SELECT DISTINCT uid FROM ({sql}) WHERE uid IS NOT NULL",
            {"owner": owner_user_id, "cutoff": cutoff},
        )
        return [safe_int(row_to_dict(row).get("uid"), 0) for row in cur.fetchall() if safe_int(row_to_dict(row).get("uid"), 0) > 0]
    except PulseAdsError:
        raise
    except Exception:
        return []


def _user_base(conn) -> int:
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM users")
        return safe_int(row_to_dict(cur.fetchone()).get("n"), 0)
    except Exception:
        return 0


def _band(matched: int, base: int) -> str:
    if matched < NARROW_THRESHOLD:
        return "narrow"
    if base and matched > base * BROAD_SHARE:
        return "broad"
    return "good"


def _band_warnings(band: str) -> list[str]:
    if band == "narrow":
        return ["This audience is narrow (fewer than 1,000 people). Delivery may be slow or stall."]
    if band == "broad":
        return ["This audience covers more than half of PulseSoc. Consider narrowing it so budget is not spread thin."]
    return []


def _lookalike_estimate(conn, owner_user_id, definition: dict) -> dict:
    """Honest lookalike estimate. Method (documented, never precise):

    1. Resolve the seed audience's real member user ids from its live source.
    2. Collect the distinct (country, preferred_language) profile combos those
       members have in the users table.
    3. Pool = count of ALL users sharing any of those combos, minus the seed
       members themselves (they are already reachable directly).
    4. Estimate = floor(pool * breadth_pct / 100), floored again to the
       nearest 100 so we present a band, not fake precision.

    Any failure (missing seed, legacy schema) yields 0 — never fabricated.
    """
    seed_audience_id = safe_int((definition or {}).get("seed_audience_id"), 0)
    breadth_pct = safe_int((definition or {}).get("breadth_pct"), 5, LOOKALIKE_MIN_BREADTH, LOOKALIKE_MAX_BREADTH)
    empty = {"estimated_size": 0, "band": "narrow", "seed_count": 0}
    if not seed_audience_id:
        return empty
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_saved_audiences WHERE id=?", (seed_audience_id,))
    seed = row_to_dict(cur.fetchone())
    if not seed or seed.get("kind") not in CUSTOM_AUDIENCE_SOURCES:
        return empty
    seed_def = pulse_ads_os._json_dict(seed.get("definition_json"))
    seed_ids = _custom_member_ids(
        conn, owner_user_id, seed.get("kind"),
        safe_int(seed_def.get("window_days"), DEFAULT_WINDOW_DAYS, 1, MAX_WINDOW_DAYS),
    )
    if not seed_ids:
        return empty
    combos: set[str] = set()
    seed_found = 0
    try:
        for start in range(0, len(seed_ids), 500):
            chunk = seed_ids[start:start + 500]
            marks = ",".join("?" for _ in chunk)
            cur.execute(
                f"""
                SELECT UPPER(COALESCE(country,''))||'|'||LOWER(COALESCE(preferred_language,'')) AS combo
                FROM users WHERE user_id IN ({marks})
                """,
                tuple(chunk),
            )
            rows = cur.fetchall()
            seed_found += len(rows)
            combos.update(row_to_dict(row).get("combo") or "|" for row in rows)
        cur.execute(
            """
            SELECT UPPER(COALESCE(country,''))||'|'||LOWER(COALESCE(preferred_language,'')) AS combo,
                   COUNT(*) AS n
            FROM users GROUP BY combo
            """
        )
        pool = 0
        for row in cur.fetchall():
            item = row_to_dict(row)
            if (item.get("combo") or "|") in combos:
                pool += safe_int(item.get("n"), 0)
        pool = max(pool - seed_found, 0)
    except Exception:
        return {"estimated_size": 0, "band": "narrow", "seed_count": len(seed_ids)}
    raw = int(pool * breadth_pct / 100)
    banded = (raw // LOOKALIKE_ROUND_TO) * LOOKALIKE_ROUND_TO
    return {
        "estimated_size": banded,
        "band": _band(banded, _user_base(conn)),
        "seed_count": len(seed_ids),
    }


def estimate_for_audience(conn, owner_user_id, kind: str, definition: dict) -> dict:
    """Read-time estimate for any audience kind. Returns
    {estimated_size, band, warnings}. All numbers are live counts (or 0)."""
    kind = clean_text(kind, 40).lower() or "saved"
    definition = definition if isinstance(definition, dict) else {}
    if kind in CUSTOM_AUDIENCE_SOURCES:
        matched = _custom_member_count(
            conn, owner_user_id, kind,
            safe_int(definition.get("window_days"), DEFAULT_WINDOW_DAYS, 1, MAX_WINDOW_DAYS),
        )
        band = _band(matched, _user_base(conn))
        return {"estimated_size": matched, "band": band, "warnings": _band_warnings(band)}
    if kind == "lookalike":
        result = _lookalike_estimate(conn, owner_user_id, definition)
        return {
            "estimated_size": result["estimated_size"],
            "band": result["band"],
            "warnings": _band_warnings(result["band"]),
        }
    # "saved" (rule-based) audiences reuse the real targeting estimator.
    estimate = pulse_ads_os._estimate_audience(conn, owner_user_id, {
        "countries": pulse_ads_os._parse_list(definition.get("countries"), upper=True),
        "languages": pulse_ads_os._parse_list(definition.get("languages"), lower=True),
        "min_age": safe_int(definition.get("min_age"), 0),
        "max_age": safe_int(definition.get("max_age"), 0),
        "audience_mode": clean_text(definition.get("audience_mode"), 20).lower() or "everyone",
    })
    band = estimate.get("band") or "narrow"
    return {"estimated_size": safe_int(estimate.get("estimated_max"), 0), "band": band, "warnings": _band_warnings(band)}


def _campaign_references(conn, account_id: int, audience_id: int) -> list[dict]:
    """Campaigns on this account whose targeting includes/excludes the
    audience (scan of saved/excluded id lists — one row per campaign)."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.id AS campaign_id, c.campaign_name, c.status,
               t.saved_audience_ids_json, t.excluded_audience_ids_json
        FROM pulse_ad_targeting t
        JOIN pulse_ad_campaigns c ON c.id=t.campaign_id
        WHERE c.ad_account_id=?
        ORDER BY c.id DESC LIMIT 500
        """,
        (account_id,),
    )
    references = []
    for row in cur.fetchall():
        item = row_to_dict(row)
        saved = pulse_ads_os._parse_int_list(pulse_ads_os._json_list(item.get("saved_audience_ids_json")))
        excluded = pulse_ads_os._parse_int_list(pulse_ads_os._json_list(item.get("excluded_audience_ids_json")))
        roles = []
        if audience_id in saved:
            roles.append("included")
        if audience_id in excluded:
            roles.append("excluded")
        if roles:
            references.append({
                "campaign_id": item.get("campaign_id"),
                "campaign_name": item.get("campaign_name") or "",
                "status": item.get("status") or "",
                "roles": roles,
            })
    return references


def audience_detail(conn, user_id, audience_id) -> dict:
    """Full audience detail: definition, live estimate + band warnings, and
    the campaigns whose targeting references it."""
    row = pulse_ads_os._owned_audience(conn, user_id, audience_id)
    public = pulse_ads_os._audience_public(row)
    estimate = estimate_for_audience(conn, user_id, public.get("kind"), public.get("definition"))
    public["estimate"] = {"estimated_size": estimate["estimated_size"], "band": estimate["band"]}
    public["warnings"] = estimate["warnings"]
    public["referenced_by_campaigns"] = _campaign_references(conn, safe_int(row.get("account_id"), 0), audience_id)
    return public


def update_audience(conn, user_id, audience_id, payload: dict) -> dict:
    """Rename / redefine an audience and recompute its estimate from live
    data (the stored estimated_size is never taken from the client)."""
    row = pulse_ads_os._owned_audience(conn, user_id, audience_id)
    if clean_text(row.get("archived_at"), 40):
        raise PulseAdsError("Archived audiences cannot be edited.", 409)
    payload = payload or {}
    name = clean_text(payload.get("name"), 120) if "name" in payload else (row.get("name") or "")
    if not name:
        raise PulseAdsError("Audience name is required.")
    kind = clean_text(row.get("kind"), 40).lower() or "saved"
    definition = payload.get("definition") if isinstance(payload.get("definition"), dict) else pulse_ads_os._json_dict(row.get("definition_json"))
    if kind in CUSTOM_AUDIENCE_SOURCES:
        definition = validate_custom_definition(kind, definition)
    elif kind == "lookalike":
        seed_audience_id = safe_int(definition.get("seed_audience_id"), 0) or safe_int(pulse_ads_os._json_dict(row.get("definition_json")).get("seed_audience_id"), 0)
        definition = {
            "seed_audience_id": seed_audience_id,
            "breadth_pct": safe_int(definition.get("breadth_pct"), 5, LOOKALIKE_MIN_BREADTH, LOOKALIKE_MAX_BREADTH),
        }
    estimate = estimate_for_audience(conn, user_id, kind, definition)
    now = now_iso()
    cur = conn.cursor()
    cur.execute(
        "UPDATE pulse_ad_saved_audiences SET name=?, definition_json=?, estimated_size=?, updated_at=? WHERE id=?",
        (name, clean_json(definition, 6000), estimate["estimated_size"], now, audience_id),
    )
    audit_log(conn, user_id, "ad_audience_updated", "pulse_ad_saved_audiences", audience_id, before={"name": row.get("name")}, after={"name": name})
    conn.commit()
    cur.execute("SELECT * FROM pulse_ad_saved_audiences WHERE id=?", (audience_id,))
    public = pulse_ads_os._audience_public(row_to_dict(cur.fetchone()))
    public["estimate"] = {"estimated_size": estimate["estimated_size"], "band": estimate["band"]}
    public["warnings"] = estimate["warnings"]
    return public


def create_custom_audience(conn, user_id, payload: dict) -> dict:
    """Create a custom audience from a real first-party engagement source.
    Nothing is materialized: the definition {source, window_days} is stored
    and the estimate is recomputed from the live source at read time."""
    payload = payload or {}
    account_id = safe_int(payload.get("account_id"), minimum=1)
    pulse_ads_service._owned_account(conn, user_id, account_id)
    source = clean_text(payload.get("source") or payload.get("kind"), 40).lower()
    if source not in CUSTOM_AUDIENCE_SOURCES:
        raise PulseAdsError(
            "Custom audience source must be one of: " + ", ".join(sorted(CUSTOM_AUDIENCE_SOURCES)) + ".")
    nested = payload.get("definition") if isinstance(payload.get("definition"), dict) else {}
    window_days = payload.get("window_days") or nested.get("window_days") or DEFAULT_WINDOW_DAYS
    definition = validate_custom_definition(source, {"source": source, "window_days": window_days})
    name = clean_text(payload.get("name"), 120)
    if not name:
        raise PulseAdsError("Audience name is required.")
    estimate = estimate_for_audience(conn, user_id, source, definition)
    now = now_iso()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO pulse_ad_saved_audiences
        (account_id, name, kind, definition_json, estimated_size, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (account_id, name, source, clean_json(definition, 6000), estimate["estimated_size"], now, now),
    )
    audience_id = cur.lastrowid
    audit_log(conn, user_id, "ad_audience_created", "pulse_ad_saved_audiences", audience_id, after={"name": name, "kind": source})
    conn.commit()
    cur.execute("SELECT * FROM pulse_ad_saved_audiences WHERE id=?", (audience_id,))
    public = pulse_ads_os._audience_public(row_to_dict(cur.fetchone()))
    public["estimate"] = {"estimated_size": estimate["estimated_size"], "band": estimate["band"]}
    public["warnings"] = estimate["warnings"]
    return public


def create_lookalike(conn, user_id, payload: dict) -> dict:
    """Create a lookalike audience from a custom-audience seed.

    The seed must be a custom audience built from real first-party engagement
    data (its member list is queryable), with at least LOOKALIKE_MIN_SEED
    members. Estimation method is documented on _lookalike_estimate — real
    counts, floored to bands of 100, never fabricated precision.
    """
    payload = payload or {}
    account_id = safe_int(payload.get("account_id"), minimum=1)
    pulse_ads_service._owned_account(conn, user_id, account_id)
    seed_audience_id = safe_int(payload.get("seed_audience_id"), minimum=1)
    seed = pulse_ads_os._owned_audience(conn, user_id, seed_audience_id)
    if safe_int(seed.get("account_id"), 0) != account_id:
        raise PulseAdsError("Seed audience belongs to a different ad account.", 404)
    if clean_text(seed.get("archived_at"), 40):
        raise PulseAdsError("Archived audiences cannot seed a lookalike.", 409)
    seed_kind = clean_text(seed.get("kind"), 40).lower()
    if seed_kind not in CUSTOM_AUDIENCE_SOURCES:
        raise PulseAdsError(
            "Lookalike seeds must be custom audiences built from first-party engagement data.")
    seed_def = pulse_ads_os._json_dict(seed.get("definition_json"))
    seed_ids = _custom_member_ids(
        conn, user_id, seed_kind,
        safe_int(seed_def.get("window_days"), DEFAULT_WINDOW_DAYS, 1, MAX_WINDOW_DAYS),
    )
    if len(seed_ids) < LOOKALIKE_MIN_SEED:
        raise PulseAdsError(
            f"Lookalike seeds need at least {LOOKALIKE_MIN_SEED} members; "
            f"'{seed.get('name')}' currently has {len(seed_ids)}.")
    breadth_pct = safe_int(payload.get("breadth_pct"), 5, LOOKALIKE_MIN_BREADTH, LOOKALIKE_MAX_BREADTH)
    definition = {"seed_audience_id": seed_audience_id, "breadth_pct": breadth_pct}
    estimate = _lookalike_estimate(conn, user_id, definition)
    name = clean_text(payload.get("name"), 120) or clean_text(f"Lookalike of {seed.get('name')}", 120)
    now = now_iso()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO pulse_ad_saved_audiences
        (account_id, name, kind, definition_json, estimated_size, created_at, updated_at)
        VALUES (?, ?, 'lookalike', ?, ?, ?, ?)
        """,
        (account_id, name, clean_json(definition, 2000), estimate["estimated_size"], now, now),
    )
    audience_id = cur.lastrowid
    audit_log(
        conn, user_id, "ad_audience_lookalike_created", "pulse_ad_saved_audiences", audience_id,
        after={"seed_audience_id": seed_audience_id, "breadth_pct": breadth_pct},
    )
    conn.commit()
    cur.execute("SELECT * FROM pulse_ad_saved_audiences WHERE id=?", (audience_id,))
    public = pulse_ads_os._audience_public(row_to_dict(cur.fetchone()))
    band = estimate["band"]
    public["estimate"] = {"estimated_size": estimate["estimated_size"], "band": band}
    public["warnings"] = _band_warnings(band)
    public["seed_count"] = estimate["seed_count"]
    return public


# ---------------------------------------------------------------------------
# Delivery-time membership (used by pulse_ads_service.select_ads)
# ---------------------------------------------------------------------------

def audience_membership(conn, audience_id, viewer_user_id, cache: dict):
    """Cheap per-viewer membership check with per-request caching.

    Returns True / False when membership is decidable with one indexed
    EXISTS query (custom engagement kinds), or None when it is not:
    anonymous viewers, archived audiences, manual 'saved' rule audiences and
    'lookalike' audiences (no per-viewer member list exists). Callers must
    apply the fail-safe: None counts as NON-matching for include lists and
    MATCHING for exclude lists.
    """
    key = ("member", safe_int(audience_id, 0), safe_int(viewer_user_id, 0))
    if key in cache:
        return cache[key]
    result = None
    try:
        if viewer_user_id:
            row_key = ("aud", safe_int(audience_id, 0))
            if row_key in cache:
                row = cache[row_key]
            else:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT sa.kind, sa.definition_json, sa.archived_at, a.owner_user_id
                    FROM pulse_ad_saved_audiences sa
                    JOIN pulse_ad_accounts a ON a.id=sa.account_id
                    WHERE sa.id=?
                    """,
                    (audience_id,),
                )
                row = row_to_dict(cur.fetchone())
                cache[row_key] = row
            if row and not clean_text(row.get("archived_at"), 40):
                kind = clean_text(row.get("kind"), 40).lower()
                if kind in CUSTOM_AUDIENCE_SOURCES:
                    definition = pulse_ads_os._json_dict(row.get("definition_json"))
                    sql, cutoff = _member_sql(
                        kind,
                        safe_int(definition.get("window_days"), DEFAULT_WINDOW_DAYS, 1, MAX_WINDOW_DAYS),
                    )
                    cur = conn.cursor()
                    cur.execute(
                        f"SELECT 1 FROM ({sql}) m WHERE m.uid=:viewer LIMIT 1",
                        {"owner": safe_int(row.get("owner_user_id"), 0), "cutoff": cutoff, "viewer": viewer_user_id},
                    )
                    result = cur.fetchone() is not None
    except Exception:
        result = None
    cache[key] = result
    return result
