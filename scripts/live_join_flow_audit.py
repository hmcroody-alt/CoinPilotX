#!/usr/bin/env python3
"""Audit real PulseSoc Join Live request/approval flow."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service  # noqa: E402


BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
JS = (ROOT / "static/js/pulse_live_studio_runtime.js").read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def create_user(cur, prefix: str, now: str) -> int:
    stamp = now.replace(":", "").replace("-", "")
    cur.execute(
        "INSERT INTO users (username, display_name, email, signup_time, created_at) VALUES (?, ?, ?, ?, ?)",
        (f"{prefix}_{stamp}", f"{prefix.title()} User", f"{prefix}-{stamp}@example.com", now, now),
    )
    return int(cur.lastrowid)


def create_live() -> tuple[int, int, int]:
    bot.init_db()
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn = db_service.connect()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    host_id = create_user(cur, "livehostaudit", now)
    viewer_id = create_user(cur, "livevieweraudit", now)
    cur.execute(
        """
        INSERT INTO pulse_live_sessions
            (user_id,title,category,status,stream_key,viewer_count,created_at,started_at,stream_uuid,hls_url,webrtc_room_id,stream_health,bitrate_kbps,fps,updated_at,publish_state,mux_live_status)
        VALUES (?, 'Join Flow Audit', 'Community', 'live', 'join_key', 1, ?, ?, 'joinflowaudit', 'https://live.coinpilotxai.app/hls/joinflowaudit.m3u8', 'pulse-live-join-audit', 'stable', 2400, 30, ?, 'browser_live_egress', 'active')
        """,
        (host_id, now, now, now),
    )
    live_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return host_id, viewer_id, live_id


def login(client, user_id: int) -> None:
    with client.session_transaction() as session:
        session["account_user_id"] = user_id


def main() -> int:
    host_id, viewer_id, live_id = create_live()
    client = bot.webhook_app.test_client()

    login(client, viewer_id)
    audience = client.post(f"/api/pulse/live/{live_id}/join", json={"source": "reels", "role": "audience"})
    audience_data = audience.get_json() or {}
    require(audience.status_code == 200 and audience_data.get("status") == "watching" and audience_data.get("role") == "audience", "Reels visibility records real audience presence without claiming co-host join")
    before_request = client.get(f"/api/pulse/live/{live_id}/join-status").get_json() or {}
    require(before_request.get("status") == "none" and before_request.get("can_publish") is False, "audience presence does not create fake co-host approval")
    denied_token = client.post(f"/api/pulse/live/{live_id}/livekit/token", json={"role": "guest"})
    require(denied_token.status_code == 403, "viewer cannot get guest publish token before approval")
    denied_token_data = denied_token.get_json() or {}
    require(denied_token_data.get("error_code") == "TOKEN_MISSING_PUBLISH_PERMISSION" and denied_token_data.get("step") == "requesting_cohost_token", "token denial returns structured co-host error")
    bad_request = client.post(f"/api/pulse/live/{live_id}/join-request", json={"requested_role": "cohost", "camera_ready": False, "mic_ready": True})
    require(bad_request.status_code == 400, "join request enforces camera/mic readiness")
    bad_request_data = bad_request.get_json() or {}
    require(bad_request_data.get("error_code") == "CAMERA_PERMISSION_DENIED" and bad_request_data.get("step") == "camera_permission_denied", "permission failure returns structured co-host error")
    request_res = client.post(
        f"/api/pulse/live/{live_id}/join-request",
        json={"requested_role": "cohost", "camera_ready": True, "mic_ready": True, "network_quality": "ready", "connection": {"camera_ready": True, "mic_ready": True}},
    )
    request_data = request_res.get_json() or {}
    request_id = int((request_data.get("request") or {}).get("id") or 0)
    require(request_res.status_code == 200 and request_data.get("status") == "pending" and request_id, "viewer creates real pending join request")
    require(request_data.get("trace_id") and request_data.get("step") == "waiting_for_host", "co-host request returns trace and waiting step")

    state = client.get(f"/api/pulse/live/{live_id}/state").get_json() or {}
    require((state.get("viewer_join_request") or {}).get("status") == "pending" and (state.get("viewer_join_request") or {}).get("requested_role") == "cohost", "viewer state shows pending co-host request")

    login(client, host_id)
    host_state = client.get(f"/api/pulse/live/{live_id}/state").get_json() or {}
    require(host_state.get("join_request_count", 0) >= 1, "host state receives pending request without refresh")
    deny_res = client.post(f"/api/pulse/live/{live_id}/join-requests/{request_id}/deny", json={})
    require(deny_res.status_code == 200 and (deny_res.get_json() or {}).get("status") == "denied", "host can deny request")

    login(client, viewer_id)
    denied_state = client.get(f"/api/pulse/live/{live_id}/join-status").get_json() or {}
    require(denied_state.get("status") == "denied" and denied_state.get("can_publish") is False, "denied viewer sees denied and receives no publish permission")
    second = client.post(f"/api/pulse/live/{live_id}/join-request", json={"requested_role": "cohost", "camera_ready": True, "mic_ready": True, "network_quality": "ready"})
    second_data = second.get_json() or {}
    second_id = int((second_data.get("request") or {}).get("id") or 0)
    require(second.status_code == 200 and second_id and second_id != request_id, "viewer can request again after denial")

    login(client, host_id)
    accept_res = client.post(f"/api/pulse/live/{live_id}/join-requests/{second_id}/accept", json={})
    accept_data = accept_res.get_json() or {}
    guest = accept_data.get("guest") or {}
    require(accept_res.status_code == 200 and accept_data.get("status") == "accepted" and guest.get("id"), "host can accept request and create guest permission")
    accepted_host_state = client.get(f"/api/pulse/live/{live_id}/state").get_json() or {}
    require(any(int(item.get("user_id") or 0) == viewer_id and item.get("role") == "cohost" for item in accepted_host_state.get("guests") or []), "accepted co-host appears in host Live guest state")

    login(client, viewer_id)
    status = client.get(f"/api/pulse/live/{live_id}/join-status").get_json() or {}
    require(status.get("can_publish") is True and (status.get("guest") or {}).get("id") == guest.get("id"), "accepted viewer receives co-host publish state")
    token_res = client.post(f"/api/pulse/live/{live_id}/livekit/token", json={"role": "cohost"})
    token_data = token_res.get_json() or {}
    require(token_res.status_code in {200, 503}, "accepted co-host reaches server-side publish token gate")
    if token_res.status_code == 200:
        require(token_data.get("role") == "cohost" and token_data.get("can_publish") is True, "accepted co-host receives publish-capable cohost token")
        require(token_data.get("can_subscribe") is True and token_data.get("can_publish_data") is True and token_data.get("room_join") is True, "co-host token response exposes required publish claims")
    else:
        require(token_data.get("error_code") == "TOKEN_GENERATION_FAILED" and token_data.get("step") == "token_failed", "token generation failure is structured")

    login(client, host_id)
    mute = client.post(f"/api/pulse/live/{live_id}/guests/{int(guest.get('id'))}/mute", json={})
    require(mute.status_code == 200, "host can mute guest")
    remove = client.post(f"/api/pulse/live/{live_id}/guests/{int(guest.get('id'))}/remove", json={})
    require(remove.status_code == 200, "host can remove guest")

    require("pulse_live_guest_requests" in BOT and "pulse_live_guests" in BOT and "pulse_live_audit_logs" in BOT, "co-host request, co-host, and audit tables exist")
    require("requested_role TEXT DEFAULT 'cohost'" in BOT and '"requested_role": clean_html' in BOT, "co-host request role is persisted and returned")
    require("guest_role TEXT DEFAULT 'cohost'" in BOT and "permissions_json" in BOT, "co-host records store role and publish permissions")
    require("requested_role in {\"guest\", \"cohost\", \"co-host\"}" in BOT and "token_role = \"cohost\"" in BOT, "co-host token generation is server-side and role-gated")
    require("PULSE_COHOST_STATE_MACHINE" in BOT and "unavailable_with_reason" in BOT and "TOKEN_MISSING_PUBLISH_PERMISSION" in BOT, "shared co-host state machine and error codes exist")
    require("/cohost-trace" in BOT and "pulse_live_cohost_trace" in BOT and "cohost_failure" in BOT, "co-host trace endpoint and structured logs exist")
    require('"cohost"' in JS and "Request to Co-host" in JS and "Accept Co-host" in JS, "co-host UI labels and token request are wired")
    require("Only the live host can manage join requests" in BOT, "host permission checks exist")
    require("Only the requesting viewer can cancel this request" in BOT, "viewer-only cancel check exists")
    require("pending" in BOT and "accepted" in BOT and "denied" in BOT and "cancelled" in BOT and "removed" in BOT, "request states exist")
    require("requestJoinLive" in JS and "publishGuestToLiveKit" in JS and "hostJoinRequestAction" in JS, "UI button states and handlers are wired")
    require("data-live-join-label" in BOT and "Request to Co-host" in BOT, "Reels Live names the co-host action clearly")
    require("recordLiveReelAudience" in BOT and "refreshLiveReelJoinStatus" in BOT and "scheduleLiveReelJoinPolling" in BOT, "Reels separates audience presence from co-host request polling")
    require("/join-request`" in BOT and "requested_role:'cohost'" in BOT, "Reels co-host button calls the real join-request endpoint")
    require("checking_permissions:'Checking permissions...'" in BOT and "waiting_for_host:'Waiting for Host'" in BOT and "host_accepted:'Accepted" in BOT and "cohost_live:'Co-host Live'" in BOT and "unavailable_with_reason:'Unavailable'" in BOT, "Reels renders the required backend-driven request states")
    require("liveReelErrorState" in BOT and "Unavailable: '+liveReelFailureMessage" in BOT and "Object.assign(err,d)" in BOT, "Reels preserves and displays backend co-host failure reasons")
    require("btn.textContent=btn.classList.contains('reel-live-join')?'Joined':'✓'" not in BOT, "Reels no longer paints a fake Joined state after audience presence")
    require('button.textContent = "Joined"' in JS and JS.index('button.textContent = "Joined"') > JS.index("await room.localParticipant.publishTrack(track)"), "Joined is rendered only after co-host tracks publish")
    require("fake join" not in BOT.lower() and "fake guest" not in BOT.lower(), "no fake join request literals")
    require('href="#"' not in BOT and "javascript:void(0)" not in BOT, "no dead href/hash or javascript void links")
    print("live join flow audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
