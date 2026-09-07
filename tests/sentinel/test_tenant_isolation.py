"""Stage 5/29 — tenant isolation invariants.

These prove the three participation invariants in ``services/sentinel/invariants.py``
can actually fail, that they stay quiet on the two shapes of legitimate data
that would otherwise look like violations, and that when they cannot see the
platform tables they say SKIPPED rather than OK.

The last property is the one that matters most in production. A tenant
isolation check that silently reports "healthy" because it queried a table
that has since been renamed is worse than no check: it converts an unknown
into a reassurance, which is precisely the failure the invariant engine's
STATUS_SKIPPED exists to prevent. ``test_the_platform_schema_still_has_the
_columns_these_invariants_read`` is the guard against that, and it is the test
to fix first if it ever goes red.
"""

import pathlib
import re
import sqlite3

import pytest

from services.sentinel import incidents, invariants, store

BOT_PY = pathlib.Path(__file__).resolve().parents[2] / "bot.py"


def _platform_schema(conn):
    """The four platform tables these invariants read, with the columns they
    read. Deliberately minimal: this is a stand-in for ``bot.init_db()``, not a
    copy of it, and ``test_the_platform_schema_still_has_the_columns_these
    _invariants_read`` is what keeps the two from drifting apart.
    """
    cur = conn.cursor()
    cur.execute("""CREATE TABLE pulse_conversation_participants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER, user_id INTEGER, left_at TEXT)""")
    cur.execute("""CREATE TABLE pulse_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER, sender_user_id INTEGER, body TEXT)""")
    cur.execute("""CREATE TABLE pulse_message_receipts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id INTEGER, conversation_id INTEGER, user_id INTEGER)""")
    cur.execute("""CREATE TABLE pulse_message_reactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id INTEGER, conversation_id INTEGER, user_id INTEGER)""")
    conn.commit()


def _join(conn, conversation_id, user_id, left_at=None):
    conn.execute(
        "INSERT INTO pulse_conversation_participants "
        "(conversation_id, user_id, left_at) VALUES (?, ?, ?)",
        (conversation_id, user_id, left_at))
    conn.commit()


def _say(conn, conversation_id, user_id, body="hi"):
    conn.execute(
        "INSERT INTO pulse_messages (conversation_id, sender_user_id, body) "
        "VALUES (?, ?, ?)", (conversation_id, user_id, body))
    conn.commit()


def _read(conn, conversation_id, user_id, message_id=1):
    conn.execute(
        "INSERT INTO pulse_message_receipts (message_id, conversation_id, user_id) "
        "VALUES (?, ?, ?)", (message_id, conversation_id, user_id))
    conn.commit()


def _react(conn, conversation_id, user_id, message_id=1):
    conn.execute(
        "INSERT INTO pulse_message_reactions (message_id, conversation_id, user_id) "
        "VALUES (?, ?, ?)", (message_id, conversation_id, user_id))
    conn.commit()


def _by_id(conn):
    return {r.invariant_id: r for r in invariants.run_all(conn=conn)}


TENANT_INVARIANTS = (
    "INV_MESSAGE_SENDER_PARTICIPANT",
    "INV_RECEIPT_READER_PARTICIPANT",
    "INV_REACTION_AUTHOR_PARTICIPANT",
)


@pytest.fixture()
def platform(conn):
    """A Sentinel store that can also see the platform's chat tables — which is
    the real production arrangement: ``store.connection()`` hands back a
    ``services.db`` connection, so Sentinel and the product share one database.
    """
    _platform_schema(conn)
    return conn


class TestTheInvariantsCanFail:
    def test_a_message_written_by_someone_never_let_in_is_caught(self, platform):
        _join(platform, 1, user_id=10)
        _say(platform, 1, user_id=99)  # 99 was never a participant
        result = _by_id(platform)["INV_MESSAGE_SENDER_PARTICIPANT"]
        assert result.status == invariants.STATUS_VIOLATED
        assert "1 of the 1 newest" in result.detail

    def test_a_read_receipt_from_a_non_participant_is_caught(self, platform):
        _join(platform, 1, user_id=10)
        _read(platform, 1, user_id=99)
        result = _by_id(platform)["INV_RECEIPT_READER_PARTICIPANT"]
        assert result.status == invariants.STATUS_VIOLATED

    def test_a_reaction_from_a_non_participant_is_caught(self, platform):
        _join(platform, 1, user_id=10)
        _react(platform, 1, user_id=99)
        result = _by_id(platform)["INV_REACTION_AUTHOR_PARTICIPANT"]
        assert result.status == invariants.STATUS_VIOLATED

    def test_each_invariant_answers_only_for_its_own_table(self, platform):
        """A bypass in one table must not colour the other two. Otherwise a
        single violation would light up all three and the owner would learn
        that "something is wrong with chat" rather than that a *read* happened.
        """
        _join(platform, 1, user_id=10)
        _react(platform, 1, user_id=99)
        by_id = _by_id(platform)
        assert by_id["INV_REACTION_AUTHOR_PARTICIPANT"].status == invariants.STATUS_VIOLATED
        assert by_id["INV_MESSAGE_SENDER_PARTICIPANT"].status != invariants.STATUS_VIOLATED
        assert by_id["INV_RECEIPT_READER_PARTICIPANT"].status != invariants.STATUS_VIOLATED


class TestTheInvariantsDoNotCryWolf:
    def test_legitimate_traffic_is_ok(self, platform):
        _join(platform, 1, user_id=10)
        _join(platform, 1, user_id=11)
        _say(platform, 1, user_id=10)
        _read(platform, 1, user_id=11)
        _react(platform, 1, user_id=11)
        by_id = _by_id(platform)
        for inv_id in TENANT_INVARIANTS:
            assert by_id[inv_id].status == invariants.STATUS_OK, by_id[inv_id].detail

    def test_a_member_who_left_still_counts_as_having_been_let_in(self, platform):
        """Leaving sets ``left_at``; it does not delete the row. The question
        the invariant asks is "was this actor ever admitted", not "is this
        actor still here" — asking the stricter one would flag every ex-member
        of every group chat on the platform.
        """
        _join(platform, 1, user_id=10, left_at="2026-01-01 00:00:00")
        _say(platform, 1, user_id=10)
        _react(platform, 1, user_id=10)
        by_id = _by_id(platform)
        assert by_id["INV_MESSAGE_SENDER_PARTICIPANT"].status == invariants.STATUS_OK
        assert by_id["INV_REACTION_AUTHOR_PARTICIPANT"].status == invariants.STATUS_OK

    def test_rows_orphaned_by_a_group_deletion_are_not_a_violation(self, platform):
        """Deleting a group removes messages, receipts, participants and the
        conversation — but not reactions. Those orphans have no roster to be
        measured against, and reporting them would leave the invariant
        permanently red for a reason that is not a security event.
        """
        _react(platform, 404, user_id=10)  # conversation 404 has no roster at all
        result = _by_id(platform)["INV_REACTION_AUTHOR_PARTICIPANT"]
        assert result.status == invariants.STATUS_OK, result.detail

    def test_an_empty_platform_is_ok_not_violated(self, platform):
        by_id = _by_id(platform)
        for inv_id in TENANT_INVARIANTS:
            assert by_id[inv_id].status == invariants.STATUS_OK


class TestUnknownIsNotHealthy:
    def test_missing_platform_tables_are_skipped_not_ok(self, conn):
        """The bare Sentinel fixture has no chat tables. Reporting OK here
        would mean a renamed table buys a permanently green tenant-isolation
        check — the exact shape of fake security this engine refuses.
        """
        by_id = _by_id(conn)
        for inv_id in TENANT_INVARIANTS:
            assert by_id[inv_id].status == invariants.STATUS_SKIPPED, by_id[inv_id].detail

    def test_a_driver_that_returns_no_row_is_skipped_not_ok(self):
        """``SELECT COUNT(*)`` always returns exactly one row on SQLite and on
        PostgreSQL, so this branch is unreachable through a real cursor — which
        is precisely why it needs a test. Defensive code no test can reach is
        code the next reader deletes as dead, and the condition it guards ("the
        driver handed me nothing") must never be read as "nothing is wrong".
        """
        class _SilentCursor:
            def execute(self, *args, **kwargs):
                return None

            def fetchone(self):
                return None

        bad, scanned = invariants._participation_bypass(_SilentCursor(), "pulse_messages")
        assert bad is None, "no answer must mean unknown, not all-clear"
        assert scanned == 0

    def test_a_renamed_actor_column_is_skipped_not_ok(self, platform):
        platform.execute("ALTER TABLE pulse_messages RENAME COLUMN sender_user_id TO author_id")
        platform.commit()
        result = _by_id(platform)["INV_MESSAGE_SENDER_PARTICIPANT"]
        assert result.status == invariants.STATUS_SKIPPED

    def test_the_platform_schema_still_has_the_columns_these_invariants_read(self):
        """Pin the invariants' column names against ``bot.py``'s own CREATE
        TABLE statements.

        Without this, the suite above would keep passing against a test-local
        schema long after production renamed a column, and the invariant would
        report SKIPPED forever in the only place it matters. SKIPPED is honest,
        but a permanently skipped check is not a check — so the drift has to be
        loud somewhere, and here is that somewhere.
        """
        source = BOT_PY.read_text(errors="replace")
        required = {
            "pulse_conversation_participants": ("conversation_id", "user_id"),
            "pulse_messages": ("conversation_id", "sender_user_id"),
            "pulse_message_receipts": ("conversation_id", "user_id"),
            "pulse_message_reactions": ("conversation_id", "user_id"),
        }
        for table, columns in required.items():
            match = re.search(
                r"CREATE TABLE IF NOT EXISTS " + table + r"\s*\((.*?)\)\s*\"\"\"",
                source, re.S)
            assert match, f"{table} is no longer created in bot.py"
            body = match.group(1)
            for column in columns:
                assert re.search(rf"\b{column}\b", body), (
                    f"invariants read {table}.{column}, which bot.py no longer "
                    f"creates — the tenant isolation checks are now SKIPPED in "
                    f"production")


class TestTheScanIsBoundedAndSaysSo:
    def test_the_ok_detail_reports_how_much_was_actually_examined(self, platform):
        _join(platform, 1, user_id=10)
        _say(platform, 1, user_id=10)
        _say(platform, 1, user_id=10)
        result = _by_id(platform)["INV_MESSAGE_SENDER_PARTICIPANT"]
        assert result.status == invariants.STATUS_OK
        assert "2 newest message(s)" in result.detail, result.detail

    def test_the_window_is_the_newest_rows_and_the_miss_is_disclosed(self, platform):
        """A bounded scan trades completeness for the ability to run at all on
        a table with tens of millions of rows. That trade is only acceptable
        while the result states the window — a violation older than the window
        is missed, and the OK line has to make that visible rather than imply
        the whole table was clean.
        """
        _join(platform, 1, user_id=10)
        _say(platform, 1, user_id=99)          # the old bypass
        for _ in range(3):
            _say(platform, 1, user_id=10)      # newer, legitimate traffic

        cur = platform.cursor()
        bad, scanned = invariants._participation_bypass(cur, "pulse_messages", limit=2)
        assert (bad, scanned) == (0, 2), "the newest two rows are clean"

        bad, scanned = invariants._participation_bypass(cur, "pulse_messages", limit=4)
        assert (bad, scanned) == (1, 4), "widening the window reaches the bypass"

    def test_the_default_window_is_not_unbounded(self):
        assert isinstance(invariants.TENANT_SCAN_LIMIT, int)
        assert 0 < invariants.TENANT_SCAN_LIMIT <= 100_000


class TestTheTableNamesAreNotTakenOnTrust:
    def test_an_unlisted_table_is_refused_rather_than_interpolated(self, platform):
        """Table and column names go into the SQL by interpolation, so the only
        thing standing between this helper and injection is the allowlist. It
        must raise, not improvise.
        """
        cur = platform.cursor()
        with pytest.raises(KeyError):
            invariants._participation_bypass(cur, "pulse_messages; DROP TABLE users")
        with pytest.raises(KeyError):
            invariants._participation_bypass(cur, "sentinel_events")

    def test_every_allowlisted_source_is_actually_used_by_an_invariant(self):
        """An entry nobody calls is an interpolation target kept alive for no
        reason."""
        source = pathlib.Path(invariants.__file__).read_text()
        for table in invariants._PARTICIPATION_SOURCES:
            assert source.count(f'"{table}"') >= 2, (
                f"{table} is allowlisted but never queried")


class TestAViolationBecomesEvidence:
    def test_a_bypass_opens_an_incident_and_emits_an_event(self, platform):
        _join(platform, 1, user_id=10)
        _read(platform, 1, user_id=99)
        invariants.run_all(conn=platform)

        cur = platform.cursor()
        cur.execute("SELECT COUNT(*) FROM sentinel_events WHERE category = 'PRIVACY' "
                    "AND subject_id = 'INV_RECEIPT_READER_PARTICIPANT'")
        assert cur.fetchone()[0] == 1

        open_incidents = [i for i in incidents.list_open(conn=platform)
                          if "INV_RECEIPT_READER_PARTICIPANT" in str(i.get("incident_key", ""))]
        assert open_incidents, "a cross-tenant read left no incident behind"
        assert open_incidents[0]["incident_type"] == "DATA_EXPOSURE"

    def test_a_read_bypass_is_filed_as_disclosure_not_as_a_generic_violation(self, platform):
        """A receipt or a reaction can only be produced after reading someone
        else's thread; a message can be written without reading anything. The
        two are filed differently so the owner summary does not average a
        confidentiality breach in with an unauthorized insert.
        """
        assert invariants.INVARIANTS["INV_RECEIPT_READER_PARTICIPANT"][1:] == (
            "PRIVACY", "DATA_EXPOSURE")
        assert invariants.INVARIANTS["INV_REACTION_AUTHOR_PARTICIPANT"][1:] == (
            "PRIVACY", "DATA_EXPOSURE")
        assert invariants.INVARIANTS["INV_MESSAGE_SENDER_PARTICIPANT"][1:] == (
            "SECURITY", "INVARIANT_VIOLATION")

    def test_the_invariants_only_read(self, platform):
        """Sentinel observes; it never corrects. A tenant-isolation check that
        deleted the offending row would be destroying the evidence of the
        breach it had just found (SC5).

        This compares full row dumps rather than row counts, and it plants rows
        in the awkward corners as well as the obvious ones — a violation, clean
        traffic, a departed member, and an orphan with no roster. Counting rows
        in one conversation would miss both an UPDATE and a DELETE aimed
        anywhere else, and "anywhere else" is where a cleanup that thought it
        was being helpful would strike first.
        """
        _join(platform, 1, user_id=10)
        _join(platform, 2, user_id=20, left_at="2026-01-01 00:00:00")
        _say(platform, 1, user_id=10)
        _say(platform, 1, user_id=99)      # a violation, which must survive
        _say(platform, 2, user_id=20)
        _read(platform, 1, user_id=99)
        _react(platform, 1, user_id=99)
        _react(platform, 404, user_id=10)  # orphan: no roster for conversation 404
        _read(platform, 404, user_id=10)

        tables = ("pulse_messages", "pulse_message_receipts",
                  "pulse_message_reactions", "pulse_conversation_participants")

        def snapshot():
            return {t: platform.execute(f"SELECT * FROM {t} ORDER BY id").fetchall()
                    for t in tables}

        before = snapshot()
        invariants.run_all(conn=platform)
        assert snapshot() == before, "run_all changed platform data"
