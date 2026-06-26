#!/usr/bin/env python3
"""Activate the internal Pulse Radio sponsored ad through the real ads flow."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_MEDIA_RELATIVE = "uploads/pulse_ads/pulse-radio-sponsored-ad.png"
PUBLIC_MEDIA_URL = f"/static/{DEFAULT_MEDIA_RELATIVE}"
DESTINATION_URL = "/pulse/music"
PLACEMENTS = [
    "feed_inline",
    "pulse_radio_sponsor",
    "dashboard_sponsor",
    "feed_side_ufo_desktop",
    "feed_inline_ufo_mobile",
]
TITLE = "Pulse Radio"
BODY = "Live music, creator shows, and nonstop PulseSoc sound."
CTA = "Listen Now"
CAMPAIGN_NAME = "Pulse Radio Sponsored Signal"
ACCOUNT_NAME = "PulseSoc Internal Promotions"


class ActivationError(RuntimeError):
    pass


def _image_source() -> Path:
    configured = os.getenv("PULSE_RADIO_AD_IMAGE_SOURCE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (ROOT / "static" / DEFAULT_MEDIA_RELATIVE).resolve()


def _validate_image(path: Path) -> None:
    if not path.exists() or not path.is_file():
        raise ActivationError("Pulse Radio ad image is missing.")
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ActivationError("Pulse Radio ad image is too large.")
    header = path.read_bytes()[:16]
    is_png = header.startswith(b"\x89PNG\r\n\x1a\n")
    is_jpeg = header.startswith(b"\xff\xd8\xff")
    if not (is_png or is_jpeg):
        raise ActivationError("Pulse Radio ad image must be PNG or JPEG.")


def import_media_asset() -> str:
    source = _image_source()
    _validate_image(source)
    destination = (ROOT / "static" / DEFAULT_MEDIA_RELATIVE).resolve()
    if ROOT not in destination.parents:
        raise ActivationError("Unsafe destination path.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source != destination:
        shutil.copy2(source, destination)
    destination.chmod(0o644)
    return PUBLIC_MEDIA_URL


def _fetch_one(cur, sql: str, params=()):
    cur.execute(sql, params)
    row = cur.fetchone()
    if not row:
        return {}
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    return dict(row)


def _fetch_all(cur, sql: str, params=()):
    cur.execute(sql, params)
    rows = cur.fetchall()
    if not rows:
        return []
    if hasattr(rows[0], "keys"):
        return [{key: row[key] for key in row.keys()} for row in rows]
    return [dict(row) for row in rows]


def _ensure_owner_user(conn, owner_user_id: int) -> None:
    from services import pulse_ads_service

    now = pulse_ads_service.now_iso()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (owner_user_id,))
    if cur.fetchone():
        return
    cur.execute(
        """
        INSERT INTO users (user_id, username, display_name, email, account_status, created_at, signup_time)
        VALUES (?, ?, ?, ?, 'active', ?, ?)
        """,
        (
            owner_user_id,
            "pulsesoc_promotions",
            "PulseSoc Promotions",
            "promotions@pulsesoc.com",
            now,
            now,
        ),
    )
    conn.commit()


def _ensure_account(conn, owner_user_id: int) -> dict:
    from services import pulse_ads_service

    cur = conn.cursor()
    account = _fetch_one(
        cur,
        "SELECT * FROM pulse_ad_accounts WHERE owner_user_id=? AND business_name=? ORDER BY id DESC LIMIT 1",
        (owner_user_id, ACCOUNT_NAME),
    )
    if not account:
        account = pulse_ads_service.create_ad_account(
            conn,
            owner_user_id,
            {
                "business_name": ACCOUNT_NAME,
                "business_email": "promotions@pulsesoc.com",
                "business_website": "https://pulsesoc.com",
                "business_type": "internal_promotion",
            },
        )
    cur.execute(
        """
        UPDATE pulse_ad_accounts
        SET status='active', verification_status='verified', updated_at=?
        WHERE id=?
        """,
        (pulse_ads_service.now_iso(), account["id"]),
    )
    conn.commit()
    return _fetch_one(cur, "SELECT * FROM pulse_ad_accounts WHERE id=?", (account["id"],))


def _ensure_campaign(conn, owner_user_id: int, account_id: int) -> dict:
    from services import pulse_ads_service

    cur = conn.cursor()
    campaign = _fetch_one(
        cur,
        "SELECT * FROM pulse_ad_campaigns WHERE ad_account_id=? AND campaign_name=? ORDER BY id DESC LIMIT 1",
        (account_id, CAMPAIGN_NAME),
    )
    if not campaign:
        campaign = pulse_ads_service.create_campaign(
            conn,
            owner_user_id,
            {
                "ad_account_id": account_id,
                "campaign_name": CAMPAIGN_NAME,
                "objective": "radio",
                "budget_type": "lifetime",
                "lifetime_budget_cents": 0,
                "placements": PLACEMENTS,
            },
        )
    pulse_ads_service.attach_campaign_placements(conn, campaign["id"], PLACEMENTS)
    cur.execute(
        """
        UPDATE pulse_ad_campaigns
        SET status='active', objective='radio', priority=100, start_at='', end_at='', updated_at=?
        WHERE id=?
        """,
        (pulse_ads_service.now_iso(), campaign["id"]),
    )
    conn.commit()
    return _fetch_one(cur, "SELECT * FROM pulse_ad_campaigns WHERE id=?", (campaign["id"],))


def _ensure_creative(conn, owner_user_id: int, campaign_id: int, media_url: str) -> dict:
    from services import pulse_ads_service

    cur = conn.cursor()
    creative = _fetch_one(
        cur,
        """
        SELECT * FROM pulse_ad_creatives
        WHERE campaign_id=? AND title=? AND body=? AND media_url=? AND destination_url=?
        ORDER BY id DESC LIMIT 1
        """,
        (campaign_id, TITLE, BODY, media_url, DESTINATION_URL),
    )
    if not creative:
        creative = pulse_ads_service.create_creative(
            conn,
            owner_user_id,
            {
                "campaign_id": campaign_id,
                "creative_type": "image",
                "title": TITLE,
                "body": BODY,
                "media_url": media_url,
                "thumbnail_url": media_url,
                "destination_url": DESTINATION_URL,
                "call_to_action": CTA,
                "category": "radio",
            },
        )
        creative = pulse_ads_service.submit_creative_for_review(conn, owner_user_id, creative["id"])
    if creative.get("moderation_status") != "approved" or creative.get("status") != "approved":
        pulse_ads_service.approve_creative(conn, owner_user_id, creative["id"], "PulseSoc-owned Pulse Radio house campaign approved.")
    return _fetch_one(cur, "SELECT * FROM pulse_ad_creatives WHERE id=?", (creative["id"],))


def _verify_eligibility(conn, creative_id: int) -> dict:
    from services import pulse_ads_service

    cur = conn.cursor()
    rows = _fetch_all(
        cur,
        """
        SELECT p.placement_key, p.device_type, p.is_active, p.supported_creative_types
        FROM pulse_ad_campaign_placements cp
        JOIN pulse_ad_placements p ON p.id=cp.placement_id
        JOIN pulse_ad_creatives cr ON cr.campaign_id=cp.campaign_id
        WHERE cr.id=?
        ORDER BY p.placement_key
        """,
        (creative_id,),
    )
    keys = {row["placement_key"] for row in rows}
    missing = sorted(set(PLACEMENTS) - keys)
    if missing:
        raise ActivationError(f"Pulse Radio campaign is missing required placements: {missing}")
    desktop_session = "pulse-radio-install-desktop"
    mobile_session = "pulse-radio-install-mobile"
    radio_session = "pulse-radio-install-radio"
    dashboard_session = "pulse-radio-install-dashboard"
    desktop_ads = pulse_ads_service.select_ads(conn, user_id=None, session_id=desktop_session, context="home", device_type="desktop", limit=5)
    mobile_ads = pulse_ads_service.select_ads(conn, user_id=None, session_id=mobile_session, context="home", device_type="mobile", limit=5)
    radio_ads = pulse_ads_service.select_ads(conn, user_id=None, session_id=radio_session, context="radio", device_type="mobile", limit=3)
    dashboard_ads = pulse_ads_service.select_ads(conn, user_id=None, session_id=dashboard_session, context="dashboard", device_type="desktop", limit=3)
    served = desktop_ads + mobile_ads + radio_ads + dashboard_ads
    if creative_id not in {int(ad.get("creative_id") or 0) for ad in served}:
        raise ActivationError("Pulse Radio creative is approved but did not serve through the delivery engine.")
    matching = next(ad for ad in desktop_ads if int(ad.get("creative_id") or 0) == creative_id)
    impression = pulse_ads_service.record_impression(conn, matching, viewer_user_id=None, session_id=desktop_session, device_type="desktop", viewport="1440x900")
    click = pulse_ads_service.record_click(conn, matching, viewer_user_id=None, session_id=desktop_session)
    hidden = pulse_ads_service.record_event(conn, {**matching, "event_type": "hide", "reason": "install verification"}, viewer_user_id=None, session_id=desktop_session)
    return {
        "placements": rows,
        "desktop_served": any(int(ad.get("creative_id") or 0) == creative_id for ad in desktop_ads),
        "mobile_served": any(int(ad.get("creative_id") or 0) == creative_id for ad in mobile_ads),
        "radio_served": any(int(ad.get("creative_id") or 0) == creative_id for ad in radio_ads),
        "dashboard_served": any(int(ad.get("creative_id") or 0) == creative_id for ad in dashboard_ads),
        "impression_id": impression.get("impression_id"),
        "click_id": click.get("click_id"),
        "hide_event_id": hidden.get("event_id"),
    }


def activate_pulse_radio_ad() -> dict:
    import bot
    from services import pulse_ads_service

    bot.INIT_DB_COMPLETED = False
    bot.init_db()
    media_url = import_media_asset()
    owner_user_id = int(os.getenv("PULSE_RADIO_AD_OWNER_USER_ID", "1") or "1")
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    try:
        pulse_ads_service.seed_placements(conn.cursor())
        conn.commit()
        _ensure_owner_user(conn, owner_user_id)
        account = _ensure_account(conn, owner_user_id)
        campaign = _ensure_campaign(conn, owner_user_id, account["id"])
        creative = _ensure_creative(conn, owner_user_id, campaign["id"], media_url)
        verification = _verify_eligibility(conn, creative["id"])
        return {
            "ok": True,
            "account_id": account["id"],
            "campaign_id": campaign["id"],
            "creative_id": creative["id"],
            "media_url": media_url,
            "destination_url": DESTINATION_URL,
            "creative_status": creative.get("status"),
            "moderation_status": creative.get("moderation_status"),
            "campaign_status": campaign.get("status"),
            "placements": [row["placement_key"] for row in verification["placements"]],
            "desktop_served": verification["desktop_served"],
            "mobile_served": verification["mobile_served"],
            "radio_served": verification["radio_served"],
            "dashboard_served": verification["dashboard_served"],
            "tracking_verified": bool(verification["impression_id"] and verification["click_id"] and verification["hide_event_id"]),
        }
    finally:
        conn.close()


def main() -> int:
    result = activate_pulse_radio_ad()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
