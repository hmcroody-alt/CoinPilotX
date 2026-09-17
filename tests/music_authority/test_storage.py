"""A purge deletes bytes, and bytes do not come back.

This is the one irreversible thing the owner can do, so the question these tests
ask is narrow and paranoid: *which keys, exactly, does it delete?*

The answer has to be "the two this track owns, derived from the urls already in
the database, and nothing else" -- because a purge is reached over HTTP and the
request body is attacker-influenced even when the caller is authorized. A key
that could be steered by the request, or a url parser loose enough to accept a
foreign host, turns one legitimate takedown into a way to delete anything in the
bucket.

The R2 client is faked throughout. No test here touches a real bucket, and the
fake records every call so the assertions are on the exact arguments rather than
on "it didn't crash".
"""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime

_FD, _DB_PATH = tempfile.mkstemp(suffix="-music-storage.db")
os.close(_FD)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ.setdefault("R2_PUBLIC_BASE_URL", "https://cdn.example.test")
os.environ.setdefault("R2_BUCKET", "pulsesoc-test-bucket")

from werkzeug.security import generate_password_hash  # noqa: E402

import bot  # noqa: E402
from services import media_storage, music_authority  # noqa: E402

PUBLIC_BASE = "https://cdn.example.test"
OWNER_ACCOUNT_ID = 9101
OWNER_PASSWORD = "Owner-Purge-P4ss!"
TARGET_AUDIO_URL = f"{PUBLIC_BASE}/pulse_music/target-audio.mp3"
TARGET_COVER_URL = f"{PUBLIC_BASE}/pulse_music/target-cover.jpg"
NEIGHBOUR_AUDIO_URL = f"{PUBLIC_BASE}/pulse_music/neighbour-audio.mp3"


class FakeObjectClient:
    """Records deletes instead of issuing them."""

    def __init__(self):
        self.deletes = []

    def delete_object(self, Bucket=None, Key=None):  # noqa: N803 - boto3's own casing
        self.deletes.append({"Bucket": Bucket, "Key": Key})
        return {}


class _FakeClientPatch:
    def __init__(self, client):
        self.client = client
        self._original = None

    def __enter__(self):
        self._original = media_storage.object_client
        media_storage.object_client = lambda: self.client
        return self.client

    def __exit__(self, *exc):
        media_storage.object_client = self._original
        return False


def _connect():
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


class KeyDerivationTests(unittest.TestCase):
    """`storage_key_from_public_url` is the only thing standing between a stored
    url and a delete, so it is strict by construction."""

    def test_a_url_under_our_own_base_yields_its_key(self):
        self.assertEqual(
            media_storage.storage_key_from_public_url(TARGET_AUDIO_URL),
            "pulse_music/target-audio.mp3",
        )

    def test_a_local_uploads_path_yields_its_key(self):
        self.assertEqual(
            media_storage.storage_key_from_public_url("/static/uploads/pulse_music/a.mp3"),
            "pulse_music/a.mp3",
        )

    def test_query_and_fragment_are_dropped(self):
        self.assertEqual(
            media_storage.storage_key_from_public_url(f"{TARGET_AUDIO_URL}?v=3#t=10"),
            "pulse_music/target-audio.mp3",
        )

    def test_percent_encoding_is_decoded(self):
        self.assertEqual(
            media_storage.storage_key_from_public_url(f"{PUBLIC_BASE}/pulse_music/a%20b.mp3"),
            "pulse_music/a b.mp3",
        )

    def test_a_foreign_host_yields_nothing(self):
        """Otherwise a url that reached the row from anywhere else names a key."""
        for url in (
            "https://evil.example/pulse_music/a.mp3",
            "https://cdn.example.test.evil.example/pulse_music/a.mp3",
            "https://cdn.example.testX/pulse_music/a.mp3",
        ):
            self.assertEqual(media_storage.storage_key_from_public_url(url), "", url)

    def test_traversal_yields_nothing(self):
        for url in (
            f"{PUBLIC_BASE}/../secrets/key.pem",
            f"{PUBLIC_BASE}/pulse_music/../../secrets/key.pem",
            "/static/uploads/../../secrets/key.pem",
        ):
            self.assertEqual(media_storage.storage_key_from_public_url(url), "", url)

    def test_empty_and_bare_base_yield_nothing(self):
        for url in ("", None, "   ", PUBLIC_BASE, f"{PUBLIC_BASE}/", "/static/uploads/"):
            self.assertEqual(media_storage.storage_key_from_public_url(url), "", repr(url))

    def test_a_bare_key_is_not_accepted_as_a_url(self):
        """A caller must pass a stored url; accepting a raw path would let any
        string in the database name any object."""
        self.assertEqual(media_storage.storage_key_from_public_url("pulse_music/a.mp3"), "")


class DeleteObjectKeyTests(unittest.TestCase):
    def test_a_valid_key_is_issued_against_the_configured_bucket(self):
        client = FakeObjectClient()
        with _FakeClientPatch(client):
            self.assertTrue(media_storage.delete_object_key("pulse_music/a.mp3"))
        self.assertEqual(
            client.deletes,
            [{"Bucket": os.environ["R2_BUCKET"], "Key": "pulse_music/a.mp3"}],
        )

    def test_a_traversal_key_is_refused_without_reaching_the_client(self):
        client = FakeObjectClient()
        with _FakeClientPatch(client):
            self.assertFalse(media_storage.delete_object_key("../secrets/key.pem"))
            self.assertFalse(media_storage.delete_object_key("pulse_music/../../x"))
            self.assertFalse(media_storage.delete_object_key(""))
            self.assertFalse(media_storage.delete_object_key(None))
        self.assertEqual(client.deletes, [], "a refused key must never reach the bucket")

    def test_a_leading_slash_is_normalised_rather_than_refused(self):
        client = FakeObjectClient()
        with _FakeClientPatch(client):
            self.assertTrue(media_storage.delete_object_key("/pulse_music/a.mp3"))
        self.assertEqual(client.deletes[0]["Key"], "pulse_music/a.mp3")

    def test_no_configured_client_is_a_refusal_not_a_crash(self):
        original = media_storage.object_client
        media_storage.object_client = lambda: None
        try:
            self.assertFalse(media_storage.delete_object_key("pulse_music/a.mp3"))
        finally:
            media_storage.object_client = original


class TrackKeyOwnershipTests(unittest.TestCase):
    """`music_storage_keys_for_track` decides the blast radius of a purge."""

    def test_only_the_tracks_own_two_urls_are_used(self):
        keys = bot.music_storage_keys_for_track(
            {
                "id": 1,
                "audio_url": TARGET_AUDIO_URL,
                "cover_art_url": TARGET_COVER_URL,
                "preview_url": NEIGHBOUR_AUDIO_URL,
                "waveform_url": f"{PUBLIC_BASE}/pulse_music/other.json",
            }
        )
        self.assertEqual(keys, ["pulse_music/target-audio.mp3", "pulse_music/target-cover.jpg"])

    def test_a_request_supplied_key_is_ignored(self):
        """The dict comes from the database row; nothing the caller sent is read."""
        keys = bot.music_storage_keys_for_track(
            {
                "audio_url": TARGET_AUDIO_URL,
                "cover_art_url": "",
                "storage_key": "secrets/key.pem",
                "object_key": "secrets/key.pem",
                "keys": ["secrets/key.pem"],
            }
        )
        self.assertEqual(keys, ["pulse_music/target-audio.mp3"])

    def test_a_foreign_url_on_the_row_contributes_no_key(self):
        keys = bot.music_storage_keys_for_track(
            {"audio_url": "https://evil.example/a.mp3", "cover_art_url": TARGET_COVER_URL}
        )
        self.assertEqual(keys, ["pulse_music/target-cover.jpg"])

    def test_a_track_with_no_urls_deletes_nothing(self):
        self.assertEqual(bot.music_storage_keys_for_track({}), [])
        self.assertEqual(bot.music_storage_keys_for_track(None), [])

    def test_one_failing_delete_does_not_abandon_the_rest(self):
        """A half-finished purge must be retryable, and the retry needs to know
        which keys actually went."""

        class Flaky(FakeObjectClient):
            def delete_object(self, Bucket=None, Key=None):  # noqa: N803
                if Key.endswith("target-audio.mp3"):
                    raise RuntimeError("network")
                return super().delete_object(Bucket=Bucket, Key=Key)

        client = Flaky()
        with _FakeClientPatch(client):
            deleted = bot.music_delete_storage_keys(
                ["pulse_music/target-audio.mp3", "pulse_music/target-cover.jpg"]
            )
        self.assertEqual(deleted, ["pulse_music/target-cover.jpg"])


class PurgeBlastRadiusTests(unittest.TestCase):
    """End to end: a real authorized purge over HTTP, with the deletes recorded.

    Two tracks are seeded. Only one is purged. The other one's object key is the
    control: if it ever appears in the recorded deletes, a takedown of one song
    is destroying another.
    """

    @classmethod
    def setUpClass(cls):
        bot.init_db()
        conn = _connect()
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, 'purge-owner')", (OWNER_ACCOUNT_ID,))
        cur.execute(
            """
            INSERT INTO admin_users (email, password_hash, role, status, account_user_id, must_change_password, created_at)
            VALUES (?, ?, 'owner', 'active', ?, 0, '2026-09-01T00:00:00')
            """,
            ("purge-owner@test.local", generate_password_hash(OWNER_PASSWORD), OWNER_ACCOUNT_ID),
        )
        cls.owner_admin_id = int(cur.lastrowid)
        conn.commit()
        conn.close()

    def setUp(self):
        bot.webhook_app.config["TESTING"] = True
        self.client = bot.webhook_app.test_client()
        conn = _connect()
        cur = conn.cursor()
        cur.execute("DELETE FROM music_owner_stepups")
        cur.execute("DELETE FROM pulse_audio_tracks WHERE id IN (5101, 5102)")
        # The audit table is append-only by design, so it is the one thing that
        # would otherwise carry an earlier test's purge into this one.
        cur.execute("DELETE FROM music_takedown_audit WHERE track_id IN (5101, 5102)")
        for track_id, title, audio_url, cover in (
            (5101, "Target", TARGET_AUDIO_URL, TARGET_COVER_URL),
            (5102, "Neighbour", NEIGHBOUR_AUDIO_URL, ""),
        ):
            cur.execute(
                """
                INSERT INTO pulse_audio_tracks
                (id, title, artist, audio_url, cover_art_url, safety_status, approved_by_admin, active,
                 commercial_use_allowed, remix_edit_allowed, lifecycle_state, legal_hold, created_at)
                VALUES (?, ?, 'Artist', ?, ?, 'approved', 1, 1, 1, 1, 'PURGE_PENDING', 0, '2026-09-01T00:00:00')
                """,
                (track_id, title, audio_url, cover),
            )
        conn.commit()
        conn.close()
        with self.client.session_transaction() as sess:
            sess.pop("account_user_id", None)
            sess["admin_user_id"] = self.owner_admin_id
            sess["admin_session_issued_at"] = datetime.now().isoformat()
            sess["admin_session_last_seen"] = datetime.now().isoformat()

    def _step_up(self):
        response = self.client.post("/api/admin/music/step-up", json={"password": OWNER_PASSWORD})
        self.assertEqual(response.status_code, 200, response.get_json())

    def _purge(self, track_id):
        return self.client.post(
            f"/api/admin/music/tracks/{track_id}/purge",
            json={
                "confirm_track_id": track_id,
                "reason_code": "COPYRIGHT",
                "reason_note": "rights holder demand, controlled test",
                "expected_state": music_authority.STATE_PURGE_PENDING,
            },
        )

    def test_a_purge_deletes_exactly_the_two_keys_that_track_owns(self):
        self._step_up()
        client = FakeObjectClient()
        with _FakeClientPatch(client):
            response = self._purge(5101)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(
            [item["Key"] for item in client.deletes],
            ["pulse_music/target-audio.mp3", "pulse_music/target-cover.jpg"],
        )

    def test_the_other_tracks_object_is_untouched(self):
        self._step_up()
        client = FakeObjectClient()
        with _FakeClientPatch(client):
            self._purge(5101)
        self.assertNotIn(
            "pulse_music/neighbour-audio.mp3",
            [item["Key"] for item in client.deletes],
        )
        conn = _connect()
        neighbour = dict(conn.execute("SELECT * FROM pulse_audio_tracks WHERE id=5102").fetchone())
        conn.close()
        self.assertEqual(neighbour["audio_url"], NEIGHBOUR_AUDIO_URL)
        self.assertEqual(neighbour["lifecycle_state"], music_authority.STATE_PURGE_PENDING)

    def test_a_refused_purge_deletes_nothing(self):
        """No step-up: the bytes must still be there afterwards.

        The ordering matters as much as the status code -- a purge that deleted
        first and authorized second would return 403 and still have destroyed the
        file.
        """
        client = FakeObjectClient()
        with _FakeClientPatch(client):
            response = self._purge(5101)
        self.assertEqual(response.status_code, 403, response.get_json())
        self.assertEqual(client.deletes, [])
        conn = _connect()
        row = dict(conn.execute("SELECT * FROM pulse_audio_tracks WHERE id=5101").fetchone())
        conn.close()
        self.assertEqual(row["audio_url"], TARGET_AUDIO_URL)
        self.assertEqual(row["lifecycle_state"], music_authority.STATE_PURGE_PENDING)

    def test_a_legal_hold_purge_deletes_nothing(self):
        conn = _connect()
        conn.execute("UPDATE pulse_audio_tracks SET legal_hold=1 WHERE id=5101")
        conn.commit()
        conn.close()
        self._step_up()
        client = FakeObjectClient()
        with _FakeClientPatch(client):
            response = self._purge(5101)
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(client.deletes, [])

    def test_the_urls_are_blanked_so_nothing_points_at_deleted_bytes(self):
        self._step_up()
        with _FakeClientPatch(FakeObjectClient()):
            self._purge(5101)
        conn = _connect()
        row = dict(conn.execute("SELECT * FROM pulse_audio_tracks WHERE id=5101").fetchone())
        conn.close()
        self.assertEqual(row["lifecycle_state"], music_authority.STATE_PURGED)
        self.assertEqual(row["audio_url"] or "", "")
        self.assertEqual(row["cover_art_url"] or "", "")

    def test_the_audit_row_outlives_the_bytes(self):
        """The whole point of a separate audit table: the evidence survives."""
        self._step_up()
        with _FakeClientPatch(FakeObjectClient()):
            self._purge(5101)
        conn = _connect()
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM music_takedown_audit WHERE track_id=5101 ORDER BY action_id"
        ).fetchall()]
        conn.close()
        self.assertTrue(rows)
        purge_rows = [row for row in rows if row["action"] == music_authority.ACTION_PURGE]
        self.assertEqual(len(purge_rows), 1)
        self.assertEqual(purge_rows[0]["new_state"], music_authority.STATE_PURGED)
        self.assertEqual(purge_rows[0]["reason_code"], "COPYRIGHT")
        self.assertTrue(purge_rows[0]["reason_note"])


if __name__ == "__main__":
    unittest.main()
