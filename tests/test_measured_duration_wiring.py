"""That the two Mux paths actually reach the enforcement, and in the right order.

`tests/test_measured_video_duration_enforcement.py` proves the rule. It cannot
prove the rule is *called*: gutting the webhook's enforcement line and gutting
the worker's reconcile call both leave that whole suite green, because neither
seam is exercised there. These are the two tests that fail when the wiring is
removed.

The ordering matters as much as the presence. In the webhook the enforcement call
sits *after* the `video.asset.ready` UPDATE that restores `is_available=1`; if the
two were swapped, an over-long video would be blocked and then immediately
un-blocked by the very same request, which no unit test of either statement would
notice.
"""

import datetime
import os
import sqlite3
import tempfile

_bootstrap = tempfile.mktemp(prefix="measured-duration-wiring-", suffix=".sqlite3")
os.environ["DATABASE_URL"] = f"sqlite:///{_bootstrap}"

import pytest  # noqa: E402

from services import mux_live_service, stored_video_policy  # noqa: E402
from tests.test_live_replay_worker import media_worker  # noqa: E402

NINETY_MINUTES = 5400
POST_SURFACE = "pulse"


def _recent_iso(days_ago=1):
    """A `created_at` inside the reconciler's age window, whatever day it is.

    That window is trailing — `now - MEDIA_WORKER_DURATION_RECONCILE_MAX_AGE_DAYS`,
    seven days by default — so a literal date sits inside it for a week and then
    does not. This fixture defaulted to `2026-09-12`, and thirteen days later the
    whole poll section was exercising a row that was not a candidate for any
    reason: two tests failed outright, and four that assert a measured, blocked,
    aged-out or unconfigured row is *not* polled kept passing while asserting
    nothing. `candidates == 0` is only evidence when the row would otherwise
    have been one.
    """
    return (
        datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        - datetime.timedelta(days=days_ago)
    ).isoformat(timespec="seconds")


def _database(tmp_path, *, context_type=POST_SURFACE, duration_seconds=None,
              created_at=None, asset_id="asset"):
    created_at = _recent_iso() if created_at is None else created_at
    path = str(tmp_path / "wiring.sqlite3")
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
        -- context_id is TEXT here because that is how attach_media_to_message
        -- writes it (str(post_id)), and pulse_reels.post_id is INTEGER. The
        -- mismatch is the point: it is what the takedown has to bridge in Python.
        CREATE TABLE pulse_reels (
            id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, video_url TEXT,
            status TEXT, moderation_status TEXT, updated_at TEXT
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
        """
    )
    conn.execute(
        """
        INSERT INTO chat_media_uploads
            (id, uploader_user_id, context_type, context_id, media_type, duration_seconds,
             moderation_status, mux_asset_id, mux_status, processing_status,
             is_available, created_at)
        VALUES (1, 7, ?, '31', 'video', ?, 'approved', ?, 'preparing', 'mux_processing', 1, ?)
        """,
        (context_type, duration_seconds, asset_id, created_at),
    )
    conn.execute(
        """
        INSERT INTO pulse_reels (id, post_id, video_url, status, moderation_status)
        VALUES (4, 31, 'https://stream.mux.com/vod.m3u8', 'active', 'approved')
        """
    )
    conn.commit()
    conn.close()
    return path


def _row(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM chat_media_uploads WHERE id=1").fetchone()
    conn.close()
    return row


def _reel(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM pulse_reels WHERE id=4").fetchone()
    conn.close()
    return row


# --------------------------------------------------------------------------
# The webhook
# --------------------------------------------------------------------------


def _deliver(monkeypatch, path, *, duration, event="video.asset.ready", asset_id="asset"):
    monkeypatch.setattr(media_worker.bot, "db", lambda: sqlite3.connect(path))
    monkeypatch.setattr(mux_live_service, "verify_mux_webhook_signature", lambda *a: {"ok": True})
    payload = {
        "id": "event-1",
        "type": event,
        "data": {
            "id": asset_id,
            "duration": duration,
            "playback_ids": [{"id": "vod", "policy": "public"}],
        },
    }
    app = media_worker.bot.webhook_app
    with app.test_request_context(json=payload):
        response = media_worker.bot.api_pulse_live_mux_webhook()
    assert not isinstance(response, tuple), response
    return response


class TestTheWebhookEnforcesWhatMuxMeasured:
    def test_an_over_long_asset_is_blocked_by_the_delivery_that_reports_it(self, tmp_path, monkeypatch):
        # The row was stored declaring nothing and passed every upload check. This
        # single webhook delivery is the first time the server knows the truth.
        path = _database(tmp_path)
        _deliver(monkeypatch, path, duration=NINETY_MINUTES + 1)

        row = _row(path)
        assert row["moderation_status"] == "blocked"
        assert row["processing_status"] == "rejected_too_long"
        assert int(row["duration_seconds"]) == NINETY_MINUTES + 1
        assert row["moderation_reason"]

    def test_the_measurement_outranks_the_ready_update_in_the_same_request(self, tmp_path, monkeypatch):
        # The ready-handler sets is_available=1 for any ready asset. Enforcement runs
        # after it on purpose, so the block is the last word. Swap the two and this
        # is the assertion that fails.
        path = _database(tmp_path)
        _deliver(monkeypatch, path, duration=NINETY_MINUTES + 1)

        assert _row(path)["is_available"] == 0

    def test_a_redelivered_event_does_not_put_the_video_back(self, tmp_path, monkeypatch):
        # Mux redelivers. Each delivery re-runs the ready-handler, so each one has
        # to re-lose to the measurement.
        path = _database(tmp_path)
        _deliver(monkeypatch, path, duration=NINETY_MINUTES + 1)
        _deliver(monkeypatch, path, duration=NINETY_MINUTES + 1)

        row = _row(path)
        assert row["is_available"] == 0
        assert row["moderation_status"] == "blocked"

    def test_an_asset_at_the_limit_is_published_normally(self, tmp_path, monkeypatch):
        path = _database(tmp_path)
        _deliver(monkeypatch, path, duration=NINETY_MINUTES)

        row = _row(path)
        assert row["moderation_status"] == "approved"
        assert row["is_available"] == 1
        assert row["mux_status"] == "ready"
        assert int(row["duration_seconds"]) == NINETY_MINUTES

    def test_a_delivery_without_a_duration_leaves_the_asset_alone(self, tmp_path, monkeypatch):
        # Mux sends asset events whose payload carries no duration. Reading that
        # absence as zero-seconds-over would block everything.
        path = _database(tmp_path)
        _deliver(monkeypatch, path, duration=None)

        row = _row(path)
        assert row["moderation_status"] == "approved"
        assert row["is_available"] == 1
        assert row["duration_seconds"] is None

    def test_an_errored_event_is_not_a_duration_verdict(self, tmp_path, monkeypatch):
        # Only `ready` carries a trustworthy measurement. An errored asset must be
        # failed, not accused of being too long.
        path = _database(tmp_path)
        _deliver(monkeypatch, path, duration=NINETY_MINUTES + 1, event="video.asset.errored")

        assert _row(path)["processing_status"] != "rejected_too_long"


class TestTheReelThatRepublishesTheVideoIsTakenDownToo:
    """Blocking the upload is not enough on the surface long video is *for*.

    `pulse_reels.video_url` is a denormalized copy of the playback URL, written at
    creation. On read it overrides the post's media (`merged = {**post, **reel_row}`),
    and the feed's `is_available` guard is applied to media items but not to that
    column -- so a Reel stays playable after its own upload has been blocked. These
    tests are about the second row the measurement has to reach.
    """

    def test_the_reel_is_blocked_by_the_delivery_that_blocks_the_upload(self, tmp_path, monkeypatch):
        path = _database(tmp_path)
        _deliver(monkeypatch, path, duration=NINETY_MINUTES + 1)

        assert _row(path)["moderation_status"] == "blocked"
        assert _reel(path)["moderation_status"] == "blocked"

    def test_the_text_post_id_is_bridged_rather_than_compared_in_sql(self, tmp_path, monkeypatch):
        # context_id is TEXT ('31'), pulse_reels.post_id is INTEGER (31). Compared
        # in SQL, Postgres raises and SQLite quietly matches nothing -- and nothing
        # matched looks exactly like a clean pass. The reel id in the return value
        # is what distinguishes "took the reel down" from "found no reel".
        path = _database(tmp_path)
        _deliver(monkeypatch, path, duration=NINETY_MINUTES + 1)

        assert _reel(path)["moderation_status"] == "blocked"
        assert _reel(path)["video_url"], "the URL is kept: a block is not a deletion"

    def test_a_video_within_the_limit_leaves_its_reel_alone(self, tmp_path, monkeypatch):
        path = _database(tmp_path)
        _deliver(monkeypatch, path, duration=NINETY_MINUTES)

        assert _reel(path)["moderation_status"] == "approved"

    def test_a_messenger_upload_cannot_block_a_reel_that_shares_its_context_id(self, tmp_path, monkeypatch):
        # The trap this guards. A messenger upload's context_id is a *message* id,
        # and message 31 has nothing to do with post 31. Reading it as a post id
        # would take down an unrelated stranger's Reel on every long chat video.
        path = _database(tmp_path, context_type="chat")
        _deliver(monkeypatch, path, duration=NINETY_MINUTES + 1)

        assert _row(path)["moderation_status"] == "blocked"
        assert _reel(path)["moderation_status"] == "approved"

    def test_the_upload_block_survives_a_reel_takedown_that_fails(self, tmp_path, monkeypatch):
        # The upload block is already durable when the reel takedown runs. Letting a
        # failure there raise through the caller's transaction would roll back the
        # block itself -- trading a playable Reel for a playable everything.
        path = _database(tmp_path)
        conn = sqlite3.connect(path)
        conn.execute("DROP TABLE pulse_reels")
        conn.commit()
        conn.close()

        _deliver(monkeypatch, path, duration=NINETY_MINUTES + 1)

        row = _row(path)
        assert row["moderation_status"] == "blocked"
        assert row["is_available"] == 0


# --------------------------------------------------------------------------
# The worker's reconciler
# --------------------------------------------------------------------------


def _mux_ready(duration):
    return lambda asset_id: {"ok": True, "mux_status": "ready", "asset": {"id": asset_id, "duration": duration}}


def _reconcile(monkeypatch, path, *, get_asset, configured=True):
    monkeypatch.setattr(media_worker.bot, "db", lambda: sqlite3.connect(path))
    monkeypatch.setattr(media_worker.media_service, "mux_diagnostics", lambda: {"configured": configured})
    monkeypatch.setattr(media_worker.media_service, "get_mux_asset", get_asset)
    return media_worker.reconcile_stored_video_durations()


class TestThePollIsTheGuaranteedPath:
    """A lost webhook delivery must not mean a permanently unmeasured video.

    An unmeasured row is indistinguishable, to every reader, from a row that was
    measured and found to be within the limit -- so "the webhook usually arrives"
    is not an enforcement story. The poll closes it, and reaches the same
    enforcement function rather than deciding for itself.
    """

    def test_an_unmeasured_asset_is_polled_and_enforced(self, tmp_path, monkeypatch):
        path = _database(tmp_path)
        outcome = _reconcile(monkeypatch, path, get_asset=_mux_ready(NINETY_MINUTES + 1))

        # The row the rest of this class disqualifies one condition at a time is a
        # candidate here. Without this, every `candidates == 0` below is satisfied
        # by a fixture that produced no candidates to begin with.
        assert outcome["candidates"] == 1
        assert outcome["blocked"] == [1]
        row = _row(path)
        assert row["moderation_status"] == "blocked"
        assert int(row["duration_seconds"]) == NINETY_MINUTES + 1

    def test_a_polled_asset_within_the_limit_is_recorded_not_blocked(self, tmp_path, monkeypatch):
        path = _database(tmp_path)
        outcome = _reconcile(monkeypatch, path, get_asset=_mux_ready(NINETY_MINUTES))

        assert outcome["blocked"] == []
        row = _row(path)
        assert row["moderation_status"] == "approved"
        # Recorded, so the next cycle does not poll this row again forever.
        assert int(row["duration_seconds"]) == NINETY_MINUTES

    def test_an_already_measured_row_is_not_polled_again(self, tmp_path, monkeypatch):
        path = _database(tmp_path, duration_seconds=42)
        outcome = _reconcile(
            monkeypatch, path,
            get_asset=lambda asset_id: pytest.fail("a measured row must not be re-fetched"))

        assert outcome["candidates"] == 0

    def test_an_asset_that_is_not_ready_yet_is_left_for_a_later_cycle(self, tmp_path, monkeypatch):
        path = _database(tmp_path)
        outcome = _reconcile(
            monkeypatch, path,
            get_asset=lambda asset_id: {"ok": True, "mux_status": "preparing", "asset": {}})

        assert outcome["measured"] == 0
        assert _row(path)["duration_seconds"] is None

    def test_a_fetch_failure_does_not_block_the_asset(self, tmp_path, monkeypatch):
        # Failing to reach Mux says nothing about how long the video is. Convicting
        # on a network error would take down video during any Mux outage.
        path = _database(tmp_path)

        def explode(asset_id):
            raise RuntimeError("mux unreachable")

        outcome = _reconcile(monkeypatch, path, get_asset=explode)

        assert outcome["blocked"] == []
        assert _row(path)["moderation_status"] == "approved"

    def test_a_row_beyond_the_age_window_is_not_polled_forever(self, tmp_path, monkeypatch):
        # Mux deletes assets. Without the window those rows would be re-fetched
        # every cycle for the life of the table.
        path = _database(tmp_path, created_at="2020-01-01T00:00:00")
        outcome = _reconcile(
            monkeypatch, path,
            get_asset=lambda asset_id: pytest.fail("an aged-out row must not be re-fetched"))

        assert outcome["candidates"] == 0

    def test_an_already_blocked_row_is_not_polled_again(self, tmp_path, monkeypatch):
        path = _database(tmp_path)
        conn = sqlite3.connect(path)
        conn.execute("UPDATE chat_media_uploads SET moderation_status='blocked' WHERE id=1")
        conn.commit()
        conn.close()
        outcome = _reconcile(
            monkeypatch, path,
            get_asset=lambda asset_id: pytest.fail("a blocked row must not be re-fetched"))

        assert outcome["candidates"] == 0

    def test_the_poll_is_skipped_entirely_when_mux_is_not_configured(self, tmp_path, monkeypatch):
        path = _database(tmp_path)
        outcome = _reconcile(
            monkeypatch, path, configured=False,
            get_asset=lambda asset_id: pytest.fail("no provider, no call"))

        assert outcome == {"skipped": "mux_not_configured"}

    def test_an_unregistered_surface_is_measured_but_not_taken_down(self, tmp_path, monkeypatch):
        # `asset_focus` reaches this table today and is not in the policy table. The
        # strictest-cap fallback would convict it at 60 seconds.
        assert not stored_video_policy.is_known_surface("asset_focus")
        path = _database(tmp_path, context_type="asset_focus")
        outcome = _reconcile(monkeypatch, path, get_asset=_mux_ready(9999))

        assert outcome["blocked"] == []
        assert _row(path)["moderation_status"] == "approved"


class TestTheWorkerCycleRunsTheReconciler:
    """A reconciler nobody calls is a silent no-op: nothing fails, nothing is measured.

    Only the call is asserted here -- the reconciler's own behaviour is covered
    functionally above. The other cycle passes are stubbed because this test is
    about `run_cycle`'s wiring, and giving it the full worker schema would make it
    fail for reasons that have nothing to do with duration.
    """

    def test_the_cycle_calls_the_duration_pass_and_reports_it(self, monkeypatch):
        for other in ("reconcile_live_replay_backlog", "process_pending_uploads",
                      "process_media_jobs", "process_playback_backlog", "process_cover_backlog"):
            monkeypatch.setattr(media_worker, other, lambda *a, **kw: {})
        calls = []
        monkeypatch.setattr(media_worker, "reconcile_stored_video_durations",
                            lambda *a, **kw: calls.append(a) or {"candidates": 0, "measured": 0, "blocked": []})

        result = media_worker.run_cycle()

        assert len(calls) == 1, "run_cycle must reconcile stored video durations"
        assert result["durations"] == {"candidates": 0, "measured": 0, "blocked": []}
