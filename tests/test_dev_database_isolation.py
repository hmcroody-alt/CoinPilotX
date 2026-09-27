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

`connect()` has a *second* leg, though, and the classes below the first two are about
that one. It reads `DATABASE_URL` before it reads `LOCAL_SQLITE_FILE`, so the fixture only
ever protected the case where that variable is unset — and it does not stay unset.
Importing `bot` sets it, to a relative path, from `.env.local`. Every test in the two
classes above clears the environment, which erases the variable and makes them
structurally blind to it: they were all green on the day 124MB `coinpilotx.db` took four
rows and a new mtime from a run that was supposed to be sandboxed.
"""

import os
import pathlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from services import db as platform_db
from services import undx_cost
from services.command_center_worker import config as local_env
from tests import conftest as root_conftest

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


def _database_connect_opens():
    """The file `connect()` actually opens under the *ambient* environment.

    Asked of SQLite rather than of the module, and deliberately without
    `patch.dict(os.environ, {}, clear=True)` — clearing the environment is what made the
    two classes above unable to see this leg at all.
    """
    connection = platform_db.connect()
    try:
        rows = connection.execute("PRAGMA database_list").fetchall()
    finally:
        connection.close()
    return os.path.realpath([r[2] for r in rows if r[1] == "main"][0])


class TheConfiguredUrlIsNotTheRepositoryEither(unittest.TestCase):
    """The `DATABASE_URL` leg, under the environment the suite really runs in.

    Asserted as a property of whatever the variable holds, not as equality with the
    conftest's own path, because ~250 test modules pin a database of their own at module
    scope and all of that happens during *collection*. In a run that shares a process the
    variable therefore holds whichever module was collected last, which is legitimate —
    what must be true of every one of those values is that it is set, and that it is not
    in this checkout.
    """

    def test_the_variable_is_set_and_points_outside_the_repository(self):
        """Set at all is half the point: absent is what let `bot` fill it in.

        `_load_local_env_file` assigns only keys missing from `os.environ`, so presence
        is what makes the capture impossible. Judged by the conftest's own predicate so
        that the test and the pin cannot disagree about what "inside the repository"
        means.
        """
        value = os.environ.get("DATABASE_URL")
        self.assertTrue(value, "DATABASE_URL is unset: bot's .env.local can capture it")
        self.assertFalse(root_conftest._would_write_inside_the_repository(value),
                         f"the configured database is inside the repo: {value}")

    def test_the_two_legs_of_connect_converge_on_one_file(self):
        """Why the pin names the fallback file rather than a second temp file.

        A suite that clears the environment, or sets `DATABASE_URL` empty — and a dozen
        do, via `setdefault("DATABASE_URL", "")` — drops through to `LOCAL_SQLITE_FILE`.
        Pointing both at the same database is what keeps those suites landing exactly
        where they landed before this pin existed.
        """
        self.assertEqual(os.path.realpath(root_conftest._FALLBACK_DB_PATH),
                         os.path.realpath(platform_db.LOCAL_SQLITE_FILE))

    def test_an_ambient_connection_actually_uses_it(self):
        """Read it through SQLite, because the variable is only evidence of intent."""
        opened = _database_connect_opens()
        self.assertFalse(opened.startswith(os.path.realpath(REPO_ROOT) + os.sep),
                         f"an ambient connection writes inside the repo: {opened}")

    def test_the_sqlalchemy_engine_is_off_the_relative_path_too(self):
        """The gap the session fixture documented as out of its reach.

        `services.db` resolves `ENGINE_URL` once, at import, so a fixture can never move
        it; only a variable set before that import can. It is asserted here because it is
        the same defect — a session opened against `sqlite:///coinpilotx.db` writes to the
        developer's file exactly as a raw connection does. No module-scope pin can move it
        afterwards, so unlike the variable this one is settled before collection begins.
        """
        self.assertFalse(
            root_conftest._would_write_inside_the_repository(platform_db.ENGINE_URL),
            f"the SQLAlchemy engine is bound inside the repo: {platform_db.ENGINE_URL}")


class TheConfiguredUrlGuardCanFail(unittest.TestCase):
    """Reproduce the capture, so the assertions above are evidence and not decoration.

    Every one of them holds while the pin is installed, which on its own proves nothing
    about the pin's necessity — the same gap the two classes at the top of this file call
    out about the fixture.
    """

    def test_patching_the_constant_alone_does_not_save_you(self):
        """The precise reason the session fixture was not enough.

        `LOCAL_SQLITE_FILE` stays pinned at the temp file for the whole of this test. Only
        `DATABASE_URL` carries the post-`bot` value — the relative URL `.env.local` supplies
        — and `connect()` still opens the developer's database, because it consults the
        variable *first* and never reaches the constant. `sqlite3.connect` is faked so that
        proving this does not itself create the file.
        """
        self.assertTrue(os.path.isabs(platform_db.LOCAL_SQLITE_FILE))
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///coinpilotx.db"}), \
                patch("sqlite3.connect") as fake_connect:
            platform_db.connect()
        self.assertEqual(fake_connect.call_args.args[0], "coinpilotx.db",
                         "the fixture's patched constant was never consulted")

    def test_a_local_env_file_captures_an_absent_variable_and_not_a_present_one(self):
        """The mechanism itself, driven through a real loader rather than a paraphrase.

        `services.command_center_worker.config._load_local_env_file` is the same
        assign-only-if-absent loader `bot` runs at module scope, over the same
        `.env.local`, and it imports in milliseconds instead of booting the monolith. Fed
        a file carrying the relative URL it takes the variable when nothing holds it —
        which is the pre-fix state and the whole defect — and is inert when the pin does.
        """
        with tempfile.TemporaryDirectory() as scratch:
            env_local = pathlib.Path(scratch, ".env.local")
            env_local.write_text("DATABASE_URL=sqlite:///coinpilotx.db\n", encoding="utf-8")

            pinned = os.environ["DATABASE_URL"]
            with patch.dict(os.environ, {}):
                local_env._load_local_env_file(env_local)
                self.assertEqual(os.environ["DATABASE_URL"], pinned,
                                 "a present DATABASE_URL must not be overwritten")

            with patch.dict(os.environ, {}):
                del os.environ["DATABASE_URL"]
                local_env._load_local_env_file(env_local)
                captured = os.environ["DATABASE_URL"]
            self.assertEqual(captured, "sqlite:///coinpilotx.db",
                             "without the pin the loader takes the variable")
            self.assertEqual(os.environ["DATABASE_URL"], pinned)


class ThePinLeavesADeliberateDatabaseAlone(unittest.TestCase):
    """What the pin must *not* do.

    It replaces a value that would write into this checkout. A developer running the suite
    against a throwaway PostgreSQL, or a suite asking for `:memory:`, has chosen a database
    and silently redirecting it to a temp file would be its own bug — a Postgres-only
    dialect crash would then never reproduce locally.
    """

    def test_it_replaces_only_what_lands_in_the_repository(self):
        pins = {
            None: True,
            "": True,
            "sqlite:///coinpilotx.db": True,
            "sqlite:///./coinpilotx.db": True,
            f"sqlite:///{os.path.join(REPO_ROOT, 'coinpilotx.db')}": True,
            "sqlite:///:memory:": False,
            "file::memory:?cache=shared": False,
            "postgresql://localhost/pulsesoc": False,
            "postgres://localhost/pulsesoc": False,
            "sqlite:////tmp/pulsesoc-test/coinpilotx.db": False,
        }
        for url, expected in pins.items():
            with self.subTest(url=url):
                self.assertEqual(
                    root_conftest._would_write_inside_the_repository(url), expected)


if __name__ == "__main__":
    unittest.main()
