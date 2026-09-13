"""Resumable Messenger uploads: the transfer that survives a phone network.

The duration policy accepts 90 minutes, but a single POST cannot carry one. A
2 GB request has to survive a cell handoff, a backgrounded app and a dropped
tunnel, and a single stream restarts from byte zero every time any of those
happen -- so "90 minutes is allowed" was true of the policy and false of the
product. These tests cover the multipart path that closes that gap.

The object client is a fake, but a *stateful* one: it stores parts, reports them
back through ``list_parts``, and refuses nothing on its own. That matters because
the rules under test are all server-side judgements about what the provider says
is stored, and a mock that merely records calls would pass whether or not those
judgements happen.

Two rules here are worth naming, because both are the difference between a
resumable upload and a corruptible one:

* the resume point is read from the provider, never from the client, and
* the byte total is checked *before* ``complete_multipart_upload``, so finishing
  early cannot produce a truncated object that later has to be explained.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("DATABASE_URL", "")

from services import messenger_media_foundation as foundation  # noqa: E402

MessengerMediaError = foundation.MessengerMediaError

SENDER = {"user_id": 7}
OTHER_MEMBER = {"user_id": 9}
STRANGER = {"user_id": 404}

MB = 1024 * 1024
JPEG_HEADER = b"\xff\xd8\xff\xe0" + b"\x00" * 12
MP4_HEADER = b"\x00\x00\x00\x18ftypisom"
EXE_HEADER = b"MZ\x90\x00" + b"\x00" * 12


class FakeObjectClient:
    """A multipart provider that keeps state, so resume has something to read."""

    def __init__(self):
        self.uploads: dict[str, dict[int, bytes]] = {}
        self.completed: list[dict] = []
        self.aborted: list[str] = []
        self.signed: list[tuple[str, int]] = []
        self._next = 0

    def create_multipart_upload(self, **kwargs):
        self._next += 1
        upload_id = f"upload-{self._next}"
        self.uploads[upload_id] = {}
        self.created_with = kwargs
        return {"UploadId": upload_id}

    def generate_presigned_url(self, operation, Params=None, ExpiresIn=0):
        params = Params or {}
        number = int(params.get("PartNumber") or 0)
        self.signed.append((str(params.get("UploadId") or ""), number))
        return f"https://storage.example/{params.get('Key')}?partNumber={number}&op={operation}"

    def list_parts(self, Bucket=None, Key=None, UploadId=None, MaxParts=1000, PartNumberMarker=0):
        stored = self.uploads.get(str(UploadId), {})
        numbers = sorted(n for n in stored if n > int(PartNumberMarker or 0))
        window = numbers[: int(MaxParts)]
        return {
            "Parts": [
                {"PartNumber": n, "ETag": f'"etag-{n}"', "Size": len(stored[n])}
                for n in window
            ],
            "IsTruncated": len(window) < len(numbers),
            "NextPartNumberMarker": window[-1] if window else 0,
        }

    def complete_multipart_upload(self, Bucket=None, Key=None, UploadId=None, MultipartUpload=None):
        self.completed.append({"key": Key, "upload_id": UploadId, "parts": (MultipartUpload or {}).get("Parts") or []})
        return {"ETag": '"final"'}

    def abort_multipart_upload(self, Bucket=None, Key=None, UploadId=None):
        self.aborted.append(str(UploadId))
        self.uploads.pop(str(UploadId), None)

    # -- test helpers -----------------------------------------------------
    def put_part(self, upload_id: str, number: int, payload: bytes):
        self.uploads.setdefault(upload_id, {})[number] = payload

    def stored_bytes(self, upload_id: str) -> int:
        return sum(len(chunk) for chunk in self.uploads.get(upload_id, {}).values())


class ResumableHarness(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.cur = self.conn.cursor()
        foundation.ensure_schema(self.cur)
        self.storage = Path(tempfile.mkdtemp(prefix="messenger-resumable-"))
        self._previous_dir = os.environ.get("MESSENGER_MEDIA_LOCAL_DIR")
        self._previous_bucket = os.environ.get("S3_BUCKET")
        os.environ["MESSENGER_MEDIA_LOCAL_DIR"] = str(self.storage)
        os.environ["S3_BUCKET"] = "pulsesoc-test"
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
            CREATE TABLE IF NOT EXISTS pulse_jobs (
                id INTEGER PRIMARY KEY, job_type TEXT, target_type TEXT, target_id INTEGER,
                status TEXT, attempts INTEGER, max_attempts INTEGER,
                created_at TEXT, updated_at TEXT);
            INSERT INTO comm_v2_conversations (id, status, deleted_at) VALUES (44, 'active', NULL);
            INSERT INTO comm_v2_participants (conversation_id, user_id, membership_state, left_at)
                VALUES (44, 7, 'active', NULL), (44, 9, 'active', NULL);
            """
        )
        self.conn.commit()

        self.client = FakeObjectClient()
        self.header_bytes = MP4_HEADER
        patches = [
            mock.patch.object(foundation.media_storage, "object_client", return_value=self.client),
            mock.patch.object(foundation.media_storage, "provider", return_value="s3"),
            mock.patch.object(foundation.media_storage, "head_object", side_effect=self._head_object),
            mock.patch.object(foundation.media_storage, "get_object", side_effect=self._get_object),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        for name, previous in (("MESSENGER_MEDIA_LOCAL_DIR", self._previous_dir), ("S3_BUCKET", self._previous_bucket)):
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
        self.conn.close()
        shutil.rmtree(self.storage, ignore_errors=True)

    def _head_object(self, storage_key):
        # Whatever the fake actually completed, so the post-complete size check is
        # answered by stored bytes rather than by the declared number it verifies.
        for record in reversed(self.client.completed):
            if record["key"] == storage_key:
                return {"ContentLength": self.completed_length}
        return {"ContentLength": 0}

    def _get_object(self, storage_key, byte_range=""):
        class _Body:
            def __init__(self, payload):
                self._payload = payload

            def read(self, size=None):
                return self._payload[: size or len(self._payload)]

        return {"Body": _Body(self.header_bytes)}

    # -- helpers ----------------------------------------------------------
    def _init(self, *, media_type="video", mime_type="video/mp4", filename="clip.mp4", size_bytes=64 * MB, duration_ms=None):
        payload = {
            "conversation_id": 44,
            "media_type": media_type,
            "mime_type": mime_type,
            "filename": filename,
            "size_bytes": size_bytes,
        }
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        result, status = foundation.init_upload(self.cur, self.conn, SENDER, payload)
        self.assertEqual(status, 201, result)
        return result

    def _row(self, attachment_id):
        self.cur.execute("SELECT * FROM message_attachments WHERE id=?", (attachment_id,))
        return self.cur.fetchone()

    def _provider_upload_id(self, attachment_id):
        return str(self._row(attachment_id)["upload_provider_id"] or "")

    def _fill(self, session, *, through=None, header=None):
        """Store the parts a real client would have PUT, in order."""
        attachment_id = session["attachment_id"]
        upload_id = self._provider_upload_id(attachment_id)
        part_size = session["part_size_bytes"]
        total = int(self._row(attachment_id)["size_bytes"])
        count = session["part_count"]
        last = count if through is None else through
        remaining = total
        for number in range(1, count + 1):
            chunk = min(part_size, remaining)
            remaining -= chunk
            if number > last:
                continue
            body = bytes(chunk)
            if number == 1:
                lead = header if header is not None else self.header_bytes
                body = lead + bytes(max(0, chunk - len(lead)))
            self.client.put_part(upload_id, number, body[:chunk])
        self.completed_length = self.client.stored_bytes(upload_id)
        return upload_id


class TheStrategyFollowsTheSize(ResumableHarness):
    """Small media keeps the cheap path; only big files pay for parts."""

    def test_a_small_photo_still_uploads_in_one_request(self):
        session = self._init(media_type="photo", mime_type="image/jpeg", filename="a.jpg", size_bytes=2 * MB)
        self.assertEqual(session["upload_method"], "direct")
        self.assertEqual(session["upload_url"], "/api/messages/media/upload")
        self.assertIsNone(self._row(session["attachment_id"])["upload_provider_id"])

    def test_a_file_at_the_threshold_opens_a_resumable_session(self):
        session = self._init(size_bytes=foundation.RESUMABLE_THRESHOLD_BYTES)
        self.assertEqual(session["upload_method"], "resumable")
        self.assertEqual(session["upload_url"], "/api/messages/media/upload/parts")
        self.assertTrue(self._provider_upload_id(session["attachment_id"]))

    def test_one_byte_under_the_threshold_stays_direct(self):
        session = self._init(size_bytes=foundation.RESUMABLE_THRESHOLD_BYTES - 1)
        self.assertEqual(session["upload_method"], "direct")

    def test_a_ninety_minute_video_is_split_into_many_parts(self):
        # 2 GB is what the 90-minute ceiling costs at a deliverable bitrate, and
        # it is the case the whole feature exists for.
        session = self._init(size_bytes=2048 * MB)
        self.assertEqual(session["upload_method"], "resumable")
        self.assertGreater(session["part_count"], 1)
        self.assertGreaterEqual(session["part_size_bytes"], 5 * MB)
        self.assertEqual(
            session["part_count"],
            -(-2048 * MB // session["part_size_bytes"]),
        )

    def test_the_part_grows_rather_than_exceeding_the_provider_cap(self):
        """S3 and R2 stop at 10,000 parts.

        Reached directly because `validate_media_request` refuses a file this
        large long before the session opens -- the arithmetic still has to be
        right, so that a future ceiling raise cannot silently mint an
        11,000-part upload the provider will reject at completion.
        """
        enormous = 400 * 1024 * MB
        session = foundation._open_resumable_session(self.cur, 1, "messenger/44/huge.mp4", "video/mp4", enormous)
        self.assertEqual(session["upload_method"], "resumable")
        self.assertLessEqual(session["part_count"], foundation.MAX_PARTS)
        self.assertGreaterEqual(session["part_size_bytes"] * session["part_count"], enormous)

    def test_no_object_storage_means_no_resumable_promise(self):
        """Local-disk development has no multipart API.

        Advertising a resumable session there would hand the client an endpoint
        that cannot work, which is worse than the request ceiling it replaces.
        """
        with mock.patch.object(foundation.media_storage, "object_client", return_value=None):
            session = self._init(size_bytes=512 * MB)
        self.assertEqual(session["upload_method"], "direct")

    def test_a_provider_failure_at_init_falls_back_instead_of_failing_the_send(self):
        with mock.patch.object(self.client, "create_multipart_upload", side_effect=RuntimeError("provider down")):
            session = self._init(size_bytes=512 * MB)
        self.assertEqual(session["upload_method"], "direct")


class TheResumePointComesFromTheProvider(ResumableHarness):
    """The feature, stated as its own test: a dropped transfer continues."""

    def test_a_dropped_transfer_reports_exactly_what_landed(self):
        session = self._init(size_bytes=64 * MB)
        self._fill(session, through=3)
        state, status = foundation.resumable_upload_state(
            self.cur, self.conn, SENDER, {"attachment_id": session["attachment_id"]}
        )
        self.assertEqual(status, 200)
        self.assertEqual(state["completed_parts"], [1, 2, 3])
        self.assertEqual(state["missing_parts"], list(range(4, session["part_count"] + 1)))
        self.assertEqual(state["bytes_stored"], 3 * session["part_size_bytes"])
        self.assertEqual(state["size_bytes"], 64 * MB)

    def test_a_client_cannot_claim_a_part_it_never_sent(self):
        """The resume point is the provider's account, not the caller's.

        Attacks `finish` rather than `state`, because that is where a trusted
        list does the damage: it would commit an object with a hole in it and
        mark the attachment uploaded. And it sweeps the plausible key spellings
        instead of pinning one -- the rule is that *no* supplied list is
        consulted, so a test naming a single field would guard that field rather
        than the rule. An earlier version asserted on the echoed `state` field
        and survived exactly this mutation.
        """
        for field in ("parts", "completed_parts", "uploaded_parts", "multipart"):
            with self.subTest(field=field):
                session = self._init(size_bytes=64 * MB)
                attachment_id = session["attachment_id"]
                upload_id = self._fill(session, through=2)
                claimed = [
                    {"part_number": n, "etag": f"etag-{n}", "size_bytes": session["part_size_bytes"]}
                    for n in range(1, session["part_count"] + 1)
                ]
                with self.assertRaises(MessengerMediaError) as caught:
                    foundation.finish_resumable_upload(
                        self.cur, self.conn, SENDER,
                        {"attachment_id": attachment_id, field: claimed,
                         "part_numbers": [p["part_number"] for p in claimed]},
                    )
                self.assertEqual(caught.exception.error, "upload_incomplete")
                self.assertEqual(self._row(attachment_id)["upload_status"], "pending")
                self.assertEqual(
                    [record for record in self.client.completed if record["upload_id"] == upload_id], []
                )

    def test_the_resume_point_ignores_a_claimed_completed_parts_list(self):
        session = self._init(size_bytes=64 * MB)
        self._fill(session, through=2)
        state, _ = foundation.resumable_upload_state(
            self.cur, self.conn, SENDER,
            {"attachment_id": session["attachment_id"], "completed_parts": [1, 2, 3, 4, 5, 6, 7, 8]},
        )
        self.assertEqual(state["completed_parts"], [1, 2])

    def test_asking_where_to_resume_extends_the_session(self):
        session = self._init(size_bytes=64 * MB)
        attachment_id = session["attachment_id"]
        self.cur.execute(
            "UPDATE message_attachments SET upload_expires_at='2000-01-01T00:00:00Z' WHERE id=?", (attachment_id,)
        )
        self.conn.commit()
        state, status = foundation.resumable_upload_state(self.cur, self.conn, SENDER, {"attachment_id": attachment_id})
        self.assertEqual(status, 200)
        self.assertGreater(state["session_expires_at"], foundation.now_iso())
        # Re-authorized, so the parts already stored stay useful.
        signed, sign_status = foundation.sign_upload_parts(
            self.cur, self.conn, SENDER, {"attachment_id": attachment_id, "part_numbers": [4]}
        )
        self.assertEqual(sign_status, 200)
        self.assertEqual([p["part_number"] for p in signed["parts"]], [4])

    def test_a_truncated_listing_is_paged_not_dropped(self):
        """`list_parts` caps at 1,000 per call; a 2 GB upload can exceed that.

        A single unpaged call would report the first page as the whole truth and
        then refuse the finish as incomplete, forever.
        """
        session = self._init(size_bytes=2048 * MB)
        upload_id = self._provider_upload_id(session["attachment_id"])
        for number in range(1, session["part_count"] + 1):
            self.client.put_part(upload_id, number, bytes(16))
        with mock.patch.object(self.client, "list_parts", wraps=self.client.list_parts) as listed:
            parts = foundation._provider_parts(self._row(session["attachment_id"]), upload_id)
        self.assertEqual(len(parts), session["part_count"])
        self.assertEqual([p["part_number"] for p in parts], sorted(p["part_number"] for p in parts))
        self.assertGreaterEqual(listed.call_count, 1)


class SigningIsScopedAndBatched(ResumableHarness):
    def test_part_urls_are_issued_per_part(self):
        session = self._init(size_bytes=64 * MB)
        signed, status = foundation.sign_upload_parts(
            self.cur, self.conn, SENDER, {"attachment_id": session["attachment_id"], "part_numbers": [1, 2, 3]}
        )
        self.assertEqual(status, 200)
        self.assertEqual([p["part_number"] for p in signed["parts"]], [1, 2, 3])
        self.assertIn("partNumber=2", signed["parts"][1]["upload_url"])

    def test_a_batch_larger_than_the_cap_is_trimmed_not_refused(self):
        """URLs expire, so signing the whole file up front hands the tail dead
        authorization. Trimming keeps the client moving; refusing would not."""
        session = self._init(size_bytes=2048 * MB)
        signed, _ = foundation.sign_upload_parts(
            self.cur, self.conn, SENDER,
            {"attachment_id": session["attachment_id"], "part_numbers": list(range(1, 60))},
        )
        self.assertEqual(len(signed["parts"]), foundation.MAX_PARTS_PER_SIGN)

    def test_a_part_beyond_the_declared_file_is_not_signed(self):
        session = self._init(size_bytes=64 * MB)
        beyond = session["part_count"] + 5
        with self.assertRaises(MessengerMediaError) as caught:
            foundation.sign_upload_parts(
                self.cur, self.conn, SENDER, {"attachment_id": session["attachment_id"], "part_numbers": [beyond]}
            )
        self.assertEqual(caught.exception.error, "invalid_part_numbers")

    def test_junk_part_numbers_are_refused_rather_than_coerced(self):
        session = self._init(size_bytes=64 * MB)
        for requested in ([], ["", None], [0], [-1], ["abc"]):
            with self.assertRaises(MessengerMediaError, msg=str(requested)) as caught:
                foundation.sign_upload_parts(
                    self.cur, self.conn, SENDER,
                    {"attachment_id": session["attachment_id"], "part_numbers": requested},
                )
            self.assertEqual(caught.exception.error, "invalid_part_numbers", str(requested))

    def test_an_expired_session_refuses_new_authorization(self):
        session = self._init(size_bytes=64 * MB)
        self.cur.execute(
            "UPDATE message_attachments SET upload_expires_at='2000-01-01T00:00:00Z' WHERE id=?",
            (session["attachment_id"],),
        )
        self.conn.commit()
        with self.assertRaises(MessengerMediaError) as caught:
            foundation.sign_upload_parts(
                self.cur, self.conn, SENDER, {"attachment_id": session["attachment_id"], "part_numbers": [1]}
            )
        self.assertEqual(caught.exception.error, "upload_session_expired")
        self.assertEqual(caught.exception.status_code, 410)

    def test_a_fresh_session_is_not_born_expired(self):
        """`now_iso` carries a trailing Z and expiry is compared as a string.

        A mismatched format would sort the future before the present, expiring
        every session the moment it opened.
        """
        session = self._init(size_bytes=64 * MB)
        expires_at = str(self._row(session["attachment_id"])["upload_expires_at"])
        self.assertTrue(expires_at.endswith("Z"), expires_at)
        self.assertGreater(expires_at, foundation.now_iso())


class OnlyTheSenderCanWriteToTheUpload(ResumableHarness):
    """A member who can read the thread must not be able to write someone
    else's upload -- signing a part is a write to private storage."""

    def test_another_member_of_the_conversation_cannot_sign_parts(self):
        session = self._init(size_bytes=64 * MB)
        with self.assertRaises(MessengerMediaError) as caught:
            foundation.sign_upload_parts(
                self.cur, self.conn, OTHER_MEMBER, {"attachment_id": session["attachment_id"], "part_numbers": [1]}
            )
        self.assertEqual(caught.exception.error, "not_attachment_owner")
        self.assertEqual(caught.exception.status_code, 403)

    def test_a_stranger_cannot_read_the_resume_state(self):
        session = self._init(size_bytes=64 * MB)
        with self.assertRaises(MessengerMediaError) as caught:
            foundation.resumable_upload_state(
                self.cur, self.conn, STRANGER, {"attachment_id": session["attachment_id"]}
            )
        self.assertNotEqual(caught.exception.status_code, 200)

    def test_another_member_cannot_finish_or_abort_it(self):
        session = self._init(size_bytes=64 * MB)
        self._fill(session)
        for operation in (foundation.finish_resumable_upload, foundation.abort_resumable_upload):
            with self.assertRaises(MessengerMediaError, msg=operation.__name__) as caught:
                operation(self.cur, self.conn, OTHER_MEMBER, {"attachment_id": session["attachment_id"]})
            self.assertEqual(caught.exception.error, "not_attachment_owner", operation.__name__)
        self.assertEqual(self.client.completed, [])
        self.assertEqual(self.client.aborted, [])

    def test_an_attachment_with_no_session_cannot_be_finished_through_this_path(self):
        session = self._init(media_type="photo", mime_type="image/jpeg", filename="a.jpg", size_bytes=2 * MB)
        with self.assertRaises(MessengerMediaError) as caught:
            foundation.finish_resumable_upload(self.cur, self.conn, SENDER, {"attachment_id": session["attachment_id"]})
        self.assertEqual(caught.exception.error, "upload_not_resumable")

    def test_a_missing_attachment_id_is_refused(self):
        with self.assertRaises(MessengerMediaError) as caught:
            foundation.sign_upload_parts(self.cur, self.conn, SENDER, {"part_numbers": [1]})
        self.assertEqual(caught.exception.error, "attachment_required")


class FinishingVerifiesBeforeItCommits(ResumableHarness):
    def test_a_complete_upload_is_stitched_and_queued_for_processing(self):
        session = self._init(size_bytes=64 * MB)
        attachment_id = session["attachment_id"]
        self._fill(session)
        result, status = foundation.finish_resumable_upload(self.cur, self.conn, SENDER, {"attachment_id": attachment_id})
        self.assertEqual(status, 200)
        row = self._row(attachment_id)
        self.assertEqual(row["upload_status"], "uploaded")
        self.assertEqual(row["processing_status"], "queued")
        self.assertEqual(len(self.client.completed), 1)
        self.assertEqual(len(self.client.completed[0]["parts"]), session["part_count"])
        self.cur.execute(
            "SELECT job_type FROM pulse_jobs WHERE target_type='message_attachment' AND target_id=?", (attachment_id,)
        )
        self.assertIn("messenger_video_metadata_thumbnail", [r["job_type"] for r in self.cur.fetchall()])

    def test_the_session_state_is_cleared_so_it_cannot_be_replayed(self):
        session = self._init(size_bytes=64 * MB)
        attachment_id = session["attachment_id"]
        self._fill(session)
        foundation.finish_resumable_upload(self.cur, self.conn, SENDER, {"attachment_id": attachment_id})
        self.assertIsNone(self._row(attachment_id)["upload_provider_id"])
        with self.assertRaises(MessengerMediaError) as caught:
            foundation.finish_resumable_upload(self.cur, self.conn, SENDER, {"attachment_id": attachment_id})
        self.assertEqual(caught.exception.error, "upload_not_resumable")
        self.assertEqual(len(self.client.completed), 1)

    def test_finishing_early_is_refused_before_a_truncated_object_exists(self):
        """The check runs *before* complete_multipart_upload.

        Completing first and detecting the short size afterwards leaves a valid
        object at the key that is not the file anyone sent.
        """
        session = self._init(size_bytes=64 * MB)
        self._fill(session, through=2)
        with self.assertRaises(MessengerMediaError) as caught:
            foundation.finish_resumable_upload(
                self.cur, self.conn, SENDER, {"attachment_id": session["attachment_id"]}
            )
        self.assertEqual(caught.exception.error, "upload_incomplete")
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.client.completed, [])
        self.assertEqual(self._row(session["attachment_id"])["upload_status"], "pending")

    def test_finishing_with_nothing_uploaded_says_so(self):
        session = self._init(size_bytes=64 * MB)
        self.completed_length = 0
        with self.assertRaises(MessengerMediaError) as caught:
            foundation.finish_resumable_upload(
                self.cur, self.conn, SENDER, {"attachment_id": session["attachment_id"]}
            )
        self.assertEqual(caught.exception.error, "no_parts_uploaded")
        self.assertEqual(self.client.completed, [])

    def test_a_stored_object_that_disagrees_with_the_declared_size_is_refused(self):
        session = self._init(size_bytes=64 * MB)
        self._fill(session)
        self.completed_length = 5 * MB
        with self.assertRaises(MessengerMediaError) as caught:
            foundation.finish_resumable_upload(
                self.cur, self.conn, SENDER, {"attachment_id": session["attachment_id"]}
            )
        self.assertEqual(caught.exception.error, "size_mismatch")

    def test_an_executable_wearing_a_video_name_is_refused_on_the_bytes(self):
        """Bytes that never passed through Flask still have to be what they claim.

        This is the same rule the direct path applies, read from object storage
        instead of a spooled temp file -- otherwise the resumable path would be
        the way around it.
        """
        session = self._init(size_bytes=64 * MB)
        self.header_bytes = EXE_HEADER
        self._fill(session, header=EXE_HEADER)
        with self.assertRaises(MessengerMediaError) as caught:
            foundation.finish_resumable_upload(
                self.cur, self.conn, SENDER, {"attachment_id": session["attachment_id"]}
            )
        self.assertEqual(caught.exception.error, "unsafe_file_contents")
        self.assertEqual(caught.exception.status_code, 415)
        self.assertEqual(self._row(session["attachment_id"])["upload_status"], "pending")

    def test_a_video_whose_bytes_are_a_photo_is_refused(self):
        """The mismatch has to be one the sniffer can actually see.

        An mp4 header answers "" -- `ftyp` at offset 4 does not separate audio
        from video -- so declaring video and sending mp4 proves nothing. A JPEG
        header under a video declaration is evidence, and this is the direction
        that matters: bytes that bypassed Flask still get checked.
        """
        session = self._init(size_bytes=64 * MB)
        self.header_bytes = JPEG_HEADER
        self._fill(session, header=JPEG_HEADER)
        with self.assertRaises(MessengerMediaError) as caught:
            foundation.finish_resumable_upload(
                self.cur, self.conn, SENDER, {"attachment_id": session["attachment_id"]}
            )
        self.assertEqual(caught.exception.error, "file_contents_mismatch")
        self.assertEqual(caught.exception.status_code, 415)
        self.assertEqual(self._row(session["attachment_id"])["upload_status"], "pending")

    def test_a_photo_is_too_small_to_ever_take_this_path(self):
        """Guards a silent strategy change, not current behaviour for its own sake.

        The photo ceiling sits below the multipart threshold, so every photo goes
        direct. Raising MESSENGER_PHOTO_MAX_MB past the threshold would quietly
        route photos through parts -- which may be fine, but it should be a
        decision somebody made rather than a side effect of a size bump.
        """
        self.assertLess(foundation.max_size_for("photo"), foundation.RESUMABLE_THRESHOLD_BYTES)
        with self.assertRaises(MessengerMediaError) as caught:
            self._init(media_type="photo", mime_type="image/jpeg", filename="a.jpg", size_bytes=64 * MB)
        self.assertEqual(caught.exception.error, "file_too_large")

    def test_unreadable_header_bytes_do_not_masquerade_as_a_bad_file(self):
        """A storage hiccup is not a malicious upload.

        The direct path makes the same call, and treating "could not read" as
        "refuse" would fail correct uploads during a provider blip.
        """
        session = self._init(size_bytes=64 * MB)
        self._fill(session)
        with mock.patch.object(foundation.media_storage, "get_object", side_effect=RuntimeError("range read failed")):
            _, status = foundation.finish_resumable_upload(
                self.cur, self.conn, SENDER, {"attachment_id": session["attachment_id"]}
            )
        self.assertEqual(status, 200)

    def test_the_duration_ceiling_still_applies_on_this_path(self):
        """90 minutes is the limit whichever way the bytes arrived.

        A resumable upload is exactly the shape of transfer that would carry an
        over-long video, so this path must not be the one without the ceiling.
        """
        session = self._init(size_bytes=64 * MB)
        self._fill(session)
        with self.assertRaises(MessengerMediaError) as caught:
            foundation.finish_resumable_upload(
                self.cur, self.conn, SENDER,
                {"attachment_id": session["attachment_id"], "duration_ms": (90 * 60 + 1) * 1000},
            )
        self.assertEqual(caught.exception.status_code, 413)
        self.assertEqual(self._row(session["attachment_id"])["upload_status"], "pending")

    def test_exactly_ninety_minutes_finishes(self):
        session = self._init(size_bytes=64 * MB)
        self._fill(session)
        _, status = foundation.finish_resumable_upload(
            self.cur, self.conn, SENDER,
            {"attachment_id": session["attachment_id"], "duration_ms": 90 * 60 * 1000},
        )
        self.assertEqual(status, 200)
        self.assertEqual(int(self._row(session["attachment_id"])["duration_ms"]), 90 * 60 * 1000)


class AbandonedUploadsAreDiscarded(ResumableHarness):
    def test_aborting_tells_the_provider_to_drop_the_parts(self):
        session = self._init(size_bytes=64 * MB)
        upload_id = self._fill(session, through=2)
        result, status = foundation.abort_resumable_upload(
            self.cur, self.conn, SENDER, {"attachment_id": session["attachment_id"]}
        )
        self.assertEqual(status, 200)
        self.assertIn(upload_id, self.client.aborted)
        row = self._row(session["attachment_id"])
        self.assertEqual(row["upload_status"], "failed")
        self.assertIsNone(row["upload_provider_id"])

    def test_an_abort_the_provider_refuses_still_closes_the_session_locally(self):
        """Otherwise a provider blip leaves a row advertising a session that is
        gone, and the client can neither resume nor start again."""
        session = self._init(size_bytes=64 * MB)
        self._fill(session, through=1)
        with mock.patch.object(self.client, "abort_multipart_upload", side_effect=RuntimeError("provider down")):
            _, status = foundation.abort_resumable_upload(
                self.cur, self.conn, SENDER, {"attachment_id": session["attachment_id"]}
            )
        self.assertEqual(status, 200)
        self.assertIsNone(self._row(session["attachment_id"])["upload_provider_id"])

    def test_an_expired_session_can_still_be_aborted(self):
        session = self._init(size_bytes=64 * MB)
        self.cur.execute(
            "UPDATE message_attachments SET upload_expires_at='2000-01-01T00:00:00Z' WHERE id=?",
            (session["attachment_id"],),
        )
        self.conn.commit()
        _, status = foundation.abort_resumable_upload(
            self.cur, self.conn, SENDER, {"attachment_id": session["attachment_id"]}
        )
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
