#!/usr/bin/env python3
"""Audit real PulseSoc Join Live request/approval flow."""

from __future__ import annotations

from datetime import datetime
import os
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


class FailingInsertCursor:
    def __init__(self, inner):
        self.inner = inner

    def execute(self, sql, params=()):
        if "INSERT INTO PULSE_LIVE_GUEST_REQUESTS" in str(sql).upper():
            raise bot.sqlite3.IntegrityError("audit forced co-host insert failure")
        return self.inner.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class FailingInsertConnection:
    def __init__(self, inner):
        object.__setattr__(self, "inner", inner)

    def cursor(self):
        return FailingInsertCursor(self.inner.cursor())

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def __setattr__(self, name, value):
        if name == "inner":
            object.__setattr__(self, name, value)
        else:
            setattr(self.inner, name, value)


class FailingSideEffectCursor(FailingInsertCursor):
    def execute(self, sql, params=()):
        if "INSERT INTO PULSE_LIVE_CHAT" in str(sql).upper():
            raise bot.sqlite3.OperationalError("audit forced optional chat failure")
        return self.inner.execute(sql, params)


class FailingSideEffectConnection(FailingInsertConnection):
    def cursor(self):
        return FailingSideEffectCursor(self.inner.cursor())


class FailingCommitConnection:
    def __init__(self, inner):
        object.__setattr__(self, "inner", inner)
        object.__setattr__(self, "commit_calls", 0)

    def cursor(self):
        return self.inner.cursor()

    def commit(self):
        object.__setattr__(self, "commit_calls", int(getattr(self, "commit_calls", 0)) + 1)
        if int(getattr(self, "commit_calls", 0)) == 1:
            raise bot.sqlite3.OperationalError("audit forced co-host commit failure")
        return self.inner.commit()

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def __setattr__(self, name, value):
        if name in {"inner", "commit_calls"}:
            object.__setattr__(self, name, value)
        else:
            setattr(self.inner, name, value)


def create_extra_viewer(prefix: str) -> int:
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn = db_service.connect()
    cur = conn.cursor()
    user_id = create_user(cur, prefix, now)
    conn.commit()
    conn.close()
    return user_id


def create_initializing_live(host_id: int) -> int:
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn = db_service.connect()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO pulse_live_sessions
            (user_id,title,category,status,stream_key,viewer_count,created_at,started_at,stream_uuid,hls_url,webrtc_room_id,stream_health,bitrate_kbps,fps,updated_at,publish_state,mux_live_status)
        VALUES (?, 'Initializing Join Audit', 'Community', 'starting', 'join_key_starting', 0, ?, ?, 'joinflowaudit-starting', '', 'pulse-live-join-audit-starting', 'starting', 0, 0, ?, 'initializing', 'idle')
        """,
        (host_id, now, now, now),
    )
    live_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return live_id


def main() -> int:
    host_id, viewer_id, live_id = create_live()
    client = bot.webhook_app.test_client()

    invalid_id = client.post(
        "/api/pulse/live/not-a-live/cohost/request",
        json={"requested_role": "cohost", "trace_id": "audit-invalid-live"},
    )
    invalid_id_data = invalid_id.get_json() or {}
    require(invalid_id.status_code == 400 and invalid_id.is_json and invalid_id_data.get("error_code") == "INVALID_LIVE_ID" and invalid_id_data.get("step") == "entry_validation", "malformed co-host live IDs return structured JSON instead of an HTML 404")
    anonymous = client.post(
        f"/api/pulse/live/{live_id}/cohost/request",
        json={"requested_role": "cohost", "trace_id": "audit-auth-failure"},
    )
    anonymous_data = anonymous.get_json() or {}
    require(anonymous.status_code == 401 and anonymous_data.get("error_code") == "AUTH_FAILED" and anonymous_data.get("step") == "auth_validation" and anonymous_data.get("trace_id") == "audit-auth-failure", "missing viewer auth returns exact stage and trace")
    invalid_payload = client.post(f"/api/pulse/live/{live_id}/cohost/request", json=["not", "an", "object"])
    invalid_payload_data = invalid_payload.get_json() or {}
    require(invalid_payload.status_code == 400 and invalid_payload_data.get("error_code") == "INVALID_REQUEST_PAYLOAD" and invalid_payload_data.get("step") == "entry_validation", "non-object JSON cannot crash or bypass co-host entry validation")
    unknown_payload = client.post(f"/api/pulse/live/{live_id}/cohost/request", json={"trace_id": "audit-unknown-field", "admin_override": True})
    unknown_payload_data = unknown_payload.get_json() or {}
    require(unknown_payload.status_code == 400 and unknown_payload_data.get("error_code") == "INVALID_REQUEST_PAYLOAD" and unknown_payload_data.get("trace_id") == "audit-unknown-field", "unknown co-host request fields are rejected with the same trace contract")
    os.environ["PULSESOC_DISABLE_COHOST"] = "1"
    try:
        disabled = client.post(f"/api/pulse/live/{live_id}/cohost/request", json={"trace_id": "audit-kill-switch"})
    finally:
        os.environ.pop("PULSESOC_DISABLE_COHOST", None)
    disabled_data = disabled.get_json() or {}
    require(disabled.status_code == 503 and disabled_data.get("error_code") == "COHOST_DISABLED" and disabled_data.get("step") == "security_gate" and disabled_data.get("trace_id") == "audit-kill-switch", "co-host kill switch failures preserve error, stage, and trace")

    login(client, viewer_id)
    debug = client.get(f"/api/pulse/live/{live_id}/cohost/debug?trace_id=audit-debug")
    debug_data = debug.get_json() or {}
    require(debug.status_code == 200 and debug_data.get("live_id_exists") is True and debug_data.get("live_status") == "active", "co-host debug endpoint reads the authoritative live session")
    require(debug_data.get("viewer_authenticated") is True and debug_data.get("viewer_id") == viewer_id and debug_data.get("live_owner_id") == host_id and debug_data.get("host_exists") is True, "co-host debug endpoint resolves viewer and host identity")
    require(debug_data.get("cohost_enabled") is True and debug_data.get("viewer_is_host") is False and debug_data.get("viewer_banned") is False, "co-host debug endpoint reports permission prerequisites")
    require(debug_data.get("db_connection_ok") is True and debug_data.get("duplicate_request_check") is False and debug_data.get("insert_possible") is True, "co-host debug endpoint verifies database and insert prerequisites")
    debug_schema = debug_data.get("schema_snapshot") or {}
    require(debug_data.get("viewer_exists") is True and debug_data.get("schema_ok") is True and debug_schema.get("primary_key") and "live_id" in (debug_schema.get("columns") or {}), "co-host debug endpoint exposes viewer FK and full request-table schema snapshot")
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
    starting_live_id = create_initializing_live(host_id)
    starting_request = client.post(
        f"/api/pulse/live/{starting_live_id}/cohost/request",
        json={"requested_role": "cohost", "camera_ready": True, "mic_ready": True, "network_quality": "ready"},
    )
    starting_data = starting_request.get_json() or {}
    require(starting_request.status_code == 409 and starting_data.get("error_code") == "LIVE_NOT_ACTIVE" and starting_data.get("step") == "permission_check", "initializing Live blocks co-host request with structured active-status error")
    original_db = bot.db
    try:
        bot.db = lambda: FailingInsertConnection(original_db())
        failed_insert = client.post(
            f"/api/pulse/live/{live_id}/cohost/request",
            json={"requested_role": "cohost", "camera_ready": True, "mic_ready": True, "network_quality": "ready", "trace_id": "audit-db-insert"},
        )
    finally:
        bot.db = original_db
    failed_insert_data = failed_insert.get_json() or {}
    require(failed_insert.status_code == 500 and failed_insert_data.get("error_code") == "DB_INSERT_FAILED" and failed_insert_data.get("step") == "db_insert" and failed_insert_data.get("trace_id") == "audit-db-insert", "database insert failures return their exact stage and trace")
    commit_failure_viewer_id = create_extra_viewer("livecommitfailaudit")
    login(client, commit_failure_viewer_id)
    original_db = bot.db
    try:
        bot.db = lambda: FailingCommitConnection(original_db())
        failed_commit = client.post(
            f"/api/pulse/live/{live_id}/cohost/request",
            json={"requested_role": "cohost", "camera_ready": True, "mic_ready": True, "network_quality": "ready", "trace_id": "audit-db-commit"},
        )
    finally:
        bot.db = original_db
    failed_commit_data = failed_commit.get_json() or {}
    require(failed_commit.status_code == 500 and failed_commit_data.get("error_code") == "DB_TRANSACTION_FAILED" and failed_commit_data.get("step") == "db_transaction" and failed_commit_data.get("trace_id") == "audit-db-commit" and failed_commit_data.get("error_type") == "OperationalError", "database commit failures return exact transaction stage, trace, and exception type")
    conn = db_service.connect(); conn.row_factory = bot.sqlite3.Row; cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS total FROM pulse_live_guest_requests WHERE live_id=? AND user_id=?", (live_id, commit_failure_viewer_id))
    rolled_back_total = int(dict(cur.fetchone() or {}).get("total") or 0)
    conn.close()
    require(rolled_back_total == 0, "failed co-host request commit rolls back without leaving a partial pending row")
    side_effect_viewer_id = create_extra_viewer("livesideeffectaudit")
    login(client, side_effect_viewer_id)
    original_db = bot.db
    try:
        bot.db = lambda: FailingSideEffectConnection(original_db())
        side_effect_request = client.post(
            f"/api/pulse/live/{live_id}/cohost/request",
            json={"requested_role": "cohost", "camera_ready": True, "mic_ready": True, "network_quality": "ready", "trace_id": "audit-side-effect"},
        )
    finally:
        bot.db = original_db
    side_effect_data = side_effect_request.get_json() or {}
    require(side_effect_request.status_code == 200 and side_effect_data.get("state") == "pending" and side_effect_data.get("notification_state") == "polling_fallback", "optional notification failure cannot roll back an authoritative pending request")
    conn = db_service.connect(); conn.row_factory = bot.sqlite3.Row; cur = conn.cursor()
    cur.execute("SELECT status FROM pulse_live_guest_requests WHERE id=?", (side_effect_data.get("request_id"),))
    persisted_side_effect_request = dict(cur.fetchone() or {})
    conn.close()
    require(persisted_side_effect_request.get("status") == "pending", "pending request remains committed after optional side-effect rollback")
    login(client, viewer_id)
    request_res = client.post(
        f"/api/pulse/live/{live_id}/join-request",
        json={"requested_role": "cohost", "camera_ready": True, "mic_ready": True, "network_quality": "ready", "connection": {"camera_ready": True, "mic_ready": True}},
    )
    request_data = request_res.get_json() or {}
    request_id = int((request_data.get("request") or {}).get("id") or 0)
    require(request_res.status_code == 200 and request_data.get("status") == "pending" and request_id, "viewer creates real pending join request")
    require(request_data.get("ok") is True and request_data.get("request_id") == request_id and request_data.get("state") == "pending", "co-host request returns required top-level success contract")
    require(request_data.get("trace_id") and request_data.get("step") == "waiting_for_host", "co-host request returns trace and waiting step")

    initializing = client.post(
        f"/api/pulse/live/{live_id}/cohost/request",
        json={"requested_role": "cohost", "camera_ready": True, "mic_ready": True, "network_quality": "ready"},
    )
    initializing_data = initializing.get_json() or {}
    require(initializing.status_code == 200 and initializing_data.get("state") == "pending", "cohost/request alias returns the same structured contract")
    require(initializing_data.get("request_id") == request_id and initializing_data.get("step") == "already_requested", "duplicate co-host requests reuse the existing pending request instead of attempting a risky insert")

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
    second = client.post(f"/api/pulse/live/{live_id}/join-request", json={"requested_role": "cohost", "camera_ready": True, "mic_ready": True, "network_quality": "ready", "trace_id": "audit-finalization"})
    second_data = second.get_json() or {}
    second_id = int((second_data.get("request") or {}).get("id") or 0)
    require(second.status_code == 200 and second_id and second_id != request_id, "viewer can request again after denial")

    login(client, host_id)
    accept_res = client.post(f"/api/pulse/live/{live_id}/join-requests/{second_id}/accept", json={})
    accept_data = accept_res.get_json() or {}
    guest = accept_data.get("guest") or {}
    require(accept_res.status_code == 200 and accept_data.get("status") == "accepted" and guest.get("id") and guest.get("status") == "accepted", "host can atomically accept request and create accepted guest permission")
    accepted_host_state = client.get(f"/api/pulse/live/{live_id}/state").get_json() or {}
    require(any(int(item.get("user_id") or 0) == viewer_id and item.get("role") == "cohost" for item in accepted_host_state.get("guests") or []), "accepted co-host appears in host Live guest state")

    login(client, viewer_id)
    status = client.get(f"/api/pulse/live/{live_id}/join-status").get_json() or {}
    require(status.get("can_publish") is True and (status.get("guest") or {}).get("id") == guest.get("id"), "accepted viewer receives co-host publish state")
    original_livekit = {key: os.environ.get(key) for key in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")}
    os.environ.update({"LIVEKIT_URL": "wss://livekit.audit.invalid", "LIVEKIT_API_KEY": "audit-key", "LIVEKIT_API_SECRET": "audit-secret"})
    try:
        token_res = client.post(f"/api/pulse/live/{live_id}/livekit/token", json={"role": "cohost", "trace_id": "audit-finalization"})
        token_data = token_res.get_json() or {}
        require(token_res.status_code == 200 and token_data.get("role") == "cohost" and token_data.get("can_publish") is True, "accepted co-host receives a verified publish-capable token")
        require(token_data.get("can_subscribe") is True and token_data.get("can_publish_data") is True and token_data.get("room_join") is True and token_data.get("participant_name"), "co-host token response exposes every required participant claim")
        claims = token_data.get("token_claims") or {}
        require(claims.get("identity") == guest.get("livekit_identity") and claims.get("room") == guest.get("livekit_room") and claims.get("role") == "cohost" and claims.get("expiration", 0) > 0, "server verifies identity, room, role, expiration, and metadata before token delivery")
        joining = client.get(f"/api/pulse/live/{live_id}/join-status").get_json() or {}
        require((joining.get("guest") or {}).get("status") == "joining", "verified token moves guest from accepted to joining")
        original_inspector = bot.pulse_livekit_room_participants
        bot.pulse_livekit_room_participants = lambda room_name, **kwargs: {"ok": True, "participants": []}
        pending_promotion = client.post(f"/api/pulse/live/{live_id}/guests/{int(guest.get('id'))}/publish-complete", json={"trace_id": "audit-finalization"})
        pending_promotion_data = pending_promotion.get_json() or {}
        require(pending_promotion.status_code == 202 and pending_promotion_data.get("state") == "joining" and pending_promotion_data.get("promotion_pending") is True and pending_promotion_data.get("missing_event") == "participant_joined", "missing provider participant remains joining with an exact pending event")
        bot.pulse_livekit_room_participants = lambda room_name, **kwargs: {"ok": True, "participants": [{"identity": guest.get("livekit_identity"), "sid": "PA_audit", "tracks": [{"sid": "TR_video", "type": "VIDEO", "muted": False}, {"sid": "TR_audio", "type": "AUDIO", "muted": False}]}]}
        try:
            promoted = client.post(
                f"/api/pulse/live/{live_id}/guests/{int(guest.get('id'))}/publish-complete",
                json={"trace_id": "audit-finalization", "participant_identity": guest.get("livekit_identity"), "room_connected": True, "video_publication_sid": "TR_video", "audio_publication_sid": "TR_audio"},
            )
        finally:
            bot.pulse_livekit_room_participants = original_inspector
        promoted_data = promoted.get_json() or {}
        require(promoted.status_code == 200 and promoted_data.get("state") == "live" and (promoted_data.get("guest") or {}).get("status") == "live", "server inspection confirms participant and both tracks before live promotion")
        trace_detail = client.get(f"/api/pulse/live/{live_id}/cohost/trace/audit-finalization").get_json() or {}
        stage_numbers = {int(stage.get("stage_number") or 0) for stage in trace_detail.get("stages") or []}
        require(trace_detail.get("ok") is True and {11, 12, 13, 14, 19, 20}.issubset(stage_numbers), "trace detail exposes acceptance, identity, token, participant, and promotion stages")
    finally:
        for key, value in original_livekit.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    login(client, host_id)
    mute = client.post(f"/api/pulse/live/{live_id}/guests/{int(guest.get('id'))}/mute", json={})
    require(mute.status_code == 200, "host can mute guest")
    remove = client.post(f"/api/pulse/live/{live_id}/guests/{int(guest.get('id'))}/remove", json={})
    require(remove.status_code == 200, "host can remove guest")

    require("pulse_live_guest_requests" in BOT and "pulse_live_guests" in BOT and "pulse_live_audit_logs" in BOT, "co-host request, co-host, and audit tables exist")
    require('("host_user_id", "INTEGER DEFAULT 0")' in BOT and '("camera_ready", "INTEGER DEFAULT 0")' in BOT and '("connection_json", "TEXT")' in BOT and '("expires_at", "TEXT")' in BOT, "legacy co-host request tables migrate required request columns")
    require("/cohost/request" in BOT and "LIVE_NOT_ACTIVE" in BOT and "pulse_live_cohost_live_status" in BOT, "co-host request alias and active-live gate exist")
    require("INVALID_LIVE_ID" in BOT and "AUTH_FAILED" in BOT and "api_pulse_live_join_request_invalid_id" in BOT, "co-host entry and auth failures cannot bypass the structured JSON contract")
    require("PULSE_COHOST_STEP" in BOT and "db_insert_attempted" in BOT and "DB_INSERT_FAILED" in BOT and "DB_TRANSACTION_FAILED" in BOT, "co-host request pipeline logs exact stages and separates database failures")
    require("pulse_live_db_exception_details" in BOT and "sqlstate" in BOT and "constraint_name" in BOT and "PULSE_COHOST_DB_EXCEPTION" in BOT, "co-host database failures log raw exception class, SQLSTATE, constraint, and stack context")
    require("pulse_live_guest_request_schema_snapshot" in BOT and "DB_SCHEMA_MISMATCH" in BOT and "FOREIGN_KEY_INVALID" in BOT, "co-host request creation verifies schema and foreign-key prerequisites before insert")
    require("duplicate_insert_race_recovered" in BOT and "duplicate_commit_race_recovered" in BOT and "pulse_live_pending_request_response" in BOT, "duplicate co-host request races return the existing pending request")
    require("pulse_cohost_guard_error" in BOT and "RATE_LIMITED" in BOT and "security_gate" in BOT, "pre-route security controls preserve the co-host diagnostic contract")
    require("api_pulse_live_cohost_debug" in BOT and "insert_possible" in BOT and "duplicate_request_check" in BOT, "authenticated co-host diagnostic endpoint exposes request prerequisites")
    require("PULSE_COHOST_PIPELINE_STAGES" in BOT and "stage_number" in BOT and "duration_ms" in BOT and "failed_stage" in BOT, "co-host trace records expose stage timing and failure detail")
    require("pulse_livekit_verify_token_claims" in BOT and "TOKEN_CLAIMS_INVALID" in BOT and "token_claims" in BOT, "LiveKit token signature and claims are verified before delivery")
    require("live_cohost_token_ready" in BOT and "live_cohost_participant_joined" in BOT and "live_cohost_publish_complete" in BOT and "live_cohost_guest_live" in BOT, "co-host finalization emits every required realtime lifecycle event")
    require("status='joining'" in BOT and "status='joined'" in BOT and "status='publishing'" in BOT and "status='live'" in BOT, "co-host database state advances through joining, joined, publishing, and live")
    require('"request_id": request_id' in BOT and '"state": "pending"' in BOT and "PULSE_COHOST_FAILURE" in BOT, "co-host request endpoint has structured success and failure logging")
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
    require("/cohost/request`" in BOT and "requested_role:'cohost'" in BOT, "Reels co-host button calls the hardened co-host request endpoint")
    require("cohost/request`" in JS and "INVALID_LIVE_ID" in JS and "Object.assign(error, data)" in JS, "Live Studio validates IDs and preserves structured backend failures")
    require("checking_permissions:'Checking permissions...'" in BOT and "waiting_for_host:'Waiting for Host'" in BOT and "host_accepted:'Accepted" in BOT and "cohost_live:'Co-host Live'" in BOT and "unavailable_with_reason:'Unable to request'" in BOT, "Reels renders the required backend-driven request states")
    require("liveReelErrorState" in BOT and "label=liveReelFailureMessage" in BOT and "Object.assign(err,d)" in BOT, "Reels preserves and displays backend co-host failure reasons")
    require("btn.textContent=btn.classList.contains('reel-live-join')?'Joined':'✓'" not in BOT, "Reels no longer paints a fake Joined state after audience presence")
    require('confirmation.state !== "live"' in JS and JS.index('button.textContent = "Joined"') > JS.index('confirmation.state !== "live"'), "Joined is rendered only after server-confirmed participant and track promotion")
    require("fake join" not in BOT.lower() and "fake guest" not in BOT.lower(), "no fake join request literals")
    require('href="#"' not in BOT and "javascript:void(0)" not in BOT, "no dead href/hash or javascript void links")
    print("live join flow audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
