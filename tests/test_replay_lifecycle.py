"""Behavioral coverage for replay readiness, provider recovery and consent."""
import hashlib
import hmac
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from services import live_archive_service, live_distribution_service, mux_live_service
from tests.test_live_replay_worker import _backlog_database, _mux_native_database, _run_due_now, media_worker


@pytest.mark.parametrize("status", ["ended", "archived", "offline"])
def test_no_expired_live_fallback(status):
    live = {"status": status, "recording_status": "processing_replay", "mux_playback_id": "old-live", "mux_recording_playback_id": "early-vod", "playback_url": "https://old/live.m3u8", "webrtc_room_id": "old-room", "mux_live_status": "live", "stream_uuid": "old"}
    result = live_distribution_service.playback_manifest(live)
    assert result["playback_url"] == result["mux_playback_id"] == ""
    assert not result["supports_webrtc"]
    assert not live_archive_service.replay_manifest(live)["replay_available"]


@pytest.mark.parametrize("seconds,message", [(30, "Replay processing"), (121, "Replay is still processing"), (301, "Replay is delayed")])
def test_processing_timing(seconds, message):
    live = {"status": "ended", "ended_at": (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(), "recording_status": "processing_replay"}
    manifest = live_archive_service.replay_manifest(live)
    assert manifest["message"].startswith(message)
    assert manifest["status"] == "processing_recording"
    assert manifest["delayed"] == (seconds >= 300)


def test_mux_live_asset_is_not_final_vod(monkeypatch):
    monkeypatch.setattr(mux_live_service, "_request", lambda *a, **kw: {"ok": True, "data": {"id": "asset", "status": "ready", "is_live": True, "playback_ids": [{"id": "vod", "policy": "public"}]}})
    assert mux_live_service.create_mux_asset_from_live_recording(recording_asset_id="asset")["mux_status"] == "preparing"


def test_signed_replay_never_falls_back_to_unsigned(monkeypatch):
    monkeypatch.delenv("MUX_SIGNING_KEY_ID", raising=False)
    monkeypatch.delenv("MUX_SIGNING_PRIVATE_KEY", raising=False)
    live = {"status": "ended", "recording_status": "mux_asset_ready", "mux_recording_playback_id": "private", "replay_url": "https://stream.mux.com/private.m3u8?token=expired"}
    assert live_distribution_service.playback_manifest(live)["playback_url"] == ""
    assert not live_archive_service.replay_manifest(live)["replay_available"]


def test_private_asset_uses_signed_policy_and_recovery_marker(monkeypatch):
    calls = []
    monkeypatch.setattr(mux_live_service, "_request", lambda path, **kw: calls.append(kw) or {"ok": True, "data": {"id": "asset"}})
    mux_live_service.create_mux_asset_from_private_recording("https://private/input", marker="pulse_replay:1:sid", private=True)
    assert calls[0]["payload"]["playback_policies"] == ["signed"]
    assert calls[0]["payload"]["inputs"] == [{"url": "https://private/input"}]
    assert calls[0]["payload"]["passthrough"] == "pulse_replay:1:sid"


def test_signed_playback_token_is_valid_and_stable(monkeypatch):
    import base64
    from urllib.parse import urlparse, parse_qs
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    monkeypatch.setenv("MUX_SIGNING_KEY_ID", "test-key")
    monkeypatch.setenv("MUX_SIGNING_PRIVATE_KEY", base64.b64encode(pem).decode())
    monkeypatch.setattr(mux_live_service.time, "time", lambda: 100000)
    first = mux_live_service.signed_playback_url("private")
    monkeypatch.setattr(mux_live_service.time, "time", lambda: 100010)
    assert mux_live_service.signed_playback_url("private") == first
    token = parse_qs(urlparse(first).query)["token"][0]
    head, body, signature = token.split(".")
    key.public_key().verify(base64.urlsafe_b64decode(signature + "=="), (head + "." + body).encode(), padding.PKCS1v15(), hashes.SHA256())
    claims = json.loads(base64.urlsafe_b64decode(body + "=="))
    assert claims["sub"] == "private" and claims["aud"] == "v"
    assert 100010 < claims["exp"] <= 121600


def test_latest_mux_asset_is_last(monkeypatch):
    monkeypatch.setattr(mux_live_service, "_request", lambda *a, **kw: {"ok": True, "data": {"recent_asset_ids": ["old", "new"]}})
    assert mux_live_service.get_mux_live_stream("stream")["mux_recording_asset_id"] == "new"


def test_signatures_accept_rotation_but_reject_tamper_and_stale(monkeypatch):
    monkeypatch.setenv("MUX_WEBHOOK_SECRET", "test-secret")
    body = b'{"type":"video.asset.ready"}'
    timestamp = str(int(time.time()))
    signature = hmac.new(b"test-secret", timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    header = f"t={timestamp},v1={signature},v1=other"
    assert mux_live_service.verify_mux_webhook_signature(body, header)["ok"]
    assert not mux_live_service.verify_mux_webhook_signature(body + b"x", header)["ok"]
    assert not mux_live_service.verify_mux_webhook_signature(body, "t=1,v1=bad")["ok"]


def test_preparing_asset_is_never_failed_for_age(tmp_path, monkeypatch):
    database = _backlog_database(tmp_path, [(11, "stream", "asset", "processing_replay")])
    monkeypatch.setattr(media_worker.bot, "db", lambda: sqlite3.connect(database))
    monkeypatch.setattr(mux_live_service, "create_mux_asset_from_live_recording", lambda **kw: {"ok": True, "mux_status": "preparing"})
    media_worker.reconcile_live_replay_backlog()
    for _ in range(7):
        _run_due_now(database)
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT status, attempts FROM pulse_jobs").fetchone() == ("pending", 0)


def test_completed_and_disabled_sessions_cannot_starve_backlog(tmp_path, monkeypatch):
    database = _backlog_database(tmp_path, [(1, "s1", "a1", "mux_asset_ready"), (2, "s2", "a2", "processing_replay"), (3, "s3", "a3", "processing_replay")])
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE pulse_live_sessions SET replay_reel_id=10 WHERE id=1")
        conn.execute("UPDATE pulse_live_sessions SET record_replay=0 WHERE id=2")
    monkeypatch.setattr(media_worker.bot, "db", lambda: sqlite3.connect(database))
    assert media_worker.reconcile_live_replay_backlog(1)["queued"] == 1
    assert media_worker.reconcile_live_replay_backlog(1)["queued"] == 0
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT target_id FROM pulse_jobs").fetchall() == [(3,)]


def test_worker_restart_recovers_claim(tmp_path, monkeypatch):
    database = _backlog_database(tmp_path, [(1, "s1", "a1", "processing_replay")])
    monkeypatch.setattr(media_worker.bot, "db", lambda: sqlite3.connect(database))
    media_worker.reconcile_live_replay_backlog()
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE pulse_jobs SET status='processing',updated_at='2000-01-01'")
    assert media_worker.reconcile_live_replay_backlog()["stale_recovered"] == 1


def test_ambiguous_creation_recovers_without_post(tmp_path, monkeypatch):
    database = _mux_native_database(tmp_path)
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE pulse_live_sessions SET mux_live_stream_id='', agora_recording_sid='sid', recording_status='mux_creation_pending'")
    monkeypatch.setattr(media_worker.bot, "db", lambda: sqlite3.connect(database))
    monkeypatch.setattr(mux_live_service, "find_recording_asset", lambda marker: {"ok": True, "mux_recording_asset_id": "recovered"})
    monkeypatch.setattr(mux_live_service, "create_mux_asset_from_private_recording", lambda *a, **kw: pytest.fail("duplicate creation"))
    _run_due_now(database)
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT mux_recording_asset_id FROM pulse_live_sessions").fetchone() == ("recovered",)


def test_disabled_recording_cannot_finalize_or_publish(tmp_path, monkeypatch):
    database = _mux_native_database(tmp_path)
    with sqlite3.connect(database) as conn:
        conn.execute("ALTER TABLE pulse_live_sessions ADD COLUMN record_replay INTEGER DEFAULT 0")
    monkeypatch.setattr(media_worker.bot, "db", lambda: sqlite3.connect(database))
    monkeypatch.setattr(mux_live_service, "get_mux_live_stream", lambda *a: pytest.fail("recording disabled"))
    _run_due_now(database)
    assert not live_archive_service.publication_allowed({"record_replay": False})
    assert not live_archive_service.publication_allowed({"replay_publish_enabled": False})


def test_webhook_duplicate_and_late_connection_preserve_ended(tmp_path, monkeypatch):
    database = _backlog_database(tmp_path, [(1, "stream", "asset", "processing_replay")])
    with sqlite3.connect(database) as conn:
        for name, definition in [("mux_recording_duration_seconds", "REAL"), ("mux_live_status", "TEXT"), ("publish_state", "TEXT"), ("provider", "TEXT"), ("is_live", "INTEGER")]:
            conn.execute(f"ALTER TABLE pulse_live_sessions ADD COLUMN {name} {definition}")
        conn.executescript("CREATE TABLE pulse_live_streams (mux_recording_asset_id TEXT, mux_recording_playback_id TEXT, mux_live_stream_id TEXT, updated_at TEXT,status TEXT,mux_live_status TEXT); CREATE TABLE pulse_live_events(event_type TEXT,actor_user_id INTEGER,post_id INTEGER,payload_json TEXT,created_at TEXT); CREATE TABLE chat_media_uploads(mux_asset_id TEXT,mux_status TEXT,mux_playback_id TEXT,playback_url TEXT,processing_status TEXT,is_available INTEGER,error_message TEXT,updated_at TEXT); CREATE TABLE pulse_media_assets(mux_asset_id TEXT,mux_status TEXT,mux_playback_id TEXT,playback_url TEXT,processing_status TEXT,updated_at TEXT);")
    monkeypatch.setattr(media_worker.bot, "db", lambda: sqlite3.connect(database))
    monkeypatch.setattr(mux_live_service, "verify_mux_webhook_signature", lambda *a: {"ok": True})
    app = media_worker.bot.webhook_app
    for event_type in ["video.asset.ready", "video.asset.ready", "video.asset.live_stream_completed", "video.live_stream.connected", "video.live_stream.disconnected"]:
        payload = {"id": "event", "type": event_type, "data": {"id": "asset" if event_type.startswith("video.asset") else "stream", "live_stream_id": "stream", "playback_ids": [{"id": "vod", "policy": "public"}]}}
        with app.test_request_context(json=payload):
            response = media_worker.bot.api_pulse_live_mux_webhook()
            assert not isinstance(response, tuple), response
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT status,recording_status,replay_url FROM pulse_live_sessions").fetchone() == ("ended", "processing_replay", "")
        assert conn.execute("SELECT count(*) FROM pulse_jobs").fetchone() == (1,)
        conn.execute("UPDATE pulse_live_sessions SET recording_status='mux_asset_ready',replay_url='https://stream.mux.com/vod.m3u8'")
    with app.test_request_context(json={"type": "video.asset.errored", "data": {"id": "asset", "live_stream_id": "stream"}}):
        assert media_worker.bot.api_pulse_live_mux_webhook().get_json()["ok"]
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT recording_status,replay_url FROM pulse_live_sessions").fetchone() == ("mux_asset_ready", "https://stream.mux.com/vod.m3u8")


@pytest.mark.parametrize("provider_status,expected_id", [("preparing", "asset"), ("errored", "")])
def test_host_retry_only_replaces_confirmed_errored_asset(tmp_path, monkeypatch, provider_status, expected_id):
    database = _backlog_database(tmp_path, [(1, "", "asset", "replay_failed")])
    with sqlite3.connect(database) as conn:
        conn.execute("ALTER TABLE pulse_live_sessions ADD COLUMN user_id INTEGER DEFAULT 7")
        conn.execute("ALTER TABLE pulse_live_sessions ADD COLUMN replay_retry_key TEXT DEFAULT ''")
        conn.execute("UPDATE pulse_live_sessions SET agora_recording_sid='sid',agora_recording_filename='recording.m3u8'")
    bot = media_worker.bot
    monkeypatch.setattr(bot, "db", lambda: sqlite3.connect(database))
    monkeypatch.setattr(bot, "init_db", lambda: None)
    monkeypatch.setattr(bot, "api_account_user", lambda: {"user_id": 7})
    monkeypatch.setattr(mux_live_service, "create_mux_asset_from_live_recording", lambda **kw: {"ok": True, "mux_status": provider_status})
    for _ in range(2):
        with bot.webhook_app.test_request_context(method="POST"):
            assert bot.api_pulse_live_replay_retry(1).get_json()["ok"]
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT mux_recording_asset_id,recording_status FROM pulse_live_sessions").fetchone() == (expected_id, "processing_replay")
        assert conn.execute("SELECT count(*) FROM pulse_jobs").fetchone() == (1,)
    monkeypatch.setattr(bot, "api_account_user", lambda: {"user_id": 8})
    with bot.webhook_app.test_request_context(method="POST"):
        response = bot.api_pulse_live_replay_retry(1)
        assert response[1] == 403
