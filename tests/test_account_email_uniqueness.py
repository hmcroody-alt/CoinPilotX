"""One address, one account -- and the two ways that was previously untrue.

`users` had no uniqueness on `email`. The only thing standing between the
platform and two accounts sharing an address was a check-then-insert in
`create_account`: a SELECT for an existing address, then an INSERT. Two
concurrent signups for the same address both pass the SELECT and both insert.

That is not a tidiness problem. Open Commerce §14 makes the email address the
anchor that ties a guest order to a person, so two accounts sharing an anchor
makes "whose order is this" unanswerable -- and unanswerable *retroactively*, for
orders already placed, which no amount of care in the claim flow can repair.

The second untruth is subtler and lives in the error path. Every hand-rolled
integrity check in this repo is written `except sqlite3.IntegrityError`, and a
PostgreSQL `UniqueViolation` is not one of those. Production is PostgreSQL. So
the local suite cannot distinguish a working uniqueness handler from one that
catches nothing at all, and `ProductionEngineCase` below exists specifically to
test the half of `is_unique_violation` that SQLite can never exercise.

Runs against a temp sqlite file so nothing touches coinpilotx.db.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="email_unique_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import account_email_uniqueness as email_unique  # noqa: E402
from services import db as db_service, schema_guard  # noqa: E402


def _fresh_users_table():
    """A `users` table with just the columns this invariant is about.

    Deliberately not `init_db()`: this suite is about one index and the states of
    one column, and building 550 tables to assert something about four rows makes
    the interesting part slower and no more true.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT, email TEXT)")
    return conn


class IndexShapeCase(unittest.TestCase):
    """What the index does and does not have an opinion about."""

    def setUp(self):
        schema_guard.reset_all()
        self.conn = _fresh_users_table()
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.close()
        schema_guard.reset_all()

    def _create(self):
        return email_unique.ensure_email_identity_index(self.cur)

    def test_it_is_created_on_a_clean_table(self):
        self.assertTrue(self._create())
        self.assertTrue(email_unique.index_exists(self.cur))

    def test_a_case_and_whitespace_variant_is_the_same_identity(self):
        """The application's notion of identity is `normalize_email`, which is
        `.strip().lower()`. A constraint on the raw column would enforce a
        *different* identity than every lookup in the codebase -- which is worse
        than no constraint, because it would permit exactly the duplicates the
        lookups then disagree about.
        """
        self._create()
        self.cur.execute("INSERT INTO users (email) VALUES ('Buyer@Example.COM')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.cur.execute("INSERT INTO users (email) VALUES ('  buyer@example.com ')")

    def test_blank_is_not_a_value(self):
        """Production has three rows with `email = ''` and one with NULL. NULLs are
        exempt from uniqueness automatically; `''` is not, so a non-partial index
        would have refused to build at all. "No address recorded" has to stay a
        legal state for any number of rows.
        """
        self._create()
        for _ in range(4):
            self.cur.execute("INSERT INTO users (email) VALUES ('')")
        for _ in range(4):
            self.cur.execute("INSERT INTO users (email) VALUES (NULL)")
        self.cur.execute("INSERT INTO users (email) VALUES ('   ')")
        self.cur.execute("SELECT COUNT(*) FROM users")
        self.assertEqual(self.cur.fetchone()[0], 9)

    def test_the_four_real_production_shapes_all_fit(self):
        """The exact rows live in production today, by user_id: three `''` (30, 28,
        34) and one NULL (42, the pulsedrop system account). Asserted as data
        rather than prose so this fails if someone narrows the predicate.
        """
        self.cur.execute(
            "INSERT INTO users (user_id, username, email) VALUES "
            "(30, 'iphone16qa_83885056', ''), (42, 'pulsedrop', NULL), "
            "(28, 'deleted-user-28-0bc14513', ''), (34, 'undxreleaseqa_85611099', '')"
        )
        self.assertEqual(self._create(), True,
                         "the index does not fit the rows production actually has")

    def test_a_second_call_issues_no_ddl(self):
        """`CREATE UNIQUE INDEX IF NOT EXISTS` takes a ShareLock on PostgreSQL even
        when the index is already there, and ShareLock conflicts with the
        RowExclusiveLock an INSERT needs -- the failure `services/schema_guard.py`
        was written for. So the steady state must issue no DDL at all.

        Checked by calling the undecorated function, since the decorator would
        otherwise short-circuit and this would prove nothing about the SQL.
        """
        raw = email_unique.ensure_email_identity_index.__wrapped_ddl__
        self.assertTrue(raw(self.cur))

        statements = []
        inner = self.cur

        class Recording:
            # A proxy rather than a patched method: `sqlite3.Cursor.execute` is
            # read-only, and a Mock would not actually run the SQL, so the second
            # call would not really be exercising the catalogue check.
            def execute(self, sql, *args):
                statements.append(str(sql))
                return inner.execute(sql, *args)

            def fetchone(self):
                return inner.fetchone()

            def fetchall(self):
                return inner.fetchall()

        self.assertTrue(raw(Recording()))
        self.assertTrue(statements, "the second call ran no SQL at all, so this proves nothing")
        self.assertFalse([s for s in statements if "CREATE" in s.upper()],
                         f"the second call issued DDL: {statements}")

    def test_existing_duplicates_block_it_and_are_named(self):
        """A failing `CREATE UNIQUE INDEX` inside a request's open transaction
        poisons that transaction on PostgreSQL, so the signup it was called from
        would fail for a reason having nothing to do with signup. It has to decline
        instead of trying.

        Which means the assertion cannot be the return value. Remove the collision
        check and `ensure_email_identity_index` still answers `False` -- before,
        because it declined; after, because the DDL it issued failed and the
        catch-all swallowed the exception. Those two are indistinguishable from
        outside and *identical* on SQLite. They differ only in production, where
        one of them has already poisoned the caller's transaction. An earlier
        version of this test asserted `assertFalse` and nothing else, and the
        mutation matrix duly reported `collision-check-removed` as surviving.

        So this asserts the two things that do differ: no DDL was attempted at all,
        and the log names which address is responsible -- because "the index is
        missing" is not actionable and "two accounts share this address" is.
        """
        self.cur.execute("INSERT INTO users (email) VALUES ('dupe@example.com')")
        self.cur.execute("INSERT INTO users (email) VALUES ('DUPE@example.com')")

        statements = []
        inner = self.cur

        class Recording:
            def execute(self, sql, *args):
                statements.append(str(sql))
                return inner.execute(sql, *args)

            def fetchone(self):
                return inner.fetchone()

            def fetchall(self):
                return inner.fetchall()

        with self.assertLogs(level="ERROR") as captured:
            self.assertFalse(
                email_unique.ensure_email_identity_index.__wrapped_ddl__(Recording()))

        self.assertTrue(statements, "no SQL ran at all, so this proves nothing")
        self.assertFalse(
            [s for s in statements if "CREATE" in s.upper()],
            "the DDL was attempted against data that cannot support it; on PostgreSQL "
            f"that poisons the caller's open transaction: {statements}")

        blocked = "\n".join(captured.output)
        self.assertIn("EMAIL_IDENTITY_INDEX_BLOCKED", blocked)
        self.assertIn("groups=1", blocked)
        self.assertIn("worst=2", blocked)
        self.assertIn(email_unique.INDEX_NAME, blocked)

        self.assertFalse(email_unique.index_exists(self.cur))
        self.assertEqual(email_unique.colliding_groups(self.cur), [("dupe@example.com", 2)])

    def test_a_refusal_is_not_cached(self):
        """`run_once_per_process` deliberately does not cache a False. A boot that
        found duplicates must retry once they are reconciled, rather than leaving
        the worker permanently convinced the index cannot exist.
        """
        self.cur.execute("INSERT INTO users (user_id, email) VALUES (1, 'dupe@example.com')")
        self.cur.execute("INSERT INTO users (user_id, email) VALUES (2, 'DUPE@example.com')")
        self.assertFalse(email_unique.ensure_email_identity_index(self.cur))
        self.cur.execute("DELETE FROM users WHERE user_id = 2")
        self.assertTrue(email_unique.ensure_email_identity_index(self.cur),
                        "the refusal was cached, so reconciling the duplicates changed nothing")

    def test_it_never_raises_into_a_boot_path(self):
        """Having no constraint is where the platform already was. Raising here
        would take down a signup path that works, which is strictly worse than
        declining to improve it.
        """
        self.conn.execute("DROP TABLE users")
        self.assertFalse(email_unique.ensure_email_identity_index(self.cur))


class ProductionEngineCase(unittest.TestCase):
    """The half of this that SQLite cannot test.

    `except sqlite3.IntegrityError` is the spelling used at all seven hand-rolled
    integrity sites in this repo, and it catches *nothing* on PostgreSQL, where a
    duplicate raises `psycopg2.errors.UniqueViolation`. A local suite cannot tell
    that apart from working code, so the predicate is tested against a stand-in
    that carries the wire-level signal instead of the class.
    """

    class _PgError(Exception):
        def __init__(self, code):
            super().__init__("duplicate key value violates unique constraint")
            self.pgcode = code

    class _PgErrorViaDiag(Exception):
        """Some psycopg2 paths expose the code only on `.diag.sqlstate`."""

        class _Diag:
            def __init__(self, code):
                self.sqlstate = code

        def __init__(self, code):
            super().__init__("duplicate key")
            self.diag = self._Diag(code)

    def test_a_postgres_unique_violation_is_recognised(self):
        self.assertTrue(db_service.is_unique_violation(self._PgError("23505")))
        self.assertTrue(db_service.is_unique_violation(self._PgErrorViaDiag("23505")))

    def test_the_sqlite_only_spelling_would_have_missed_it(self):
        """The bug this predicate exists to avoid, asserted directly: the obvious
        `except sqlite3.IntegrityError` does not catch the production exception.
        Without this test the fix looks like a stylistic preference.
        """
        self.assertFalse(isinstance(self._PgError("23505"), sqlite3.IntegrityError))

    def test_a_sqlite_unique_violation_is_recognised(self):
        self.assertTrue(db_service.is_unique_violation(
            sqlite3.IntegrityError("UNIQUE constraint failed: index 'ux_users_email_identity'")))

    def test_other_constraints_are_not_mistaken_for_duplicates(self):
        """A foreign-key or not-null violation is a different bug and must not be
        answered with "an account already exists".
        """
        self.assertFalse(db_service.is_unique_violation(
            sqlite3.IntegrityError("NOT NULL constraint failed: users.email")))
        self.assertFalse(db_service.is_unique_violation(
            sqlite3.IntegrityError("FOREIGN KEY constraint failed")))
        self.assertFalse(db_service.is_unique_violation(self._PgError("23503")))  # FK
        self.assertFalse(db_service.is_unique_violation(self._PgError("23502")))  # NOT NULL
        self.assertFalse(db_service.is_unique_violation(ValueError("nope")))


class SignupRaceCase(unittest.TestCase):
    """What a caller is told when it loses the race the precheck cannot win."""

    def test_the_race_answers_exactly_as_the_precheck_does(self):
        """Not "the same wording" -- the same object. Two literals could drift, and
        a caller who could tell the two branches apart would learn that its request
        raced another, which is to say that the address is being registered right
        now. That is the same oracle §16 forbids, arriving by a side channel.
        """
        source = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "bot.py"), encoding="utf-8").read()
        self.assertEqual(
            source.count('"An account already exists for that contact method."'), 1,
            "the duplicate message is spelled out more than once, so the branches can drift")
        self.assertEqual(source.count("return None, ACCOUNT_ALREADY_EXISTS_MESSAGE"), 2,
                         "expected exactly the precheck branch and the race branch")

    def test_the_race_is_not_told_to_try_again(self):
        """The generic handler says "try again shortly", which for a uniqueness
        violation is false -- retrying fails forever, and the user would keep doing
        it having been told to.
        """
        self.assertIsNotNone(getattr(bot, "ACCOUNT_ALREADY_EXISTS_MESSAGE", None))
        self.assertNotIn("try again", bot.ACCOUNT_ALREADY_EXISTS_MESSAGE.lower())

    def test_the_precheck_uses_the_index_expression(self):
        """If the precheck asks a narrower question than the index enforces, an
        ordinary duplicate arrives as a caught exception instead of a civil answer.
        They have to be the same expression, not two expressions that agree today.
        """
        source = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "bot.py"), encoding="utf-8").read()
        self.assertIn("account_email_uniqueness.EMAIL_IDENTITY_EXPRESSION", source)
        self.assertIn("account_email_uniqueness.EMAIL_PRESENT_PREDICATE", source)

    def test_the_index_is_ensured_before_the_insert(self):
        source = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "bot.py"), encoding="utf-8").read()
        ensure = source.index("account_email_uniqueness.ensure_email_identity_index(cur)")
        insert = source.index("INSERT INTO users (", ensure)
        self.assertLess(ensure, insert)


class EndToEndSignupCase(unittest.TestCase):
    """`create_account` against the real schema, which is the only place the index,
    the precheck and the error branch are all in play at once.

    The static assertions above prove the wiring is spelled correctly; these prove
    it runs.
    """

    def setUp(self):
        schema_guard.reset_all()
        self.address = f"e2e-{os.urandom(6).hex()}@example.com"

    def _signup(self, email, **kw):
        return bot.create_account(
            "E2E Buyer", email, "correct horse battery staple",
            username=kw.pop("username", f"e2e{os.urandom(4).hex()}"),
            age_confirmed=True, **kw)

    def test_the_index_actually_lands_on_the_real_users_table(self):
        user, error = self._signup(self.address)
        self.assertFalse(error, f"the first signup failed: {error}")
        self.assertTrue(user)
        conn = db_service.connect()
        try:
            self.assertTrue(email_unique.index_exists(conn.cursor()))
        finally:
            conn.close()

    def test_a_second_signup_for_the_same_address_is_refused(self):
        first, error = self._signup(self.address)
        self.assertFalse(error)
        self.assertTrue(first)
        second, error = self._signup(self.address)
        self.assertIsNone(second)
        self.assertEqual(error, bot.ACCOUNT_ALREADY_EXISTS_MESSAGE)

    def test_a_case_variant_is_refused_too(self):
        """The precheck now asks with the index's expression, so this is caught as
        an ordinary duplicate rather than arriving as an exception.
        """
        _, error = self._signup(self.address)
        self.assertFalse(error)
        _, error = self._signup(self.address.upper())
        self.assertEqual(error, bot.ACCOUNT_ALREADY_EXISTS_MESSAGE)

    def test_losing_the_race_gives_the_same_answer_as_the_precheck(self):
        """The precheck is blinded so the INSERT is what discovers the duplicate --
        which is exactly the state a real concurrent signup reaches. Before the
        index existed this produced two accounts; before the error branch existed
        it produced "temporarily unavailable, try again shortly", which is false.
        """
        _, error = self._signup(self.address)
        self.assertFalse(error)

        real_connect = db_service.connect

        class BlindPrecheck:
            """Answers the duplicate precheck with "nothing found", and is otherwise
            the real connection. Narrower than patching `create_account`: the INSERT
            still goes to the real table and the real index still refuses it.
            """

            def __init__(self, conn):
                self._conn = conn

            def cursor(self):
                inner = self._conn.cursor()

                class Cur:
                    def execute(self, sql, *args):
                        result = inner.execute(sql, *args)
                        self._blind = "SELECT user_id FROM users WHERE lower(trim(email))" in str(sql)
                        return result

                    def fetchone(self):
                        row = inner.fetchone()
                        return None if getattr(self, "_blind", False) else row

                    def fetchall(self):
                        return inner.fetchall()

                    def __getattr__(self, name):
                        return getattr(inner, name)

                return Cur()

            def __getattr__(self, name):
                return getattr(self._conn, name)

        try:
            db_service.connect = lambda: BlindPrecheck(real_connect())
            user, error = self._signup(self.address, username=f"race{os.urandom(4).hex()}")
        finally:
            db_service.connect = real_connect

        self.assertIsNone(user, "the duplicate was inserted; the index did not hold")
        self.assertEqual(error, bot.ACCOUNT_ALREADY_EXISTS_MESSAGE)
        self.assertNotIn("try again", (error or "").lower())

    def test_only_one_row_exists_for_the_address_afterwards(self):
        self._signup(self.address)
        self._signup(self.address)
        self._signup(self.address.title())
        conn = db_service.connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT COUNT(*) FROM users WHERE {email_unique.EMAIL_IDENTITY_EXPRESSION}=?",
                (self.address,))
            self.assertEqual(int(db_service.row_values(cur.fetchone())[0]), 1)
        finally:
            conn.close()


class PortabilityCase(unittest.TestCase):
    """The DDL string has to be correct on both engines, from one source."""

    def test_the_expression_avoids_postgres_only_spellings(self):
        """`btrim` is PostgreSQL-only; `trim` means the same thing on both. One DDL
        string has to serve local SQLite and production PostgreSQL, because there
        is no migration framework here to hold two.
        """
        self.assertIn("trim(", email_unique.EMAIL_IDENTITY_EXPRESSION)
        self.assertNotIn("btrim", email_unique.EMAIL_IDENTITY_EXPRESSION)
        self.assertNotIn("btrim", email_unique.EMAIL_PRESENT_PREDICATE)

    def test_the_predicate_excludes_both_null_and_blank(self):
        self.assertIn("IS NOT NULL", email_unique.EMAIL_PRESENT_PREDICATE)
        self.assertIn("trim(email) <> ''", email_unique.EMAIL_PRESENT_PREDICATE)

    def test_sqlite_accepts_the_real_ddl_string(self):
        """Not a paraphrase of it -- the exact constant the production path runs."""
        conn = _fresh_users_table()
        conn.execute(email_unique.CREATE_INDEX_SQL)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
            (email_unique.INDEX_NAME,)).fetchone()
        self.assertIsNotNone(row)
        conn.close()


if __name__ == "__main__":
    unittest.main()
