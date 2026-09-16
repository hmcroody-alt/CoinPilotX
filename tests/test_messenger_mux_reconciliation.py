"""That a messenger attachment's Mux asset is ever told it became ready.

`comm_v2_attachments` has carried `mux_asset_id`/`mux_playback_id`/`mux_status`
since it was introduced, and the Mux webhook has never written a single one of
them. It updates `chat_media_uploads`, `pulse_media_assets`, `pulse_live_sessions`
and `pulse_live_streams` -- four tables, none of them the one messenger reads.
So an ingested conversation video would stay at the status it was created with
forever, and the ingest that produced it would be worse than useless.

The ordering constraint is the reason this file exists rather than an assertion
bolted onto an existing suite. `_attachment_payload` serves `playback_url` as a
video's `url`. Mux hands out a playback id at asset-creation time, long before
the manifest exists, so writing the HLS URL on anything except `ready` points
the player at a manifest that 404s -- and a 404 manifest paints black with no
error, which is the precise failure the whole media mission exists to remove.

Hence the two halves of the contract, one per direction:

  ready   -> flip `playback_url` to HLS, because adaptive streaming is the point
  errored -> leave `playback_url` ALONE, because the progressive download URL
             still works and a failed transcode must degrade to slow, never to
             broken

The second is the one that would rot silently. Nothing user-visible breaks the
day it regresses; videos simply start disappearing for the subset of uploads
Mux failed to process.
"""

import os
import sqlite3
import tempfile

_bootstrap = tempfile.mktemp(prefix="messenger-mux-reconcile-", suffix=".sqlite3")
os.environ["DATABASE_URL"] = f"sqlite:///{_bootstrap}"

from services import mux_live_service  # noqa: E402
from tests.test_live_replay_worker import media_worker  # noqa: E402

# The production shape: attachment 601 / media_upload 87, a foundation-backed
# video whose `playback_url` is the progressive download endpoint because that
# is all `_attach_foundation_media` has ever written.
PROGRESSIVE = "/api/messages/media/87/download"
ASSET = "asset-601"


def _database(tmp_path, *, asset_id=ASSET, playback_url=PROGRESSIVE, mux_status="preparing"):
    path = str(tmp_path / "reconcile.sqlite3")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE chat_media_uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT, uploader_user_id INTEGER,
            context_type TEXT, context_id TEXT, media_type TEXT, duration_seconds REAL,
            moderation_status TEXT, moderation_reason TEXT, mux_asset_id TEXT,
            mux_status TEXT, mux_playback_id TEXT, playback_url TEXT,
            processing_status TEXT, is_available INTEGER, error_message TEXT,
            created_at TEXT, updated_at TEXT, deleted_at TEXT
        );
        CREATE TABLE pulse_media_assets (
            media_id INTEGER, mux_asset_id TEXT, mux_status TEXT, mux_playback_id TEXT,
            playback_url TEXT, processing_status TEXT, updated_at TEXT
        );
        CREATE TABLE pulse_live_sessions (
            id INTEGER PRIMARY KEY, status TEXT, mux_live_stream_id TEXT,
            mux_recording_asset_id TEXT, mux_recording_playback_id TEXT,
            mux_recording_duration_seconds REAL, replay_url TEXT, recording_status TEXT,
            recording_error TEXT, mux_live_status TEXT, publish_state TEXT,
            provider TEXT, is_live INTEGER, record_replay INTEGER DEFAULT 1,
            replay_reel_id INTEGER DEFAULT 0, updated_at TEXT
        );
        CREATE TABLE pulse_live_streams (
            mux_recording_asset_id TEXT, mux_recording_playback_id TEXT,
            mux_live_stream_id TEXT, session_id INTEGER, updated_at TEXT,
            status TEXT, mux_live_status TEXT
        );
        CREATE TABLE pulse_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_type TEXT, target_type TEXT,
            target_id INTEGER, status TEXT, attempts INTEGER, max_attempts INTEGER,
            error_message TEXT, run_after TEXT, created_at TEXT, updated_at TEXT,
            completed_at TEXT
        );
        CREATE TABLE pulse_live_events (
            event_type TEXT, actor_user_id INTEGER, post_id INTEGER,
            payload_json TEXT, created_at TEXT
        );
        CREATE TABLE comm_v2_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER,
            conversation_id INTEGER, media_upload_id INTEGER, media_type TEXT,
            storage_provider TEXT, url TEXT, cdn_url TEXT, playback_url TEXT,
            thumbnail_url TEXT, mime_type TEXT, mux_asset_id TEXT,
            mux_playback_id TEXT, mux_status TEXT, created_at TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO comm_v2_attachments
            (id, message_id, conversation_id, media_upload_id, media_type,
             storage_provider, url, playback_url, thumbnail_url, mime_type,
             mux_asset_id, mux_playback_id, mux_status, created_at)
        VALUES (601, 1728, 6, 87, 'video', 'messenger_media_foundation', ?, ?,
                '/api/messages/media/87/thumbnail', 'video/quicktime', ?, '', ?,
                '2026-09-14T00:00:00')
        """,
        (PROGRESSIVE, playback_url, asset_id, mux_status),
    )
    # A second attachment on a different asset, so "the right row" is a claim
    # this file can actually make rather than one it assumes.
    conn.execute(
        """
        INSERT INTO comm_v2_attachments
            (id, message_id, conversation_id, media_upload_id, media_type,
             storage_provider, url, playback_url, mux_asset_id, mux_playback_id,
             mux_status, created_at)
        VALUES (600, 1727, 6, 86, 'video', 'messenger_media_foundation',
                '/api/messages/media/86/download', '/api/messages/media/86/download',
                'asset-600', '', 'preparing', '2026-09-14T00:00:00')
        """
    )
    conn.commit()
    conn.close()
    return path


def _attachment(path, attachment_id=601):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM comm_v2_attachments WHERE id=?", (attachment_id,)
    ).fetchone()
    conn.close()
    return row


def _deliver(monkeypatch, path, *, event="video.asset.ready", asset_id=ASSET,
             playback_ids=({"id": "vod601", "policy": "public"},)):
    monkeypatch.setattr(media_worker.bot, "db", lambda: sqlite3.connect(path))
    monkeypatch.setattr(mux_live_service, "verify_mux_webhook_signature", lambda *a: {"ok": True})
    payload = {
        "id": "event-1",
        "type": event,
        "data": {"id": asset_id, "duration": 12.5, "playback_ids": list(playback_ids)},
    }
    app = media_worker.bot.webhook_app
    with app.test_request_context(json=payload):
        response = media_worker.bot.api_pulse_live_mux_webhook()
    assert not isinstance(response, tuple), response
    return response


class TestReadyIsTheOnlyThingThatFlipsPlayback:
    def test_a_ready_asset_becomes_adaptive(self, tmp_path, monkeypatch):
        # The whole point of the ingest: a 172 MB progressive QuickTime stops
        # being progressive.
        path = _database(tmp_path)
        _deliver(monkeypatch, path)

        row = _attachment(path)
        assert row["mux_status"] == "ready"
        assert row["mux_playback_id"] == "vod601"
        assert row["playback_url"] == "https://stream.mux.com/vod601.m3u8"

    def test_an_errored_asset_keeps_the_url_that_still_works(self, tmp_path, monkeypatch):
        # The safety property. Mux failing to transcode is not a reason to take
        # away a video the user can already download and watch.
        path = _database(tmp_path)
        _deliver(monkeypatch, path, event="video.asset.errored")

        row = _attachment(path)
        assert row["mux_status"] == "errored"
        assert row["playback_url"] == PROGRESSIVE

    def test_a_ready_event_without_a_playback_id_does_not_blank_the_url(self, tmp_path, monkeypatch):
        # `mux_live_service.playback_url("")` returns "". Writing that would
        # leave the attachment with no source at all -- a black frame produced
        # by the very event that was supposed to fix it.
        path = _database(tmp_path)
        _deliver(monkeypatch, path, playback_ids=())

        assert _attachment(path)["playback_url"] == PROGRESSIVE

    def test_only_the_attachment_that_owns_the_asset_moves(self, tmp_path, monkeypatch):
        # `mux_asset_id` is the join key and it is the only one. A webhook for
        # one conversation video must not touch its neighbour.
        path = _database(tmp_path)
        _deliver(monkeypatch, path)

        neighbour = _attachment(path, 600)
        assert neighbour["mux_status"] == "preparing"
        assert neighbour["playback_url"] == "/api/messages/media/86/download"

    def test_redelivery_is_idempotent(self, tmp_path, monkeypatch):
        # Mux redelivers. The second delivery must land on the same state as the
        # first rather than, say, re-flipping a URL that is already HLS.
        path = _database(tmp_path)
        _deliver(monkeypatch, path)
        first = _attachment(path)["playback_url"]
        _deliver(monkeypatch, path)

        assert _attachment(path)["playback_url"] == first


class TestTheGuardAroundAMissingTable:
    def test_a_deployment_without_comm_v2_still_reconciles_everything_else(self, tmp_path, monkeypatch):
        # comm_v2 route packs register inside `except Exception`, so a broken
        # one leaves the table absent. On Postgres a single failed statement
        # poisons the surrounding transaction, which would lose the live-replay
        # reconciliation above it AND make Mux retry the event forever. The
        # `table_exists` guard is what keeps an absent table boring.
        path = _database(tmp_path)
        conn = sqlite3.connect(path)
        conn.execute("DROP TABLE comm_v2_attachments")
        conn.commit()
        conn.close()

        _deliver(monkeypatch, path)

        conn = sqlite3.connect(path)
        events = conn.execute("SELECT COUNT(*) FROM pulse_live_events").fetchone()[0]
        conn.close()
        # The event was recorded, so the transaction reached its end and
        # committed rather than rolling back.
        assert events == 1
