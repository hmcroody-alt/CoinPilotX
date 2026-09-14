"""Guards for the playback-transcode backlog in media_worker.

Two production defects are covered here:

1. `-map 0:a?` mapped *every* audio track. iPhone spatial-audio .mov files carry a
   second `apple_apac` track that ffmpeg cannot decode, so ffmpeg aborted with
   "Error opening output files: Invalid argument".
2. The backlog query keyed only on `playback_storage_key`, so rows Mux had already
   made playable never left the queue and the failure above repeated every cycle.
"""

import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest

_bootstrap_database = tempfile.mktemp(prefix="media-worker-backlog-bootstrap-", suffix=".sqlite3")
os.environ["DATABASE_URL"] = f"sqlite:///{_bootstrap_database}"

import media_worker


MUX_HLS_URL = "https://stream.mux.com/laoLJh53johK0000kFFlyx8t76o3iZ01rETUevAPY02Dshk.m3u8"

SCHEMA = """
CREATE TABLE chat_media_uploads (
  id INTEGER PRIMARY KEY, message_id INTEGER, context_type TEXT, context_id TEXT,
  media_type TEXT, mime_type TEXT, storage_key TEXT, object_key TEXT,
  stored_filename TEXT, media_url TEXT, public_url TEXT, storage_provider TEXT,
  playback_url TEXT, playback_storage_key TEXT, playback_mime_type TEXT,
  processing_status TEXT, verification_status TEXT, is_available INTEGER,
  availability_error TEXT, error_message TEXT, mux_status TEXT,
  transcoded_at TEXT, deleted_at TEXT, created_at TEXT, updated_at TEXT
);
"""


def _insert(conn, **overrides):
    row = {
        "id": 1,
        "context_type": "pulse_status",
        "context_id": "draft",
        "media_type": "video",
        "mime_type": "video/quicktime",
        "storage_key": "pulse_media/4/2026/09/11/abc/437A1B24.mov",
        "object_key": "pulse_media/4/2026/09/11/abc/437A1B24.mov",
        "media_url": "https://cdn.coinpilotx.app/pulse_media/4/2026/09/11/abc/437A1B24.mov",
        "storage_provider": "r2",
        "playback_url": None,
        "playback_storage_key": None,
        "processing_status": "ready",
        "is_available": 1,
        "mux_status": None,
        "deleted_at": None,
    }
    row.update(overrides)
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO chat_media_uploads ({columns}) VALUES ({placeholders})", tuple(row.values()))


@pytest.fixture
def backlog_db(tmp_path, monkeypatch):
    """A worker pointed at a throwaway sqlite file, with ffmpeg reported as present."""
    database = str(tmp_path / "media-worker.sqlite3")
    conn = sqlite3.connect(database)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()

    monkeypatch.setattr(media_worker.bot, "db", lambda: sqlite3.connect(database))
    monkeypatch.setattr(media_worker.shutil, "which", lambda name: f"/usr/bin/{name}")

    def _seed(**overrides):
        seeded = sqlite3.connect(database)
        _insert(seeded, **overrides)
        seeded.commit()
        seeded.close()

    return _seed


def test_backlog_skips_videos_mux_has_already_made_playable(backlog_db, monkeypatch):
    """A Mux-ready row has a playback_url but never a playback_storage_key.

    Keying only on the storage key left 106 production rows queued forever, each one
    re-downloaded from R2 and re-transcoded every ~6s.
    """
    backlog_db(playback_url=MUX_HLS_URL, playback_mime_type="application/vnd.apple.mpegurl", mux_status="ready")

    def _explode(*args, **kwargs):
        raise AssertionError("an already-playable video must not be transcoded again")

    monkeypatch.setattr(media_worker, "_transcode_video_to_mp4", _explode)

    assert media_worker.process_playback_backlog() == {"checked": 0, "processed": 0, "failed": 0}


def test_backlog_does_not_retry_a_row_it_already_marked_blocked(backlog_db):
    """Without this, a permanently broken row is retried every cycle forever."""
    backlog_db(processing_status="processing_blocked", availability_error="Original video file is missing.")

    assert media_worker.process_playback_backlog()["checked"] == 0


def test_backlog_still_claims_an_untranscoded_mov(backlog_db):
    """The filters above must not starve the rows that genuinely need transcoding."""
    backlog_db(processing_status="ready", playback_url="", mux_status="waiting")

    assert media_worker.process_playback_backlog()["checked"] == 1


@pytest.mark.parametrize(
    "row, expected",
    [
        ({"id": 1, "media_type": "video", "mime_type": "video/quicktime", "playback_url": MUX_HLS_URL}, False),
        ({"id": 1, "media_type": "video", "mime_type": "video/quicktime", "playback_storage_key": "a-playback.mp4"}, False),
        ({"id": 1, "media_type": "video", "mime_type": "video/quicktime"}, True),
        ({"id": 1, "media_type": "video", "storage_key": "clip.mov"}, True),
    ],
)
def test_needs_playback_transcode_treats_any_playback_url_as_done(row, expected):
    assert media_worker._needs_playback_transcode(row) is expected


def _fake_transcode_run(captured):
    def _run(command, **kwargs):
        captured["command"] = command
        Path(command[-1]).write_bytes(b"fake mp4 payload")
        return subprocess.CompletedProcess(command, 0, "", "")

    return _run


def test_transcode_never_maps_every_audio_track(tmp_path, monkeypatch):
    """`0:a?` tolerates zero audio streams; it does not skip undecodable extra ones."""
    captured = {}
    monkeypatch.setattr(media_worker.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(media_worker, "_audio_map_specifier", lambda source: "0:a:0?")
    monkeypatch.setattr(media_worker.subprocess, "run", _fake_transcode_run(captured))

    media_worker._transcode_video_to_mp4(tmp_path / "source.mov", tmp_path / "playback.mp4")

    assert "0:a:0?" in captured["command"]
    assert "0:a?" not in captured["command"]


def test_transcode_emits_video_only_when_no_audio_is_decodable(tmp_path, monkeypatch):
    """Losing the audio track beats failing the whole video."""
    captured = {}
    monkeypatch.setattr(media_worker.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(media_worker, "_audio_map_specifier", lambda source: None)
    monkeypatch.setattr(media_worker.subprocess, "run", _fake_transcode_run(captured))

    media_worker._transcode_video_to_mp4(tmp_path / "source.mov", tmp_path / "playback.mp4")

    command = captured["command"]
    assert [command[i + 1] for i, part in enumerate(command) if part == "-map"] == ["0:v:0"]


@pytest.mark.parametrize(
    "probed_codecs, expected",
    [
        # Apple's usual layout: the AAC compatibility track leads.
        (["aac", "apple_apac"], "0:a:0?"),
        # The same phone also writes the spatial track first. Position is not a
        # safe proxy for decodability, which is the whole point of probing.
        (["apple_apac", "aac"], "0:a:1?"),
        (["apple_apac"], None),
        ([], None),
        (["aac"], "0:a:0?"),
    ],
)
def test_audio_map_specifier_picks_the_first_decodable_track(tmp_path, monkeypatch, probed_codecs, expected):
    monkeypatch.setattr(media_worker.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(media_worker, "_decodable_audio_codecs", lambda: frozenset({"aac", "opus", "mp3"}))
    monkeypatch.setattr(
        media_worker.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "\n".join(probed_codecs) + "\n", ""),
    )

    assert media_worker._audio_map_specifier(tmp_path / "source.mov") == expected


def test_audio_map_specifier_falls_back_when_ffprobe_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(media_worker.shutil, "which", lambda name: None if name == "ffprobe" else "/usr/bin/ffmpeg")

    assert media_worker._audio_map_specifier(tmp_path / "source.mov") == "0:a:0?"


def test_decodable_audio_codecs_excludes_codecs_with_no_decoder(monkeypatch):
    """apple_apac is listed by -codecs but absent from -decoders; only the latter counts."""
    listing = (
        "Decoders:\n V..... = Video\n A..... = Audio\n ------\n"
        " V....D h264                 H.264\n"
        " A....D aac                  AAC (Advanced Audio Coding)\n"
        " A....D opus                 Opus\n"
    )
    monkeypatch.setattr(media_worker.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        media_worker.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 0, listing, "")
    )
    media_worker._decodable_audio_codecs.cache_clear()
    try:
        codecs = media_worker._decodable_audio_codecs()
    finally:
        media_worker._decodable_audio_codecs.cache_clear()

    assert "aac" in codecs and "opus" in codecs
    assert "apple_apac" not in codecs
    assert "h264" not in codecs
    assert "=" not in codecs  # the legend block must not leak in


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg/ffprobe not installed")
def test_transcode_drops_the_extra_audio_track_end_to_end(tmp_path):
    """The real command against a real multi-audio .mov, the shape iPhones produce."""
    source = tmp_path / "two-audio-tracks.mov"
    build = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=15",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
            "-map", "0:v", "-map", "1:a", "-map", "2:a",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(source),
        ],
        capture_output=True, text=True, timeout=120,
    )
    assert build.returncode == 0, build.stderr[-2000:]

    target = tmp_path / "playback.mp4"
    media_worker._transcode_video_to_mp4(source, target)

    assert target.exists() and target.stat().st_size > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(target)],
        capture_output=True, text=True, timeout=60,
    )
    audio_streams = [line for line in probe.stdout.splitlines() if line.strip()]
    assert len(audio_streams) == 1, f"expected exactly one audio track, got {audio_streams}"
