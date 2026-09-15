"""Guards for the availability sweep in media_worker.

A media row used to be written `ready` + `is_available=1` and never looked at again.
When the bytes behind it went away the row kept promising them, the API kept handing
the client a URL, and the player drew a black rectangle -- an outage that looked
exactly like an empty state. The sweep exists to retire that promise.

The delicate part is not detecting absence, it is refusing to *infer* it. Storage that
cannot be reached must never be read as storage that answered "gone", or one bad
minute of connectivity marks the library dead and nothing puts it back.
"""

import os
import sqlite3
import tempfile

import pytest

_bootstrap_database = tempfile.mktemp(prefix="media-worker-availability-bootstrap-", suffix=".sqlite3")
os.environ["DATABASE_URL"] = f"sqlite:///{_bootstrap_database}"

import media_worker


SCHEMA = """
CREATE TABLE chat_media_uploads (
  id INTEGER PRIMARY KEY, media_type TEXT, mime_type TEXT,
  storage_key TEXT, object_key TEXT, playback_storage_key TEXT,
  media_url TEXT, playback_url TEXT, storage_provider TEXT,
  mux_status TEXT, mux_playback_id TEXT,
  processing_status TEXT, is_available INTEGER,
  availability_error TEXT, availability_checked_at TEXT,
  deleted_at TEXT, updated_at TEXT
);
"""


def _defaults(**overrides):
    row = {
        "id": 1,
        "media_type": "video",
        "storage_key": "pulse_media/4/2026/09/11/abc/clip.mp4",
        "object_key": "",
        "playback_storage_key": "",
        "media_url": "https://cdn.coinpilotx.app/pulse_media/4/2026/09/11/abc/clip.mp4",
        "playback_url": "",
        "storage_provider": "r2",
        "mux_status": "",
        "mux_playback_id": "",
        "processing_status": "ready",
        "is_available": 1,
        "availability_error": "",
        "availability_checked_at": "",
        "deleted_at": None,
    }
    row.update(overrides)
    return row


@pytest.fixture
def worker_db(tmp_path, monkeypatch):
    database = str(tmp_path / "media-worker.sqlite3")
    conn = sqlite3.connect(database)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    monkeypatch.setattr(media_worker.bot, "db", lambda: sqlite3.connect(database))

    def _seed(**overrides):
        row = _defaults(**overrides)
        seeded = sqlite3.connect(database)
        seeded.execute(
            f"INSERT INTO chat_media_uploads ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
            tuple(row.values()),
        )
        seeded.commit()
        seeded.close()

    def _read(media_id=1):
        readback = sqlite3.connect(database)
        readback.row_factory = sqlite3.Row
        found = dict(readback.execute("SELECT * FROM chat_media_uploads WHERE id=?", (media_id,)).fetchone())
        readback.close()
        return found

    return _seed, _read


def _storage(monkeypatch, behaviour):
    """Point the worker's head_object at `behaviour`, keyed by object key."""
    def _head(key):
        outcome = behaviour(key)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(media_worker.media_storage, "head_object", _head)


def test_a_row_promising_bytes_the_bucket_lost_is_downgraded(worker_db, monkeypatch):
    """The acceptance case: a row claims ready+available, the object is gone."""
    seed, read = worker_db
    seed()
    _storage(monkeypatch, lambda key: Exception("An error occurred (404) when calling the HeadObject operation: Not Found"))

    assert media_worker.reconcile_media_availability() == {"checked": 1, "downgraded": 1}

    row = read()
    assert row["is_available"] == 0
    assert row["processing_status"] == "failed"
    assert row["availability_error"] == "SOURCE_MEDIA_MISSING"
    assert row["deleted_at"] is None, "the record must survive the repair"


def test_unreachable_storage_is_not_read_as_a_missing_object(worker_db, monkeypatch):
    """The whole library is one bad minute away from this being wrong."""
    seed, read = worker_db
    seed()
    _storage(monkeypatch, lambda key: Exception("Connection reset by peer"))

    assert media_worker.reconcile_media_availability() == {"checked": 1, "downgraded": 0}
    assert read()["is_available"] == 1


def test_a_healthy_row_is_left_alone(worker_db, monkeypatch):
    seed, read = worker_db
    seed()
    _storage(monkeypatch, lambda key: {"ContentLength": 1024})

    assert media_worker.reconcile_media_availability() == {"checked": 1, "downgraded": 0}
    row = read()
    assert row["is_available"] == 1 and row["processing_status"] == "ready"
    assert row["availability_checked_at"], "a clean check must still advance the cursor"


def test_one_unanswerable_key_protects_the_row(worker_db, monkeypatch):
    """Absent source + unreachable playback copy is not proof of anything."""
    seed, read = worker_db
    seed(playback_storage_key="pulse_media/playback/clip.mp4")
    _storage(monkeypatch, lambda key: Exception("404 Not Found") if "playback" not in key else Exception("timed out"))

    assert media_worker.reconcile_media_availability()["downgraded"] == 0
    assert read()["is_available"] == 1


def test_local_rows_are_never_judged_from_this_container(worker_db, monkeypatch):
    """Their bytes live on the web container's disk, which this worker cannot see."""
    seed, read = worker_db
    seed(storage_provider="local", storage_key="pulse_media/2026/05/24/ScreenRecording.mp4")
    _storage(monkeypatch, lambda key: pytest.fail("a local row must not be probed against the bucket"))

    assert media_worker.reconcile_media_availability() == {"checked": 0, "downgraded": 0}
    assert read()["is_available"] == 1


def test_mux_keeps_a_row_alive_when_the_bucket_copy_is_gone(worker_db, monkeypatch):
    """Playback does not depend on the bucket for a Mux-served asset."""
    seed, read = worker_db
    seed(mux_playback_id="abc123", mux_status="ready", playback_url="https://stream.mux.com/abc123.m3u8")
    _storage(monkeypatch, lambda key: Exception("404 Not Found"))

    assert media_worker.reconcile_media_availability()["downgraded"] == 0
    assert read()["is_available"] == 1


def test_a_row_checked_today_is_not_rechecked(worker_db, monkeypatch):
    seed, _ = worker_db
    seed(availability_checked_at=media_worker._now())
    _storage(monkeypatch, lambda key: pytest.fail("a freshly checked row must not be probed again"))

    assert media_worker.reconcile_media_availability() == {"checked": 0, "downgraded": 0}


def test_an_already_unavailable_row_is_not_rechecked(worker_db, monkeypatch):
    seed, _ = worker_db
    seed(is_available=0, processing_status="failed")
    _storage(monkeypatch, lambda key: pytest.fail("nothing to re-prove about a row that already admits it"))

    assert media_worker.reconcile_media_availability() == {"checked": 0, "downgraded": 0}


@pytest.mark.parametrize(
    "error, expected",
    [
        ("An error occurred (404) when calling the HeadObject operation", False),
        ("NoSuchKey", False),
        ("Not Found", False),
        ("Connection reset by peer", None),
        ("An error occurred (403) when calling the HeadObject operation", None),
        ("An error occurred (500) when calling the HeadObject operation", None),
    ],
)
def test_only_a_definitive_not_found_counts_as_absent(monkeypatch, error, expected):
    monkeypatch.setattr(media_worker.media_storage, "head_object", lambda key: (_ for _ in ()).throw(Exception(error)))
    assert media_worker._object_presence("some/key.mp4") is expected


def test_a_present_object_reports_present(monkeypatch):
    monkeypatch.setattr(media_worker.media_storage, "head_object", lambda key: {"ContentLength": 10})
    assert media_worker._object_presence("some/key.mp4") is True
