"""That a video whose original was never kept can still get a real cover.

Measured in production: 145 video rows, 56 with no usable thumbnail, all 56
with `cover_attempts` already at the cap and `cover_generated_at` never set.
Cover generation ran for every one of them and produced nothing, every time.

The cause is not ffmpeg (7.1.5 is installed in the media engine) and not the
worker (it is deployed, and the exhausted attempt counters are its fingerprint).
It is that 54 of those 56 rows have an empty `storage_key` -- and every other
source column empty too -- because the upload was handed to Mux and the local
copy discarded. `ensure_covers_for_row` resolves its source from `storage_key`
alone, so it returned `{}` before ffmpeg was ever invoked. Deterministic, which
is why the failure rate was exactly 100%.

Two properties are pinned here, and the second is the one that will rot
silently:

  * a ready Mux asset is an acceptable source, so those rows produce a cover
  * the manifest URL reaches ffmpeg intact -- `Path("https://h/x.m3u8")`
    collapses the double slash to `https:/h/x.m3u8`, which ffmpeg cannot open.
    Nothing downstream would report that as anything but "no cover generated",
    the same symptom this file exists to remove.

The point of sourcing the frame from Mux is to stop depending on Mux: the
result is a JPEG stored in our own bucket, not an `image.mux.com` URL the apps
must fetch live on every render.
"""

import os
import sqlite3
import tempfile

_bootstrap = tempfile.mktemp(prefix="media-cover-mux-", suffix=".sqlite3")
os.environ["DATABASE_URL"] = f"sqlite:///{_bootstrap}"

from services import media_covers  # noqa: E402
from tests.test_live_replay_worker import media_worker  # noqa: E402

PLAYBACK_ID = "feVBmrljlxinob00RsxpMke39rPJL3u8oKzgO1vsYr1g"
MANIFEST = f"https://stream.mux.com/{PLAYBACK_ID}.m3u8"


def _mux_only_row(**overrides):
    """Production shape of the 48: a ready Mux asset and nothing else."""
    row = {
        "id": 722,
        "media_type": "video",
        "mime_type": "video/quicktime",
        "storage_key": "",
        "object_key": "",
        "media_url": "",
        "small_url": "",
        "poster_url": "",
        "thumbnail_url": "",
        "mux_status": "ready",
        "mux_playback_id": PLAYBACK_ID,
        "playback_url": MANIFEST,
    }
    row.update(overrides)
    return row


class TestMuxSourcedCovers:
    def _capture(self, monkeypatch):
        """Run the backfill with ffmpeg stubbed; report what it was asked to read."""
        seen = {}
        monkeypatch.setattr(media_covers, "ffmpeg_available", lambda: True)

        def fake_frame(source, tmp_dir):
            seen["source"] = source
            still = tmp_dir / "frame.jpg"
            still.write_bytes(b"jpeg")
            return still

        def fake_generate(source_path, media_type, storage_key):
            seen["generate"] = (str(source_path), media_type, storage_key)
            return {"thumbnail_url": "t.jpg", "poster_url": "p.jpg"}

        monkeypatch.setattr(media_covers, "extract_video_poster_frame", fake_frame)
        monkeypatch.setattr(media_covers, "generate_covers", fake_generate)
        return seen

    def test_a_mux_only_video_still_gets_a_cover(self, monkeypatch):
        self._capture(monkeypatch)
        assert media_covers.ensure_covers_for_row(_mux_only_row()) != {}

    def test_the_manifest_url_reaches_ffmpeg_unmangled(self, monkeypatch):
        seen = self._capture(monkeypatch)
        media_covers.ensure_covers_for_row(_mux_only_row())
        # A str, not a Path: Path() would have eaten one of the two slashes.
        assert seen["source"] == MANIFEST
        assert seen["source"].startswith("https://")

    def test_the_cover_is_stored_under_a_key_unique_to_the_asset(self, monkeypatch):
        seen = self._capture(monkeypatch)
        media_covers.ensure_covers_for_row(_mux_only_row())
        _, media_type, storage_key = seen["generate"]
        # Scaled as a still, because the frame is already a local JPEG.
        assert media_type == "image"
        assert PLAYBACK_ID in storage_key
        assert media_covers._cover_key(storage_key, "medium").endswith(f"{PLAYBACK_ID}-cover-medium.jpg")

    def test_two_assets_do_not_collide_on_one_key(self, monkeypatch):
        seen = self._capture(monkeypatch)
        media_covers.ensure_covers_for_row(_mux_only_row())
        first = seen["generate"][2]
        media_covers.ensure_covers_for_row(_mux_only_row(id=459, mux_playback_id="other-playback-id"))
        assert seen["generate"][2] != first

    def test_an_unready_asset_is_not_a_source(self, monkeypatch):
        # Mux hands out a playback id long before the manifest exists; reading it
        # early yields a 404, which paints black rather than erroring.
        seen = self._capture(monkeypatch)
        assert media_covers.ensure_covers_for_row(_mux_only_row(mux_status="waiting")) == {}
        assert "source" not in seen

    def test_a_row_with_neither_a_file_nor_an_asset_is_left_alone(self, monkeypatch):
        seen = self._capture(monkeypatch)
        row = _mux_only_row(mux_status="", mux_playback_id="", playback_url="")
        assert media_covers.ensure_covers_for_row(row) == {}
        assert "source" not in seen

    def test_a_signed_manifest_keeps_its_token(self, monkeypatch):
        signed = f"{MANIFEST}?token=abc123"
        seen = self._capture(monkeypatch)
        media_covers.ensure_covers_for_row(_mux_only_row(playback_url=signed))
        assert seen["source"] == signed


class TestAudioMislabelledAsVideo:
    """Three production rows are `audio/webm` stored as media_type='video'.

    There is no frame in an audio file. Asking for one cannot succeed, so each
    attempt only spends part of a retry budget that a genuinely fixable row
    might need -- and the apps render a designed card for audio anyway.
    """

    def test_audio_does_not_need_a_cover(self):
        row = {"media_type": "video", "mime_type": "audio/webm", "media_url": "/static/uploads/a.webm", "small_url": ""}
        assert media_covers.row_needs_covers(row) is False

    def test_a_real_video_still_does(self):
        row = {"media_type": "video", "mime_type": "video/quicktime", "media_url": "/static/uploads/a.mov", "small_url": ""}
        assert media_covers.row_needs_covers(row) is True

    def test_the_backlog_query_skips_audio(self, tmp_path, monkeypatch):
        path = _cover_backlog_database(tmp_path)
        monkeypatch.setattr(media_worker.bot, "db", lambda: sqlite3.connect(path))
        monkeypatch.setattr(media_worker.media_covers, "ffmpeg_available", lambda: True)
        monkeypatch.setattr(media_worker.media_covers, "ensure_covers_for_row", lambda row: {})

        media_worker.process_cover_backlog(10)

        conn = sqlite3.connect(path)
        attempts = dict(conn.execute("SELECT id, COALESCE(cover_attempts, 0) FROM chat_media_uploads").fetchall())
        conn.close()
        # The video was tried and spent an attempt; the voice note was not.
        assert attempts[1] == 1
        assert attempts[2] == 0


def _cover_backlog_database(tmp_path):
    path = str(tmp_path / "covers.sqlite3")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE chat_media_uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT, media_type TEXT, mime_type TEXT,
            media_url TEXT, small_url TEXT, medium_url TEXT, large_url TEXT,
            thumbnail_url TEXT, poster_url TEXT, storage_key TEXT,
            cover_attempts INTEGER, cover_generated_at TEXT, is_available INTEGER,
            deleted_at TEXT
        );
        INSERT INTO chat_media_uploads (id, media_type, mime_type, media_url, small_url, is_available)
        VALUES (1, 'video', 'video/quicktime', '/static/uploads/a.mov', '', 1);
        INSERT INTO chat_media_uploads (id, media_type, mime_type, media_url, small_url, is_available)
        VALUES (2, 'video', 'audio/webm', '/static/uploads/a.webm', '', 1);
        """
    )
    conn.commit()
    conn.close()
    return path


class TestTheFeedMirror:
    """That a cover reaches the table the Pulse feed actually reads.

    `pulse_media_assets` is written once at upload from the upload result, whose
    `thumbnail_url` for a video is the video's own URL -- covers do not exist
    yet at that moment. The only later writers are the Mux webhooks, and they
    touch the `mux_*` columns only. Measured: 0 of 94 production video rows in
    the mirror carried a cover, while 69 of them had one sitting in
    `chat_media_uploads` the whole time.
    """

    def _database(self, tmp_path, **asset):
        path = str(tmp_path / "mirror.sqlite3")
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE chat_media_uploads (
                id INTEGER PRIMARY KEY, thumbnail_url TEXT, poster_url TEXT
            );
            CREATE TABLE pulse_media_assets (
                id INTEGER PRIMARY KEY, media_id INTEGER, media_type TEXT,
                public_url TEXT, thumbnail_url TEXT, poster_url TEXT, updated_at TEXT
            );
            INSERT INTO chat_media_uploads (id, thumbnail_url, poster_url)
            VALUES (602, 'https://cdn/x-cover-medium.jpg', 'https://cdn/x-cover-large.jpg');
            """
        )
        row = {
            "public_url": "https://cdn/x.mov",
            "thumbnail_url": "https://cdn/x.mov",
            "poster_url": "https://image.mux.com/abc/thumbnail.jpg",
        }
        row.update(asset)
        conn.execute(
            "INSERT INTO pulse_media_assets (id, media_id, media_type, public_url, thumbnail_url, poster_url)"
            " VALUES (192, 602, 'video', ?, ?, ?)",
            (row["public_url"], row["thumbnail_url"], row["poster_url"]),
        )
        conn.commit()
        conn.close()
        return path

    def _sync(self, tmp_path, monkeypatch, **asset):
        path = self._database(tmp_path, **asset)
        monkeypatch.setattr(media_worker.bot, "db", lambda: sqlite3.connect(path))
        result = media_worker.process_media_asset_cover_sync(50)
        conn = sqlite3.connect(path)
        stored = conn.execute("SELECT thumbnail_url, poster_url FROM pulse_media_assets WHERE id=192").fetchone()
        conn.close()
        return result, stored

    def test_the_video_url_in_the_thumbnail_field_is_replaced_by_the_cover(self, tmp_path, monkeypatch):
        _, (thumbnail, _) = self._sync(tmp_path, monkeypatch)
        assert thumbnail == "https://cdn/x-cover-medium.jpg"

    def test_a_live_mux_still_is_replaced_by_our_stored_one(self, tmp_path, monkeypatch):
        _, (_, poster) = self._sync(tmp_path, monkeypatch)
        assert poster == "https://cdn/x-cover-large.jpg"
        assert "image.mux.com" not in poster

    def test_a_cover_someone_chose_is_not_clobbered(self, tmp_path, monkeypatch):
        _, (thumbnail, poster) = self._sync(
            tmp_path,
            monkeypatch,
            thumbnail_url="https://cdn/creator-choice.jpg",
            poster_url="https://cdn/creator-choice.jpg",
        )
        assert thumbnail == "https://cdn/creator-choice.jpg"
        assert poster == "https://cdn/creator-choice.jpg"

    def test_a_second_pass_finds_nothing_left_to_do(self, tmp_path, monkeypatch):
        """The predicate has to clear itself, or the worker rewrites forever."""
        path = self._database(tmp_path)
        monkeypatch.setattr(media_worker.bot, "db", lambda: sqlite3.connect(path))
        assert media_worker.process_media_asset_cover_sync(50)["synced"] == 1
        assert media_worker.process_media_asset_cover_sync(50)["checked"] == 0
