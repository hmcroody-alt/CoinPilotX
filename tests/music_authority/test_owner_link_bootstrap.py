"""The owner link must survive the deploy that introduces the column it writes.

`ensure_owner_super_user()` calls `ensure_owner_admin_account_link()` early in
`init_db()` -- above `CREATE TABLE admin_users` -- but the `account_user_id`
column that link writes is added thousands of lines later. On every boot after
the first that ordering is harmless, because the column already exists. On the
one deploy that introduces it, the early attempt raises UndefinedColumn, the
`except` in `ensure_owner_admin_account_link` swallows it, and the owner is left
unlinked.

That is the worst shape of failure this feature can take: the endpoints deploy,
they authenticate, they return a clean 401/403, and the only person authorised to
use them cannot. Nothing is broken enough to notice. Production did exactly this
-- `OWNER_ADMIN_ACCOUNT_LINK_SKIPPED error=UndefinedColumn` -- while every route
reported itself live.

So the guarantee pinned here is not "the link function works" (that is the easy
half) but "init leaves the owner linked even when the column did not exist when
init started". The test therefore simulates the introducing deploy: it runs the
link against a table with no `account_user_id`, then adds the column, then runs
it again, and asserts the second pass repairs what the first could not.
"""
import os
import sqlite3
import unittest


def _column_names(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


class OwnerLinkOrderingTests(unittest.TestCase):
    """A first boot where `admin_users` predates `account_user_id`."""

    EMAIL = "owner@example.test"

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        # Deliberately WITHOUT account_user_id: this is the pre-upgrade shape of
        # the table on the deploy that introduces the column.
        self.conn.execute(
            "CREATE TABLE admin_users (id INTEGER PRIMARY KEY, email TEXT, role TEXT, status TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE users (user_id INTEGER PRIMARY KEY, email TEXT, email_verified INTEGER)"
        )
        self.conn.execute(
            "INSERT INTO admin_users (id, email, role, status) VALUES (1, ?, 'owner', 'active')",
            (self.EMAIL,),
        )
        self.conn.execute(
            "INSERT INTO users (user_id, email, email_verified) VALUES (35, ?, 1)",
            (self.EMAIL,),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _link(self):
        """Run the real linker against this connection.

        Imported lazily and driven through `bot`'s own function so the test moves
        with the implementation rather than restating it.
        """
        import bot

        original = bot.owner_email_value
        bot.owner_email_value = lambda: self.EMAIL
        try:
            cur = self.conn.cursor()
            return bot.ensure_owner_admin_account_link(cur)
        finally:
            bot.owner_email_value = original

    def _linked_account_id(self):
        if "account_user_id" not in _column_names(self.conn, "admin_users"):
            return None
        row = self.conn.execute("SELECT account_user_id FROM admin_users WHERE id=1").fetchone()
        return row["account_user_id"] if row else None

    def test_the_early_attempt_cannot_link_and_does_not_raise(self):
        """The pre-column attempt fails softly -- this is the state to recover from."""
        self.assertNotIn("account_user_id", _column_names(self.conn, "admin_users"))
        self.assertFalse(self._link(), "linking should report failure when the column is absent")
        self.assertIsNone(self._linked_account_id())

    def test_the_link_is_repaired_once_the_column_exists(self):
        """The fix: run it again after the column is added, in the same init."""
        self._link()  # the doomed early attempt, exactly as init does it
        self.conn.execute("ALTER TABLE admin_users ADD COLUMN account_user_id INTEGER")
        self.conn.commit()

        self.assertTrue(self._link(), "the post-column retry must link the owner")
        self.assertEqual(self._linked_account_id(), 35)

    def test_running_it_twice_more_changes_nothing(self):
        """Idempotent, because init runs it on every boot and not just the first."""
        self.conn.execute("ALTER TABLE admin_users ADD COLUMN account_user_id INTEGER")
        self.conn.commit()
        self._link()
        self._link()
        self._link()

        self.assertEqual(self._linked_account_id(), 35)
        rows = self.conn.execute(
            "SELECT COUNT(*) AS n FROM admin_users WHERE account_user_id=35"
        ).fetchone()
        self.assertEqual(rows["n"], 1)

    def test_an_unverified_account_on_the_owner_address_is_not_linked(self):
        """The guard that stops a signup on the owner's address inheriting authority."""
        self.conn.execute("ALTER TABLE admin_users ADD COLUMN account_user_id INTEGER")
        self.conn.execute("UPDATE users SET email_verified=0 WHERE user_id=35")
        self.conn.commit()

        self.assertFalse(self._link())
        self.assertIsNone(self._linked_account_id())


class InitDbCallOrderTests(unittest.TestCase):
    """The ordering itself, asserted on the source rather than on behaviour.

    Running `init_db()` twice inside one process would not reproduce the bug --
    the column exists by the second pass, which is precisely why the failure
    survived a green test suite. What actually has to hold is a source-order
    property: there is a link call *after* the statement that adds the column.
    """

    def test_a_link_call_follows_the_column_creation(self):
        import bot

        source = open(bot.__file__, "r", encoding="utf-8").read()

        column_at = source.find('("account_user_id", "INTEGER")')
        self.assertGreater(column_at, 0, "the column creation should be findable")

        link_calls = []
        start = 0
        needle = "ensure_owner_admin_account_link(cur)"
        while True:
            found = source.find(needle, start)
            if found < 0:
                break
            link_calls.append(found)
            start = found + 1

        self.assertTrue(link_calls, "the linker should be called somewhere")
        self.assertTrue(
            any(pos > column_at for pos in link_calls),
            "every link call precedes the column creation, so the deploy that "
            "introduces `account_user_id` will leave the owner unlinked",
        )


if __name__ == "__main__":
    unittest.main()
