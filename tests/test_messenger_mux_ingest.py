"""That a conversation video is ever handed to Mux at all.

PulseSoc delivers video through Mux. Reels do, live replays do, the
``chat_media_uploads`` path does -- ``_prepare_attachment_media`` calls
``create_mux_asset_from_url`` and writes the ids down. The foundation upload
path, which is the one every modern messenger attachment actually takes, writes
``''`` into ``mux_asset_id``, ``mux_playback_id`` and ``mux_status`` and has
never called Mux once. So a video sent in a conversation is the only video on
the product served as a progressive byte range off ``/download``: the whole file,
in order, before the last frame is seekable.

The tests below are about the two things that are easy to get wrong here, both
of which cost money or cost the user their video.

**Never ready at creation.** Mux issues a playback id the moment the asset
exists, minutes before a manifest does. ``_attachment_payload`` serves
``playback_url`` as a video's ``url``, so writing the HLS URL on creation points
the player at a ``.m3u8`` that 404s -- and a 404 manifest paints black and
raises nothing. The ingest writes the ids and Mux's own status, and leaves
``playback_url`` alone; the webhook flips it when the asset is genuinely ready.

**Never twice.** An encode is billed. A job redelivered, a video forwarded to a
second conversation, or a second job row for the same attachment must all
resolve to one asset.

The failure-signal tests exist because the worker's messenger handler has
exactly two outcomes -- ``deferred`` reschedules without spending the error
budget, and *everything else retires the job as done*. That makes the
distinction between "Mux is down for a minute" and "this deployment has no Mux"
the difference between a video that gets adaptive playback later and one that
never does.
"""

import os
import sqlite3
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_URL", "")

from services import media_storage  # noqa: E402
from services import messenger_media_foundation as foundation  # noqa: E402

ATTACHMENT_ID = 87
COMM_V2_ID = 601
PROGRESSIVE = "/api/messages/media/87/download"
STORAGE_KEY = "messenger/6/2026/09/87.mov"

COMM_V2_SCHEMA = """
CREATE TABLE comm_v2_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER,
    conversation_id INTEGER, media_upload_id INTEGER, media_type TEXT,
    storage_provider TEXT, url TEXT, playback_url TEXT, thumbnail_url TEXT,
    mime_type TEXT, mux_asset_id TEXT, mux_playback_id TEXT, mux_status TEXT,
    created_at TEXT
);
"""


class _FakeClient:
    """Records what was presigned, so the TTL is assertable rather than assumed."""

    def __init__(self):
        self.calls = []

    def generate_presigned_url(self, operation, Params=None, ExpiresIn=None):  # noqa: N803
        self.calls.append({"operation": operation, "params": dict(Params or {}), "expires_in": ExpiresIn})
        return "https://r2.example.com/%s?signed=1" % (Params or {}).get("Key", "")


class MuxIngestHarness(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.cur = self.conn.cursor()
        foundation.ensure_schema(self.cur)
        self.cur.executescript(COMM_V2_SCHEMA)
        self.cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS pulse_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_type TEXT, target_type TEXT,
                target_id INTEGER, status TEXT, attempts INTEGER, max_attempts INTEGER,
                error_message TEXT, run_after TEXT, created_at TEXT, updated_at TEXT,
                completed_at TEXT
            );
            """
        )
        self.conn.commit()

        self.client = _FakeClient()
        self._real_object_client = media_storage.object_client
        media_storage.object_client = lambda: self.client

        from services import media_service

        self.media_service = media_service
        self._real_create = media_service.create_mux_asset_from_url
        self.mux_calls = []
        self.mux_result = {
            "ok": True,
            "asset_id": "asset-601",
            "playback_id": "vod601",
            "status": "preparing",
            "status_code": 201,
            "error_type": "",
        }
        media_service.create_mux_asset_from_url = self._record_mux

    def tearDown(self):
        media_storage.object_client = self._real_object_client
        self.media_service.create_mux_asset_from_url = self._real_create
        self.conn.close()

    def _record_mux(self, input_url, **kwargs):
        self.mux_calls.append({"input_url": input_url, **kwargs})
        return self.mux_result

    # -- fixtures ---------------------------------------------------------

    def _attachment(self, *, media_type="video", strategy="r2", upload_status="attached", created_at=None):
        stamp = created_at or foundation.now_iso()
        self.cur.execute(
            """
            INSERT INTO message_attachments
                (id, conversation_id, sender_id, media_type, mime_type, original_filename,
                 storage_key, signed_url_strategy, size_bytes, upload_status,
                 processing_status, created_at, updated_at)
            VALUES (?, 6, 7, ?, 'video/quicktime', 'clip.mov', ?, ?, 1024, ?, 'ready', ?, ?)
            """,
            (ATTACHMENT_ID, media_type, STORAGE_KEY, strategy, upload_status, stamp, stamp),
        )
        self.conn.commit()

    def _comm_v2_row(self, row_id=COMM_V2_ID, *, asset_id="", playback_id="", mux_status="", media_upload_id=ATTACHMENT_ID):
        self.cur.execute(
            """
            INSERT INTO comm_v2_attachments
                (id, message_id, conversation_id, media_upload_id, media_type,
                 storage_provider, url, playback_url, mux_asset_id, mux_playback_id,
                 mux_status, created_at)
            VALUES (?, 1728, 6, ?, 'video', 'messenger_media_foundation', ?, ?, ?, ?, ?, ?)
            """,
            (row_id, media_upload_id, PROGRESSIVE, PROGRESSIVE, asset_id, playback_id, mux_status, foundation.now_iso()),
        )
        self.conn.commit()

    def _run(self):
        return foundation.process_attachment(self.cur, ATTACHMENT_ID, foundation.MUX_INGEST_JOB_TYPE)

    def _fetch(self, row_id=COMM_V2_ID):
        return self.cur.execute("SELECT * FROM comm_v2_attachments WHERE id=?", (row_id,)).fetchone()


class TheIngestCreatesTheAssetMessengerNeverHad(MuxIngestHarness):
    def test_a_sent_video_gets_a_mux_asset(self):
        self._attachment()
        self._comm_v2_row()

        result = self._run()

        self.assertEqual(result["status"], "processed")
        row = self._fetch()
        self.assertEqual(row["mux_asset_id"], "asset-601")
        self.assertEqual(row["mux_playback_id"], "vod601")

    def test_the_asset_is_never_recorded_as_ready_at_creation(self):
        """Mux issues a playback id long before a manifest exists behind it."""
        self._attachment()
        self._comm_v2_row()

        self._run()

        self.assertEqual(self._fetch()["mux_status"], "preparing")
        self.assertNotEqual(self._fetch()["mux_status"], "ready")

    def test_playback_url_is_left_on_the_progressive_url_that_works(self):
        """The safety property, and the one that would rot silently.

        Flipping this to HLS here would point the player at a manifest Mux has
        not produced yet. A 404 manifest paints black and raises nothing, so the
        regression would look exactly like "still loading", forever.
        """
        self._attachment()
        self._comm_v2_row()

        self._run()

        self.assertEqual(self._fetch()["playback_url"], PROGRESSIVE)

    def test_mux_is_handed_a_presigned_url_on_its_own_clock(self):
        """Not the viewer's fifteen minutes.

        Mux downloads the source on its own queue. Reusing
        ``SIGNED_URL_TTL_SECONDS`` would expire the URL before a busy queue
        reached it, and widening that constant instead would hand every viewer a
        two-hour credential.
        """
        self._attachment()
        self._comm_v2_row()

        self._run()

        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.client.calls[0]["expires_in"], foundation.MUX_INGEST_URL_TTL_SECONDS)
        self.assertGreater(foundation.MUX_INGEST_URL_TTL_SECONDS, foundation.SIGNED_URL_TTL_SECONDS)
        self.assertEqual(self.client.calls[0]["params"]["Key"], STORAGE_KEY)

    def test_the_source_url_handed_to_mux_is_the_presigned_one(self):
        self._attachment()
        self._comm_v2_row()

        self._run()

        self.assertEqual(len(self.mux_calls), 1)
        self.assertTrue(self.mux_calls[0]["input_url"].startswith("https://"))


class AnEncodeIsBilledSoItHappensOnce(MuxIngestHarness):
    def test_a_redelivered_job_does_not_create_a_second_asset(self):
        self._attachment()
        self._comm_v2_row()

        self._run()
        self._run()

        self.assertEqual(len(self.mux_calls), 1)

    def test_an_attachment_that_already_has_an_asset_is_left_alone(self):
        self._attachment()
        self._comm_v2_row(asset_id="asset-existing", playback_id="vodexisting", mux_status="ready")

        result = self._run()

        self.assertEqual(result["reason"], "already_ingested")
        self.assertEqual(self.mux_calls, [])
        self.assertEqual(self._fetch()["mux_asset_id"], "asset-existing")

    def test_a_forwarded_video_reuses_the_asset_rather_than_paying_twice(self):
        """Two rows, one file. Mux has already transcoded these exact bytes."""
        self._attachment()
        self._comm_v2_row(row_id=601, asset_id="asset-601", playback_id="vod601", mux_status="ready")
        self._comm_v2_row(row_id=777)

        result = self._run()

        self.assertEqual(self.mux_calls, [])
        self.assertEqual(result["reason"], "reused_existing_asset")
        self.assertEqual(self._fetch(777)["mux_asset_id"], "asset-601")
        self.assertEqual(self._fetch(777)["mux_playback_id"], "vod601")

    def test_reuse_does_not_disturb_the_row_that_already_had_it(self):
        self._attachment()
        self._comm_v2_row(row_id=601, asset_id="asset-601", playback_id="vod601", mux_status="ready")
        self._comm_v2_row(row_id=777)

        self._run()

        self.assertEqual(self._fetch(601)["mux_status"], "ready")

    def test_the_queue_refuses_to_stack_two_ingests_on_one_attachment(self):
        """Two jobs claimed concurrently would both read an empty asset id."""
        foundation.enqueue_mux_ingest(self.cur, ATTACHMENT_ID)
        foundation.enqueue_mux_ingest(self.cur, ATTACHMENT_ID)

        count = self.cur.execute(
            "SELECT COUNT(*) FROM pulse_jobs WHERE job_type=? AND target_id=?",
            (foundation.MUX_INGEST_JOB_TYPE, ATTACHMENT_ID),
        ).fetchone()[0]
        self.assertEqual(count, 1)


class FailureIsDeferredOrSettledButNeverConfused(MuxIngestHarness):
    def test_mux_being_unreachable_defers_instead_of_retiring_the_video(self):
        """``deferred`` is the only outcome the worker retries.

        Every other return value retires the job as done, so classifying a
        one-minute outage as anything else costs that video adaptive playback
        permanently.
        """
        self._attachment()
        self._comm_v2_row()
        self.mux_result = {"ok": False, "error_type": "source_unreachable", "status_code": 0}

        result = self._run()

        self.assertEqual(result["status"], "deferred")
        self.assertEqual(self._fetch()["mux_asset_id"], "")

    def test_a_deployment_without_mux_credentials_is_settled_not_retried(self):
        """No amount of retrying gives a server credentials it does not have."""
        self._attachment()
        self._comm_v2_row()
        self.mux_result = {"ok": False, "error_type": "not_configured", "status_code": 0}

        result = self._run()

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "not_configured")

    def test_rejected_credentials_are_settled_too(self):
        self._attachment()
        self._comm_v2_row()
        self.mux_result = {"ok": False, "error_type": "unauthorized", "status_code": 401}

        self.assertEqual(self._run()["status"], "skipped")

    def test_a_video_that_was_never_sent_defers_then_gives_up(self):
        """The upload finished; the message never did.

        Deferring is right at first -- the row this writes into is created when
        the message is sent, which can be a moment later. Deferring forever is a
        job polling every two minutes for the life of the row.
        """
        self._attachment()

        self.assertEqual(self._run()["status"], "deferred")

        self.cur.execute("DELETE FROM message_attachments WHERE id=?", (ATTACHMENT_ID,))
        stale = (datetime.utcnow() - timedelta(seconds=foundation.MUX_INGEST_MAX_WAIT_SECONDS + 60)).isoformat()
        self._attachment(created_at=stale)

        result = self._run()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "never_attached")

    def test_a_long_outage_eventually_stops_polling_mux(self):
        stale = (datetime.utcnow() - timedelta(seconds=foundation.MUX_INGEST_MAX_WAIT_SECONDS + 60)).isoformat()
        self._attachment(created_at=stale)
        self._comm_v2_row()
        self.mux_result = {"ok": False, "error_type": "network_error", "status_code": 0}

        result = self._run()

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "mux_unreachable_gave_up")


class ThingsMuxCannotOrShouldNotBeAskedToDo(MuxIngestHarness):
    def test_an_attachment_on_local_disk_is_skipped_rather_than_retried(self):
        """There is no remote object for Mux to fetch, and there never will be."""
        self._attachment(strategy="private_local_endpoint")
        self._comm_v2_row()

        result = self._run()

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_remote_object")
        self.assertEqual(self.mux_calls, [])

    def test_a_photo_is_not_sent_to_a_video_platform(self):
        self._attachment(media_type="photo")
        self._comm_v2_row()

        result = self._run()

        self.assertEqual(result["reason"], "no_mux_ingest_for_type")
        self.assertEqual(self.mux_calls, [])

    def test_a_deployment_without_comm_v2_does_not_poison_the_transaction(self):
        """comm_v2 registers inside ``except Exception``, so the table can be absent.

        On Postgres a single failed statement aborts the surrounding
        transaction. Asking whether the table is there, rather than finding out
        by failing, is what keeps that boring.
        """
        self._attachment()
        self.cur.execute("DROP TABLE comm_v2_attachments")
        self.conn.commit()

        result = self._run()

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "comm_v2_absent")
        self.assertEqual(self.mux_calls, [])
        # The transaction is still usable, which is the actual claim.
        self.assertEqual(self.cur.execute("SELECT 1").fetchone()[0], 1)

    def test_a_deleted_attachment_is_not_ingested(self):
        self._attachment()
        self._comm_v2_row()
        self.cur.execute("UPDATE message_attachments SET deleted_at=? WHERE id=?", (foundation.now_iso(), ATTACHMENT_ID))
        self.conn.commit()

        self.assertEqual(self._run()["status"], "skipped")
        self.assertEqual(self.mux_calls, [])

    def test_an_upload_still_in_flight_is_deferred(self):
        self._attachment(upload_status="pending")
        self._comm_v2_row()

        self.assertEqual(self._run()["status"], "deferred")
        self.assertEqual(self.mux_calls, [])


class TheAttachPathIsWhatActuallyQueuesIt(unittest.TestCase):
    """Driven through the real attach path, not asserted against its source text.

    A source-text check passes just as happily when the call is unreachable --
    behind the wrong branch, or gated on a media-type spelling the other end of
    the job does not use. `_attach_foundation_media` translates `photo` into
    `image` on its way to the wire, so "which vocabulary is this gate written
    in" is a live question with a wrong answer available.
    """

    def setUp(self):
        from pulse_communications_v2 import service
        from pulse_communications_v2.models import ensure_schema as comm_v2_schema

        self.service = service
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.cur = self.conn.cursor()
        foundation.ensure_schema(self.cur)
        comm_v2_schema(self.cur)
        self.cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS pulse_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_type TEXT, target_type TEXT,
                target_id INTEGER, status TEXT, attempts INTEGER, max_attempts INTEGER,
                error_message TEXT, run_after TEXT, created_at TEXT, updated_at TEXT,
                completed_at TEXT
            );
            """
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _uploaded(self, attachment_id: int, media_type: str):
        stamp = foundation.now_iso()
        self.cur.execute(
            """
            INSERT INTO message_attachments
                (id, conversation_id, conversation_model, sender_id, media_type, mime_type,
                 original_filename, storage_key, signed_url_strategy, size_bytes,
                 upload_status, processing_status, created_at, updated_at)
            VALUES (?, 6, 'comm_v2', 7, ?, 'video/quicktime', 'clip.mov', ?, 'r2', 1024,
                    'uploaded', 'ready', ?, ?)
            """,
            (attachment_id, media_type, "messenger/6/%d.mov" % attachment_id, stamp, stamp),
        )
        self.conn.commit()

    def _queued_ingests(self):
        return self.cur.execute(
            "SELECT target_id FROM pulse_jobs WHERE job_type=?",
            (foundation.MUX_INGEST_JOB_TYPE,),
        ).fetchall()

    def test_sending_a_video_queues_its_mux_ingest(self):
        self._uploaded(87, "video")

        self.service._attach_foundation_media(self.cur, 7, 6, 1728, [87])

        self.assertEqual([r[0] for r in self._queued_ingests()], [87])

    def test_the_job_is_queued_only_once_the_row_it_writes_into_exists(self):
        """Queued after the insert, so the ingest never races the user's own send."""
        self._uploaded(87, "video")

        self.service._attach_foundation_media(self.cur, 7, 6, 1728, [87])

        attached = self.cur.execute(
            "SELECT COUNT(*) FROM comm_v2_attachments WHERE media_upload_id=?", (87,)
        ).fetchone()[0]
        self.assertEqual(attached, 1)
        self.assertEqual(len(self._queued_ingests()), 1)

    def test_sending_a_photo_queues_nothing(self):
        """`photo` is translated to `image` on the way to the wire.

        A gate written in the translated vocabulary would read `image` and never
        match, or worse, match a type the ingest end does not recognise.
        """
        self._uploaded(88, "photo")

        self.service._attach_foundation_media(self.cur, 7, 6, 1729, [88])

        self.assertEqual(self._queued_ingests(), [])


if __name__ == "__main__":
    unittest.main()
