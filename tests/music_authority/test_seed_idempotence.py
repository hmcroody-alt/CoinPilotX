"""`init_db()` was inserting three tracks into the catalogue on every call.

The seed block used `INSERT OR IGNORE INTO pulse_audio_tracks (title, artist, ...)`,
which reads like a de-duplicator and is not one. `OR IGNORE` -- and the
`ON CONFLICT DO NOTHING` that `services/db._translate_sql` rewrites it to for
Postgres -- suppresses a *constraint violation*. There is no unique index on
(title, artist), so no conflict ever arose and every call appended three more
rows.

`init_db()` runs once per process, not once per deploy: gunicorn workers, worker
restarts, and six Railway services all count. Production had reached **21,603
copies of three titles** -- 99.3% of a 21,751-row music catalogue -- each one
`approved_by_admin=1, active=1`, and therefore live in search results, trending
sounds and the Reels audio picker for real users.

Nothing in the product noticed. The rows were individually valid, every read path
filtered them correctly, and the only visible symptom was a music library full of
the same three songs -- which looks like a seeding choice rather than a leak.

The guarantee pinned here is the one the original code only appeared to make:
calling `init_db()` again does not change the row count. Asserted on the count
rather than on the SQL, because the defect was precisely that the SQL *looked*
right.
"""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="music_seed_idempotence_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402

SEEDED_TITLES = ("PulseSoc Neon Rise", "Trust Signal", "Creator Glow Loop")


def _counts():
    conn = sqlite3.connect(_DB_PATH)
    try:
        rows = conn.execute(
            "SELECT title, COUNT(*) FROM pulse_audio_tracks WHERE title IN (?, ?, ?) GROUP BY title",
            SEEDED_TITLES,
        ).fetchall()
    finally:
        conn.close()
    return dict(rows)


def _total():
    conn = sqlite3.connect(_DB_PATH)
    try:
        return conn.execute("SELECT COUNT(*) FROM pulse_audio_tracks").fetchone()[0]
    finally:
        conn.close()


def _reinit():
    """Force a real second pass.

    `init_db()` short-circuits on `INIT_DB_COMPLETED`, which is exactly why this
    bug survived: inside one process the seed runs once and looks idempotent. The
    thing that actually happened in production is a *new process* running init
    against a database that already had the rows, and `FORCE_INIT_DB` is the
    supported way to reproduce that without spawning one.
    """
    os.environ["FORCE_INIT_DB"] = "1"
    try:
        bot.init_db()
    finally:
        os.environ.pop("FORCE_INIT_DB", None)


class SeedIdempotenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bot.init_db()

    def test_the_default_sounds_are_seeded_once(self):
        """Positive control: the seed still does its job on a fresh database."""
        counts = _counts()
        self.assertEqual(set(counts), set(SEEDED_TITLES), "all three seeds should exist")
        for title, count in counts.items():
            self.assertEqual(count, 1, f"{title} should be seeded exactly once")

    def test_re_running_init_does_not_add_another_copy(self):
        """The defect, stated directly."""
        before = _counts()
        _reinit()
        after = _counts()
        self.assertEqual(
            after,
            before,
            "init_db() appended duplicate seed tracks -- this is the leak that "
            "grew the production catalogue to 21,603 copies of three songs",
        )

    def test_the_catalogue_does_not_grow_across_repeated_inits(self):
        """Counted on the whole table, so a future seed block is covered too."""
        before = _total()
        _reinit()
        _reinit()
        _reinit()
        self.assertEqual(
            _total(), before, "init_db() must not add rows to pulse_audio_tracks on re-run"
        )

    def test_an_existing_seed_row_is_still_repaired(self):
        """Skipping the INSERT must not skip the backfill beside it.

        The fix sits between an INSERT and an UPDATE that repairs older rows
        missing `license_type` / `mood` / `genre`. Returning early would have
        stopped that repair running -- trading a duplicate-row bug for a silent
        data-drift one.
        """
        conn = sqlite3.connect(_DB_PATH)
        conn.execute(
            "UPDATE pulse_audio_tracks SET license_type='', mood='', active=0 WHERE title=?",
            ("Trust Signal",),
        )
        conn.commit()
        conn.close()

        _reinit()

        conn = sqlite3.connect(_DB_PATH)
        row = conn.execute(
            "SELECT license_type, mood, active FROM pulse_audio_tracks WHERE title=?",
            ("Trust Signal",),
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "PulseSoc original work")
        self.assertEqual(row[1], "focused")
        self.assertEqual(row[2], 1)
        self.assertEqual(_counts()["Trust Signal"], 1, "and still exactly one row")


if __name__ == "__main__":
    unittest.main()
