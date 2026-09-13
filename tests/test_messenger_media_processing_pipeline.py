"""The derived assets a media bubble needs, and who produces them.

Every Messenger photo, video and voice note has been enqueuing a processing job
since the foundation was written, and nothing has ever consumed one. The media
engine's ``MEDIA_JOB_TYPES`` never listed the three ``messenger_*`` types, and
its dispatcher retires an unrecognised type as *done* -- so the jobs drained
silently, no thumbnail or duration or waveform was ever produced,
``processing_status`` stayed ``queued`` for the life of the attachment, and the
thread had nothing to render but the full asset. That is both the black card and
most of the "media loads too slowly".

These tests run real ffmpeg against real generated media rather than asserting
that a mock was called. A mocked pipeline passes whether or not the bytes it
produces are an image, and the defect being fixed here is precisely that a
plausible-looking pipeline produced nothing.

The suite skips rather than fails when ffmpeg is absent: the handler's contract
in that case is to *defer* (return the job to the queue without spending its
error budget), and `DeferralIsNotFailure` covers that branch without needing the
binary to be missing.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "")

from services import messenger_media_foundation as foundation  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _generate(path: Path, args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args, str(path)], check=True, timeout=120)


class ProcessingHarness(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.cur = self.conn.cursor()
        foundation.ensure_schema(self.cur)
        self.conn.commit()
        self.storage = Path(tempfile.mkdtemp(prefix="messenger-storage-"))
        # The env var takes precedence over the module constant in
        # local_private_root(), so patching only the constant would silently lose
        # to any other test in the process that exported it.
        self._previous_dir = os.environ.get("MESSENGER_MEDIA_LOCAL_DIR")
        os.environ["MESSENGER_MEDIA_LOCAL_DIR"] = str(self.storage)
        # Membership tables, seeded for user 7 and empty for the fallbacks: a
        # refusal has to come from reaching the end of the chain, not from a
        # missing-table error that would mask a real regression.
        self.cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS comm_v2_conversations (
                id INTEGER PRIMARY KEY, status TEXT, deleted_at TEXT);
            CREATE TABLE IF NOT EXISTS comm_v2_participants (
                conversation_id INTEGER, user_id INTEGER,
                membership_state TEXT, left_at TEXT);
            CREATE TABLE IF NOT EXISTS pulse_conversations (
                id INTEGER PRIMARY KEY, status TEXT, deleted_at TEXT);
            CREATE TABLE IF NOT EXISTS pulse_conversation_participants (
                conversation_id INTEGER, user_id INTEGER, left_at TEXT);
            CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS conversation_members (
                conversation_id INTEGER, user_id INTEGER);
            CREATE TABLE IF NOT EXISTS blocked_users (
                blocker_user_id INTEGER, blocked_user_id INTEGER);
            INSERT INTO comm_v2_conversations (id, status, deleted_at) VALUES (44, 'active', NULL);
            INSERT INTO comm_v2_participants (conversation_id, user_id, membership_state, left_at)
                VALUES (44, 7, 'active', NULL);
            """
        )
        self.conn.commit()

    def tearDown(self):
        if self._previous_dir is None:
            os.environ.pop("MESSENGER_MEDIA_LOCAL_DIR", None)
        else:
            os.environ["MESSENGER_MEDIA_LOCAL_DIR"] = self._previous_dir
        self.conn.close()
        shutil.rmtree(self.storage, ignore_errors=True)

    def _attachment(self, media_type, mime_type, storage_key, *, upload_status="uploaded"):
        self.cur.execute(
            """
            INSERT INTO message_attachments
            (conversation_id, conversation_model, sender_id, media_type, mime_type,
             original_filename, storage_key, signed_url_strategy, upload_status,
             processing_status, created_at, updated_at)
            VALUES (?, 'pulse', 7, ?, ?, ?, ?, 'private', ?, 'queued', ?, ?)
            """,
            (44, media_type, mime_type, Path(storage_key).name, storage_key,
             upload_status, foundation.now_iso(), foundation.now_iso()),
        )
        return int(self.cur.lastrowid)

    def _row(self, attachment_id):
        self.cur.execute("SELECT * FROM message_attachments WHERE id=?", (attachment_id,))
        return self.cur.fetchone()

    def _place(self, storage_key, args):
        target = foundation._local_path(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        _generate(target, args)
        return target


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg and ffprobe are required to generate and probe fixtures")
class VideoProcessingProducesAPoster(ProcessingHarness):
    def test_a_video_gains_a_poster_duration_and_dimensions(self):
        key = "messenger/44/clip.mp4"
        self._place(key, ["-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=3",
                          "-c:v", "libx264", "-pix_fmt", "yuv420p"])
        attachment_id = self._attachment("video", "video/mp4", key)

        result = foundation.process_attachment(self.cur, attachment_id, "messenger_video_metadata_thumbnail")
        self.assertEqual(result["status"], "processed")

        row = self._row(attachment_id)
        self.assertEqual(row["processing_status"], "ready")
        self.assertEqual(row["width"], 640)
        self.assertEqual(row["height"], 360)
        self.assertAlmostEqual(row["duration_ms"], 3000, delta=200)
        self.assertTrue(row["thumbnail_key"])
        self.assertNotEqual(row["thumbnail_key"], row["storage_key"])

    def test_the_poster_is_a_real_decodable_image_smaller_than_the_video(self):
        """The failure this catches is a zero-byte or non-image thumbnail.

        A poster key written into the row is a promise that the bubble can
        render it. An ffmpeg invocation that exits non-zero still leaves the
        output file behind, so "the file exists" is not evidence.
        """
        key = "messenger/44/clip.mp4"
        source = self._place(key, ["-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=4",
                                   "-c:v", "libx264", "-pix_fmt", "yuv420p"])
        attachment_id = self._attachment("video", "video/mp4", key)
        foundation.process_attachment(self.cur, attachment_id, "messenger_video_metadata_thumbnail")

        poster = foundation._local_path(self._row(attachment_id)["thumbnail_key"])
        self.assertTrue(poster.exists())
        dimensions = foundation._probe_dimensions(poster)
        self.assertIsNotNone(dimensions, "the poster is not a decodable image")
        self.assertLessEqual(dimensions[0], foundation.THUMBNAIL_MAX_EDGE)
        self.assertLess(poster.stat().st_size, source.stat().st_size)

    def test_the_poster_is_not_taken_from_the_very_first_frame(self):
        """A phone's first frame is routinely black. Seeking past it is the point."""
        source = Path(foundation.__file__).read_text(encoding="utf-8")
        extractor = source.split("def _extract_video_poster(")[1].split("\ndef ")[0]
        self.assertIn('"-ss"', extractor)
        self.assertNotIn("offset = 0.0\n    return", extractor)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg and ffprobe are required to generate and probe fixtures")
class PhotoProcessingProducesAThumbnail(ProcessingHarness):
    def test_a_photo_gains_a_smaller_thumbnail_and_its_dimensions(self):
        key = "messenger/44/shot.jpg"
        self._place(key, ["-f", "lavfi", "-i", "testsrc=size=1600x1200:rate=1:duration=1", "-frames:v", "1"])
        attachment_id = self._attachment("photo", "image/jpeg", key)

        result = foundation.process_attachment(self.cur, attachment_id, "messenger_photo_thumbnail")
        self.assertEqual(result["status"], "processed")

        row = self._row(attachment_id)
        self.assertEqual((row["width"], row["height"]), (1600, 1200))
        self.assertEqual(row["processing_status"], "ready")
        thumbnail = foundation._local_path(row["thumbnail_key"])
        self.assertEqual(foundation._probe_dimensions(thumbnail)[0], foundation.THUMBNAIL_MAX_EDGE)

    def test_the_thumbnail_never_overwrites_the_original(self):
        key = "messenger/44/shot.jpg"
        source = self._place(key, ["-f", "lavfi", "-i", "testsrc=size=1600x1200:rate=1:duration=1", "-frames:v", "1"])
        original_bytes = source.read_bytes()
        attachment_id = self._attachment("photo", "image/jpeg", key)
        foundation.process_attachment(self.cur, attachment_id, "messenger_photo_thumbnail")
        self.assertEqual(source.read_bytes(), original_bytes)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg and ffprobe are required to generate and probe fixtures")
class VoiceProcessingRecoversDuration(ProcessingHarness):
    def test_a_voice_note_gains_a_server_measured_duration(self):
        key = "messenger/44/note.m4a"
        self._place(key, ["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "aac"])
        attachment_id = self._attachment("voice", "audio/mp4", key)

        result = foundation.process_attachment(self.cur, attachment_id, "messenger_voice_waveform")
        self.assertEqual(result["status"], "processed")
        row = self._row(attachment_id)
        self.assertEqual(row["processing_status"], "ready")
        self.assertAlmostEqual(row["duration_ms"], 2000, delta=250)


class ProcessingNeverDestroysExistingMetadata(ProcessingHarness):
    def test_a_value_the_client_already_supplied_survives_a_pass_that_finds_nothing(self):
        """COALESCE, not assignment.

        The client sends width/height/duration at upload time from the asset it
        picked. A processing pass that cannot measure them -- a codec ffprobe
        does not know, a truncated file -- must leave those values alone. Writing
        NULL over them would turn a working bubble into a black card, which is
        the failure this whole mission is about.
        """
        key = "messenger/44/opaque.mp4"
        target = foundation._local_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\x00" * 256)
        attachment_id = self._attachment("video", "video/mp4", key)
        self.cur.execute(
            "UPDATE message_attachments SET width=1920, height=1080, duration_ms=5000 WHERE id=?",
            (attachment_id,),
        )

        foundation._write_processing_result(self.cur, attachment_id, {
            "width": None, "height": None, "duration_ms": None, "thumbnail_key": None,
        })

        row = self._row(attachment_id)
        self.assertEqual((row["width"], row["height"], row["duration_ms"]), (1920, 1080, 5000))
        self.assertEqual(row["processing_status"], "ready")


class DeferralIsNotFailure(ProcessingHarness):
    def test_an_upload_that_has_not_landed_defers_instead_of_failing(self):
        attachment_id = self._attachment("video", "video/mp4", "messenger/44/pending.mp4", upload_status="pending")
        result = foundation.process_attachment(self.cur, attachment_id, "messenger_video_metadata_thumbnail")
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(result["reason"], "upload_incomplete")
        self.assertEqual(self._row(attachment_id)["processing_status"], "queued")

    def test_missing_bytes_defer_rather_than_marking_the_attachment_ready(self):
        attachment_id = self._attachment("video", "video/mp4", "messenger/44/absent.mp4")
        result = foundation.process_attachment(self.cur, attachment_id, "messenger_video_metadata_thumbnail")
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(result["reason"], "bytes_unavailable")
        self.assertEqual(self._row(attachment_id)["processing_status"], "queued")

    def test_an_unknown_job_type_is_skipped_without_touching_the_row(self):
        attachment_id = self._attachment("video", "video/mp4", "messenger/44/clip.mp4")
        result = foundation.process_attachment(self.cur, attachment_id, "not_a_messenger_job")
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(self._row(attachment_id)["processing_status"], "queued")

    def test_a_deleted_attachment_is_skipped(self):
        attachment_id = self._attachment("photo", "image/jpeg", "messenger/44/gone.jpg")
        self.cur.execute("UPDATE message_attachments SET deleted_at=? WHERE id=?", (foundation.now_iso(), attachment_id))
        result = foundation.process_attachment(self.cur, attachment_id, "messenger_photo_thumbnail")
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "deleted")


class TheWorkerActuallyConsumesTheseJobs(unittest.TestCase):
    """The jobs were orphaned by the dispatcher, so the wiring is the fix.

    Read as source rather than by importing ``media_worker``: importing it pulls
    in ``bot`` and every integration the monolith touches, which no unit test
    should require in order to assert that three strings are in a set.
    """

    def setUp(self):
        root = Path(__file__).resolve().parent.parent
        self.worker_source = (root / "media_worker.py").read_text(encoding="utf-8")

    def test_the_job_type_allowlist_includes_the_messenger_types(self):
        self.assertIn("messenger_media_foundation.PROCESSING_JOB_TYPES", self.worker_source)
        line = next(l for l in self.worker_source.splitlines() if l.startswith("MEDIA_JOB_TYPES"))
        self.assertIn("PROCESSING_JOB_TYPES", line)

    def test_the_dispatcher_routes_them_before_the_silent_done_branch(self):
        """Order is the whole defect.

        ``_process_media_job`` retires an unrecognised job type as ``done``. A
        handler placed after that branch would never run, and the bug would look
        fixed in the diff while nothing changed in production.
        """
        body = self.worker_source.split("def _process_media_job(")[1]
        route_at = body.index("PROCESSING_JOB_TYPES")
        silent_done_at = body.index("if job_type not in MEDIA_JOB_TYPES:")
        self.assertLess(route_at, silent_done_at)

    def test_a_deferral_reschedules_instead_of_spending_the_error_budget(self):
        handler = self.worker_source.split("def _process_messenger_attachment_job(")[1].split("\ndef ")[0]
        self.assertIn('if status == "deferred"', handler)
        self.assertIn("_reschedule(cur, job", handler)
        # A deferral must not be reported as a completed job.
        deferral_block = handler.split('if status == "deferred"')[1].split("return")[0]
        self.assertNotIn("_complete_job", deferral_block)

    def test_the_enqueued_types_and_the_consumed_types_are_the_same_set(self):
        """The two lists drifting apart is the original bug, restated."""
        source = Path(foundation.__file__).read_text(encoding="utf-8")
        enqueued = set()
        table = source.split("def _enqueue_processing_jobs(")[1].split("}.get(media_type)")[0]
        for line in table.splitlines():
            if '"messenger_' in line:
                enqueued.add(line.split('"messenger_')[1].split('"')[0])
        self.assertEqual({f"messenger_{name}" for name in enqueued}, foundation.PROCESSING_JOB_TYPES)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg and ffprobe are required to generate and probe fixtures")
class ThePreviewIsDeliveredSeparatelyFromTheOriginal(ProcessingHarness):
    """A thumbnail the pipeline produced is worth nothing until a bubble can fetch it.

    The client had a thumbnail slot all along, but it resolved to the same
    attachment id as the full asset and therefore to the same ``/download`` URL.
    So "thumbnail-first" downloaded every original at full size, and a video
    bubble handed an entire movie to an image loader. A separate route is what
    makes the two distinguishable.
    """

    VIEWER = {"user_id": 7}

    def _processed_video(self):
        key = "messenger/44/clip.mp4"
        self._place(key, ["-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=3",
                          "-c:v", "libx264", "-pix_fmt", "yuv420p"])
        attachment_id = self._attachment("video", "video/mp4", key)
        foundation.process_attachment(self.cur, attachment_id, "messenger_video_metadata_thumbnail")
        return attachment_id, foundation._local_path(key)

    def test_the_preview_target_is_the_derived_jpeg_not_the_original(self):
        attachment_id, original = self._processed_video()
        target = foundation.attachment_thumbnail_target(self.cur, self.VIEWER, attachment_id)
        self.assertEqual(target["mime_type"], foundation.THUMBNAIL_MIME)
        self.assertNotEqual(target["path"], original)
        self.assertLess(target["path"].stat().st_size, original.stat().st_size)

    def test_an_unprocessed_attachment_has_no_preview_and_does_not_get_the_original(self):
        """404, not a silent substitution.

        Answering "no preview yet" with the full asset is how a thumbnail request
        becomes a multi-gigabyte download for a 90-minute video -- the exact cost
        this route exists to remove, reintroduced invisibly.
        """
        key = "messenger/44/unprocessed.mp4"
        self._place(key, ["-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=1",
                          "-c:v", "libx264", "-pix_fmt", "yuv420p"])
        attachment_id = self._attachment("video", "video/mp4", key)
        with self.assertRaises(foundation.MessengerMediaError) as caught:
            foundation.attachment_thumbnail_target(self.cur, self.VIEWER, attachment_id)
        self.assertEqual(caught.exception.error, "thumbnail_not_available")
        self.assertEqual(caught.exception.status_code, 404)

    def test_a_preview_is_advertised_only_once_it_exists(self):
        key = "messenger/44/shot.jpg"
        self._place(key, ["-f", "lavfi", "-i", "testsrc=size=1600x1200:rate=1:duration=1", "-frames:v", "1"])
        attachment_id = self._attachment("photo", "image/jpeg", key)

        before = foundation._attachment_payload(self.cur, attachment_id, 7, include_url=False)
        self.assertNotIn("thumbnail_url", before)

        foundation.process_attachment(self.cur, attachment_id, "messenger_photo_thumbnail")
        after = foundation._attachment_payload(self.cur, attachment_id, 7, include_url=False)
        self.assertEqual(after["thumbnail_url"], f"/api/messages/media/{attachment_id}/thumbnail")

    def test_a_non_member_cannot_fetch_a_preview(self):
        """The preview is the same authorization decision, rendered small."""
        attachment_id, _ = self._processed_video()
        with self.assertRaises(foundation.MessengerMediaError) as caught:
            foundation.attachment_thumbnail_target(self.cur, {"user_id": 99}, attachment_id)
        self.assertEqual(caught.exception.error, "not_conversation_member")

    def test_a_deleted_attachment_has_no_preview(self):
        attachment_id, _ = self._processed_video()
        self.cur.execute(
            "UPDATE message_attachments SET deleted_at=? WHERE id=?", (foundation.now_iso(), attachment_id)
        )
        with self.assertRaises(foundation.MessengerMediaError) as caught:
            foundation.attachment_thumbnail_target(self.cur, self.VIEWER, attachment_id)
        self.assertEqual(caught.exception.error, "attachment_deleted")

    def test_a_document_preview_never_inherits_the_document_mime_type(self):
        """A thumbnail's type is a property of the pipeline, not of the upload.

        Reading it off the row would let an arbitrary uploaded MIME type ride out
        on a route whose response the client renders as an image.
        """
        source = Path(foundation.__file__).read_text(encoding="utf-8")
        target = source.split("def attachment_thumbnail_target(")[1].split("\ndef ")[0]
        self.assertIn("THUMBNAIL_MIME", target)
        self.assertNotIn('_row_get(row, "mime_type"', target)


if __name__ == "__main__":
    unittest.main()
