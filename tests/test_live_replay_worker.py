import os
import sqlite3
import tempfile

_bootstrap_database = tempfile.mktemp(prefix="live-replay-worker-bootstrap-", suffix=".sqlite3")
os.environ["DATABASE_URL"] = f"sqlite:///{_bootstrap_database}"

import media_worker


def _connect(path):
    return sqlite3.connect(path)


def test_live_replay_job_creates_one_mux_asset_then_reconciles_ready(tmp_path, monkeypatch):
    database = str(tmp_path / "replay-worker.sqlite3")
    conn = _connect(database)
    conn.executescript(
        """
        CREATE TABLE pulse_jobs (
          id INTEGER PRIMARY KEY, job_type TEXT, target_type TEXT, target_id INTEGER,
          status TEXT, attempts INTEGER, max_attempts INTEGER, error_message TEXT,
          run_after TEXT, created_at TEXT, updated_at TEXT, completed_at TEXT
        );
        CREATE TABLE pulse_live_sessions (
          id INTEGER PRIMARY KEY, status TEXT, agora_recording_sid TEXT,
          agora_recording_filename TEXT, agora_recording_prefix TEXT,
          agora_converter_id TEXT, webrtc_room_id TEXT, agora_recording_resource_id TEXT,
          agora_recording_uid TEXT, mux_recording_asset_id TEXT,
          mux_recording_playback_id TEXT, replay_url TEXT, recording_status TEXT,
          recording_error TEXT, thumbnail_url TEXT, viewer_count INTEGER, updated_at TEXT
        );
        CREATE TABLE pulse_posts (
          id INTEGER PRIMARY KEY, live_session_id INTEGER, live_status TEXT,
          live_viewer_count INTEGER, replay_url TEXT, playback_url TEXT,
          preview_url TEXT, body TEXT, title TEXT, status TEXT, deleted_at TEXT,
          updated_at TEXT
        );
        INSERT INTO pulse_live_sessions
          (id,status,agora_recording_sid,agora_recording_filename,agora_recording_prefix,
           mux_recording_asset_id,mux_recording_playback_id,replay_url,recording_status,
           recording_error,thumbnail_url,viewer_count,updated_at)
        VALUES (7,'ended','sid-7','recording.m3u8','pulsesoc/live-recordings/7',
                '','','','processing_replay','','poster.jpg',3,'2026-01-01T00:00:00');
        INSERT INTO pulse_posts
          (id,live_session_id,live_status,live_viewer_count,replay_url,playback_url,
           preview_url,body,title,status,updated_at)
        VALUES (70,7,'processing',3,'','','poster.jpg','Live','Live','published','2026-01-01T00:00:00');
        INSERT INTO pulse_jobs
          (id,job_type,target_type,target_id,status,attempts,max_attempts,run_after,created_at,updated_at)
        VALUES (1,'finalize_live_replay','live',7,'pending',0,120,'2026-01-01T00:00:00','2026-01-01T00:00:00','2026-01-01T00:00:00');
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(media_worker.bot, "db", lambda: _connect(database))
    prepared = []
    created = []
    published = []
    monkeypatch.setattr(media_worker.agora_cloud_recording_service, "prepare_private_mux_input", lambda prefix, filename: prepared.append((prefix, filename)) or {"ok": True, "input_url": "https://signed.example/replay"})
    monkeypatch.setattr(media_worker.mux_live_service, "create_mux_asset_from_private_recording", lambda url: created.append(url) or {"ok": True, "mux_recording_asset_id": "asset-7", "mux_recording_playback_id": "play-7", "mux_status": "preparing"})
    monkeypatch.setattr(media_worker.mux_live_service, "create_mux_asset_from_live_recording", lambda **kwargs: {"ok": True, "mux_recording_asset_id": "asset-7", "mux_recording_playback_id": "play-7", "playback_url": "https://stream.mux.com/play-7.m3u8", "mux_status": "ready"})
    monkeypatch.setattr(media_worker.bot, "pulse_live_publish_replay_reel", lambda live_id, trace_id: published.append((live_id, trace_id)) or {"ok": True})

    first = media_worker.process_media_jobs(1)
    assert first == {"queued": 1, "processed": 1, "failed": 0}
    conn = _connect(database)
    conn.execute("UPDATE pulse_jobs SET run_after='2026-01-01T00:00:00' WHERE id=1")
    conn.commit()
    conn.close()
    second = media_worker.process_media_jobs(1)

    assert second == {"queued": 1, "processed": 1, "failed": 0}
    assert prepared == [("pulsesoc/live-recordings/7", "recording.m3u8")]
    assert created == ["https://signed.example/replay"]
    assert published and published[0][0] == 7
    conn = _connect(database)
    session = conn.execute("SELECT mux_recording_asset_id,recording_status,replay_url FROM pulse_live_sessions WHERE id=7").fetchone()
    post = conn.execute("SELECT live_status,replay_url FROM pulse_posts WHERE live_session_id=7").fetchone()
    job = conn.execute("SELECT status FROM pulse_jobs WHERE id=1").fetchone()
    conn.close()
    assert session == ("asset-7", "mux_asset_ready", "https://stream.mux.com/play-7.m3u8")
    assert post == ("archived", "https://stream.mux.com/play-7.m3u8")
    assert job == ("done",)
