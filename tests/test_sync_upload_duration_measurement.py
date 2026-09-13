"""That the plain form POST measures a video's length for itself.

Every web upload surface calls `PulseUploadManager.upload`, which probes the file
with an HTMLMediaElement and sends `duration_ms`. Every one of them also has a
fallback for when that script has not loaded:

    window.PulseUploadManager ? await window.PulseUploadManager.upload({...})
                              : await api('/api/pulse/media/upload', {body: fd})

Seven of those fallbacks exist (four inline in bot.py, three in static/js), and
none of them declares a duration. `/api/media/upload` never had one either -- it
calls `save_upload` directly and skips the staging wrapper where the declared
check lives. So the declared ceiling was enforced only for clients that chose to
be measured.

Nothing measured those uploads afterwards either. The worker's reconciler selects
`COALESCE(mux_asset_id,'')<>''`, so a video stored on R2 without a Mux asset is
never probed, and an unmeasured video is indistinguishable from a short one.

The fix is one probe in `save_upload`, which is the chokepoint all of those paths
share -- rather than seven copies of a duration probe in seven inline strings,
where the eighth surface added later would not have one. These tests are about
that probe: that it runs, that it is what the refusal is based on, and that it
refuses to convict on the things it must not.
"""

from __future__ import annotations

import io
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest

_DB = tempfile.mktemp(prefix="sync-upload-duration-", suffix=".sqlite3")
_UPLOADS = tempfile.mkdtemp(prefix="sync-upload-duration-root-")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["MEDIA_UPLOAD_DIR"] = _UPLOADS

from werkzeug.datastructures import FileStorage  # noqa: E402

from services import media_covers, media_service, media_storage, stored_video_policy  # noqa: E402

NINETY_MINUTES = 5400
# An mp4 the header check accepts. Its real duration is irrelevant: these tests
# control the measurement, because what is under test is what the upload path does
# with a measurement, not ffprobe's arithmetic. TestTheProbeReadsARealContainer
# covers the probe itself.
MP4_HEADER = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 512


@pytest.fixture(autouse=True)
def database():
    conn = sqlite3.connect(_DB)
    conn.executescript(
        """
        DROP TABLE IF EXISTS chat_media_uploads;
        CREATE TABLE chat_media_uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT, uploader_user_id INTEGER,
            context_type TEXT, context_id TEXT, message_id INTEGER,
            original_filename TEXT, stored_filename TEXT, media_url TEXT,
            thumbnail_url TEXT, media_type TEXT, mime_type TEXT,
            file_size_bytes INTEGER, duration_seconds REAL, width INTEGER,
            height INTEGER, moderation_status TEXT, moderation_reason TEXT,
            storage_provider TEXT, storage_key TEXT, bucket TEXT, object_key TEXT,
            cdn_url TEXT, public_url TEXT, private_url TEXT, poster_url TEXT,
            playback_url TEXT, playback_mime_type TEXT, small_url TEXT,
            medium_url TEXT, large_url TEXT, is_available INTEGER,
            processing_status TEXT, verification_status TEXT,
            availability_checked_at TEXT, availability_error TEXT, trace_id TEXT,
            error_message TEXT, upload_complete_at TEXT, mux_asset_id TEXT,
            mux_playback_id TEXT, mux_status TEXT, mux_upload_id TEXT,
            mux_asset_created_at TEXT, mux_ready_at TEXT, db_ready_update_at TEXT,
            created_at TEXT, updated_at TEXT, deleted_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    yield
    shutil.rmtree(Path(_UPLOADS) / "pulse_media", ignore_errors=True)


@pytest.fixture
def measured(monkeypatch):
    """Pin the measurement and record the path it was asked about."""
    calls: list[str] = []

    def install(seconds):
        def probe(source):
            calls.append(str(source))
            return float(seconds)

        monkeypatch.setattr(media_covers, "video_duration_seconds", probe)
        return calls

    return install


def _upload(user_id, context_type="pulse", filename="clip.mp4"):
    handle = FileStorage(
        stream=io.BytesIO(MP4_HEADER),
        filename=filename,
        content_type="video/mp4",
    )
    return media_service.save_upload(user_id, handle, context_type=context_type, context_id="draft")


def _rows():
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM chat_media_uploads ORDER BY id").fetchall()]
    conn.close()
    return rows


class TestAnUndeclaredOverLongVideoIsRefused:
    def test_a_ninety_one_minute_upload_is_refused_with_no_declared_duration(self, measured):
        # The whole point: the form POST declared nothing, and the upload still
        # fails on length. Before the probe this returned 200 and published.
        measured(NINETY_MINUTES + 60)
        result, status = _upload(4001)
        assert status == 413
        assert result["ok"] is False
        assert result["message"] == "Videos can be up to 90 minutes long."
        assert result["measured_duration_seconds"] == NINETY_MINUTES + 60

    def test_the_refusal_names_its_reason_where_the_client_reads_it(self, measured):
        # pulseApi reads `error_code`; a body carrying only `error` collapses this
        # into a generic upload failure and the user never learns it was length.
        measured(NINETY_MINUTES + 1)
        result, _ = _upload(4002)
        assert result["error_code"] == stored_video_policy.MEASURED_REJECTION_CODE
        assert result["error"] == stored_video_policy.MEASURED_REJECTION_CODE
        assert result["max_duration_seconds"] == NINETY_MINUTES

    def test_a_refused_upload_leaves_no_row_behind(self, measured):
        # Refusing before the INSERT, not blocking after it. A blocked row would
        # need the reel takedown, the feed filters and the availability guard to
        # each hold; no row needs none of them.
        measured(NINETY_MINUTES + 1)
        _upload(4003)
        assert _rows() == []

    def test_a_refused_upload_does_not_keep_the_bytes(self, measured):
        measured(NINETY_MINUTES + 1)
        _upload(4004)
        stored = list((Path(_UPLOADS) / "pulse_media").rglob("*.mp4"))
        assert stored == [], f"refused upload left {stored} on disk"

    def test_the_measurement_is_taken_from_the_stored_file(self, measured):
        # Not from the request, and not from the filename. If the probe were handed
        # anything the uploader controls, it would not be a measurement.
        calls = measured(10)
        _upload(4005)
        assert len(calls) >= 1
        probed = Path(calls[0])
        assert probed.name.endswith(".mp4")
        assert probed.is_relative_to(Path(_UPLOADS).resolve())


class TestTheBoundaryIsTheSameOneEverySurfaceUses:
    @pytest.mark.parametrize("seconds", [NINETY_MINUTES - 1, NINETY_MINUTES, NINETY_MINUTES + 0.4])
    def test_ninety_minutes_exactly_is_accepted(self, measured, seconds):
        measured(seconds)
        result, status = _upload(4100 + int(seconds % 97))
        assert status == 200, result.get("message")
        assert result["ok"] is True

    def test_ninety_minutes_and_one_second_is_not(self, measured):
        measured(NINETY_MINUTES + 1)
        _, status = _upload(4200)
        assert status == 413

    def test_status_keeps_its_own_shorter_ceiling(self, measured):
        # 90 minutes is the platform maximum, not every surface's limit. Status is
        # short-form by product decision and the probe must read that from the one
        # policy rather than comparing against the platform number.
        measured(120)
        result, status = _upload(4201, context_type="pulse_status")
        assert status == 413
        assert result["message"] == "Videos can be up to 1 minute long."

    def test_a_reel_gets_the_full_ninety_minutes(self, measured):
        measured(NINETY_MINUTES - 5)
        _, status = _upload(4202, context_type="pulse_reel")
        assert status == 200


class TestWhatTheProbeMustNotConvict:
    def test_an_unmeasurable_video_is_accepted(self, measured):
        # 0.0 is "no ffprobe on the box" or "unreadable container", not "zero
        # seconds". Refusing on absence would reject every upload on any host
        # without ffprobe -- which is how a duration ceiling becomes an outage.
        measured(0.0)
        result, status = _upload(4300)
        assert status == 200
        assert result["ok"] is True

    def test_a_surface_nobody_registered_is_left_alone(self, measured):
        # save_upload is reached with free-form context_types. An unregistered name
        # falls through to the strictest cap, which is the right default for "may
        # this upload start" and wrong here: it would cut asset_focus, native and
        # pulse_comment video to 60 seconds while looking deliberate.
        assert not stored_video_policy.is_known_surface("pulse_comment")
        measured(600)
        _, status = _upload(4301, context_type="pulse_comment")
        assert status == 200

    def test_an_image_is_never_probed(self, measured):
        calls = measured(NINETY_MINUTES + 600)
        handle = FileStorage(
            stream=io.BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * 256),
            filename="photo.jpg",
            content_type="image/jpeg",
        )
        _, status = media_service.save_upload(4302, handle, context_type="pulse", context_id="draft")
        assert status == 200
        assert calls == []


class TestTheMeasurementIsWrittenDown:
    def test_an_accepted_video_carries_its_measured_length(self, measured):
        # The worker's reconciler selects on duration_seconds<=0. A row the upload
        # already measured must not come back round for a second measurement, and
        # every reader of this row should be able to see how long the video is.
        measured(742.5)
        result, status = _upload(4400)
        assert status == 200
        rows = _rows()
        assert len(rows) == 1
        assert rows[0]["duration_seconds"] == pytest.approx(742.5)
        assert result["media"]["id"] == rows[0]["id"]

    def test_an_unmeasurable_video_is_left_for_the_reconciler(self, measured):
        # NULL rather than 0, so the reconciler's COALESCE(duration_seconds,0)<=0
        # still selects it. Writing 0.0 would read as "measured, and it was zero".
        measured(0.0)
        _upload(4401)
        rows = _rows()
        assert len(rows) == 1
        assert rows[0]["duration_seconds"] is None


@pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe not installed; the probe is exercised through its monkeypatch elsewhere",
)
class TestTheProbeReadsARealContainer:
    """The one test that does not pin the measurement.

    Everything above monkeypatches `video_duration_seconds`, so all of it would
    still pass if the probe returned a constant. This synthesizes a container of a
    known length and reads it back.
    """

    def test_a_real_file_measures_its_real_length(self, tmp_path):
        source = tmp_path / "two-seconds.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=2",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
            check=True,
            capture_output=True,
            timeout=60,
        )
        assert media_covers.video_duration_seconds(source) == pytest.approx(2.0, abs=0.3)

    def test_an_unreadable_file_measures_as_absent(self, tmp_path):
        broken = tmp_path / "not-a-video.mp4"
        broken.write_bytes(MP4_HEADER)
        assert media_covers.video_duration_seconds(broken) == 0.0
