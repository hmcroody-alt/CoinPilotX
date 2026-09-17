"""`init_db()` must not put anything into the music catalogue.

It used to seed three tracks -- "PulseSoc Neon Rise", "Trust Signal", "Creator
Glow Loop" -- with `INSERT OR IGNORE INTO pulse_audio_tracks (title, artist, ...)`,
which reads like a de-duplicator and is not one. `OR IGNORE` -- and the
`ON CONFLICT DO NOTHING` that `services/db._translate_sql` rewrites it to for
Postgres -- suppresses a *constraint violation*. There is no unique index on
(title, artist), so no conflict ever arose and every call appended three more
rows. `init_db()` runs once per process, not once per deploy: gunicorn workers,
worker restarts, and six Railway services all count. Production reached **21,612
copies of three titles** -- 99.3% of a 21,760-row catalogue.

The first fix was an existence check, which stopped the growth. Measuring
production afterwards showed the seed should not exist at all:

  * the INSERT named no `audio_url` column, so not one of the 21,612 rows was
    playable, while all 148 real user uploads were. The catalogue users searched
    was almost entirely entries that play nothing;
  * the repair UPDATE beside it matched `WHERE title=? AND artist=?` with no
    ownership filter and forced `approved_by_admin=1, active=1` -- so naming an
    upload after a default sound would have walked it past moderation on the
    next boot.

So the guarantee pinned here is inverted from the original: `init_db()` creates
no catalogue rows, adds none on re-run, and does not touch a row a user uploaded
-- including one named exactly like a retired default sound.

Asserted on row counts and on the stored columns rather than on the SQL, because
the defect was precisely that the SQL *looked* right.
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

# The titles the retired seed used. Kept as data so the "nothing named like a
# former default sound gets special treatment" check names the real strings.
RETIRED_SEED_TITLES = (
    ("PulseSoc Neon Rise", "CoinPlotXAI"),
    ("Trust Signal", "PulseSoc Studio"),
    ("Creator Glow Loop", "CoinPlotXAI"),
)


def _query(sql, params=()):
    conn = sqlite3.connect(_DB_PATH)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _total():
    return _query("SELECT COUNT(*) FROM pulse_audio_tracks")[0][0]


def _reinit():
    """Force a real second pass.

    `init_db()` short-circuits on `INIT_DB_COMPLETED`, which is exactly why the
    original bug survived review: inside one process the seed runs once and looks
    idempotent. What actually happened in production is a *new process* running
    init against a database that already had the rows, and `FORCE_INIT_DB` is the
    supported way to reproduce that without spawning one.
    """
    os.environ["FORCE_INIT_DB"] = "1"
    try:
        bot.init_db()
    finally:
        os.environ.pop("FORCE_INIT_DB", None)


class SeedRemovalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bot.init_db()

    def test_a_fresh_database_has_an_empty_music_catalogue(self):
        self.assertEqual(
            _total(),
            0,
            "init_db() seeded the music catalogue -- every seeded row is an "
            "unplayable entry served to real users in search and the Reels picker",
        )

    def test_no_seeded_title_exists(self):
        """Named directly, so re-adding the old block by name fails here."""
        for title, artist in RETIRED_SEED_TITLES:
            with self.subTest(title=title):
                rows = _query(
                    "SELECT id FROM pulse_audio_tracks WHERE title=? AND artist=?",
                    (title, artist),
                )
                self.assertEqual(rows, [], f"{title!r} was seeded by init_db()")

    def test_nothing_is_linked_into_trending_sounds(self):
        """The seed also wrote `pulse_trending_sounds`.

        That table is what the Reels "trending" rail reads, so a seed row there
        is more visible than one in the catalogue: production had 1,539 of its
        1,540 trending rows pointing at seeded tracks and *none* at a real one.
        """
        self.assertEqual(
            _query("SELECT COUNT(*) FROM pulse_trending_sounds")[0][0],
            0,
            "init_db() populated trending sounds",
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

    def test_a_user_upload_survives_init_untouched(self):
        """Positive control, and the moderation-bypass guard in one.

        The row is inserted pending and inactive under a *retired seed title*:
        the shape the old repair UPDATE would have force-approved, since it
        matched on title and artist alone with no check on who uploaded it.
        Anything that re-introduces a title-keyed UPDATE fails here.
        """
        title, artist = RETIRED_SEED_TITLES[1]
        conn = sqlite3.connect(_DB_PATH)
        conn.execute(
            "INSERT INTO pulse_audio_tracks "
            "(title, artist, uploader_user_id, audio_url, safety_status, "
            " approved_by_admin, active, license_type, created_at) "
            "VALUES (?, ?, 7, 'https://cdn.example/test.mp3', 'pending', 0, 0, '', '2026-09-17T00:00:00')",
            (title, artist),
        )
        conn.commit()
        conn.close()

        try:
            _reinit()

            row = _query(
                "SELECT safety_status, approved_by_admin, active, license_type, uploader_user_id "
                "FROM pulse_audio_tracks WHERE title=? AND artist=?",
                (title, artist),
            )
            self.assertEqual(len(row), 1, "init_db() added a row beside the upload")
            status, approved, active, license_type, uploader = row[0]
            self.assertEqual(status, "pending", "init_db() advanced a pending upload")
            self.assertEqual(approved, 0, "init_db() approved an upload it does not own")
            self.assertEqual(active, 0, "init_db() activated an unapproved upload")
            self.assertEqual(license_type, "", "init_db() wrote licensing onto a user upload")
            self.assertEqual(uploader, 7)
        finally:
            conn = sqlite3.connect(_DB_PATH)
            conn.execute(
                "DELETE FROM pulse_audio_tracks WHERE title=? AND artist=?", (title, artist)
            )
            conn.commit()
            conn.close()


if __name__ == "__main__":
    unittest.main()
