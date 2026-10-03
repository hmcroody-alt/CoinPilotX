"""A database error must not quietly undo a privacy preference.

Three reads decided who may observe whom, and all three answered the
permissive value on any exception with nothing written to the log:

* ``presence_service._privacy_settings``  -- ``except Exception: pass``, leaving
  ``hide_last_seen=False``, ``invisible_mode=False``, ``presence_privacy=everyone``.
  A user who had set "nobody" became visible to everyone for as long as the
  read kept failing.
* ``presence_service._blocked_pairs``     -- warned when neither block store was
  readable, then returned the empty set, which the caller reads as "nobody is
  blocked" and renders full presence for.
* ``message_privacy._settings_rows``      -- returned ``{}``, resolving to
  ``everyone``.

The first two now fail closed. That is affordable precisely here, and the
reason is worth stating because it does not generalise: ``_hidden_presence`` is
byte-for-byte identical to the payload of a genuinely offline user, so the
degraded state is "these accounts look offline" rather than an error, an empty
screen or a denial. ``presence_for`` also exempts ``is_self`` before consulting
any of it, so nobody loses sight of their own presence.

The third deliberately does NOT fail closed, and that asymmetry is the point of
this file. DM preference is a *preference*, not a safety boundary -- blocks,
account status and profile visibility are enforced by the caller and fail
closed on their own. Denying every new conversation because ``user_settings``
hiccuped would convert a storage fault into a platform-wide messaging outage.
What it must not do is stay silent, because "no row" and "the read failed"
produce the same permission and only one of them is a decision a user made.

So: two fail closed, one fails open and logs. A later reader who flips the
third to match the other two should fail
``test_a_dm_settings_error_still_resolves_to_everyone`` and come read this.
"""
from __future__ import annotations

import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import message_privacy  # noqa: E402
from services import presence_service  # noqa: E402


class _BrokenCursor:
    """Every read raises, as it would during a connection or schema fault."""

    def __init__(self, message="connection reset by peer"):
        self.message = message
        self.attempts = 0

    def execute(self, *_args, **_kwargs):
        self.attempts += 1
        raise RuntimeError(self.message)

    def fetchall(self):
        raise AssertionError("fetchall must not be reached after execute raised")

    def fetchone(self):
        raise AssertionError("fetchone must not be reached after execute raised")


class _EmptyCursor:
    """A working database holding no rows."""

    def execute(self, *_args, **_kwargs):
        return None

    def fetchall(self):
        return []

    def fetchone(self):
        return None


VIEWER = 7701
TARGETS = [7702, 7703]


class PresencePrivacyFailsClosed(unittest.TestCase):
    def setUp(self):
        # _warn_once dedupes by key for the lifetime of the process, which
        # would make the second test to run observe no log line at all.
        presence_service._WARNED.clear()

    def test_an_unreadable_settings_table_hides_presence(self):
        settings = presence_service._privacy_settings(_BrokenCursor(), TARGETS)
        for uid in TARGETS:
            self.assertEqual(
                settings[uid]["presence_privacy"], "nobody",
                "a failed privacy read left the user visible to everyone")
            self.assertTrue(settings[uid]["invisible_mode"])
            self.assertTrue(settings[uid]["hide_last_seen"])

    def test_the_failure_is_logged_rather_than_swallowed(self):
        with self.assertLogs(level=logging.WARNING) as captured:
            presence_service._privacy_settings(_BrokenCursor(), TARGETS)
        self.assertTrue(
            any("PRESENCE_PRIVACY_READ_FAILED" in line for line in captured.output),
            f"no log line names the failure: {captured.output}")

    def test_a_working_empty_database_leaves_presence_visible(self):
        """Failing closed is for the error path only.

        A healthy database with no preference rows has answered the question,
        and the answer is "this user expressed no restriction".
        """
        settings = presence_service._privacy_settings(_EmptyCursor(), TARGETS)
        for uid in TARGETS:
            self.assertEqual(settings[uid]["presence_privacy"], "everyone")
            self.assertFalse(settings[uid]["invisible_mode"])
            self.assertFalse(settings[uid]["hide_last_seen"])

    def test_a_missing_settings_table_is_not_treated_as_a_fault(self):
        """Same boundary on the first read: absent is not the same as broken."""
        missing = _BrokenCursor("no such table: presence_privacy_settings")
        settings = presence_service._privacy_settings(missing, TARGETS)
        for uid in TARGETS:
            self.assertEqual(
                settings[uid]["presence_privacy"], "everyone",
                "an unprovisioned settings table hid everyone's presence")
            self.assertFalse(settings[uid]["invisible_mode"])

    def test_a_missing_messenger_table_is_not_treated_as_a_fault(self):
        """comm_v2_user_settings may legitimately not exist in a deployment."""

        class _NoCommV2(_EmptyCursor):
            def execute(self, sql, *_args, **_kwargs):
                if "comm_v2_user_settings" in sql:
                    raise RuntimeError("no such table: comm_v2_user_settings")
                return None

        settings = presence_service._privacy_settings(_NoCommV2(), TARGETS)
        for uid in TARGETS:
            self.assertEqual(
                settings[uid]["presence_privacy"], "everyone",
                "an absent messenger preference store was treated as a fault; "
                "that would hide all presence on every deployment without it")


class PresenceBlockLookupFailsClosed(unittest.TestCase):
    def setUp(self):
        presence_service._WARNED.clear()

    def test_no_readable_block_store_hides_every_target(self):
        hidden = presence_service._blocked_pairs(_BrokenCursor(), VIEWER, TARGETS)
        self.assertEqual(
            hidden, set(TARGETS),
            "with neither block store readable the lookup reported 'nobody is "
            "blocked', which shows a blocked viewer the presence the block exists "
            "to withhold")

    def test_a_failed_block_lookup_names_the_store_that_failed(self):
        """Both stores are attempted, so both failures must be attributable."""
        with self.assertLogs(level=logging.WARNING) as captured:
            presence_service._blocked_pairs(_BrokenCursor(), VIEWER, TARGETS)
        failures = [line for line in captured.output
                    if "PRESENCE_BLOCK_LOOKUP_FAILED" in line]
        self.assertEqual(len(failures), 2, captured.output)
        self.assertTrue(any("comm_v2_blocks" in line for line in failures))
        self.assertTrue(any("blocked_users" in line for line in failures))

    def test_an_absent_block_source_is_logged_distinctly_from_a_failure(self):
        """The two conditions have different consequences, so different lines."""
        with self.assertLogs(level=logging.WARNING) as captured:
            presence_service._blocked_pairs(
                _BrokenCursor("no such table: comm_v2_blocks"), VIEWER, TARGETS)
        self.assertTrue(
            any("PRESENCE_BLOCK_LOOKUP_NO_SOURCE" in line for line in captured.output),
            f"no log line names the absent store: {captured.output}")
        self.assertFalse(
            any("PRESENCE_BLOCK_LOOKUP_FAILED" in line for line in captured.output),
            "an unprovisioned store was reported as a read failure")

    def test_a_working_empty_database_blocks_nobody(self):
        self.assertEqual(
            presence_service._blocked_pairs(_EmptyCursor(), VIEWER, TARGETS), set(),
            "a healthy database with no block rows must not hide presence")

    def test_a_deployment_with_no_block_table_at_all_still_shows_presence(self):
        """The boundary that keeps this fix from becoming a site-wide denial.

        "Neither store exists" is a different claim from "the read failed". A
        deployment with no block table has no way to express a block, so there
        are none to enforce; hiding all presence there would disable the
        feature rather than protect anyone. The first draft of this fix did
        exactly that and took out eight presence tests, which is how the
        distinction got found.
        """
        missing = _BrokenCursor("no such table: comm_v2_blocks")
        self.assertEqual(
            presence_service._blocked_pairs(missing, VIEWER, TARGETS), set(),
            "an unprovisioned block store was treated as a read failure")

    def test_an_anonymous_viewer_is_not_a_failed_lookup(self):
        self.assertEqual(
            presence_service._blocked_pairs(_BrokenCursor(), 0, TARGETS), set())


class DmPreferenceFailsOpenButLoudly(unittest.TestCase):
    """The deliberate asymmetry. Read the module docstring before changing it."""

    def test_a_dm_settings_error_still_resolves_to_everyone(self):
        """Do not "fix" this to match presence without reading why.

        This gate governs a preference, not a safety boundary. Blocks, account
        status and profile visibility are enforced by the caller and fail
        closed on their own. Denying every new conversation on a transient
        ``user_settings`` error would be a platform-wide messaging outage
        caused by the privacy layer.
        """
        self.assertEqual(
            message_privacy.resolve_preference(_BrokenCursor(), 4242),
            message_privacy.EVERYONE)

    def test_but_the_error_is_logged(self):
        with self.assertLogs(level=logging.ERROR) as captured:
            message_privacy.resolve_preference(_BrokenCursor(), 4242)
        self.assertTrue(
            any("DM_PRIVACY_SETTINGS_READ_FAILED" in line for line in captured.output),
            "an unenforced DM preference was indistinguishable from a user who "
            f"never set one: {captured.output}")

    def test_a_working_database_with_no_row_is_not_logged_as_an_error(self):
        """Absence is the common case and must not produce error noise."""
        logger = logging.getLogger()
        previous = logger.level
        logger.setLevel(logging.ERROR)
        try:
            with self.assertNoLogs(level=logging.ERROR):
                result = message_privacy.resolve_preference(_EmptyCursor(), 4242)
        finally:
            logger.setLevel(previous)
        self.assertEqual(result, message_privacy.EVERYONE)

    def test_a_real_preference_is_still_honoured(self):
        """The logging change must not disturb the resolution it wraps."""

        class _NobodyCursor(_EmptyCursor):
            def fetchall(self):
                return [{"setting_key": "message_requests", "setting_value": "none"}]

        self.assertEqual(
            message_privacy.resolve_preference(_NobodyCursor(), 4242),
            message_privacy.NOBODY)


if __name__ == "__main__":
    unittest.main()
