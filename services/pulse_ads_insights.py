"""Rule-based advertiser insights with approve-to-apply actions (slice 3).

Every recommendation is computed from real aggregates in the delivery and
ledger tables — nothing is modelled, sampled or invented. Each item carries the
actual numbers that triggered it, a whitelisted machine-applyable action, and
``requires_approval: True``: nothing here mutates anything until the advertiser
explicitly approves via ``apply_insight`` (which recomputes and rejects stale
suggestions).
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

from services import pulse_ad_payments, pulse_ads_service, pulse_advertiser_portal
from services.pulse_ads_service import (
    PulseAdsError,
    canonical_objective,
    clean_text,
    now_iso,
    row_to_dict,
    safe_int,
)
from services.pulse_ads_reporting import SPEND_DESCRIPTION_PREFIX

# Thresholds — every rule states its trigger with real numbers in `why`.
PLACEMENT_MIN_IMPRESSIONS = 200
PLACEMENT_MIN_CLICKS = 10
PLACEMENT_CPC_ADVANTAGE = 0.7    # placement CPC <= 70% of campaign CPC
PLACEMENT_CTR_ADVANTAGE = 1.3    # or placement CTR >= 130% of campaign CTR
SATURATION_FREQUENCY = 3.5       # trailing-7d impressions per reached viewer
SATURATION_MIN_REACH = 10
HIGH_CPC_MIN_CLICKS = 20
HIGH_CPC_MULTIPLE = 2.0          # campaign CPC > 2x account median CPC
FATIGUE_MIN_IMPRESSIONS = 200    # per 3-day window
FATIGUE_CTR_RATIO = 0.6          # trailing-3d CTR < 60% of first-3d CTR
STRONG_MIN_IMPRESSIONS = 200
STRONG_CTR_MULTIPLE = 1.5
BUDGET_PAUSE_DAYS_THRESHOLD = 3  # daily-limit pauses on >= 3 of the last 7 days
EXPANSION_MIN_AGE_DAYS = 3
EXPANSION_LOW_IMPRESSIONS_7D = 50

APPLY_WHITELIST = {
    "pause_campaign",
    "focus_placement",
    "archive_creative",
    "increase_daily_budget",
    "raise_lifetime_budget",
    "expand_audience",
}


def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


def _campaigns(conn, account_id) -> list[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_campaigns WHERE ad_account_id=?", (account_id,))
    return [row_to_dict(row) for row in cur.fetchall()]


def _spend_by_campaign_placement(conn, account_id) -> dict:
    """{(campaign_id, placement_key): cents} from posted spend rows."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT campaign_id, description, SUM(amount_cents) AS cents
        FROM pulse_ad_wallet_transactions
        WHERE account_id=? AND transaction_type='spend' AND status='posted'
        GROUP BY campaign_id, description
        """,
        (account_id,),
    )
    out: dict = {}
    for raw in cur.fetchall():
        row = row_to_dict(raw)
        description = clean_text(row.get("description"), 240)
        placement = ""
        if description.startswith(SPEND_DESCRIPTION_PREFIX):
            placement = description[len(SPEND_DESCRIPTION_PREFIX):]
        key = (safe_int(row.get("campaign_id")), placement)
        out[key] = out.get(key, 0) + safe_int(row.get("cents"), 0)
    return out


def _counts_by_campaign_placement(conn, account_id, table: str) -> dict:
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT i.campaign_id, i.placement_key, COUNT(*) AS n
        FROM {table} i JOIN pulse_ad_campaigns c ON c.id=i.campaign_id AND c.ad_account_id=?
        GROUP BY i.campaign_id, i.placement_key
        """,
        (account_id,),
    )
    return {
        (safe_int(item.get("campaign_id")), item.get("placement_key") or ""): safe_int(item.get("n"), 0)
        for item in (row_to_dict(row) for row in cur.fetchall())
    }


def build_insights(conn, user_id, account_id) -> dict:
    """Recommendations from real aggregates only. Advisory until applied."""
    account_id = safe_int(account_id, minimum=1)
    pulse_advertiser_portal._require_account_role(
        conn, user_id, account_id, pulse_advertiser_portal.ANALYTICS_ROLES
    )
    cur = conn.cursor()
    recommendations: list[dict] = []
    campaigns = _campaigns(conn, account_id)
    campaign_by_id = {safe_int(c.get("id")): c for c in campaigns}
    active = [c for c in campaigns if c.get("status") == "active"]
    cutoff_3d, cutoff_7d = _cutoff(3), _cutoff(7)

    impressions_cp = _counts_by_campaign_placement(conn, account_id, "pulse_ad_impressions")
    clicks_cp = _counts_by_campaign_placement(conn, account_id, "pulse_ad_clicks")
    spend_cp = _spend_by_campaign_placement(conn, account_id)
    total_impressions = sum(impressions_cp.values())
    total_clicks = sum(clicks_cp.values())

    def add(item: dict) -> None:
        item["requires_approval"] = True
        recommendations.append(item)

    # -- placement_opportunity ---------------------------------------------
    for campaign in active:
        campaign_id = safe_int(campaign.get("id"))
        name = campaign.get("campaign_name") or f"Campaign {campaign_id}"
        c_impr = sum(n for (cid, _), n in impressions_cp.items() if cid == campaign_id)
        c_clicks = sum(n for (cid, _), n in clicks_cp.items() if cid == campaign_id)
        c_spend = sum(n for (cid, _), n in spend_cp.items() if cid == campaign_id)
        if not c_impr:
            continue
        c_ctr = c_clicks / c_impr
        c_cpc = (c_spend / c_clicks) if c_clicks else 0
        placements = {p for (cid, p), n in impressions_cp.items() if cid == campaign_id and p}
        for placement in sorted(placements):
            p_impr = impressions_cp.get((campaign_id, placement), 0)
            if p_impr < PLACEMENT_MIN_IMPRESSIONS:
                continue
            p_clicks = clicks_cp.get((campaign_id, placement), 0)
            if p_clicks < PLACEMENT_MIN_CLICKS:
                continue
            p_ctr = p_clicks / p_impr
            p_spend = spend_cp.get((campaign_id, placement), 0)
            p_cpc = (p_spend / p_clicks) if p_clicks else 0
            cpc_better = bool(p_cpc and c_cpc and p_cpc <= c_cpc * PLACEMENT_CPC_ADVANTAGE)
            ctr_better = bool(c_ctr and p_ctr >= c_ctr * PLACEMENT_CTR_ADVANTAGE)
            if not (cpc_better or ctr_better):
                continue
            why = (
                f"In '{name}', placement {placement} has {p_impr} impressions, {p_clicks} clicks "
                f"(CTR {p_ctr:.2%} vs campaign {c_ctr:.2%}"
            )
            if p_cpc and c_cpc:
                why += f"; CPC {p_cpc / 100:.2f} vs campaign {c_cpc / 100:.2f}"
            why += ")."
            add({
                "id": f"placement_opportunity:{campaign_id}:{placement}",
                "kind": "placement_opportunity",
                "severity": "opportunity",
                "title": f"'{placement}' outperforms the rest of '{name}'",
                "why": why,
                "campaign_id": campaign_id,
                "action": {"type": "focus_placement", "params": {"campaign_id": campaign_id, "placement_key": placement}},
            })

    # -- audience_saturation (trailing-7d frequency > 3.5) -------------------
    for campaign in active:
        campaign_id = safe_int(campaign.get("id"))
        cur.execute(
            """
            SELECT COUNT(*) AS n,
                   COUNT(DISTINCT COALESCE(viewer_user_id, session_id)) AS reach
            FROM pulse_ad_impressions WHERE campaign_id=? AND created_at>=?
            """,
            (campaign_id, cutoff_7d),
        )
        row = row_to_dict(cur.fetchone())
        impressions_7d = safe_int(row.get("n"), 0)
        reach_7d = safe_int(row.get("reach"), 0)
        if reach_7d < SATURATION_MIN_REACH:
            continue
        frequency = impressions_7d / reach_7d
        if frequency <= SATURATION_FREQUENCY:
            continue
        name = campaign.get("campaign_name") or f"Campaign {campaign_id}"
        add({
            "id": f"audience_saturation:{campaign_id}",
            "kind": "audience_saturation",
            "severity": "warning",
            "title": f"'{name}' is saturating its audience",
            "why": (
                f"In the trailing 7 days, {impressions_7d} impressions reached {reach_7d} viewers — "
                f"frequency {frequency:.2f}, above the {SATURATION_FREQUENCY} saturation threshold."
            ),
            "campaign_id": campaign_id,
            "action": {"type": "pause_campaign", "params": {"campaign_id": campaign_id}},
        })

    # -- high_cpc (campaign CPC > 2x account median, min 20 clicks) ----------
    campaign_cpcs: dict = {}
    for campaign in campaigns:
        campaign_id = safe_int(campaign.get("id"))
        c_clicks = sum(n for (cid, _), n in clicks_cp.items() if cid == campaign_id)
        c_spend = sum(n for (cid, _), n in spend_cp.items() if cid == campaign_id)
        if c_clicks > 0 and c_spend > 0:
            campaign_cpcs[campaign_id] = (c_spend / c_clicks, c_clicks, c_spend)
    if campaign_cpcs:
        median_cpc = statistics.median(value[0] for value in campaign_cpcs.values())
        for campaign_id, (cpc, c_clicks, c_spend) in campaign_cpcs.items():
            if c_clicks < HIGH_CPC_MIN_CLICKS or median_cpc <= 0:
                continue
            if cpc <= median_cpc * HIGH_CPC_MULTIPLE:
                continue
            campaign = campaign_by_id.get(campaign_id) or {}
            name = campaign.get("campaign_name") or f"Campaign {campaign_id}"
            add({
                "id": f"high_cpc:{campaign_id}",
                "kind": "high_cpc",
                "severity": "warning",
                "title": f"'{name}' pays far above your usual cost per click",
                "why": (
                    f"{c_spend} cents over {c_clicks} clicks = CPC {cpc / 100:.2f}, more than "
                    f"{HIGH_CPC_MULTIPLE:.0f}x the account median CPC of {median_cpc / 100:.2f}."
                ),
                "campaign_id": campaign_id,
                "action": {"type": "pause_campaign", "params": {"campaign_id": campaign_id}},
            })

    # -- creative_fatigue (trailing-3d CTR < 60% of first-3d CTR) ------------
    cur.execute(
        "SELECT id, title, campaign_id FROM pulse_ad_creatives WHERE ad_account_id=? AND status NOT IN ('archived')",
        (account_id,),
    )
    for raw in cur.fetchall():
        creative = row_to_dict(raw)
        creative_id = safe_int(creative.get("id"))
        cur.execute("SELECT MIN(created_at) AS first FROM pulse_ad_impressions WHERE creative_id=?", (creative_id,))
        first_at = clean_text(row_to_dict(cur.fetchone()).get("first"), 40)
        if not first_at:
            continue
        try:
            first_dt = datetime.fromisoformat(first_at.replace("Z", "+00:00"))
            if first_dt.tzinfo is None:
                first_dt = first_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        first_end = (first_dt + timedelta(days=3)).isoformat()
        if first_end > cutoff_3d:
            continue  # windows would overlap; not enough history to compare
        windows = {}
        for label, lo, hi in (("first", first_at, first_end), ("last", cutoff_3d, "")):
            clause, params = "created_at>=?", [creative_id, lo]
            if hi:
                clause += " AND created_at<?"
                params.append(hi)
            cur.execute(f"SELECT COUNT(*) AS n FROM pulse_ad_impressions WHERE creative_id=? AND {clause}", tuple(params))
            impr = safe_int(row_to_dict(cur.fetchone()).get("n"), 0)
            cur.execute(f"SELECT COUNT(*) AS n FROM pulse_ad_clicks WHERE creative_id=? AND {clause}", tuple(params))
            clk = safe_int(row_to_dict(cur.fetchone()).get("n"), 0)
            windows[label] = (impr, clk)
        first_impr, first_clicks = windows["first"]
        last_impr, last_clicks = windows["last"]
        if first_impr < FATIGUE_MIN_IMPRESSIONS or last_impr < FATIGUE_MIN_IMPRESSIONS or not first_clicks:
            continue
        first_ctr = first_clicks / first_impr
        last_ctr = last_clicks / last_impr
        if last_ctr >= first_ctr * FATIGUE_CTR_RATIO:
            continue
        add({
            "id": f"creative_fatigue:{creative_id}",
            "kind": "creative_fatigue",
            "severity": "warning",
            "title": f"Creative '{creative.get('title')}' is fatiguing",
            "why": (
                f"CTR fell from {first_ctr:.2%} in its first 3 days ({first_impr} impressions) to "
                f"{last_ctr:.2%} in the trailing 3 days ({last_impr} impressions) — below 60% of launch CTR."
            ),
            "campaign_id": safe_int(creative.get("campaign_id")),
            "creative_id": creative_id,
            "action": {"type": "archive_creative", "params": {"creative_id": creative_id}},
        })

    # -- strong_creative -----------------------------------------------------
    if total_impressions:
        account_ctr = total_clicks / total_impressions
        cur.execute(
            """
            SELECT cr.id, cr.title, cr.campaign_id,
                   (SELECT COUNT(*) FROM pulse_ad_impressions i WHERE i.creative_id=cr.id) AS impr,
                   (SELECT COUNT(*) FROM pulse_ad_clicks k WHERE k.creative_id=cr.id) AS clk
            FROM pulse_ad_creatives cr WHERE cr.ad_account_id=? AND cr.status NOT IN ('archived')
            """,
            (account_id,),
        )
        for raw in cur.fetchall():
            creative = row_to_dict(raw)
            impr = safe_int(creative.get("impr"), 0)
            clk = safe_int(creative.get("clk"), 0)
            if impr < STRONG_MIN_IMPRESSIONS or not account_ctr:
                continue
            ctr = clk / impr
            if ctr < account_ctr * STRONG_CTR_MULTIPLE:
                continue
            campaign_id = safe_int(creative.get("campaign_id"))
            campaign = campaign_by_id.get(campaign_id) or {}
            daily = safe_int(campaign.get("daily_budget_cents"), 0)
            if daily <= 0 or campaign.get("status") != "active":
                continue  # only recommend a budget raise where one can be applied
            proposed = int(daily * 1.2)
            add({
                "id": f"strong_creative:{safe_int(creative.get('id'))}",
                "kind": "strong_creative",
                "severity": "opportunity",
                "title": f"Creative '{creative.get('title')}' is outperforming your account",
                "why": (
                    f"CTR {ctr:.2%} across {impr} impressions vs account average {account_ctr:.2%} — "
                    f"at least {STRONG_CTR_MULTIPLE}x better."
                ),
                "campaign_id": campaign_id,
                "creative_id": safe_int(creative.get("id")),
                "action": {
                    "type": "increase_daily_budget",
                    "params": {"campaign_id": campaign_id, "daily_budget_cents": proposed},
                },
            })

    # -- budget_constrained (from real auto-pause records) -------------------
    limit_days: set = set()
    wallet_pauses = 0
    try:
        cur.execute(
            """
            SELECT action, substr(created_at, 1, 10) AS day, entity_id
            FROM pulse_ad_audit_logs
            WHERE action IN ('ad_campaign_auto_paused_spend_limit', 'ad_campaign_auto_paused_insufficient_wallet')
              AND created_at>=?
            """,
            (cutoff_7d,),
        )
        pause_rows = [row_to_dict(row) for row in cur.fetchall()]
    except Exception:
        pause_rows = []
    account_campaign_ids = {str(safe_int(c.get("id"))) for c in campaigns}
    for row in pause_rows:
        if clean_text(row.get("entity_id"), 40) not in account_campaign_ids:
            continue
        if row.get("action") == "ad_campaign_auto_paused_spend_limit":
            limit_days.add(row.get("day"))
        else:
            wallet_pauses += 1
    spendable = pulse_ad_payments.spendable_balance_cents(conn, account_id)
    if len(limit_days) >= BUDGET_PAUSE_DAYS_THRESHOLD:
        add({
            "id": f"budget_constrained:limits:{account_id}",
            "kind": "budget_constrained",
            "severity": "warning",
            "title": "Spending limits keep stopping delivery",
            "why": (
                f"Campaigns were auto-paused by your spending limit on {len(limit_days)} of the last 7 days "
                f"({', '.join(sorted(limit_days))})."
            ),
            "action": {"type": "topup_wallet", "params": {"account_id": account_id}},
        })
    elif wallet_pauses:
        add({
            "id": f"budget_constrained:wallet:{account_id}",
            "kind": "budget_constrained",
            "severity": "warning",
            "title": "Delivery stopped for insufficient funds",
            "why": f"{wallet_pauses} campaign auto-pause(s) for an insufficient wallet in the last 7 days.",
            "action": {"type": "topup_wallet", "params": {"account_id": account_id}},
        })
    elif active and spendable <= 0:
        add({
            "id": f"budget_constrained:empty:{account_id}",
            "kind": "budget_constrained",
            "severity": "warning",
            "title": "Wallet is empty while campaigns are active",
            "why": f"{len(active)} active campaign(s) with a spendable balance of 0 cents; delivery will pause.",
            "action": {"type": "topup_wallet", "params": {"account_id": account_id}},
        })

    # -- budget_exhaustion (>= 90% of a lifetime budget already spent) -------
    for campaign in campaigns:
        lifetime_budget = safe_int(campaign.get("lifetime_budget_cents"), 0)
        spent = safe_int(campaign.get("spent_cents"), 0)
        if lifetime_budget > 0 and spent >= lifetime_budget * 0.9 and campaign.get("status") in {"active", "paused"}:
            campaign_id = safe_int(campaign.get("id"))
            add({
                "id": f"budget_exhaustion:{campaign_id}",
                "kind": "budget_exhaustion",
                "severity": "warning",
                "title": f"'{campaign.get('campaign_name')}' has nearly exhausted its budget",
                "why": (
                    f"{spent} of {lifetime_budget} lifetime budget cents "
                    f"({spent * 100 // lifetime_budget}%) is already spent."
                ),
                "campaign_id": campaign_id,
                "action": {
                    "type": "raise_lifetime_budget",
                    "params": {"campaign_id": campaign_id, "lifetime_budget_cents": int(lifetime_budget * 1.25)},
                },
            })

    # -- audience_expansion (narrow audience + low delivery) -----------------
    audience_modes: dict = {}
    try:
        cur.execute(
            """
            SELECT t.campaign_id, t.audience_mode FROM pulse_ad_targeting t
            JOIN pulse_ad_campaigns c ON c.id=t.campaign_id WHERE c.ad_account_id=?
            """,
            (account_id,),
        )
        audience_modes = {
            safe_int(item.get("campaign_id")): clean_text(item.get("audience_mode"), 20).lower() or "everyone"
            for item in (row_to_dict(row) for row in cur.fetchall())
        }
    except Exception:
        audience_modes = {}
    for campaign in active:
        campaign_id = safe_int(campaign.get("id"))
        mode = audience_modes.get(campaign_id, "everyone")
        if mode == "everyone":
            continue
        created = clean_text(campaign.get("created_at"), 40)
        if not created or created > _cutoff(EXPANSION_MIN_AGE_DAYS):
            continue
        cur.execute(
            "SELECT COUNT(*) AS n FROM pulse_ad_impressions WHERE campaign_id=? AND created_at>=?",
            (campaign_id, cutoff_7d),
        )
        impressions_7d = safe_int(row_to_dict(cur.fetchone()).get("n"), 0)
        if impressions_7d >= EXPANSION_LOW_IMPRESSIONS_7D:
            continue
        name = campaign.get("campaign_name") or f"Campaign {campaign_id}"
        add({
            "id": f"audience_expansion:{campaign_id}",
            "kind": "audience_expansion",
            "severity": "opportunity",
            "title": f"'{name}' targets a narrow audience and barely delivers",
            "why": (
                f"Audience mode is '{mode}' and the campaign served only {impressions_7d} impressions "
                f"in the last 7 days (threshold {EXPANSION_LOW_IMPRESSIONS_7D})."
            ),
            "campaign_id": campaign_id,
            "action": {"type": "expand_audience", "params": {"campaign_id": campaign_id}},
        })

    data_status = {
        "campaigns": len(campaigns),
        "active_campaigns": len(active),
        "impressions": total_impressions,
        "clicks": total_clicks,
    }
    if not recommendations:
        data_status["note"] = (
            "No recommendations: not enough delivery data crossed any rule threshold. "
            "Recommendations appear only when real aggregates justify them."
        )
    return {"recommendations": recommendations, "data_status": data_status, "generated_at": now_iso()}


# ---------------------------------------------------------------------------
# Apply (approve-gated, recompute-verified, whitelisted, audited)
# ---------------------------------------------------------------------------

def _campaign_in_account(conn, campaign_id, account_id) -> dict:
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM pulse_ad_campaigns WHERE id=? AND ad_account_id=?",
        (safe_int(campaign_id, minimum=1), account_id),
    )
    campaign = row_to_dict(cur.fetchone())
    if not campaign:
        raise PulseAdsError("Campaign not found.", 404)
    return campaign


def apply_insight(conn, user_id, payload: dict) -> dict:
    """Apply one recommendation after explicit approval.

    The insight is recomputed from current data before anything mutates: if
    the triggering condition no longer holds (or the id is unknown), the
    request is rejected as stale rather than applied on faith.
    """
    payload = payload or {}
    account_id = safe_int(payload.get("account_id"), minimum=1)
    pulse_advertiser_portal._require_account_role(
        conn, user_id, account_id, pulse_advertiser_portal.WRITE_ROLES
    )
    insight_id = clean_text(payload.get("insight_id") or payload.get("id"), 160)
    if not insight_id:
        raise PulseAdsError("insight_id is required.")
    if not payload.get("approve"):
        raise PulseAdsError("Applying an insight requires approve=true.", 400)

    current = build_insights(conn, user_id, account_id)
    match = next((item for item in current["recommendations"] if item.get("id") == insight_id), None)
    if not match:
        raise PulseAdsError("This insight is stale — conditions no longer hold. Refresh insights.", 409)
    action = match.get("action") or {}
    action_type = clean_text(action.get("type"), 40)
    action_params = action.get("params") if isinstance(action.get("params"), dict) else {}
    if action_type not in APPLY_WHITELIST:
        raise PulseAdsError(
            f"Action '{action_type}' cannot be applied automatically. "
            "Wallet funding always goes through the explicit Stripe checkout flow.",
            400,
        )

    cur = conn.cursor()
    now = now_iso()
    before: dict = {}
    after: dict = {}

    if action_type == "pause_campaign":
        campaign = _campaign_in_account(conn, action_params.get("campaign_id"), account_id)
        before = {"status": campaign.get("status")}
        cur.execute("UPDATE pulse_ad_campaigns SET status='paused', updated_at=? WHERE id=?", (now, campaign.get("id")))
        after = {"status": "paused"}
    elif action_type == "focus_placement":
        campaign = _campaign_in_account(conn, action_params.get("campaign_id"), account_id)
        placement_key = clean_text(action_params.get("placement_key"), 80)
        cur.execute("SELECT id FROM pulse_ad_placements WHERE placement_key=? AND is_active=1", (placement_key,))
        placement = row_to_dict(cur.fetchone())
        if not placement:
            raise PulseAdsError("Placement not found.", 404)
        cur.execute(
            "INSERT OR IGNORE INTO pulse_ad_campaign_placements (campaign_id, placement_id, created_at) VALUES (?, ?, ?)",
            (campaign.get("id"), placement.get("id"), now),
        )
        cur.execute(
            "DELETE FROM pulse_ad_campaign_placements WHERE campaign_id=? AND placement_id != ?",
            (campaign.get("id"), placement.get("id")),
        )
        after = {"placements": [placement_key]}
    elif action_type == "archive_creative":
        creative_id = safe_int(action_params.get("creative_id"), minimum=1)
        cur.execute(
            "SELECT * FROM pulse_ad_creatives WHERE id=? AND ad_account_id=?", (creative_id, account_id)
        )
        creative = row_to_dict(cur.fetchone())
        if not creative:
            raise PulseAdsError("Creative not found.", 404)
        before = {"status": creative.get("status")}
        cur.execute(
            "UPDATE pulse_ad_creatives SET status='archived', archived_at=?, updated_at=? WHERE id=?",
            (now, now, creative_id),
        )
        after = {"status": "archived"}
    elif action_type == "increase_daily_budget":
        campaign = _campaign_in_account(conn, action_params.get("campaign_id"), account_id)
        current_daily = safe_int(campaign.get("daily_budget_cents"), 0)
        proposed = safe_int(action_params.get("daily_budget_cents"), 0)
        if proposed <= current_daily or proposed > max(current_daily * 2, current_daily + 100):
            raise PulseAdsError("Proposed daily budget is outside the allowed adjustment range.", 400)
        before = {"daily_budget_cents": current_daily}
        cur.execute(
            "UPDATE pulse_ad_campaigns SET daily_budget_cents=?, updated_at=? WHERE id=?",
            (proposed, now, campaign.get("id")),
        )
        after = {"daily_budget_cents": proposed}
    elif action_type == "raise_lifetime_budget":
        campaign = _campaign_in_account(conn, action_params.get("campaign_id"), account_id)
        current_budget = safe_int(campaign.get("lifetime_budget_cents"), 0)
        proposed = safe_int(action_params.get("lifetime_budget_cents"), 0)
        if proposed <= current_budget or proposed > max(current_budget * 2, current_budget + 100):
            raise PulseAdsError("Proposed lifetime budget is outside the allowed adjustment range.", 400)
        before = {"lifetime_budget_cents": current_budget}
        cur.execute(
            "UPDATE pulse_ad_campaigns SET lifetime_budget_cents=?, updated_at=? WHERE id=?",
            (proposed, now, campaign.get("id")),
        )
        after = {"lifetime_budget_cents": proposed}
    elif action_type == "expand_audience":
        campaign = _campaign_in_account(conn, action_params.get("campaign_id"), account_id)
        cur.execute("SELECT audience_mode FROM pulse_ad_targeting WHERE campaign_id=?", (campaign.get("id"),))
        targeting = row_to_dict(cur.fetchone())
        if not targeting:
            raise PulseAdsError("Campaign has no targeting row to expand.", 404)
        before = {"audience_mode": targeting.get("audience_mode")}
        cur.execute(
            "UPDATE pulse_ad_targeting SET audience_mode='everyone', updated_at=? WHERE campaign_id=?",
            (now, campaign.get("id")),
        )
        after = {"audience_mode": "everyone"}

    pulse_ads_service.audit_log(
        conn,
        user_id,
        "ad_insight_applied",
        "pulse_ad_insights",
        insight_id,
        before=before,
        after={**after, "kind": match.get("kind"), "action_type": action_type},
    )
    conn.commit()
    return {
        "applied": True,
        "insight_id": insight_id,
        "kind": match.get("kind"),
        "action": {"type": action_type, "params": action_params},
        "before": before,
        "after": after,
    }
