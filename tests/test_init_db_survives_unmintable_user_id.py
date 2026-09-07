"""`init_db` must finish the schema even when a user row defeats a migration.

`bot._init_db_impl` is this repo's entire schema mechanism — there is no
migration framework, so ~586 tables are created imperatively in that one
function. Anything that raises partway through does not fail loudly; it silently
truncates the schema at that line and leaves everything below it uncreated.

`pulse_id_service.ensure_schema` sits about 500 lines into that function and
backfills every row of `users`. It could not mint an identity for a non-positive
id, and `ORDER BY user_id ASC` sorts those first, so it raised on the very first
row. A boot against such a database produced **49 tables instead of 586** — the
users table existed, so the failure read as a scattering of unrelated
"no such table" errors rather than as a failed migration.

Production has no such row today; the local dev database has two, left by old
smoke-test fixtures using Telegram-shaped ids. This test pins the property that
matters either way: one unusable row costs at most that row's pulse_id, never
the schema.

Run: .venv/bin/python -m pytest tests/test_init_db_survives_unmintable_user_id.py
"""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="init_db_unmintable_")
os.close(_HANDLE)

# The row has to be in place before bot's import-time init_db runs, so the
# database is seeded here rather than in setUp.
_seed = sqlite3.connect(_DB_PATH)
_seed.execute(
    "CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT, "
    "display_name TEXT, email TEXT, signup_time TEXT)"
)
_seed.executemany(
    "INSERT INTO users(user_id, username) VALUES (?, ?)",
    [(-920871340, "tg_group"), (-910251359, "tg_channel"), (4242, "real_account")],
)
_seed.commit()
_seed.close()

os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
# Force the synchronous path: the async one runs init_db on a daemon thread that
# logs and discards the failure, which would hide the truncation from this test.
os.environ["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"

import bot  # noqa: E402


# Defined well below the pulse_id backfill in _init_db_impl, so none of these
# survive an abort at that point.
TABLES_CREATED_AFTER_THE_BACKFILL = [
    "i18n_missing_translations",
    "user_security_events",
    "pulse_posts",
    "enterprise_leads",
    "livestream_access",
]


def _tables():
    conn = sqlite3.connect(_DB_PATH)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        conn.close()
    return {row[0] for row in rows}


class InitDbCompletesTest(unittest.TestCase):
    def test_importing_bot_did_not_abort_the_migration(self):
        self.assertTrue(bot.INIT_DB_COMPLETED, "init_db never reached its completion flag")

    def test_tables_defined_after_the_backfill_still_exist(self):
        missing = [t for t in TABLES_CREATED_AFTER_THE_BACKFILL if t not in _tables()]
        self.assertEqual(
            missing, [],
            "the schema was truncated at the pulse_id backfill; everything defined "
            "below that line is missing")

    def test_the_full_schema_is_present_rather_than_a_truncated_prefix(self):
        # The abort left 49; a complete pass builds several hundred. The floor is
        # deliberately loose because the real signal is the order of magnitude.
        self.assertGreater(
            len(_tables()), 400,
            "far too few tables for a completed migration")


class BackfillOutcomeTest(unittest.TestCase):
    def test_the_real_account_was_still_backfilled(self):
        conn = sqlite3.connect(_DB_PATH)
        try:
            row = conn.execute("SELECT pulse_id FROM users WHERE user_id=4242").fetchone()
        finally:
            conn.close()
        self.assertEqual(
            row[0], "PLS-004242",
            "skipping the unusable rows must not cost the usable ones their identity; "
            "the abort used to leave every account unbackfilled")

    def test_the_unmintable_rows_are_left_null_rather_than_guessed(self):
        conn = sqlite3.connect(_DB_PATH)
        try:
            rows = conn.execute(
                "SELECT user_id, pulse_id FROM users WHERE user_id<=0").fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 2)
        for user_id, pulse_id in rows:
            self.assertIsNone(pulse_id, f"user_id={user_id} was given an invented identity")


class PulseIdForUserTest(unittest.TestCase):
    """The same root cause on the request path rather than the boot path.

    `canonical_pulse_id` raising on a non-positive id also defeated
    `bot.pulse_id_for_user`, whose `except` arm called it a second time — so the
    fallback re-raised the error it existed to absorb, and both arms failed.
    Tested here because this module already pays for the `bot` import.
    """

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT, pulse_id TEXT)")
        self.addCleanup(self.conn.close)

    def test_a_real_account_still_gets_its_identity(self):
        self.conn.execute("INSERT INTO users(user_id, username) VALUES (77, 'real')")
        self.assertEqual(bot.pulse_id_for_user(self.conn.cursor(), 77), "PLS-000077")

    def test_a_non_positive_id_returns_empty_rather_than_raising(self):
        self.conn.execute("INSERT INTO users(user_id, username) VALUES (-920871340, 'tg_group')")
        self.assertEqual(bot.pulse_id_for_user(self.conn.cursor(), -920871340), "")

    def test_the_fallback_arm_survives_a_broken_cursor(self):
        """What the `except` is actually for: a database failure, not a bad id."""

        class BrokenCursor:
            def execute(self, *_args, **_kwargs):
                raise sqlite3.OperationalError("no such column: pulse_id")

        self.assertEqual(bot.pulse_id_for_user(BrokenCursor(), 77), "PLS-000077")
        self.assertEqual(bot.pulse_id_for_user(BrokenCursor(), -1), "")


if __name__ == "__main__":
    unittest.main()
