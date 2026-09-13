"""The ceiling enforced against a measurement instead of against a claim.

Every duration check that existed before this suite tested `duration_ms` sent by
the client. That number buys a signed URL, so a caller talking to the API
directly simply chooses it -- the 90-minute rule was advisory on every stored
surface, and a 4-hour video inside the byte ceiling was accepted, stored and
served. The server already *had* the real number in two places and threw both
away: the Mux webhook parsed `data.duration` and spent it only on live-replay
rows, and the Messenger worker ran ffprobe and wrote `duration_ms` down without
ever comparing it to the limit.

So these tests are about the measurement outranking the claim. They assert the
state of the row *after* enforcement rather than the dict handed back, because a
function that returns `{"blocked": [7]}` while writing nothing is exactly the
shape of bug this replaces.

Three seams, one rule: the pure verdict in `stored_video_policy`, the shared
DB-applying enforcement in `media_service` that both the webhook and the
reconciler call, and the Messenger worker's ffprobe pass.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "")

from services import media_service, stored_video_policy  # noqa: E402
from services import messenger_media_foundation as foundation  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))

NINETY_MINUTES = 5400
POST_SURFACE = "pulse"           # what every feed composer actually sends
MARKETPLACE_SURFACE = "marketplace_product"


# --------------------------------------------------------------------------
# The verdict
# --------------------------------------------------------------------------


class TestTheMeasuredVerdictSitsOnTheSameBoundary:
    """A measurement is judged at exactly the boundary the upload check uses."""

    def test_exactly_ninety_minutes_is_not_a_violation(self):
        assert stored_video_policy.measured_violation(POST_SURFACE, NINETY_MINUTES) == ""

    def test_one_second_under_is_not_a_violation(self):
        assert stored_video_policy.measured_violation(POST_SURFACE, 89 * 60 + 59) == ""

    def test_one_second_over_is_a_violation(self):
        assert stored_video_policy.measured_violation(POST_SURFACE, NINETY_MINUTES + 1) != ""

    def test_the_reason_names_both_the_measurement_and_the_limit(self):
        # The reason is written into a column a human reads when an owner asks why
        # their video vanished. "Too long" without the two numbers means the next
        # person has to re-download the file to find out what happened.
        reason = stored_video_policy.measured_violation(POST_SURFACE, 5401)
        assert "5401" in reason and "5400" in reason

    @pytest.mark.parametrize("absent", [None, 0, -1, "", "not-a-number"])
    def test_an_absent_measurement_is_not_a_violation(self, absent):
        # Nothing has measured yet. Convicting here would block every video in the
        # window between finalize and the first probe.
        assert stored_video_policy.measured_violation(POST_SURFACE, absent) == ""

    def test_a_fractional_overshoot_inside_the_same_second_is_not_a_violation(self):
        assert stored_video_policy.measured_violation(POST_SURFACE, 5400.37) == ""


class TestAnUnregisteredSurfaceIsNeverConvicted:
    """The strictest-cap fallback is right for an upload and wrong for a takedown.

    `max_duration_seconds` answers an unknown surface with the *strictest* cap on
    purpose -- a typo must not buy the longest limit in the table. That is correct
    while the question is "may this upload start", because the caller still holds
    its bytes and gets an error it can read.

    It inverts once the question is "take this stored video away from its owner".
    `context_type` is free-form data written by many call sites, so an unregistered
    name would convict a perfectly valid 5-minute video against a 60-second cap
    nobody chose for that surface, silently and after the fact.
    """

    # Live values that reach chat_media_uploads today and are absent from the
    # policy table. Asserted rather than assumed, so registering one of them
    # turns this into a visible decision instead of a quiet behaviour change.
    UNREGISTERED_IN_USE = ["asset_focus", "native", "pulse_comment", "pulse_music"]

    @pytest.mark.parametrize("surface", UNREGISTERED_IN_USE)
    def test_these_surfaces_really_are_unregistered(self, surface):
        assert not stored_video_policy.is_known_surface(surface)

    @pytest.mark.parametrize("surface", UNREGISTERED_IN_USE)
    def test_an_unregistered_surface_is_not_convicted_however_long(self, surface):
        assert stored_video_policy.measured_violation(surface, 4 * 60 * 60) == ""

    def test_a_registered_short_surface_is_still_convicted(self):
        # The escape above must not become a blanket amnesty: Status is 60s by a
        # product decision, and that decision still has to bite.
        assert stored_video_policy.measured_violation("status", 61) != ""

    def test_the_messenger_surface_is_registered(self):
        # Now that an unregistered surface means "do not enforce", Messenger's
        # enforcement depends on this name being in the table. If it were ever
        # dropped, the worker would go quiet rather than fail.
        assert stored_video_policy.is_known_surface(foundation.MESSENGER_VIDEO_SURFACE)


class TestTheMillisecondDoorAgreesWithTheSecondsDoor:
    def test_the_two_spellings_reach_the_same_verdict(self):
        # A factor of 1000 between these is the mistake that fails *open*: 5401
        # read as milliseconds is 5.4 seconds, which passes everything.
        assert stored_video_policy.measured_violation_ms(POST_SURFACE, 5401 * 1000) == \
            stored_video_policy.measured_violation(POST_SURFACE, 5401)

    def test_ninety_minutes_in_milliseconds_is_accepted(self):
        assert stored_video_policy.measured_violation_ms(POST_SURFACE, NINETY_MINUTES * 1000) == ""

    def test_one_second_over_in_milliseconds_is_refused(self):
        assert stored_video_policy.measured_violation_ms(POST_SURFACE, (NINETY_MINUTES + 1) * 1000) != ""


# --------------------------------------------------------------------------
# The shared enforcement both Mux paths call
# --------------------------------------------------------------------------


@pytest.fixture()
def uploads_db():
    """A chat_media_uploads table carrying only the columns enforcement touches."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE chat_media_uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uploader_user_id INTEGER,
            context_type TEXT,
            media_type TEXT,
            duration_seconds REAL,
            moderation_status TEXT DEFAULT 'approved',
            moderation_reason TEXT,
            mux_asset_id TEXT,
            is_available INTEGER DEFAULT 1,
            processing_status TEXT DEFAULT 'ready',
            error_message TEXT,
            created_at TEXT,
            updated_at TEXT,
            deleted_at TEXT
        )
        """
    )
    conn.commit()
    yield conn
    conn.close()


def _insert_upload(conn, *, context_type=POST_SURFACE, asset_id="mux-asset-1",
                   declared_seconds=None, created_at="2026-09-10T00:00:00"):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO chat_media_uploads
            (uploader_user_id, context_type, media_type, duration_seconds,
             moderation_status, mux_asset_id, is_available, processing_status, created_at)
        VALUES (7, ?, 'video', ?, 'approved', ?, 1, 'ready', ?)
        """,
        (context_type, declared_seconds, asset_id, created_at),
    )
    conn.commit()
    return int(cur.lastrowid)


def _row(conn, media_id):
    cur = conn.cursor()
    cur.execute("SELECT * FROM chat_media_uploads WHERE id=?", (media_id,))
    return cur.fetchone()


class TestTheMeasurementOutranksTheClaim:
    def test_a_lying_client_does_not_help_itself(self, uploads_db):
        # The whole defect in one test: the row was created declaring 60 seconds,
        # which is what got it past the upload check. Mux measures 5401.
        media_id = _insert_upload(uploads_db, declared_seconds=60)
        outcome = media_service.enforce_measured_video_duration(
            uploads_db.cursor(), asset_id="mux-asset-1", duration_seconds=5401)
        uploads_db.commit()

        assert outcome["blocked"] == [media_id]
        row = _row(uploads_db, media_id)
        assert row["moderation_status"] == "blocked"
        assert row["is_available"] == 0
        assert row["processing_status"] == "rejected_too_long"
        # The declared 60 is replaced by the measured 5401, so the row no longer
        # carries the number the uploader chose.
        assert int(row["duration_seconds"]) == 5401
        assert "5401" in (row["moderation_reason"] or "")
        assert row["error_message"]

    def test_a_video_within_the_limit_keeps_its_row_intact(self, uploads_db):
        media_id = _insert_upload(uploads_db)
        outcome = media_service.enforce_measured_video_duration(
            uploads_db.cursor(), asset_id="mux-asset-1", duration_seconds=NINETY_MINUTES)
        uploads_db.commit()

        assert outcome["blocked"] == []
        row = _row(uploads_db, media_id)
        assert row["moderation_status"] == "approved"
        assert row["is_available"] == 1
        assert row["processing_status"] == "ready"
        # Measured and recorded even though it passed: the next reconciler pass
        # must not treat this row as still unmeasured.
        assert int(row["duration_seconds"]) == NINETY_MINUTES

    def test_the_boundary_second_decides_the_takedown(self, uploads_db):
        accepted = _insert_upload(uploads_db, asset_id="asset-accept")
        refused = _insert_upload(uploads_db, asset_id="asset-refuse")

        media_service.enforce_measured_video_duration(
            uploads_db.cursor(), asset_id="asset-accept", duration_seconds=NINETY_MINUTES)
        media_service.enforce_measured_video_duration(
            uploads_db.cursor(), asset_id="asset-refuse", duration_seconds=NINETY_MINUTES + 1)
        uploads_db.commit()

        assert _row(uploads_db, accepted)["moderation_status"] == "approved"
        assert _row(uploads_db, refused)["moderation_status"] == "blocked"

    def test_an_absent_measurement_leaves_the_row_untouched(self, uploads_db):
        # The window between finalize and the first probe. Mux sends asset.ready
        # events without a duration; treating that as 0 seconds over the limit
        # would block every video on the platform.
        media_id = _insert_upload(uploads_db)
        outcome = media_service.enforce_measured_video_duration(
            uploads_db.cursor(), asset_id="mux-asset-1", duration_seconds=0)
        uploads_db.commit()

        assert outcome["blocked"] == []
        row = _row(uploads_db, media_id)
        assert row["duration_seconds"] is None
        assert row["moderation_status"] == "approved"
        assert row["is_available"] == 1

    def test_the_ceiling_is_read_per_row_not_once_for_the_platform(self, uploads_db):
        # 601 seconds is fine for a feed post and over the limit for a marketplace
        # listing. A single global constant -- or a verdict reached inside the SQL,
        # which is where this would be tempting -- cannot produce both answers.
        post_id = _insert_upload(uploads_db, context_type=POST_SURFACE, asset_id="asset-post")
        listing_id = _insert_upload(uploads_db, context_type=MARKETPLACE_SURFACE, asset_id="asset-listing")

        media_service.enforce_measured_video_duration(
            uploads_db.cursor(), asset_id="asset-post", duration_seconds=601)
        media_service.enforce_measured_video_duration(
            uploads_db.cursor(), asset_id="asset-listing", duration_seconds=601)
        uploads_db.commit()

        assert _row(uploads_db, post_id)["moderation_status"] == "approved"
        assert _row(uploads_db, listing_id)["moderation_status"] == "blocked"

    def test_a_row_on_an_unregistered_surface_is_measured_but_not_taken_down(self, uploads_db):
        media_id = _insert_upload(uploads_db, context_type="asset_focus")
        outcome = media_service.enforce_measured_video_duration(
            uploads_db.cursor(), asset_id="mux-asset-1", duration_seconds=9999)
        uploads_db.commit()

        assert outcome["blocked"] == []
        row = _row(uploads_db, media_id)
        assert row["moderation_status"] == "approved"
        # Still measured. The number is the evidence for registering the surface.
        assert int(row["duration_seconds"]) == 9999

    def test_only_the_named_asset_is_touched(self, uploads_db):
        target = _insert_upload(uploads_db, asset_id="asset-target")
        bystander = _insert_upload(uploads_db, asset_id="asset-bystander")

        media_service.enforce_measured_video_duration(
            uploads_db.cursor(), asset_id="asset-target", duration_seconds=5401)
        uploads_db.commit()

        assert _row(uploads_db, target)["moderation_status"] == "blocked"
        bystander_row = _row(uploads_db, bystander)
        assert bystander_row["moderation_status"] == "approved"
        assert bystander_row["is_available"] == 1
        assert bystander_row["duration_seconds"] is None

    def test_it_can_be_addressed_by_media_id_for_the_reconciler(self, uploads_db):
        # The worker polls by row, not by webhook payload, so both doors have to
        # reach the same enforcement.
        media_id = _insert_upload(uploads_db, asset_id="asset-poll")
        outcome = media_service.enforce_measured_video_duration(
            uploads_db.cursor(), media_id=media_id, duration_seconds=5401)
        uploads_db.commit()

        assert outcome["blocked"] == [media_id]
        assert _row(uploads_db, media_id)["moderation_status"] == "blocked"

    def test_a_blank_reference_does_nothing(self, uploads_db):
        media_id = _insert_upload(uploads_db)
        outcome = media_service.enforce_measured_video_duration(
            uploads_db.cursor(), asset_id="", media_id=0, duration_seconds=5401)
        uploads_db.commit()

        assert outcome["checked"] == 0
        assert _row(uploads_db, media_id)["moderation_status"] == "approved"


class TestRedeliveryCannotResurrectABlockedAsset:
    """Mux redelivers `video.asset.ready`, and that handler sets is_available=1.

    The enforcement call sits after that UPDATE precisely so the measurement gets
    the last word. If the two were reordered -- or if enforcement wrote the block
    only once and skipped an already-blocked row -- a retried webhook delivery
    would quietly put an over-long video back in the feed.
    """

    def test_the_block_is_reasserted_on_every_delivery(self, uploads_db):
        media_id = _insert_upload(uploads_db)
        cur = uploads_db.cursor()

        media_service.enforce_measured_video_duration(
            cur, asset_id="mux-asset-1", duration_seconds=5401)
        uploads_db.commit()
        assert _row(uploads_db, media_id)["is_available"] == 0

        # Exactly what the ready-handler ahead of the enforcement call does on a
        # redelivery: restore availability for a ready asset.
        cur.execute(
            "UPDATE chat_media_uploads SET is_available=1, processing_status='ready' WHERE mux_asset_id=?",
            ("mux-asset-1",),
        )
        outcome = media_service.enforce_measured_video_duration(
            cur, asset_id="mux-asset-1", duration_seconds=5401)
        uploads_db.commit()

        assert outcome["blocked"] == [media_id]
        row = _row(uploads_db, media_id)
        assert row["is_available"] == 0
        assert row["moderation_status"] == "blocked"
        assert row["processing_status"] == "rejected_too_long"


# --------------------------------------------------------------------------
# The Messenger worker's ffprobe pass
# --------------------------------------------------------------------------


@pytest.fixture()
def messenger_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    foundation.ensure_schema(cur)
    conn.commit()
    storage = Path(tempfile.mkdtemp(prefix="measured-duration-"))
    previous = os.environ.get("MESSENGER_MEDIA_LOCAL_DIR")
    os.environ["MESSENGER_MEDIA_LOCAL_DIR"] = str(storage)
    yield conn, cur, storage
    if previous is None:
        os.environ.pop("MESSENGER_MEDIA_LOCAL_DIR", None)
    else:
        os.environ["MESSENGER_MEDIA_LOCAL_DIR"] = previous
    conn.close()
    shutil.rmtree(storage, ignore_errors=True)


def _attachment(cur, *, storage_key="messenger/44/clip.mp4", upload_status="uploaded"):
    cur.execute(
        """
        INSERT INTO message_attachments
        (conversation_id, conversation_model, sender_id, media_type, mime_type,
         original_filename, storage_key, signed_url_strategy, upload_status,
         processing_status, created_at, updated_at)
        VALUES (44, 'pulse', 7, 'video', 'video/mp4', ?, ?, 'private', ?, 'queued', ?, ?)
        """,
        (Path(storage_key).name, storage_key, upload_status,
         foundation.now_iso(), foundation.now_iso()),
    )
    return int(cur.lastrowid)


def _attachment_row(cur, attachment_id):
    cur.execute("SELECT * FROM message_attachments WHERE id=?", (attachment_id,))
    return cur.fetchone()


def _measured(monkeypatch, duration_ms):
    """Let the real pipeline run but substitute the measurement.

    Encoding a genuine 90-minute video per test is not affordable, and the rule
    under test is not about ffmpeg. `_derive_video_assets` is replaced rather than
    `process_attachment` so the enforcement branch, the DB write and the returned
    contract are all the real code.
    """
    monkeypatch.setattr(
        foundation, "_derive_video_assets",
        lambda cur, row, path: {"status": "processed",
                                "updates": {"duration_ms": duration_ms, "width": 640, "height": 360}},
    )


def _place_bytes(storage, storage_key):
    target = foundation._local_path(storage_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 256)
    return target


JOB = "messenger_video_metadata_thumbnail"


class TestTheProbeMeasurementIsEnforcedNotMerelyRecorded:
    def test_an_over_long_video_is_blocked_after_measurement(self, messenger_db, monkeypatch):
        conn, cur, storage = messenger_db
        attachment_id = _attachment(cur)
        _place_bytes(storage, "messenger/44/clip.mp4")
        _measured(monkeypatch, (NINETY_MINUTES + 1) * 1000)

        result = foundation.process_attachment(cur, attachment_id, JOB)
        conn.commit()

        assert result["status"] == "rejected"
        assert result["reason"] == stored_video_policy.MEASURED_REJECTION_CODE
        row = _attachment_row(cur, attachment_id)
        assert row["upload_status"] == "blocked"
        assert row["processing_status"] == "rejected_too_long"
        assert row["error_code"] == stored_video_policy.MEASURED_REJECTION_CODE
        # The measurement is stored with the refusal, so nobody has to re-download
        # the file to find out what was wrong with it.
        assert row["duration_ms"] == (NINETY_MINUTES + 1) * 1000
        assert row["error_message"]

    def test_a_video_at_the_limit_is_processed_normally(self, messenger_db, monkeypatch):
        conn, cur, storage = messenger_db
        attachment_id = _attachment(cur)
        _place_bytes(storage, "messenger/44/clip.mp4")
        _measured(monkeypatch, NINETY_MINUTES * 1000)

        result = foundation.process_attachment(cur, attachment_id, JOB)
        conn.commit()

        assert result["status"] == "processed"
        row = _attachment_row(cur, attachment_id)
        assert row["upload_status"] == "uploaded"
        assert row["processing_status"] == "ready"
        assert row["duration_ms"] == NINETY_MINUTES * 1000

    def test_a_blocked_attachment_is_settled_not_deferred(self, messenger_db, monkeypatch):
        # `deferred` reschedules without spending the error budget, so returning it
        # here would retry a permanently-blocked row every two minutes forever.
        conn, cur, storage = messenger_db
        attachment_id = _attachment(cur)
        _place_bytes(storage, "messenger/44/clip.mp4")
        _measured(monkeypatch, (NINETY_MINUTES + 1) * 1000)

        foundation.process_attachment(cur, attachment_id, JOB)
        conn.commit()
        again = foundation.process_attachment(cur, attachment_id, JOB)

        assert again["status"] == "skipped"
        assert again["reason"] == "blocked"

    def test_a_blocked_attachment_is_refused_to_every_reader(self, messenger_db, monkeypatch):
        # Gating the download route alone would leave the thumbnail and the
        # metadata still serving a video that was taken down.
        conn, cur, storage = messenger_db
        attachment_id = _attachment(cur)
        _place_bytes(storage, "messenger/44/clip.mp4")
        _measured(monkeypatch, (NINETY_MINUTES + 1) * 1000)
        foundation.process_attachment(cur, attachment_id, JOB)
        conn.commit()

        row = _attachment_row(cur, attachment_id)
        with pytest.raises(foundation.MessengerMediaError) as caught:
            foundation._require_attachment_access(cur, row, 7)

        assert caught.value.error == "attachment_blocked"
        assert caught.value.status_code == 410

    def test_a_photo_is_not_judged_against_the_video_ceiling(self, messenger_db, monkeypatch):
        # `duration_ms` on a non-video row must not reach the video rule.
        conn, cur, storage = messenger_db
        cur.execute(
            """
            INSERT INTO message_attachments
            (conversation_id, conversation_model, sender_id, media_type, mime_type,
             original_filename, storage_key, signed_url_strategy, upload_status,
             processing_status, created_at, updated_at)
            VALUES (44, 'pulse', 7, 'photo', 'image/jpeg', 'p.jpg', 'messenger/44/p.jpg',
                    'private', 'uploaded', 'queued', ?, ?)
            """,
            (foundation.now_iso(), foundation.now_iso()),
        )
        attachment_id = int(cur.lastrowid)
        _place_bytes(storage, "messenger/44/p.jpg")
        monkeypatch.setattr(
            foundation, "_derive_photo_assets",
            lambda cur, row, path: {"status": "processed",
                                    "updates": {"duration_ms": (NINETY_MINUTES + 1) * 1000}},
        )

        result = foundation.process_attachment(cur, attachment_id, "messenger_photo_thumbnail")
        conn.commit()

        assert result["status"] == "processed"
        assert _attachment_row(cur, attachment_id)["upload_status"] == "uploaded"


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg and ffprobe are required to generate a fixture")
class TestTheEnforcementReadsTheKeyTheProbeActuallyWrites:
    """The real deriver runs; only the number ffprobe reports is substituted.

    Patching the whole deriver in the tests above proves the rule but not the
    wiring: if `_derive_video_assets` ever renamed its `duration_ms` key, those
    tests would keep passing while enforcement read `None` and convicted nobody.
    Here the genuine pipeline produces the updates dict.
    """

    def test_a_real_pipeline_measurement_over_the_limit_is_blocked(self, messenger_db, monkeypatch):
        conn, cur, storage = messenger_db
        key = "messenger/44/real.mp4"
        target = foundation._local_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", "testsrc=size=320x180:rate=15:duration=2",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target)],
            check=True, timeout=120,
        )
        attachment_id = _attachment(cur, storage_key=key)
        monkeypatch.setattr(foundation, "_probe_duration_ms",
                            lambda path: (NINETY_MINUTES + 1) * 1000)

        result = foundation.process_attachment(cur, attachment_id, JOB)
        conn.commit()

        assert result["status"] == "rejected"
        assert _attachment_row(cur, attachment_id)["upload_status"] == "blocked"

    def test_a_real_short_video_is_processed(self, messenger_db):
        conn, cur, storage = messenger_db
        key = "messenger/44/short.mp4"
        target = foundation._local_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", "testsrc=size=320x180:rate=15:duration=2",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target)],
            check=True, timeout=120,
        )
        attachment_id = _attachment(cur, storage_key=key)

        result = foundation.process_attachment(cur, attachment_id, JOB)
        conn.commit()

        assert result["status"] == "processed"
        assert _attachment_row(cur, attachment_id)["upload_status"] == "uploaded"
