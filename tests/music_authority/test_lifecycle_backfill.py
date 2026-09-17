"""A track an admin already removed must not read as ACTIVE.

`lifecycle_state` was added to a table with ten years of rows in it. Those rows
carry their state in the legacy trio -- `active`, `safety_status`, `removed_at` --
so `init_db()` has a backfill that reads the trio and sets `TAKEN_DOWN`.

It never ran. It was keyed on `COALESCE(lifecycle_state,'')=''`, and the column is
added with `DEFAULT 'ACTIVE'`; both Postgres and SQLite apply a column default to
the rows that already exist, so nothing was ever left NULL for the backfill to
find. Production carried three tracks reading `ACTIVE` that a human had removed
through `/api/admin/pulse/music/<id>/remove`.

The visible consequence is in the admin menu, which is computed from
`lifecycle_state`: it offered "Take down" on a track that was already down and
withheld "Restore", so the one action an operator would actually want was the one
action the UI would not give them. Restoring such a track required taking it down
first.

What is pinned here is the backfill running at all, and then three ways it could
overcorrect: demoting a state that is further along than `TAKEN_DOWN`, undoing a
restore, and touching a track that was never removed.
"""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="music_lifecycle_backfill_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402


def _reinit():
    os.environ["FORCE_INIT_DB"] = "1"
    try:
        bot.init_db()
    finally:
        os.environ.pop("FORCE_INIT_DB", None)


class LifecycleBackfillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bot.init_db()

    def setUp(self):
        self.conn = sqlite3.connect(_DB_PATH)
        self.conn.execute("DELETE FROM pulse_audio_tracks")
        self.conn.commit()

    def tearDown(self):
        self.conn.execute("DELETE FROM pulse_audio_tracks")
        self.conn.commit()
        self.conn.close()

    def _insert(self, track_id, *, lifecycle_state, safety_status, active, removed_at=""):
        self.conn.execute(
            "INSERT INTO pulse_audio_tracks (id, title, artist, uploader_user_id, audio_url, "
            "lifecycle_state, safety_status, active, approved_by_admin, removed_at, created_at) "
            "VALUES (?, ?, 'PulseSoc Music', 15, 'https://cdn.example/a.mp3', ?, ?, ?, ?, ?, "
            "'2026-09-17T00:00:00')",
            (
                track_id,
                f"Track {track_id}",
                lifecycle_state,
                safety_status,
                active,
                1 if active else 0,
                removed_at,
            ),
        )
        self.conn.commit()

    def _state(self, track_id):
        return self.conn.execute(
            "SELECT lifecycle_state FROM pulse_audio_tracks WHERE id=?", (track_id,)
        ).fetchone()[0]

    def test_a_legacy_removed_track_is_corrected_to_taken_down(self):
        """The defect, as production had it: ACTIVE beside safety_status='removed'."""
        self._insert(801, lifecycle_state="ACTIVE", safety_status="removed", active=0)
        _reinit()
        self.assertEqual(
            self._state(801),
            "TAKEN_DOWN",
            "a track an admin removed still reads ACTIVE -- the admin menu will "
            "offer Take down on it and refuse to offer Restore",
        )

    def test_a_removed_at_stamp_alone_is_enough(self):
        """The two legacy writers do not agree on which column they stamp."""
        self._insert(
            802,
            lifecycle_state="ACTIVE",
            safety_status="pending",
            active=0,
            removed_at="2026-01-02T03:04:05",
        )
        _reinit()
        self.assertEqual(self._state(802), "TAKEN_DOWN")

    def test_an_ordinary_live_track_is_left_alone(self):
        """Positive control. Without it, `SET lifecycle_state='TAKEN_DOWN'` passes."""
        self._insert(803, lifecycle_state="ACTIVE", safety_status="approved", active=1)
        _reinit()
        self.assertEqual(
            self._state(803), "ACTIVE", "the backfill took down a track nobody removed"
        )

    def test_a_state_further_along_is_not_demoted(self):
        """QUARANTINED and PURGED also carry safety_status='removed'.

        They are what the legacy trio looks like for every non-ACTIVE state, so a
        backfill keyed on the trio alone would walk a purged track backwards to
        TAKEN_DOWN on the next boot -- and TAKEN_DOWN is restorable, which PURGED
        deliberately is not.
        """
        for track_id, state in ((804, "QUARANTINED"), (805, "PURGE_PENDING"), (806, "PURGED")):
            with self.subTest(state=state):
                self._insert(
                    track_id,
                    lifecycle_state=state,
                    safety_status="removed",
                    active=0,
                    removed_at="2026-01-02T03:04:05",
                )
        _reinit()
        self.assertEqual(self._state(804), "QUARANTINED")
        self.assertEqual(self._state(805), "PURGE_PENDING")
        self.assertEqual(self._state(806), "PURGED")

    def test_a_restore_is_not_undone_by_the_next_boot(self):
        """The backfill runs on every init_db, so it has to be a fixed point.

        Restore writes the ACTIVE legacy columns -- safety_status='approved' and an
        empty removed_at -- which is exactly what stops the row matching again.
        """
        self._insert(807, lifecycle_state="ACTIVE", safety_status="removed", active=0)
        _reinit()
        self.assertEqual(self._state(807), "TAKEN_DOWN")

        self.conn.execute(
            "UPDATE pulse_audio_tracks SET lifecycle_state='ACTIVE', safety_status='approved', "
            "active=1, approved_by_admin=1, removed_at='' WHERE id=807"
        )
        self.conn.commit()

        _reinit()
        _reinit()
        self.assertEqual(
            self._state(807), "ACTIVE", "the backfill took a restored track back down"
        )

    def test_the_correction_is_stable_across_repeated_boots(self):
        self._insert(808, lifecycle_state="ACTIVE", safety_status="removed", active=0)
        _reinit()
        _reinit()
        _reinit()
        self.assertEqual(self._state(808), "TAKEN_DOWN")


if __name__ == "__main__":
    unittest.main()
