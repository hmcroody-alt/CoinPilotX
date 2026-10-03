"""A database error must not grant profile access.

``profile_access`` closes a profile when either party has blocked the other.
That decision rests on one query against ``blocked_users``. Until this was
fixed, the query ran through a shared ``_exists`` helper that answered False on
any exception -- and False here means *not blocked*, so an unreadable block
table fell straight through to ``can_view_public_profile = True`` and every
public content flag, on both the web profile page and the native profile
payload.

That is the same failure the database-contract sentinel exists to prevent, one
layer up: the check is present, it reads correctly, it is covered by tests that
use a working database, and in the one situation it was written for it does
nothing. The sentinel catches "the table does not exist"; this catches "the
table exists but the read failed".

The direction matters and is not uniform across this module. ``_follows`` and
``_friends`` answering False on error is correct -- no relationship means less
access. Only the block check inverts, so only the block check was changed.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import profile_viewer_permissions as viewer  # noqa: E402


class _BrokenCursor:
    """Every read raises, as it would during a connection or schema fault."""

    def __init__(self):
        self.attempts = 0

    def execute(self, *_args, **_kwargs):
        self.attempts += 1
        raise RuntimeError("connection reset by peer")

    def fetchone(self):
        raise AssertionError("fetchone must not be reached after execute raised")


class _EmptyCursor:
    """A working database with no rows: nobody is blocked, nobody follows."""

    def execute(self, *_args, **_kwargs):
        return None

    def fetchone(self):
        return None


VIEWER = 4101
TARGET = 4102
PUBLIC_ACCOUNT = {"account_status": "active", "profile_visibility": "public"}


class BlockCheckFailsClosed(unittest.TestCase):
    def test_an_unreadable_block_table_reads_as_blocked(self):
        cursor = _BrokenCursor()
        self.assertIs(
            viewer._blocked_either_way(cursor, TARGET, VIEWER), True,
            "a failed block lookup answered 'not blocked'; a database error is "
            "not evidence that the two accounts are on speaking terms",
        )
        self.assertEqual(cursor.attempts, 1)

    def test_a_database_error_closes_the_profile(self):
        state, permissions = viewer.profile_access(
            _BrokenCursor(), TARGET, VIEWER, account=dict(PUBLIC_ACCOUNT))
        self.assertEqual(state, viewer.ACCESS_BLOCKED)
        self.assertIn(state, viewer.CLOSED_STATES)
        self.assertFalse(permissions.get("can_view_public_profile"))
        self.assertFalse(permissions.get("can_message"))
        for flag in viewer.PUBLIC_CONTENT_FLAGS:
            self.assertFalse(
                permissions.get(flag),
                f"content flag {flag!r} opened despite an unreadable block table")

    def test_the_viewer_can_still_report_and_block(self):
        """Closing the profile must not strip the escalation route."""
        _state, permissions = viewer.profile_access(
            _BrokenCursor(), TARGET, VIEWER, account=dict(PUBLIC_ACCOUNT))
        self.assertTrue(permissions.get("can_report"))
        self.assertTrue(permissions.get("can_block"))

    def test_a_working_empty_database_still_opens_a_public_profile(self):
        """The fix must not turn every profile into a denial.

        Failing closed is only correct for the error path. A healthy database
        that simply holds no block row has answered the question, and the
        answer is "not blocked".
        """
        state, permissions = viewer.profile_access(
            _EmptyCursor(), TARGET, VIEWER, account=dict(PUBLIC_ACCOUNT))
        self.assertEqual(state, viewer.ACCESS_OK)
        self.assertTrue(permissions.get("can_view_public_profile"))

    def test_an_anonymous_viewer_is_not_treated_as_blocked(self):
        """No viewer id means no relationship to check, not a failed check."""
        self.assertIs(viewer._blocked_either_way(_BrokenCursor(), TARGET, 0), False)

    def test_relationship_checks_keep_failing_to_no_relationship(self):
        """Only the block check inverts; follows/friends must stay restrictive."""
        self.assertIs(viewer._follows(_BrokenCursor(), VIEWER, TARGET), False)
        self.assertIs(viewer._friends(_BrokenCursor(), VIEWER, TARGET), False)


if __name__ == "__main__":
    unittest.main()
