#!/usr/bin/env python3
"""Audit PulseSoc Status evolution contracts."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402

BOT = ROOT / "bot.py"
STATUS_JS = ROOT / "static/js/pulse_status_viewer.js"
HOME_JS = ROOT / "static/js/pulse_home_core.js"
STATUS_CSS = ROOT / "static/css/pulse_status_system.css"


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def ensure_user(user_id: int, username: str, email: str) -> int:
    conn = bot.db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (user_id,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (user_id, username, display_name, email, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, ?, 1)",
            (
                user_id,
                username,
                username.replace("_", " ").title(),
                email,
                bot.datetime.utcnow().isoformat(timespec="seconds"),
            ),
        )
    conn.commit()
    conn.close()
    return user_id


def runtime_audit() -> None:
    bot.init_db()
    owner_id = ensure_user(950781, "status_evolution_owner", "status-evolution-owner@example.test")
    viewer_id = ensure_user(950782, "status_evolution_viewer", "status-evolution-viewer@example.test")

    owner = bot.webhook_app.test_client()
    with owner.session_transaction() as sess:
        sess["account_user_id"] = owner_id
    viewer = bot.webhook_app.test_client()
    with viewer.session_transaction() as sess:
        sess["account_user_id"] = viewer_id

    created = owner.post(
        "/api/pulse/status",
        json={"status_type": "text", "body": "Status evolution runtime audit", "visibility": "public"},
    )
    created_payload = created.get_json() or {}
    expect(created.status_code == 200 and created_payload.get("ok") and created_payload.get("status_id"), "Runtime status publish works")
    status_id = int(created_payload["status_id"])

    rail = viewer.get("/api/pulse/status/rail?lane=global")
    rail_payload = rail.get_json() or {}
    expect(rail.status_code == 200 and rail_payload.get("ok"), "Runtime status rail returns ok")
    expect(isinstance(rail_payload.get("rail_items"), list) and isinstance(rail_payload.get("items"), list), "Runtime rail exposes grouped and viewer sequence items")
    expect((rail_payload.get("discovery_signal") or {}).get("ranking") == "live_unseen_creator_grouped_engagement", "Runtime rail exposes discovery ranking")

    feed = viewer.get("/api/pulse/feed?limit=3")
    feed_payload = feed.get_json() or {}
    expect(feed.status_code == 200 and "status_activity" in (feed_payload.get("intelligence") or {}), "Runtime feed includes status activity signal")

    view_response = viewer.post(
        f"/api/pulse/status/{status_id}/view",
        json={"source": "runtime_audit", "completed": True, "completion_ratio": 0.9, "watch_ms": 1200},
    )
    view_payload = view_response.get_json() or {}
    expect(view_response.status_code == 200 and view_payload.get("ok") and not view_payload.get("owner_analytics"), "Non-owner view is accepted without owner analytics")

    share_response = viewer.post(f"/api/pulse/status/{status_id}/share", json={"surface": "runtime_audit"})
    share_payload = share_response.get_json() or {}
    expect(share_response.status_code == 200 and share_payload.get("ok") and int(share_payload.get("share_count") or 0) >= 1, "Runtime status share tracking works")

    owner_view = owner.post(f"/api/pulse/status/{status_id}/view", json={"source": "owner_runtime_audit"})
    owner_payload = owner_view.get_json() or {}
    expect(owner_view.status_code == 200 and bool(owner_payload.get("owner_analytics")), "Owner receives analytics only for owned status")

    conn = bot.db()
    cur = conn.cursor()
    now = bot.datetime.utcnow()
    cur.execute(
        "INSERT INTO pulse_status (user_id, status_type, body, visibility, created_at, expires_at) VALUES (?, 'text', ?, 'private', ?, ?)",
        (
            owner_id,
            "Private status evolution audit",
            now.isoformat(timespec="seconds"),
            (now + bot.timedelta(hours=24)).isoformat(timespec="seconds"),
        ),
    )
    private_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    private_view = viewer.post(f"/api/pulse/status/{private_id}/view", json={"source": "privacy_runtime_audit"})
    expect(private_view.status_code == 404, "Runtime private status is not exposed to another user")


def main() -> None:
    bot = BOT.read_text(encoding="utf-8")
    viewer = STATUS_JS.read_text(encoding="utf-8")
    home = HOME_JS.read_text(encoding="utf-8")
    css = STATUS_CSS.read_text(encoding="utf-8")

    expect("pulse_status_group_creator_rows" in bot, "Status rail groups multiple statuses per creator")
    expect("author_live" in bot and "pulse_live_sessions" in bot, "Status rail can prioritize live creators")
    expect("viewer_viewed" in bot and "pulse_status_sort_key" in bot, "Status rail prioritizes unseen statuses")
    expect("rail_items" in bot and "data.rail_items || data.items" in home, "Home consumes grouped rail items without breaking full viewer items")
    expect("pulse-status-ring-progress" in css and "pulseStatusRingOrbit" in css, "Neon animated progress rings are styled")
    expect(".pulse-status-story-nav" in css and "display: none !important" in css, "Visible next/back buttons remain hidden")
    expect("pointerdown" in viewer and "pauseStory()" in viewer and "resumeStory()" in viewer, "Viewer supports hold-to-pause")
    expect("navigateStory" in viewer and "data-status-story-next" in bot, "Viewer supports tap/swipe navigation and auto-advance")
    expect("object-fit: contain !important" in css, "Viewer preserves original media aspect ratios")
    expect("https://stream.mux.com/${media.mux_playback_id}.m3u8" in viewer and "mux_hls_url" in bot, "Mux playback remains canonical")
    expect("pulse_status_row_for_viewer" in bot and "COALESCE(s.visibility,'public')='public'" in bot and "followers" in bot, "Status interactions enforce privacy server-side")
    expect("owner_analytics" in bot and "completion_rate" in bot and "pulse_status_shares" in bot, "Creator-only analytics are available")
    expect("navigator.sendBeacon" in viewer and "completion_ratio" in viewer and "watch_ms" in viewer, "Viewer reports completion without blocking UI")
    expect("/api/pulse/status/${statusViewerCurrentId}/share" in bot, "Share tracking is wired")
    expect("viewer.dataset.statusCurrentId=String(currentStatusId||'')" in bot, "Legacy Home status opener exposes current status id")
    expect("/api/pulse/status/${currentStatusId}/share" in bot and "surface:'home_rail'" in bot, "Legacy Home status share is tracked")
    expect("let currentStatusItems=[]" in bot and "renderOpenStatusItem" in bot, "Home rail opener supports grouped story sequencing")
    expect("status_activity" in bot and "ranking_signal_only_no_feed_injection" in bot, "Status activity informs feed discovery without feed spam")
    expect("pointer-events: none" in css and "statusCloseHardened" in viewer, "Hidden overlays and close controls preserve touch safety")
    runtime_audit()

    print("pulse status evolution audit ok")


if __name__ == "__main__":
    main()
