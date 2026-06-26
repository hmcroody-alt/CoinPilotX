"""PulseSoc advertiser portal service.

This layer builds advertiser-facing workflows on top of the ads foundation and
delivery engine. It keeps permissions server-side and avoids exposing billing
provider identifiers or private tracking data to clients.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from services import pulse_ad_payments, pulse_ads_service


ACCOUNT_ROLES = {"owner", "campaign_manager", "marketing_manager", "analyst", "viewer"}
WRITE_ROLES = {"owner", "campaign_manager", "marketing_manager"}
ANALYTICS_ROLES = {"owner", "campaign_manager", "marketing_manager", "analyst"}
CAMPAIGN_ACTIONS = {"pause", "resume", "archive", "duplicate", "submit", "complete"}
CREATIVE_ACTIONS = {"duplicate", "archive", "delete_draft", "submit"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def row_to_dict(row) -> dict:
    return pulse_ads_service.row_to_dict(row)


def clean_text(value, max_len: int = 240) -> str:
    return pulse_ads_service.clean_text(value, max_len)


def clean_json(value, max_len: int = 8000) -> str:
    return pulse_ads_service.clean_json(value, max_len)


def safe_int(value, default=0, minimum=None, maximum=None) -> int:
    return pulse_ads_service.safe_int(value, default, minimum, maximum)


def money(cents) -> str:
    amount = safe_int(cents, 0)
    return f"${amount / 100:,.2f}"


def _table_columns(conn, table_name: str) -> set[str]:
    cur = conn.cursor()
    try:
        cur.execute(f"PRAGMA table_info({table_name})")
        return {row_to_dict(row).get("name") for row in cur.fetchall()}
    except Exception:
        return set()


def _has_column(conn, table_name: str, column_name: str) -> bool:
    return column_name in _table_columns(conn, table_name)


def _role_for_account(conn, user_id, account_id) -> str:
    cur = conn.cursor()
    cur.execute("SELECT owner_user_id FROM pulse_ad_accounts WHERE id=?", (account_id,))
    account = row_to_dict(cur.fetchone())
    if not account:
        raise pulse_ads_service.PulseAdsError("Ad account not found.", 404)
    if safe_int(account.get("owner_user_id")) == safe_int(user_id):
        return "owner"
    cur.execute(
        """
        SELECT role FROM pulse_ad_team_members
        WHERE account_id=? AND user_id=? AND status='active'
        ORDER BY id DESC LIMIT 1
        """,
        (account_id, user_id),
    )
    member = row_to_dict(cur.fetchone())
    role = clean_text(member.get("role"), 40)
    if role in ACCOUNT_ROLES:
        return role
    raise pulse_ads_service.PulseAdsError("Ad account not found.", 404)


def _require_account_role(conn, user_id, account_id, allowed_roles=None) -> str:
    role = _role_for_account(conn, user_id, account_id)
    if allowed_roles and role not in allowed_roles:
        raise pulse_ads_service.PulseAdsError("You do not have permission for this ad account.", 403)
    return role


def _campaign_account_id(conn, campaign_id) -> int:
    cur = conn.cursor()
    cur.execute("SELECT ad_account_id FROM pulse_ad_campaigns WHERE id=?", (campaign_id,))
    campaign = row_to_dict(cur.fetchone())
    if not campaign:
        raise pulse_ads_service.PulseAdsError("Campaign not found.", 404)
    return safe_int(campaign.get("ad_account_id"))


def _creative_account_id(conn, creative_id) -> int:
    cur = conn.cursor()
    cur.execute("SELECT ad_account_id FROM pulse_ad_creatives WHERE id=?", (creative_id,))
    creative = row_to_dict(cur.fetchone())
    if not creative:
        raise pulse_ads_service.PulseAdsError("Creative not found.", 404)
    return safe_int(creative.get("ad_account_id"))


def _safe_profile(profile: dict) -> dict:
    public = dict(profile or {})
    if public.get("tax_identifier_masked"):
        public["tax_identifier_masked"] = _mask_tax(public.get("tax_identifier_masked"))
    return public


def _mask_tax(value: str) -> str:
    cleaned = clean_text(value, 80)
    if not cleaned:
        return ""
    tail = cleaned[-4:] if len(cleaned) > 4 else cleaned
    return f"***{tail}"


def ensure_account_profile(conn, account_id) -> dict:
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_account_profiles WHERE account_id=?", (account_id,))
    profile = row_to_dict(cur.fetchone())
    if profile:
        return profile
    cur.execute("SELECT business_name, business_email, business_phone, business_website, business_type FROM pulse_ad_accounts WHERE id=?", (account_id,))
    account = row_to_dict(cur.fetchone())
    now = now_iso()
    cur.execute(
        """
        INSERT INTO pulse_ad_account_profiles
        (account_id, legal_name, company_address, tax_country, tax_identifier_masked, contact_name,
         contact_email, contact_phone, billing_email, website, industry, created_at, updated_at)
        VALUES (?, ?, '', '', '', '', ?, ?, '', ?, ?, ?, ?)
        """,
        (
            account_id,
            clean_text(account.get("business_name"), 160),
            clean_text(account.get("business_email"), 160),
            clean_text(account.get("business_phone"), 60),
            clean_text(account.get("business_website"), 240),
            clean_text(account.get("business_type"), 80),
            now,
            now,
        ),
    )
    conn.commit()
    cur.execute("SELECT * FROM pulse_ad_account_profiles WHERE account_id=?", (account_id,))
    return row_to_dict(cur.fetchone())


def update_account_profile(conn, user_id, account_id, payload: dict) -> dict:
    _require_account_role(conn, user_id, account_id, WRITE_ROLES)
    ensure_account_profile(conn, account_id)
    fields = {
        "legal_name": clean_text(payload.get("legal_name"), 160),
        "company_address": clean_text(payload.get("company_address"), 300),
        "tax_country": clean_text(payload.get("tax_country"), 40).upper(),
        "tax_identifier_masked": _mask_tax(payload.get("tax_identifier") or payload.get("tax_identifier_masked")),
        "contact_name": clean_text(payload.get("contact_name"), 120),
        "contact_email": clean_text(payload.get("contact_email"), 160),
        "contact_phone": clean_text(payload.get("contact_phone"), 60),
        "billing_email": clean_text(payload.get("billing_email"), 160),
        "website": pulse_ads_service.validate_destination_url(payload.get("website"), required=False),
        "industry": clean_text(payload.get("industry"), 80),
    }
    now = now_iso()
    cur = conn.cursor()
    before = ensure_account_profile(conn, account_id)
    cur.execute(
        """
        UPDATE pulse_ad_account_profiles
        SET legal_name=?, company_address=?, tax_country=?, tax_identifier_masked=?, contact_name=?,
            contact_email=?, contact_phone=?, billing_email=?, website=?, industry=?, updated_at=?
        WHERE account_id=?
        """,
        (
            fields["legal_name"],
            fields["company_address"],
            fields["tax_country"],
            fields["tax_identifier_masked"],
            fields["contact_name"],
            fields["contact_email"],
            fields["contact_phone"],
            fields["billing_email"],
            fields["website"],
            fields["industry"],
            now,
            account_id,
        ),
    )
    pulse_ads_service.audit_log(conn, user_id, "ad_account_profile_updated", "pulse_ad_account_profiles", account_id, before=before, after=_safe_profile(fields))
    _add_notification(conn, account_id, None, None, user_id, "account_profile", "Business profile updated", "Your advertiser account profile was updated.")
    conn.commit()
    return _safe_profile(ensure_account_profile(conn, account_id))


def get_account_profile(conn, user_id, account_id) -> dict:
    role = _require_account_role(conn, user_id, account_id)
    return {"role": role, "profile": _safe_profile(ensure_account_profile(conn, account_id))}


def _add_history(conn, campaign_id, actor_user_id, action, before=None, after=None) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO pulse_ad_campaign_history
        (campaign_id, actor_user_id, action, before_json, after_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (campaign_id, actor_user_id, clean_text(action, 80), clean_json(before or {}), clean_json(after or {}), now_iso()),
    )


def _add_notification(conn, account_id, campaign_id, creative_id, recipient_user_id, notification_type, title, body) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO pulse_ad_notifications
        (account_id, campaign_id, creative_id, recipient_user_id, notification_type, title, body, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'unread', ?)
        """,
        (
            account_id,
            campaign_id,
            creative_id,
            recipient_user_id,
            clean_text(notification_type, 80),
            clean_text(title, 160),
            clean_text(body, 500),
            now_iso(),
        ),
    )


def _campaign_placements(conn, campaign_id) -> list[str]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.placement_key
        FROM pulse_ad_campaign_placements cp
        JOIN pulse_ad_placements p ON p.id=cp.placement_id
        WHERE cp.campaign_id=?
        ORDER BY p.placement_key
        """,
        (campaign_id,),
    )
    return [row_to_dict(row).get("placement_key") for row in cur.fetchall()]


def _account_ids_for_user(conn, user_id) -> list[int]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id FROM pulse_ad_accounts WHERE owner_user_id=?
        UNION
        SELECT account_id AS id FROM pulse_ad_team_members WHERE user_id=? AND status='active'
        """,
        (user_id, user_id),
    )
    return [safe_int(row_to_dict(row).get("id")) for row in cur.fetchall()]


def list_accounts(conn, user_id) -> list[dict]:
    account_ids = _account_ids_for_user(conn, user_id)
    if not account_ids:
        return []
    placeholders = ",".join("?" for _ in account_ids)
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT a.*, p.industry, p.website, p.contact_email,
               COUNT(DISTINCT c.id) AS campaign_count,
               SUM(CASE WHEN c.status IN ('running','active') THEN 1 ELSE 0 END) AS active_campaigns,
               SUM(CASE WHEN cr.moderation_status='pending' THEN 1 ELSE 0 END) AS pending_reviews,
               COALESCE(SUM(c.spent_cents), 0) AS total_spend_cents
        FROM pulse_ad_accounts a
        LEFT JOIN pulse_ad_account_profiles p ON p.account_id=a.id
        LEFT JOIN pulse_ad_campaigns c ON c.ad_account_id=a.id
        LEFT JOIN pulse_ad_creatives cr ON cr.ad_account_id=a.id
        WHERE a.id IN ({placeholders})
        GROUP BY a.id, p.industry, p.website, p.contact_email
        ORDER BY a.id DESC
        LIMIT 100
        """,
        tuple(account_ids),
    )
    accounts = []
    for row in cur.fetchall():
        item = row_to_dict(row)
        item["role"] = _role_for_account(conn, user_id, item.get("id"))
        item["health_score"] = _account_health(item)
        item["total_spend"] = money(item.get("total_spend_cents"))
        accounts.append(item)
    return accounts


def _account_health(account: dict) -> int:
    score = 50
    if account.get("status") == "active":
        score += 20
    if account.get("verification_status") in {"verified", "approved"}:
        score += 15
    if safe_int(account.get("campaign_count")):
        score += 10
    if safe_int(account.get("pending_reviews")) == 0:
        score += 5
    return min(100, score)


def campaign_status_counts(conn, account_ids: list[int]) -> dict:
    if not account_ids:
        return {}
    placeholders = ",".join("?" for _ in account_ids)
    cur = conn.cursor()
    cur.execute(
        f"SELECT status, COUNT(*) AS total FROM pulse_ad_campaigns WHERE ad_account_id IN ({placeholders}) GROUP BY status",
        tuple(account_ids),
    )
    return {clean_text(row_to_dict(row).get("status"), 40): safe_int(row_to_dict(row).get("total")) for row in cur.fetchall()}


def list_campaigns(conn, user_id) -> list[dict]:
    account_ids = _account_ids_for_user(conn, user_id)
    if not account_ids:
        return []
    placeholders = ",".join("?" for _ in account_ids)
    cur = conn.cursor()
    archived_clause = " AND COALESCE(c.archived_at, '')=''" if _has_column(conn, "pulse_ad_campaigns", "archived_at") else ""
    cur.execute(
        f"""
        SELECT c.*, a.business_name,
               COUNT(DISTINCT cr.id) AS creative_count,
               SUM(CASE WHEN cr.moderation_status='approved' THEN 1 ELSE 0 END) AS approved_creatives,
               SUM(CASE WHEN cr.moderation_status='pending' THEN 1 ELSE 0 END) AS pending_creatives
        FROM pulse_ad_campaigns c
        JOIN pulse_ad_accounts a ON a.id=c.ad_account_id
        LEFT JOIN pulse_ad_creatives cr ON cr.campaign_id=c.id
        WHERE c.ad_account_id IN ({placeholders}){archived_clause}
        GROUP BY c.id, a.business_name
        ORDER BY c.id DESC
        LIMIT 150
        """,
        tuple(account_ids),
    )
    campaigns = []
    for row in cur.fetchall():
        item = row_to_dict(row)
        item["placements"] = _campaign_placements(conn, item.get("id"))
        item["budget_display"] = money(item.get("daily_budget_cents") if item.get("budget_type") == "daily" else item.get("lifetime_budget_cents"))
        item["remaining_budget_cents"] = max(
            0,
            safe_int(item.get("lifetime_budget_cents") or item.get("daily_budget_cents")) - safe_int(item.get("spent_cents")),
        )
        item["remaining_budget"] = money(item["remaining_budget_cents"])
        campaigns.append(item)
    return campaigns


def list_creatives(conn, user_id) -> list[dict]:
    account_ids = _account_ids_for_user(conn, user_id)
    if not account_ids:
        return []
    placeholders = ",".join("?" for _ in account_ids)
    archived_clause = " AND COALESCE(cr.archived_at, '')=''" if _has_column(conn, "pulse_ad_creatives", "archived_at") else ""
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT cr.*, c.campaign_name, a.business_name
        FROM pulse_ad_creatives cr
        JOIN pulse_ad_campaigns c ON c.id=cr.campaign_id
        JOIN pulse_ad_accounts a ON a.id=cr.ad_account_id
        WHERE cr.ad_account_id IN ({placeholders}){archived_clause}
        ORDER BY cr.id DESC
        LIMIT 150
        """,
        tuple(account_ids),
    )
    return [_creative_public(pulse_ads_service.attach_creative_media(conn, row_to_dict(row))) for row in cur.fetchall()]


def _creative_public(creative: dict) -> dict:
    item = dict(creative or {})
    item["performance_state"] = "Ready" if item.get("moderation_status") == "approved" else "Waiting for review"
    item["media_ready"] = bool(item.get("media_asset_id") or item.get("media_url") or item.get("creative_type") == "text")
    item["destination_safe"] = bool(item.get("destination_url", "").startswith(("http://", "https://")))
    return item


def review_status(conn, user_id) -> list[dict]:
    account_ids = _account_ids_for_user(conn, user_id)
    if not account_ids:
        return []
    placeholders = ",".join("?" for _ in account_ids)
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT rb.id AS review_id, rb.review_status, rb.risk_score, rb.automated_review_status,
               rb.human_review_status, rb.review_reason, rb.reviewed_at, rb.created_at, rb.updated_at,
               cr.id AS creative_id, cr.title, cr.moderation_status, cr.rejection_reason,
               c.id AS campaign_id, c.campaign_name
        FROM pulse_ad_review_board rb
        JOIN pulse_ad_creatives cr ON cr.id=rb.creative_id
        JOIN pulse_ad_campaigns c ON c.id=rb.campaign_id
        WHERE cr.ad_account_id IN ({placeholders})
        ORDER BY rb.id DESC
        LIMIT 80
        """,
        tuple(account_ids),
    )
    return [row_to_dict(row) for row in cur.fetchall()]


def notifications(conn, user_id) -> list[dict]:
    account_ids = _account_ids_for_user(conn, user_id)
    if not account_ids:
        return []
    placeholders = ",".join("?" for _ in account_ids)
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT id, account_id, campaign_id, creative_id, notification_type, title, body, status, created_at, read_at
        FROM pulse_ad_notifications
        WHERE account_id IN ({placeholders}) AND (recipient_user_id IS NULL OR recipient_user_id=?)
        ORDER BY id DESC
        LIMIT 50
        """,
        tuple(account_ids + [user_id]),
    )
    return [row_to_dict(row) for row in cur.fetchall()]


def portal_summary(conn, user_id) -> dict:
    accounts = list_accounts(conn, user_id)
    account_ids = [safe_int(account.get("id")) for account in accounts]
    campaigns = list_campaigns(conn, user_id)
    creatives = list_creatives(conn, user_id)
    analytics = pulse_ads_service.advertiser_analytics(conn, user_id)
    review_rows = review_status(conn, user_id)
    note_rows = notifications(conn, user_id)
    wallet_rows = []
    for account in accounts:
        try:
            wallet_rows.append(pulse_ad_payments.wallet_summary(conn, user_id, account.get("id")))
        except Exception:
            wallet_rows.append({
                "account_id": safe_int(account.get("id")),
                "available_balance_cents": 0,
                "reserved_budget_cents": 0,
                "lifetime_funded_cents": 0,
                "lifetime_spent_cents": 0,
                "spendable_balance_cents": 0,
                "available_balance": "$0.00",
                "reserved_budget": "$0.00",
                "spendable_balance": "$0.00",
                "transactions": [],
                "receipts": [],
                "billing_enabled": pulse_ad_payments.billing_enabled(),
                "stripe_ready": pulse_ad_payments.stripe_ready(),
            })
    unread_notes = sum(1 for item in note_rows if item.get("status") == "unread")
    status_counts = campaign_status_counts(conn, account_ids)
    spend_total = sum(safe_int(account.get("total_spend_cents")) for account in accounts)
    wallet_total = sum(safe_int(wallet.get("available_balance_cents")) for wallet in wallet_rows)
    reserved_total = sum(safe_int(wallet.get("reserved_budget_cents")) for wallet in wallet_rows)
    spendable_total = sum(safe_int(wallet.get("spendable_balance_cents")) for wallet in wallet_rows)
    billing_enabled = os.getenv("PULSE_ADS_BILLING_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    return {
        "accounts": accounts,
        "campaigns": campaigns,
        "creatives": creatives,
        "wallets": wallet_rows,
        "analytics": analytics,
        "review_board": review_rows,
        "notifications": note_rows,
        "billing": {
            "enabled": billing_enabled,
            "mode": "prepared" if not billing_enabled else "web_advertiser_billing",
            "stripe_customer_visible": False,
            "live_charging": False,
            "summary": "Billing controls are prepared behind a server feature flag. No live advertiser charging occurs here.",
        },
        "metrics": {
            "account_count": len(accounts),
            "campaign_count": len(campaigns),
            "creative_count": len(creatives),
            "pending_reviews": sum(safe_int(account.get("pending_reviews")) for account in accounts),
            "active_campaigns": sum(1 for campaign in campaigns if campaign.get("status") in {"active", "running"}),
            "draft_campaigns": status_counts.get("draft", 0),
            "unread_notifications": unread_notes,
            "total_spend_cents": spend_total,
            "total_spend": money(spend_total),
            "wallet_balance_cents": wallet_total,
            "wallet_balance": money(wallet_total),
            "reserved_budget_cents": reserved_total,
            "reserved_budget": money(reserved_total),
            "spendable_balance_cents": spendable_total,
            "spendable_balance": money(spendable_total),
        },
        "campaign_status_counts": status_counts,
        "placements": pulse_ads_service.PLACEMENT_METADATA,
        "roles": {
            "current": "owner" if any(account.get("role") == "owner" for account in accounts) else (accounts[0].get("role") if accounts else "none"),
            "allowed": sorted(ACCOUNT_ROLES),
        },
    }


def update_campaign(conn, user_id, campaign_id, payload: dict) -> dict:
    account_id = _campaign_account_id(conn, campaign_id)
    _require_account_role(conn, user_id, account_id, WRITE_ROLES)
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_campaigns WHERE id=?", (campaign_id,))
    before = row_to_dict(cur.fetchone())
    if before.get("status") not in {"draft", "paused", "pending_review", "rejected"}:
        raise pulse_ads_service.PulseAdsError("Only draft, paused, pending, or rejected campaigns can be edited.", 409)
    objective = clean_text(payload.get("objective") or before.get("objective") or "awareness", 40).lower()
    if objective not in pulse_ads_service.VALID_OBJECTIVES:
        raise pulse_ads_service.PulseAdsError("Unsupported campaign objective.")
    budget_type = clean_text(payload.get("budget_type") or before.get("budget_type") or "daily", 20).lower()
    if budget_type not in pulse_ads_service.VALID_BUDGET_TYPES:
        raise pulse_ads_service.PulseAdsError("Unsupported budget type.")
    now = now_iso()
    cur.execute(
        """
        UPDATE pulse_ad_campaigns
        SET campaign_name=?, objective=?, budget_type=?, daily_budget_cents=?, lifetime_budget_cents=?,
            start_at=?, end_at=?, pacing_mode=?, updated_at=?
        WHERE id=?
        """,
        (
            clean_text(payload.get("campaign_name") or before.get("campaign_name"), 120),
            objective,
            budget_type,
            safe_int(payload.get("daily_budget_cents"), safe_int(before.get("daily_budget_cents")), 0, 10_000_000),
            safe_int(payload.get("lifetime_budget_cents"), safe_int(before.get("lifetime_budget_cents")), 0, 100_000_000),
            clean_text(payload.get("start_at") or before.get("start_at"), 40),
            clean_text(payload.get("end_at") or before.get("end_at"), 40),
            clean_text(payload.get("pacing_mode") or before.get("pacing_mode") or "standard", 40),
            now,
            campaign_id,
        ),
    )
    if "placements" in payload:
        cur.execute("DELETE FROM pulse_ad_campaign_placements WHERE campaign_id=?", (campaign_id,))
        pulse_ads_service.attach_campaign_placements(conn, campaign_id, payload.get("placements") or ["feed_inline"])
    cur.execute("SELECT * FROM pulse_ad_campaigns WHERE id=?", (campaign_id,))
    after = row_to_dict(cur.fetchone())
    _add_history(conn, campaign_id, user_id, "campaign_updated", before, after)
    pulse_ads_service.audit_log(conn, user_id, "ad_campaign_updated", "pulse_ad_campaigns", campaign_id, before=before, after=after)
    conn.commit()
    after["placements"] = _campaign_placements(conn, campaign_id)
    return after


def campaign_action(conn, user_id, campaign_id, action: str) -> dict:
    action = clean_text(action, 40).lower()
    if action not in CAMPAIGN_ACTIONS:
        raise pulse_ads_service.PulseAdsError("Unsupported campaign action.")
    account_id = _campaign_account_id(conn, campaign_id)
    _require_account_role(conn, user_id, account_id, WRITE_ROLES)
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_campaigns WHERE id=?", (campaign_id,))
    before = row_to_dict(cur.fetchone())
    now = now_iso()
    if action == "duplicate":
        cur.execute(
            """
            INSERT INTO pulse_ad_campaigns
            (ad_account_id, campaign_name, objective, status, budget_type, daily_budget_cents, lifetime_budget_cents,
             spent_cents, start_at, end_at, priority, pacing_mode, created_at, updated_at)
            VALUES (?, ?, ?, 'draft', ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
            """,
            (
                before.get("ad_account_id"),
                clean_text(f"{before.get('campaign_name')} copy", 120),
                before.get("objective"),
                before.get("budget_type"),
                before.get("daily_budget_cents"),
                before.get("lifetime_budget_cents"),
                before.get("start_at"),
                before.get("end_at"),
                before.get("priority") or 0,
                before.get("pacing_mode") or "standard",
                now,
                now,
            ),
        )
        new_campaign_id = cur.lastrowid
        pulse_ads_service.attach_campaign_placements(conn, new_campaign_id, _campaign_placements(conn, campaign_id))
        _add_history(conn, new_campaign_id, user_id, "campaign_duplicated", before, {"source_campaign_id": campaign_id})
        conn.commit()
        return {"campaign_id": new_campaign_id, "status": "draft", "action": action}
    status_map = {
        "pause": "paused",
        "resume": "active",
        "archive": "archived",
        "submit": "pending_review",
        "complete": "completed",
    }
    new_status = status_map[action]
    reserve_result = None
    if action == "resume":
        reserve_result = pulse_ad_payments.reserve_campaign_budget(conn, user_id, campaign_id)
    set_parts = ["status=?", "updated_at=?"]
    params = [new_status, now]
    if action == "archive" and _has_column(conn, "pulse_ad_campaigns", "archived_at"):
        set_parts.append("archived_at=?")
        params.append(now)
    if action == "submit" and _has_column(conn, "pulse_ad_campaigns", "submitted_at"):
        set_parts.append("submitted_at=?")
        params.append(now)
    if action == "complete" and _has_column(conn, "pulse_ad_campaigns", "completed_at"):
        set_parts.append("completed_at=?")
        params.append(now)
    params.append(campaign_id)
    cur.execute(f"UPDATE pulse_ad_campaigns SET {', '.join(set_parts)} WHERE id=?", tuple(params))
    cur.execute("SELECT * FROM pulse_ad_campaigns WHERE id=?", (campaign_id,))
    after = row_to_dict(cur.fetchone())
    _add_history(conn, campaign_id, user_id, f"campaign_{action}", before, after)
    _add_notification(conn, account_id, campaign_id, None, user_id, f"campaign_{action}", f"Campaign {new_status}", f"{before.get('campaign_name')} is now {new_status}.")
    pulse_ads_service.audit_log(conn, user_id, f"ad_campaign_{action}", "pulse_ad_campaigns", campaign_id, before=before, after=after)
    conn.commit()
    result = {"campaign_id": campaign_id, "status": new_status, "action": action}
    if reserve_result:
        result["budget_reserve"] = reserve_result
    return result


def creative_action(conn, user_id, creative_id, action: str) -> dict:
    action = clean_text(action, 40).lower()
    if action not in CREATIVE_ACTIONS:
        raise pulse_ads_service.PulseAdsError("Unsupported creative action.")
    account_id = _creative_account_id(conn, creative_id)
    _require_account_role(conn, user_id, account_id, WRITE_ROLES)
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_creatives WHERE id=?", (creative_id,))
    before = row_to_dict(cur.fetchone())
    now = now_iso()
    if action == "submit":
        return {"creative": pulse_ads_service.submit_creative_for_review(conn, user_id, creative_id), "action": action}
    if action == "duplicate":
        cur.execute(
            """
            INSERT INTO pulse_ad_creatives
            (ad_account_id, campaign_id, creative_type, title, body, media_url, thumbnail_url, destination_url,
             media_asset_id, thumbnail_asset_id, media_ready, media_metadata_json, call_to_action,
             status, moderation_status, rejection_reason, metadata_json, compatibility_json,
             moderation_history_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 'draft', '', ?, ?, ?, ?, ?)
            """,
            (
                before.get("ad_account_id"),
                before.get("campaign_id"),
                before.get("creative_type"),
                clean_text(f"{before.get('title')} copy", 100),
                before.get("body"),
                before.get("media_url"),
                before.get("thumbnail_url"),
                before.get("destination_url"),
                before.get("media_asset_id"),
                before.get("thumbnail_asset_id"),
                before.get("media_ready") or 0,
                before.get("media_metadata_json") or "{}",
                before.get("call_to_action"),
                before.get("metadata_json") or "{}",
                before.get("compatibility_json") or "{}",
                clean_json({"source_creative_id": creative_id, "duplicated_at": now}),
                now,
                now,
            ),
        )
        new_id = cur.lastrowid
        pulse_ads_service.audit_log(conn, user_id, "ad_creative_duplicated", "pulse_ad_creatives", new_id, before=before, after={"source_creative_id": creative_id})
        conn.commit()
        return {"creative_id": new_id, "status": "draft", "action": action}
    if action == "delete_draft":
        if before.get("status") != "draft" or before.get("moderation_status") != "draft":
            raise pulse_ads_service.PulseAdsError("Only draft creatives can be deleted. Archive this creative instead.", 409)
        cur.execute("DELETE FROM pulse_ad_creatives WHERE id=?", (creative_id,))
        pulse_ads_service.audit_log(conn, user_id, "ad_creative_draft_deleted", "pulse_ad_creatives", creative_id, before=before, after={})
        conn.commit()
        return {"creative_id": creative_id, "deleted": True, "action": action}
    set_parts = ["status='archived'", "updated_at=?"]
    params = [now]
    if _has_column(conn, "pulse_ad_creatives", "archived_at"):
        set_parts.append("archived_at=?")
        params.append(now)
    params.append(creative_id)
    cur.execute(f"UPDATE pulse_ad_creatives SET {', '.join(set_parts)} WHERE id=?", tuple(params))
    cur.execute("SELECT * FROM pulse_ad_creatives WHERE id=?", (creative_id,))
    after = row_to_dict(cur.fetchone())
    pulse_ads_service.audit_log(conn, user_id, "ad_creative_archived", "pulse_ad_creatives", creative_id, before=before, after=after)
    conn.commit()
    return {"creative_id": creative_id, "status": "archived", "action": action}


def replace_creative(conn, user_id, creative_id, payload: dict) -> dict:
    account_id = _creative_account_id(conn, creative_id)
    _require_account_role(conn, user_id, account_id, WRITE_ROLES)
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_creatives WHERE id=?", (creative_id,))
    before = row_to_dict(cur.fetchone())
    if before.get("moderation_status") == "approved":
        raise pulse_ads_service.PulseAdsError("Approved creatives cannot be replaced. Duplicate it and submit a new version.", 409)
    if payload.get("media_url") or payload.get("thumbnail_url"):
        raise pulse_ads_service.PulseAdsError("Upload replacement media through PulseSoc Creative Studio instead of pasting media URLs.")
    media_asset_id = safe_int(payload.get("media_asset_id"), 0)
    thumbnail_asset_id = safe_int(payload.get("thumbnail_asset_id"), 0)
    if not media_asset_id:
        raise pulse_ads_service.PulseAdsError("Upload replacement media before replacing this creative.")
    media_asset = pulse_ads_service._owned_ad_media_asset(conn, user_id, account_id, media_asset_id, allowed_kinds={"creative_media", "companion_image"})
    if not pulse_ads_service._asset_type_allowed(before.get("creative_type"), media_asset.get("media_type")):
        raise pulse_ads_service.PulseAdsError("Replacement media is not compatible with this creative type.")
    thumbnail_asset = {}
    if thumbnail_asset_id:
        thumbnail_asset = pulse_ads_service._owned_ad_media_asset(conn, user_id, account_id, thumbnail_asset_id, allowed_kinds={"thumbnail", "companion_image"})
    media_public = pulse_ads_service._ad_asset_public(media_asset)
    thumb_public = pulse_ads_service._ad_asset_public(thumbnail_asset)
    metadata = {
        "media_asset_id": media_asset.get("id"),
        "thumbnail_asset_id": thumbnail_asset.get("id") if thumbnail_asset else None,
        "media_type": media_public.get("media_type"),
        "file_size": media_public.get("file_size"),
        "duration_seconds": media_public.get("duration_seconds"),
        "replaced_at": now_iso(),
    }
    cur.execute(
        """
        UPDATE pulse_ad_creatives
        SET media_url=?, thumbnail_url=?, media_asset_id=?, thumbnail_asset_id=?, media_ready=1,
            media_metadata_json=?, metadata_json=?, moderation_status='draft', status='draft', updated_at=?
        WHERE id=?
        """,
        (
            media_public.get("public_url") or "",
            thumb_public.get("thumbnail_url") or media_public.get("thumbnail_url") or "",
            media_asset.get("id"),
            thumbnail_asset.get("id") if thumbnail_asset else None,
            clean_json(metadata),
            clean_json(metadata),
            now_iso(),
            creative_id,
        ),
    )
    cur.execute("SELECT * FROM pulse_ad_creatives WHERE id=?", (creative_id,))
    after = pulse_ads_service.attach_creative_media(conn, row_to_dict(cur.fetchone()))
    pulse_ads_service.audit_log(conn, user_id, "ad_creative_media_replaced", "pulse_ad_creatives", creative_id, before=before, after={"metadata": metadata})
    conn.commit()
    return _creative_public(after)


def billing_summary(conn, user_id, account_id) -> dict:
    _require_account_role(conn, user_id, account_id, {"owner"})
    wallet = pulse_ad_payments.wallet_summary(conn, user_id, account_id)
    cur = conn.cursor()
    cur.execute("SELECT wallet_balance_cents, spend_limit_cents, billing_status, funding_status, updated_at FROM pulse_ad_billing_profiles WHERE account_id=?", (account_id,))
    billing = row_to_dict(cur.fetchone())
    if not billing:
        billing = {
            "wallet_balance_cents": 0,
            "spend_limit_cents": 0,
            "billing_status": "not_configured",
            "funding_status": "prepared",
            "updated_at": "",
        }
    billing["wallet_balance"] = money(billing.get("wallet_balance_cents"))
    billing["spend_limit"] = money(billing.get("spend_limit_cents"))
    billing["live_charging"] = False
    billing["billing_enabled"] = os.getenv("PULSE_ADS_BILLING_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    billing["wallet"] = wallet
    billing["stripe_customer_visible"] = False
    return billing
