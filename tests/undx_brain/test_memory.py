"""Memory isolation, tested by trying to break it against a real database.

These build an actual sqlite database with two accounts' rows in it and then attempt,
in every way the API permits, to read one account's memory from the other's scope. A
test that asserts an owner clause was *generated* proves the generator ran. Only a test
that queries real rows and counts them proves the row did not come back.

The statement-shape tests look like linting and are not. Each rejected statement is one
that would have executed correctly and returned the wrong rows:

* no owner clause at all — the whole table, which is the bug this module exists for
* a JOIN onto another memory table — a preference scope reading the message log
* two owner markers — the value binds to whichever the caller happened to order first

The last group is about the flag. ``UNDX_MEMORY_FAIL_CLOSED`` picks between failing the
request and answering without memory. It must not be reachable as a way of returning
the data anyway, so the isolation tests are re-run with it off.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.undx_brain import memory as m  # noqa: E402
from services.undx_brain.memory import MemoryKind  # noqa: E402


ON = {
    "UNDX_BRAIN_ENABLED": "1",
    "UNDX_BRAIN_MEMORY_ENABLED": "1",
    "UNDX_MEMORY_USER_PREFERENCES_ENABLED": "1",
}

ALICE = 41
BOB = 42


def _db() -> sqlite3.Connection:
    """A database with both accounts' rows in every governed table.

    Populated for *both* owners deliberately. A table containing only the scope's own
    rows would pass an isolation test that does nothing at all.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    for item in m.CLASSES:
        cur.execute(
            f"CREATE TABLE {item.table} "
            f"(id INTEGER PRIMARY KEY, {item.owner_column} INTEGER, body TEXT, status TEXT)"
        )
        for owner, body in ((ALICE, "alice-secret"), (BOB, "bob-secret")):
            cur.execute(
                f"INSERT INTO {item.table} ({item.owner_column}, body, status) "
                f"VALUES (?, ?, 'active')",
                (owner, body),
            )
    conn.commit()
    return conn


class OwnerScopeMustBeEstablished(unittest.TestCase):
    def test_a_missing_owner_refuses_rather_than_defaulting(self):
        for owner in (None, 0, -1, "", "  ", "abc", [], {}, 3.7):
            with self.subTest(owner=repr(owner)):
                scope = m.open_scope(owner, env=ON)
                self.assertFalse(scope.ok, f"{owner!r} was accepted as an owner")
                self.assertTrue(scope.reason)

    def test_true_is_not_an_owner_id(self):
        # ``bool`` is an ``int`` subclass, so a stray truthy flag would otherwise
        # resolve to account 1 — an account that exists.
        self.assertFalse(m.open_scope(True, env=ON).ok)

    def test_a_float_is_not_truncated_into_somebody_elses_account(self):
        # ``int(41.9)`` is 41, and ``int(42.0)`` is 42. Both are real accounts and
        # neither is necessarily the one the caller meant. A float owner id means
        # something upstream did arithmetic on an identity; the safe reading is to
        # refuse, not to round.
        for owner in (3.7, 41.9, 42.0):
            with self.subTest(owner=owner):
                self.assertFalse(m.open_scope(owner, env=ON).ok)

    def test_a_decimal_string_is_refused_rather_than_floored(self):
        self.assertFalse(m.open_scope("41.9", env=ON).ok)
        self.assertFalse(m.open_scope("4_1", env=ON).ok)

    def test_a_digit_that_is_not_an_ascii_digit_is_not_that_account(self):
        # ``int("٩٩")`` is 99, ``int("１００")`` is 100, ``int("𝟵𝟵")`` is 99 — Python
        # accepts every Unicode decimal digit, and ``str.isdigit`` agrees with it, which
        # is what an earlier version of this resolver relied on. Account 99 is a real
        # person, and this is a way to reach their memory through a string that spells
        # their id in no character a reviewer would recognise.
        for spelling in ("٩٩", "１００", "𝟵𝟵", "۴۲"):
            with self.subTest(spelling=spelling):
                self.assertFalse(m.open_scope(spelling, env=ON).ok)
                self.assertIsNone(m.owner_id(spelling))

    def test_a_negative_string_is_refused_rather_than_read_as_positive(self):
        self.assertIsNone(m.owner_id("-42"))
        self.assertFalse(m.open_scope("-42", env=ON).ok)

    def test_the_resolver_is_public_so_it_is_not_reimplemented(self):
        # It was reimplemented once, in the rollout module, with the same intent and a
        # slightly different set of accepted spellings. Two answers to "whose account is
        # this" is one too many, and the divergence shows up as an account inside a
        # rollout and outside its own scope.
        self.assertIn("owner_id", m.__all__)
        self.assertEqual(m.owner_id(" 41 "), 41)
        self.assertEqual(m.owner_id("+41"), 41)

    def test_a_numeric_string_is_accepted(self):
        # Ids arrive from JSON and from URL segments as strings. Refusing those would
        # push callers into doing the conversion themselves, which is where a silent
        # ``int(x or 0)`` gets written.
        scope = m.open_scope("41", env=ON)
        self.assertTrue(scope.ok)
        self.assertEqual(scope.owner_id, 41)

    def test_memory_is_off_until_both_switches_are_on(self):
        self.assertFalse(m.open_scope(ALICE, env={}).ok)
        self.assertFalse(m.open_scope(ALICE, env={"UNDX_BRAIN_ENABLED": "1"}).ok)
        self.assertTrue(m.open_scope(ALICE, env=ON).ok)

    def test_a_kind_behind_its_own_flag_is_withheld(self):
        scope = m.open_scope(
            ALICE, env={"UNDX_BRAIN_ENABLED": "1", "UNDX_BRAIN_MEMORY_ENABLED": "1"}
        )
        self.assertTrue(scope.ok)
        self.assertNotIn(MemoryKind.PREFERENCE, scope.enabled)
        self.assertIn(MemoryKind.CONVERSATION, scope.enabled)
        self.assertTrue(scope.notes, "a withheld kind should say why")

    def test_a_scope_covers_exactly_one_owner(self):
        scope = m.open_scope(ALICE, env=ON)
        self.assertEqual(scope.owner_id, ALICE)
        with self.assertRaises(Exception):
            scope.owner_id = BOB  # frozen


class OneAccountCannotReadAnother(unittest.TestCase):
    """The claim, tested against rows that are actually there."""

    def setUp(self):
        self.conn = _db()
        self.cur = self.conn.cursor()
        self.alice = m.open_scope(ALICE, env=ON)
        self.bob = m.open_scope(BOB, env=ON)
        self.addCleanup(self.conn.close)

    def test_a_scoped_read_returns_only_its_owners_rows(self):
        for item in m.CLASSES:
            with self.subTest(kind=item.kind.value):
                result = m.read(
                    self.alice, item.kind, self.cur,
                    f"SELECT body FROM {item.table} WHERE {item.owner_column} = {{owner}}",
                )
                self.assertTrue(result.ok, result.reason)
                self.assertEqual([row["body"] for row in result.rows], ["alice-secret"])

    def test_the_other_account_sees_its_own_rows(self):
        # Without this the test above passes for a layer that returns nothing at all.
        result = m.read(
            self.bob, MemoryKind.CONVERSATION, self.cur,
            "SELECT body FROM pulse_ai_messages WHERE user_id = {owner}",
        )
        self.assertEqual([row["body"] for row in result.rows], ["bob-secret"])

    def test_the_caller_cannot_supply_an_owner_value(self):
        # The marker is bound by the layer. A caller who passes BOB's id as a parameter
        # binds it to some *other* placeholder, never to the owner clause.
        result = m.read(
            self.alice, MemoryKind.CONVERSATION, self.cur,
            "SELECT body FROM pulse_ai_messages WHERE user_id = {owner} AND status = ?",
            ("active",),
        )
        self.assertTrue(result.ok, result.reason)
        self.assertEqual([row["body"] for row in result.rows], ["alice-secret"])

    def test_a_statement_with_no_owner_clause_is_refused(self):
        result = m.read(
            self.alice, MemoryKind.CONVERSATION, self.cur,
            "SELECT body FROM pulse_ai_messages",
        )
        self.assertTrue(result.denied)
        self.assertEqual(result.rows, ())
        self.assertIn("owner", result.reason)

    def test_a_hand_written_owner_clause_is_refused(self):
        # Writing the id inline is exactly the habit this layer replaces, and it is the
        # form in which a cross-account read would be typed.
        result = m.read(
            self.alice, MemoryKind.CONVERSATION, self.cur,
            f"SELECT body FROM pulse_ai_messages WHERE user_id = {BOB}",
        )
        self.assertTrue(result.denied)

    def test_reaching_another_memory_table_is_refused(self):
        result = m.read(
            self.alice, MemoryKind.PREFERENCE, self.cur,
            "SELECT p.body FROM pulse_ai_user_memory p "
            "JOIN pulse_ai_messages msg ON msg.user_id = p.user_id "
            "WHERE p.user_id = {owner}",
        )
        self.assertTrue(result.denied)
        self.assertIn("pulse_ai_messages", result.reason)

    def test_the_wrong_table_for_the_kind_is_refused(self):
        result = m.read(
            self.alice, MemoryKind.PREFERENCE, self.cur,
            "SELECT body FROM pulse_ai_messages WHERE user_id = {owner}",
        )
        self.assertTrue(result.denied)

    def test_two_owner_markers_are_refused(self):
        result = m.read(
            self.alice, MemoryKind.CONVERSATION, self.cur,
            "SELECT body FROM pulse_ai_messages "
            "WHERE user_id = {owner} OR user_id = {owner}",
        )
        self.assertTrue(result.denied)

    def test_a_stacked_statement_is_refused(self):
        result = m.read(
            self.alice, MemoryKind.CONVERSATION, self.cur,
            "SELECT body FROM pulse_ai_messages WHERE user_id = {owner}; "
            "SELECT body FROM pulse_ai_messages",
        )
        self.assertTrue(result.denied)

    def test_a_commented_statement_is_refused(self):
        result = m.read(
            self.alice, MemoryKind.CONVERSATION, self.cur,
            "SELECT body FROM pulse_ai_messages WHERE user_id = {owner} -- AND 1=1",
        )
        self.assertTrue(result.denied)

    def test_a_write_verb_cannot_arrive_through_read(self):
        result = m.read(
            self.alice, MemoryKind.CONVERSATION, self.cur,
            "DELETE FROM pulse_ai_messages WHERE user_id = {owner}",
        )
        self.assertTrue(result.denied)
        self.cur.execute("SELECT COUNT(*) FROM pulse_ai_messages")
        self.assertEqual(self.cur.fetchone()[0], 2, "the refused DELETE still ran")

    def test_a_select_cannot_arrive_through_write(self):
        result = m.write(
            self.alice, MemoryKind.CONVERSATION, self.cur,
            "SELECT body FROM pulse_ai_messages WHERE user_id = {owner}",
        )
        self.assertTrue(result.denied)


class WritesStayInsideTheScope(unittest.TestCase):
    def setUp(self):
        self.conn = _db()
        self.cur = self.conn.cursor()
        self.alice = m.open_scope(ALICE, env=ON)
        self.addCleanup(self.conn.close)

    def _bodies(self, owner: int) -> list[str]:
        self.cur.execute(
            "SELECT body FROM pulse_ai_messages WHERE user_id = ? ORDER BY id", (owner,)
        )
        return [row["body"] for row in self.cur.fetchall()]

    def test_an_insert_is_written_under_the_scopes_owner(self):
        result = m.write(
            self.alice, MemoryKind.CONVERSATION, self.cur,
            "INSERT INTO pulse_ai_messages (user_id, body, status) "
            "VALUES ({owner}, ?, 'active')",
            ("written-by-alice",),
        )
        self.assertTrue(result.ok, result.reason)
        self.assertIn("written-by-alice", self._bodies(ALICE))
        self.assertNotIn("written-by-alice", self._bodies(BOB))

    def test_an_update_cannot_reach_the_other_account(self):
        result = m.write(
            self.alice, MemoryKind.CONVERSATION, self.cur,
            "UPDATE pulse_ai_messages SET body = ? WHERE user_id = {owner}",
            ("rewritten",),
        )
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.rowcount, 1)
        self.assertEqual(self._bodies(BOB), ["bob-secret"])

    def test_forget_deletes_one_owners_rows_and_no_others(self):
        result = m.forget(self.alice, MemoryKind.CONVERSATION, self.cur)
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(self._bodies(ALICE), [])
        self.assertEqual(self._bodies(BOB), ["bob-secret"])

    def test_forget_on_a_denied_scope_deletes_nothing(self):
        denied = m.open_scope(0, env=ON)
        result = m.forget(denied, MemoryKind.CONVERSATION, self.cur)
        self.assertTrue(result.denied)
        self.assertEqual(self._bodies(ALICE), ["alice-secret"])
        self.assertEqual(self._bodies(BOB), ["bob-secret"])


class TheFlagChoosesBetweenTwoSafeOutcomes(unittest.TestCase):
    """``UNDX_MEMORY_FAIL_CLOSED`` must not be reachable as a way to see the data."""

    OFF = dict(ON, UNDX_MEMORY_FAIL_CLOSED="0")

    def setUp(self):
        self.conn = _db()
        self.cur = self.conn.cursor()
        self.addCleanup(self.conn.close)

    def test_isolation_holds_with_the_flag_off(self):
        scope = m.open_scope(ALICE, env=self.OFF)
        self.assertTrue(scope.ok)
        self.assertFalse(scope.fail_closed)
        result = m.read(
            scope, MemoryKind.CONVERSATION, self.cur,
            "SELECT body FROM pulse_ai_messages WHERE user_id = {owner}",
        )
        self.assertEqual([row["body"] for row in result.rows], ["alice-secret"])

    def test_an_unfiltered_read_is_still_refused_with_the_flag_off(self):
        scope = m.open_scope(ALICE, env=self.OFF)
        result = m.read(
            scope, MemoryKind.CONVERSATION, self.cur, "SELECT body FROM pulse_ai_messages"
        )
        self.assertTrue(result.denied)
        self.assertEqual(result.rows, ())

    def test_an_unresolvable_owner_is_still_refused_with_the_flag_off(self):
        self.assertFalse(m.open_scope(None, env=self.OFF).ok)

    def test_the_flag_only_changes_whether_the_denial_is_fatal(self):
        closed = m.read(
            m.open_scope(ALICE, env=ON), MemoryKind.CONVERSATION, self.cur,
            "SELECT body FROM pulse_ai_messages",
        )
        opened = m.read(
            m.open_scope(ALICE, env=self.OFF), MemoryKind.CONVERSATION, self.cur,
            "SELECT body FROM pulse_ai_messages",
        )
        self.assertTrue(closed.denied and opened.denied)
        self.assertTrue(closed.fatal)
        self.assertFalse(opened.fatal)


class DegradesRatherThanRaises(unittest.TestCase):
    def setUp(self):
        self.conn = _db()
        self.cur = self.conn.cursor()
        self.scope = m.open_scope(ALICE, env=ON)
        self.addCleanup(self.conn.close)

    def test_a_broken_statement_returns_a_failed_result_not_an_exception(self):
        result = m.read(
            self.scope, MemoryKind.CONVERSATION, self.cur,
            "SELECT no_such_column FROM pulse_ai_messages WHERE user_id = {owner}",
        )
        self.assertFalse(result.ok)
        self.assertFalse(result.denied, "a database error is not a permission denial")
        self.assertTrue(result.reason)

    def test_a_missing_table_does_not_raise(self):
        self.cur.execute("DROP TABLE pulse_ai_messages")
        result = m.read(
            self.scope, MemoryKind.CONVERSATION, self.cur,
            "SELECT body FROM pulse_ai_messages WHERE user_id = {owner}",
        )
        self.assertFalse(result.ok)

    def test_nothing_in_the_api_raises_on_junk(self):
        junk = ("", None, 0, "SELECT", "{owner}", "/**/", ";" * 50, "SELECT * FROM x")
        for sql in junk:
            with self.subTest(sql=repr(sql)[:30]):
                m.read(self.scope, MemoryKind.CONVERSATION, self.cur, sql)
                m.write(self.scope, MemoryKind.CONVERSATION, self.cur, sql)

    def test_a_non_scope_is_refused_rather_than_trusted(self):
        # A caller passing something scope-shaped must not get through on duck typing.
        class Fake:
            ok = True
            owner_id = BOB
            enabled = frozenset(MemoryKind)
            fail_closed = False
            reason = ""

        result = m.read(
            Fake(), MemoryKind.CONVERSATION, self.cur,
            "SELECT body FROM pulse_ai_messages WHERE user_id = {owner}",
        )
        self.assertTrue(result.denied)
        self.assertEqual(result.rows, ())

    def test_an_empty_result_is_distinguishable_from_a_denial(self):
        m.forget(self.scope, MemoryKind.CONVERSATION, self.cur)
        empty = m.read(
            self.scope, MemoryKind.CONVERSATION, self.cur,
            "SELECT body FROM pulse_ai_messages WHERE user_id = {owner}",
        )
        denied = m.read(
            self.scope, MemoryKind.CONVERSATION, self.cur,
            "SELECT body FROM pulse_ai_messages",
        )
        self.assertTrue(empty.ok)
        self.assertEqual(empty.rows, ())
        self.assertFalse(empty.denied)
        self.assertTrue(denied.denied)


class TheClassTableIsHonest(unittest.TestCase):
    def test_every_kind_has_exactly_one_class(self):
        self.assertEqual({item.kind for item in m.CLASSES}, set(MemoryKind))
        self.assertEqual(len(m.CLASSES), len(MemoryKind))

    def test_every_class_names_a_table_that_exists_in_the_schema(self):
        # The kinds are only meaningful if they describe storage this repository
        # actually writes. A kind backed by nothing is an aspiration.
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        schema = ""
        for name in ("services/pulse_ai_service.py", "services/undx_architecture.py"):
            with open(os.path.join(root, name), encoding="utf-8") as handle:
                schema += handle.read()
        for item in m.CLASSES:
            with self.subTest(table=item.table):
                self.assertIn(item.table, schema)
                self.assertIn(item.owner_column, schema)

    def test_both_owner_column_spellings_are_represented(self):
        # Two spellings in the schema is the reason this table exists rather than a
        # constant. If that ever stops being true, the guard can be simplified.
        columns = {item.owner_column for item in m.CLASSES}
        self.assertEqual(columns, {"user_id", "owner_user_id"})

    def test_governed_tables_are_unique(self):
        tables = [item.table for item in m.CLASSES]
        self.assertEqual(len(set(tables)), len(tables))


if __name__ == "__main__":
    unittest.main()
