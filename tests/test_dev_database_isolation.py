"""The suite must not write into the developer's own database.

`services.db.connect()` falls back to `LOCAL_SQLITE_FILE` — the relative string
`"coinpilotx.db"` — whenever `DATABASE_URL` is unset. Every test that reaches a raw
connection without pinning a database therefore used to write into the repository
working directory, and three UNDX suites did exactly that: a thousand phantom embedding
calls and 1.29M phantom tokens had accumulated in `undx_cost_ledger` before anyone
checked, plus `chat` rows for four providers.

That mattered from the moment the embedding budget guard began reading its month-to-date
out of that table. Test residue then becomes spend the guard counts, and a test asserting
that a call is *allowed* turns into a test of how many times the suite has been run —
green until the accumulated total crosses the ceiling, at which point it fails for a
reason nothing in the test mentions.

The fix is the session fixture in `conftest.py`. This file is what keeps it honest: the
fixture is a single assignment with no observable effect on any passing test, so without
these assertions it could be deleted, or have its `finally` restore moved above the
`yield`, and the whole suite would stay green while quietly writing to the repo again.
"""

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from services import db as platform_db
from services import undx_cost

#: The repository root, which is where a relative fallback path resolves to when pytest
#: is invoked from there — the normal case, and the one that did the damage.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TheFallbackPathIsNotTheRepository(unittest.TestCase):
    """Where an unconfigured connection lands."""

    def test_the_fallback_is_absolute(self):
        """A relative path is the defect itself, not merely a symptom of it.

        `"coinpilotx.db"` resolves against the *current working directory*, so the same
        code writes to a different file depending on where pytest was invoked from —
        which is also why the pollution was hard to attribute. An absolute path can at
        least be pointed somewhere harmless.
        """
        self.assertTrue(os.path.isabs(platform_db.LOCAL_SQLITE_FILE),
                        f"fallback is relative: {platform_db.LOCAL_SQLITE_FILE!r}")

    def test_the_fallback_is_outside_the_repository(self):
        """Absolute is not sufficient; it must also not be the developer's file.

        Compared by resolved path rather than by basename, because the fixture keeps the
        name `coinpilotx.db` on purpose — a temp file called something else would make
        any error message about it harder to recognise.
        """
        resolved = os.path.realpath(platform_db.LOCAL_SQLITE_FILE)
        self.assertFalse(resolved.startswith(os.path.realpath(REPO_ROOT) + os.sep),
                         f"the suite would write inside the repo: {resolved}")

    def test_an_unconfigured_connection_actually_uses_it(self):
        """Read the path *through* `connect()`, not just off the module.

        Asserting on the attribute alone would keep passing if `connect()` stopped
        consulting it — if the fallback were inlined, say, or resolved once at import
        into a constant. So this drives a real connection with the environment cleared,
        which is the exact state the polluting suites ran in, and asks SQLite itself
        which file it opened.
        """
        with patch.dict(os.environ, {}, clear=True):
            connection = platform_db.connect()
            try:
                row = connection.execute("PRAGMA database_list").fetchall()
            finally:
                connection.close()

        opened = os.path.realpath([r[2] for r in row if r[1] == "main"][0])
        self.assertEqual(opened, os.path.realpath(platform_db.LOCAL_SQLITE_FILE))
        self.assertFalse(opened.startswith(os.path.realpath(REPO_ROOT) + os.sep))


class TheGuardCanFail(unittest.TestCase):
    """The pairing that makes the assertions above evidence.

    Everything above passes while the fixture is installed, and a check that has never
    been seen to fail is a comment. These two reproduce the pre-fixture behaviour by
    restoring the relative path for the duration of one call, and assert that the same
    assertions then catch it — so the tests are demonstrably sensitive to the thing they
    are protecting, not merely true.
    """

    def test_restoring_the_relative_path_is_detected(self):
        with patch.object(platform_db, "LOCAL_SQLITE_FILE", "coinpilotx.db"):
            self.assertFalse(os.path.isabs(platform_db.LOCAL_SQLITE_FILE))
            with patch.dict(os.environ, {}, clear=True), \
                    patch("sqlite3.connect") as fake_connect:
                platform_db.connect()
        self.assertEqual(fake_connect.call_args.args[0], "coinpilotx.db",
                         "without the fixture this is the file that gets written")

    def test_the_ledger_is_where_the_consequence_lands(self):
        """Name the specific table, because that is what made this load-bearing.

        A generic "the suite writes somewhere" finding is easy to defer. `undx_cost.record`
        reaching `undx_cost_ledger` in the repo is the one that feeds the embedding budget
        guard, so it gets its own assertion.
        """
        with tempfile.TemporaryDirectory() as scratch:
            decoy = os.path.join(scratch, "coinpilotx.db")
            with patch.object(platform_db, "LOCAL_SQLITE_FILE", decoy):
                with patch.dict(os.environ, {}, clear=True):
                    undx_cost.reset_for_tests()
                    undx_cost.record({"provider": "perplexity",
                                      "call_kind": "embedding",
                                      "input_tokens": 1_000})
                    undx_cost.reset_for_tests()

            connection = sqlite3.connect(decoy)
            try:
                total = connection.execute(
                    f"SELECT SUM(input_tokens) FROM {undx_cost.LEDGER_TABLE} "
                    "WHERE call_kind = 'embedding'").fetchone()[0]
            finally:
                connection.close()

        self.assertEqual(total, 1_000,
                         "an unconfigured record() lands in whatever LOCAL_SQLITE_FILE "
                         "names — which is why it must not name the developer's file")
        undx_cost.reset_for_tests()


if __name__ == "__main__":
    unittest.main()
