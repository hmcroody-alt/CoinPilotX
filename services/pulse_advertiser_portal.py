"""PulseSoc advertiser portal service.

This layer builds advertiser-facing workflows on top of the ads foundation and
delivery engine. It keeps permissions server-side and avoids exposing billing
provider identifiers or private tracking data to clients.
"""

from __future__ import annotations

import json
import logging
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


# The four gates §37 names, in the order the client already presents them.
#
# `campaign_action("resume")` used to check exactly one of them — funding, and
# only as a side effect of `reserve_campaign_budget` — then wrote
# `status='active'` and took a real reserve out of the wallet. Meanwhile
# `select_ads` requires an active account and an approved creative with its media
# approved, neither of which resume looked at. So the advertiser's money could be
# locked against a campaign that was structurally incapable of serving one
# impression, and the product called that campaign "Active".
#
# The order below is not arbitrary. It matches `deliveryBlocker` in
# mobile-native/src/api/adsDelivery.ts — account, then creative, then placement,
# then budget — so an advertiser who is blocked reads the same first reason on
# the campaign card that the server gives them when they press Resume. Two
# surfaces naming different blockers for one campaign is its own kind of dead
# end: you fix the one you were told about and nothing changes.
ACTIVATION_BLOCKERS = {
    "account_suspended": "This ad account is suspended, so its campaigns can't run. Contact support to find out what's needed to lift it.",
    "account_verification_pending": "Your account verification is still in review. Campaigns can run once it's approved.",
    "account_verification_rejected": "Account verification was declined. Update your business details and request verification again.",
    "account_not_verified": "Request account verification before running campaigns. Ads only deliver from a verified account.",
    "account_not_active": "This ad account isn't active yet, so its campaigns can't deliver.",
    "no_creative": "Add an ad to this campaign and submit it for review before running it.",
    "creative_in_review": "No ad in this campaign has been approved yet. It can run once review is decided.",
    "creative_rejected": "Every ad in this campaign was rejected. Open the Policy Center to read the decision, then fix and resubmit.",
    "creative_media_missing": "The approved ad has no uploaded media. Re-upload the file in the campaign editor.",
    "no_placement": "Choose at least one placement so this campaign has somewhere to run.",
    "no_budget": "Set a daily or lifetime budget before running this campaign.",
    "wallet_insufficient": "Your ad wallet doesn't have enough spendable balance to run this campaign. Top up your wallet to activate it.",
}


def _account_activation_blocker(conn, account_id) -> tuple[str, str] | None:
    """Verification and eligibility — the first two of §37's four gates.

    Suspension is checked before verification because it is the more specific
    fact: telling a suspended advertiser to request verification would send them
    to a queue that cannot help them.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT status, verification_status, verification_reason FROM pulse_ad_accounts WHERE id=?",
        (safe_int(account_id),),
    )
    account = row_to_dict(cur.fetchone())
    if not account:
        raise pulse_ads_service.PulseAdsError("Ad account not found.", 404)
    status = clean_text(account.get("status"), 40).lower()
    if status == "suspended":
        return ("account_suspended", ACTIVATION_BLOCKERS["account_suspended"])
    verification = pulse_ads_service.account_verification_state(account)
    if verification == "pending":
        return ("account_verification_pending", ACTIVATION_BLOCKERS["account_verification_pending"])
    if verification == "rejected":
        reason = clean_text(account.get("verification_reason"), 500)
        detail = ACTIVATION_BLOCKERS["account_verification_rejected"]
        # The reviewer's own words, when there are any. A rejection the advertiser
        # cannot answer is the dead end; the reason is what makes it answerable.
        if reason:
            detail = f"Account verification was declined: {reason} Update your details and request verification again."
        return ("account_verification_rejected", detail)
    if verification != "verified":
        return ("account_not_verified", ACTIVATION_BLOCKERS["account_not_verified"])
    if status != "active":
        # Verified but not active is a state approval is supposed to make
        # impossible — `approve_account_verification` writes both columns
        # together. Reaching it means something wrote one without the other, and
        # the selector reads `status`, so this fails closed rather than trusting
        # the friendlier of two disagreeing columns.
        return ("account_not_active", ACTIVATION_BLOCKERS["account_not_active"])
    return None


def _creative_activation_blocker(conn, campaign_id) -> tuple[str, str] | None:
    """Policy approval — §37's third gate, read the way the selector reads it.

    The selector needs a creative that is approved on *both* columns and whose
    media (and thumbnail, if it has one) is approved too. Checking only
    `moderation_status` here would let a campaign through that the selector then
    silently drops, which is the same contradiction one layer down.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT cr.id, cr.creative_type, cr.status, cr.moderation_status,
               cr.media_asset_id, cr.thumbnail_asset_id,
               ma.moderation_status AS media_moderation,
               ta.moderation_status AS thumb_moderation
        FROM pulse_ad_creatives cr
        LEFT JOIN pulse_ad_media_assets ma ON ma.id=cr.media_asset_id
        LEFT JOIN pulse_ad_media_assets ta ON ta.id=cr.thumbnail_asset_id
        WHERE cr.campaign_id=?
        """,
        (safe_int(campaign_id),),
    )
    creatives = [row_to_dict(row) for row in cur.fetchall()]
    if not creatives:
        return ("no_creative", ACTIVATION_BLOCKERS["no_creative"])

    media_required = {"image", "video", "audio"}
    approved_but_unusable = False
    saw_pending = False
    saw_rejected = False
    for creative in creatives:
        moderation = clean_text(creative.get("moderation_status"), 40).lower()
        status = clean_text(creative.get("status"), 40).lower()
        if moderation != "approved" or status != "approved":
            if moderation in {"pending", "submitted", "in_review"}:
                saw_pending = True
            elif moderation in {"rejected", "declined"}:
                saw_rejected = True
            continue
        creative_type = clean_text(creative.get("creative_type"), 40).lower()
        if creative_type in media_required and not safe_int(creative.get("media_asset_id")):
            approved_but_unusable = True
            continue
        if safe_int(creative.get("media_asset_id")) and clean_text(creative.get("media_moderation"), 40).lower() != "approved":
            approved_but_unusable = True
            continue
        if safe_int(creative.get("thumbnail_asset_id")) and clean_text(creative.get("thumb_moderation"), 40).lower() != "approved":
            approved_but_unusable = True
            continue
        return None

    if approved_but_unusable:
        return ("creative_media_missing", ACTIVATION_BLOCKERS["creative_media_missing"])
    if saw_pending:
        return ("creative_in_review", ACTIVATION_BLOCKERS["creative_in_review"])
    if saw_rejected:
        return ("creative_rejected", ACTIVATION_BLOCKERS["creative_rejected"])
    return ("no_creative", ACTIVATION_BLOCKERS["no_creative"])


def activation_blocker(conn, account_id, campaign: dict) -> tuple[str, str] | None:
    """The first reason this campaign cannot run, or None if it can.

    "First" rather than "all": a list of four problems is harder to act on than
    one, and the reader clears them in this order anyway — an approved creative
    on a suspended account still cannot run.
    """
    account_gate = _account_activation_blocker(conn, account_id)
    if account_gate:
        return account_gate
    campaign_id = safe_int(campaign.get("id"))
    creative_gate = _creative_activation_blocker(conn, campaign_id)
    if creative_gate:
        return creative_gate
    if not _campaign_placements(conn, campaign_id):
        return ("no_placement", ACTIVATION_BLOCKERS["no_placement"])
    budget = safe_int(campaign.get("lifetime_budget_cents")) or safe_int(campaign.get("daily_budget_cents"))
    if budget <= 0:
        return ("no_budget", ACTIVATION_BLOCKERS["no_budget"])
    return None


# Which campaign statuses each action may be applied from.
#
# There was no precondition at all: `resume` on an archived or completed campaign
# set it back to 'active' and reserved budget for it, so "archive" was a label
# rather than an end state and a finished campaign could start spending again.
#
# `resume` from 'active' is allowed and idempotent — a second press of a button
# whose first press succeeded should not be an error message.
CAMPAIGN_TRANSITIONS = {
    "pause": {"active", "running", "paused"},
    "resume": {"paused", "active", "running"},
    "archive": {"draft", "pending_review", "paused", "completed", "rejected"},
    "submit": {"draft", "rejected"},
    "complete": {"active", "running", "paused"},
}

# Why a given action is refused from a given status, in the advertiser's terms.
# A bare "invalid transition" tells the reader what the machine thinks, not what
# they can do about it.
TRANSITION_REFUSALS = {
    ("resume", "archived"): "This campaign is archived. Duplicate it to run it again.",
    ("resume", "completed"): "This campaign has finished. Duplicate it to run it again.",
    ("resume", "draft"): "Submit this campaign for review before running it.",
    ("resume", "pending_review"): "This campaign is still in review. It can run once that's decided.",
    ("resume", "rejected"): "This campaign was rejected. Open the Policy Center to read the decision.",
    ("resume", "suspended"): "This campaign was suspended by our team. Contact support before running it again.",
    ("pause", "draft"): "This campaign hasn't started, so there's nothing to pause.",
    ("pause", "archived"): "This campaign is archived and isn't running.",
    ("pause", "completed"): "This campaign has already finished.",
    ("submit", "active"): "This campaign is already running.",
    ("submit", "pending_review"): "This campaign is already in review.",
    ("submit", "archived"): "This campaign is archived. Duplicate it to submit it again.",
    ("complete", "draft"): "This campaign hasn't run yet, so there's nothing to complete.",
    ("complete", "archived"): "This campaign is archived.",
    ("complete", "completed"): "This campaign has already finished.",
    ("archive", "archived"): "This campaign is already archived.",
    ("archive", "active"): "Pause this campaign before archiving it.",
    ("archive", "running"): "Pause this campaign before archiving it.",
}


def _assert_transition_allowed(action: str, current_status: str) -> None:
    allowed = CAMPAIGN_TRANSITIONS.get(action)
    if allowed is None:
        return
    status = clean_text(current_status, 40).lower() or "draft"
    if status in allowed:
        return
    message = TRANSITION_REFUSALS.get((action, status))
    if not message:
        message = f"This campaign is {status} and can't be {action}d from there."
    raise pulse_ads_service.PulseAdsError(message, 409)


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
    """Every account the user can reach, with per-account counts and spend.

    The per-account figures are correlated subqueries rather than aggregates over
    joined rows, and that is the whole point. This query used to LEFT JOIN both
    `pulse_ad_campaigns` and `pulse_ad_creatives` onto one account row, which is a
    cartesian product: an account with 4 campaigns and 9 creatives produced 36
    rows, and every aggregate that was not wrapped in DISTINCT counted each fact
    once per row of the *other* table.

    `campaign_count` survived because it was `COUNT(DISTINCT c.id)`. The other
    three did not. `active_campaigns` was multiplied by the creative count,
    `pending_reviews` by the campaign count, and `total_spend_cents` by the
    creative count — so an advertiser with nine creatives saw nine times their
    real spend on the accounts list, and `portal_summary` sums this column into
    `metrics.total_spend`, so the portal rollup was wrong by the same factor.
    `_account_health` reads `pending_reviews`, so the health score was wrong too.

    A subquery cannot be multiplied by a sibling join because there is no sibling
    join. The profile LEFT JOIN stays: `pulse_ad_account_profiles.account_id` is a
    PRIMARY KEY, so it is 1:1 and cannot fan out.
    """
    account_ids = _account_ids_for_user(conn, user_id)
    if not account_ids:
        return []
    placeholders = ",".join("?" for _ in account_ids)
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT a.*, p.industry, p.website, p.contact_email,
               (SELECT COUNT(*) FROM pulse_ad_campaigns c
                 WHERE c.ad_account_id=a.id) AS campaign_count,
               (SELECT COUNT(*) FROM pulse_ad_campaigns c
                 WHERE c.ad_account_id=a.id
                   AND c.status IN ('running','active')) AS active_campaigns,
               (SELECT COUNT(*) FROM pulse_ad_creatives cr
                 WHERE cr.ad_account_id=a.id
                   AND cr.moderation_status='pending') AS pending_reviews,
               (SELECT COALESCE(SUM(c.spent_cents), 0) FROM pulse_ad_campaigns c
                 WHERE c.ad_account_id=a.id) AS total_spend_cents
        FROM pulse_ad_accounts a
        LEFT JOIN pulse_ad_account_profiles p ON p.account_id=a.id
        WHERE a.id IN ({placeholders})
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
            # A wallet we could not read is not a wallet holding nothing. This
            # used to return a fully populated $0.00 summary, which the portal
            # rendered exactly like a real empty wallet: the advertiser was told
            # their balance was zero when the truth was that we did not know.
            # The figures are omitted rather than invented, and the client is
            # told why so it can say so.
            logging.exception(
                "PULSE_AD_WALLET_SUMMARY_FAILED user_id=%s account_id=%s", user_id, account.get("id")
            )
            wallet_rows.append({
                "account_id": safe_int(account.get("id")),
                "unavailable": True,
                "unavailable_reason": "Wallet balance could not be loaded. This is a temporary error, not a zero balance.",
                "available_balance_cents": None,
                "reserved_budget_cents": None,
                "lifetime_funded_cents": None,
                "lifetime_spent_cents": None,
                "spendable_balance_cents": None,
                "amount_owed_cents": None,
                "available_balance": "",
                "reserved_budget": "",
                "spendable_balance": "",
                "amount_owed": "",
                "transactions": [],
                "receipts": [],
                "billing_enabled": pulse_ad_payments.billing_enabled(),
                "stripe_ready": pulse_ad_payments.stripe_ready(),
            })
    unread_notes = sum(1 for item in note_rows if item.get("status") == "unread")
    status_counts = campaign_status_counts(conn, account_ids)
    spend_total = sum(safe_int(account.get("total_spend_cents")) for account in accounts)
    # Totals are summed only over wallets that actually loaded, and the count of
    # the ones that did not travels with them. A total that quietly treats an
    # unreadable wallet as zero is a wrong number presented as a right one.
    readable_wallets = [wallet for wallet in wallet_rows if not wallet.get("unavailable")]
    unavailable_wallets = len(wallet_rows) - len(readable_wallets)
    wallet_total = sum(safe_int(wallet.get("available_balance_cents")) for wallet in readable_wallets)
    reserved_total = sum(safe_int(wallet.get("reserved_budget_cents")) for wallet in readable_wallets)
    spendable_total = sum(safe_int(wallet.get("spendable_balance_cents")) for wallet in readable_wallets)
    owed_total = sum(safe_int(wallet.get("amount_owed_cents")) for wallet in readable_wallets)
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
            # A refunded or disputed top-up can leave the account owing money.
            # Spendable correctly reads $0.00 in that case, which on its own
            # looks identical to an account that simply never funded.
            "amount_owed_cents": owed_total,
            "amount_owed": money(owed_total),
            "wallets_unavailable": unavailable_wallets,
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
        # The `or ["feed_inline"]` that used to be here turned "run this campaign
        # nowhere" into "run it in the feed". An advertiser clearing their
        # placement selection — to pause a surface, or to rebuild the set — had a
        # placement they did not choose attached in its place, and kept paying.
        # Clearing the list now leaves the campaign with none, which
        # `_campaign_activation_blocker` reports as `no_placement` rather than
        # letting it spend.
        #
        # Resolved before the DELETE, not after. This function destroys the whole
        # placement set and rebuilds it, so a bad key discovered halfway through
        # the rebuild would leave the campaign attached to less than it had when
        # the advertiser pressed Save — and whether that survives depends on the
        # connection's transaction mode, which is not a thing to bet a live
        # campaign's delivery on.
        resolved = pulse_ads_service.resolve_placement_keys(conn, payload.get("placements"))
        cur.execute("DELETE FROM pulse_ad_campaign_placements WHERE campaign_id=?", (campaign_id,))
        pulse_ads_service.attach_campaign_placements(conn, campaign_id, [key for key, _id in resolved])
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
    role = _require_account_role(conn, user_id, account_id, WRITE_ROLES)
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_ad_campaigns WHERE id=?", (campaign_id,))
    before = row_to_dict(cur.fetchone())
    if not before:
        raise pulse_ads_service.PulseAdsError("Campaign not found.", 404)
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
    _assert_transition_allowed(action, before.get("status"))
    reserve_result = None
    if action == "resume":
        # Verification, policy, eligibility and placement — checked before any
        # money moves, because `reserve_campaign_budget` reduces the account's
        # spendable balance and there is no point locking funds behind a gate the
        # campaign cannot pass.
        gate = activation_blocker(conn, account_id, before)
        if gate:
            raise pulse_ads_service.PulseAdsError(gate[1], 409)
        # The role rule, stated here rather than discovered inside the payment
        # layer. `reserve_campaign_budget` begins with an owner check and raises
        # "Campaign not found." 404 when it fails — so a campaign manager, a
        # write role this same function authorised four lines above, was told the
        # campaign did not exist. It does exist; they simply cannot spend from
        # the wallet. That is a different sentence and a different status code.
        if role != "owner":
            raise pulse_ads_service.PulseAdsError(
                "Only the account owner can resume a campaign, because resuming reserves budget from the wallet.",
                403,
            )
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


def campaign_review_gate(conn, account_id, campaign: dict) -> tuple[str, str] | None:
    """The activation blockers plus the wallet check, for review-time decisions.

    `activation_blocker` covers account, creative, placement and budget but not
    the wallet — resume leaves that to `reserve_campaign_budget`, which raises.
    Review-time activation needs a non-raising answer so the auto-activate hook
    can leave a blocked campaign in `pending_review` and tell the owner why,
    instead of failing the creative approval that triggered it.
    """
    gate = activation_blocker(conn, account_id, campaign)
    if gate:
        return gate
    if not pulse_ad_payments.campaign_can_spend(conn, campaign):
        return ("wallet_insufficient", ACTIVATION_BLOCKERS["wallet_insufficient"])
    return None


def activate_reviewed_campaign(conn, actor_user_id, campaign: dict, owner_user_id) -> dict:
    """The one `pending_review` → `active` implementation.

    Called from admin `approve_campaign` and from `approve_creative`'s
    auto-activate hook — one implementation so the two paths cannot drift.
    Caller has already verified the campaign is `pending_review` and that
    `campaign_review_gate` passes.

    Budget is reserved exactly the way resume reserves it — via
    `pulse_ad_payments.reserve_campaign_budget`, run as the account owner
    because that function's owner check is the rule that only the owner's
    wallet backs a campaign. It can still raise (its spendable threshold is
    stricter than `campaign_can_spend`); nothing is written before it runs.

    There is no `scheduled` status in this product. A campaign whose
    `start_at` is in the future activates now and `select_ads` withholds it
    until `start_at` — delivery already filters on `start_at`/`end_at`.
    """
    campaign_id = safe_int(campaign.get("id"), minimum=1)
    account_id = safe_int(campaign.get("ad_account_id"), minimum=1)
    reserve_result = pulse_ad_payments.reserve_campaign_budget(conn, owner_user_id, campaign_id)
    now = now_iso()
    set_parts = ["status='active'", "updated_at=?"]
    params = [now]
    if _has_column(conn, "pulse_ad_campaigns", "approved_at"):
        set_parts.append("approved_at=?")
        params.append(now)
    params.append(campaign_id)
    cur = conn.cursor()
    cur.execute(f"UPDATE pulse_ad_campaigns SET {', '.join(set_parts)} WHERE id=?", tuple(params))
    cur.execute("SELECT * FROM pulse_ad_campaigns WHERE id=?", (campaign_id,))
    after = row_to_dict(cur.fetchone())
    name = clean_text(campaign.get("campaign_name"), 120)
    start_at = clean_text(campaign.get("start_at"), 40)
    body = f"{name} was approved and is now active."
    if start_at and start_at > now:
        body = f"{name} was approved and will start delivering at its scheduled start time ({start_at})."
    _add_history(conn, campaign_id, actor_user_id, "campaign_approved", campaign, after)
    _add_notification(conn, account_id, campaign_id, None, owner_user_id, "campaign_approved", "Campaign approved", body)
    pulse_ads_service.audit_log(conn, actor_user_id, "ad_campaign_approved", "pulse_ad_campaigns", campaign_id, before=campaign, after=after)
    conn.commit()
    return {"campaign_id": campaign_id, "status": "active", "budget_reserve": reserve_result}


def _campaign_with_owner(conn, campaign_id) -> dict:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.*, a.owner_user_id AS account_owner_user_id
        FROM pulse_ad_campaigns c
        JOIN pulse_ad_accounts a ON a.id=c.ad_account_id
        WHERE c.id=?
        """,
        (safe_int(campaign_id, minimum=1),),
    )
    campaign = row_to_dict(cur.fetchone())
    if not campaign:
        raise pulse_ads_service.PulseAdsError("Campaign not found.", 404)
    return campaign


def approve_campaign(conn, admin_user_id, campaign_id) -> dict:
    """Admin approves a submitted campaign, closing the review dead end.

    `approve_creative` decides the creative and the auto-activate hook usually
    finishes the job; this is the explicit admin decision for campaigns the
    hook could not activate (blocked at approval time, then fixed) or where a
    reviewer wants to decide the campaign as a whole.

    Only `pending_review` can be approved. Approving an already-active
    campaign is idempotent rather than an error, matching the transitions
    map's stance on resume. The blockers are re-checked here — approval is
    the moment money gets reserved, and the state may have changed since the
    creatives were reviewed.
    """
    campaign = _campaign_with_owner(conn, campaign_id)
    status = clean_text(campaign.get("status"), 40).lower()
    if status in {"active", "running"}:
        return {"campaign_id": safe_int(campaign.get("id")), "status": status, "already_active": True}
    if status != "pending_review":
        raise pulse_ads_service.PulseAdsError(
            f"This campaign is {status or 'draft'} and isn't waiting for review, so it can't be approved.", 409
        )
    account_id = safe_int(campaign.get("ad_account_id"))
    gate = campaign_review_gate(conn, account_id, campaign)
    if gate:
        raise pulse_ads_service.PulseAdsError(gate[1], 409)
    return activate_reviewed_campaign(conn, admin_user_id, campaign, safe_int(campaign.get("account_owner_user_id")))


def reject_campaign(conn, admin_user_id, campaign_id, reason: str = "") -> dict:
    """Admin declines a submitted campaign, with a reason the advertiser can act on.

    The reason is required for the same cause `reject_account_verification`
    requires one: a rejection with no reason is a locked door with no sign on
    it. `rejected` is a modeled status — submit and archive both accept it, so
    the advertiser can fix the campaign and resubmit, or archive it.
    """
    reason = clean_text(reason, 500)
    if not reason:
        raise pulse_ads_service.PulseAdsError("A rejection reason is required so the advertiser knows what to fix.")
    campaign = _campaign_with_owner(conn, campaign_id)
    status = clean_text(campaign.get("status"), 40).lower()
    if status == "rejected":
        return {"campaign_id": safe_int(campaign.get("id")), "status": "rejected", "already_rejected": True}
    if status != "pending_review":
        raise pulse_ads_service.PulseAdsError(
            f"This campaign is {status or 'draft'} and isn't waiting for review, so it can't be rejected.", 409
        )
    resolved_id = safe_int(campaign.get("id"))
    account_id = safe_int(campaign.get("ad_account_id"))
    owner_user_id = safe_int(campaign.get("account_owner_user_id"))
    now = now_iso()
    cur = conn.cursor()
    cur.execute("UPDATE pulse_ad_campaigns SET status='rejected', updated_at=? WHERE id=?", (now, resolved_id))
    cur.execute("SELECT * FROM pulse_ad_campaigns WHERE id=?", (resolved_id,))
    after = row_to_dict(cur.fetchone())
    name = clean_text(campaign.get("campaign_name"), 120)
    _add_history(conn, resolved_id, admin_user_id, "campaign_rejected", campaign, after)
    _add_notification(
        conn, account_id, resolved_id, None, owner_user_id, "campaign_rejected",
        "Campaign rejected", f"{name} was rejected: {reason} Fix it and submit it again, or archive it.",
    )
    pulse_ads_service.audit_log(
        conn, admin_user_id, "ad_campaign_rejected", "pulse_ad_campaigns", resolved_id,
        before=campaign, after={"status": "rejected", "reason": reason},
    )
    conn.commit()
    return {"campaign_id": resolved_id, "status": "rejected", "reason": reason}


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
