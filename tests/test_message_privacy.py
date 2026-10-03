"""The "Message requests" preference must actually deny messages.

PulseSoc shipped a DM-privacy control in two settings screens and wired it to
nothing. ``profile_viewer_permissions._can_message`` read
``users.message_privacy`` / ``users.dm_privacy``; neither column exists in
``init_db()`` or in production, so the lookup always fell through to the
``everyone`` default and ``can_message`` was unconditionally true for any
public, unblocked profile.

The control is stored in ``user_settings`` by two independent writers:

* ``message_requests`` — Account Command Center (web + mobile), values
  ``everyone`` / ``followers`` / ``none``
* ``pulse_native_preferences`` — native privacy settings, a JSON blob whose
  ``privacy.allowDirectMessages`` is ``everyone`` / ``followers`` / ``nobody``

These tests cover three things, and the split matters:

1. **Resolution** — both stores are read, the stricter wins, and an
   unreadable store cannot widen a restriction set in the other.
2. **The hint** — ``can_message`` in the profile payload reflects the
   preference, so the client stops offering a button that would fail.
3. **The gate** — the code that actually opens a conversation refuses. This
   is the half that matters. A hidden button is not access control, and the
   module being fixed here says so in its own docstring; a fix that only
   corrected the flag would reproduce exactly the failure it warns about.

Every fixture value is distinct. An earlier privacy suite in this repo gave
several fields the same placeholder and was blind to the one field it existed
to protect, so "followers" vs "nobody" vs "everyone" are never stand-ins for
each other here, and the two stores are always given *different* values when
the point of the test is which one wins.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import message_privacy  # noqa: E402
from services import profile_viewer_permissions  # noqa: E402

# Distinct ids so a crossed-wires bug reads as a wrong number, not a coincidence.
OWNER = 70101           # the account whose preference is under test
STRANGER = 70202        # no relationship to OWNER
FOLLOWER = 70303        # follows OWNER, not a friend
FRIEND = 70404          # accepted friend of OWNER


def _db():
    """A schema with only what these rules touch, so a pass means the rule ran."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE users (user_id INTEGER PRIMARY KEY, account_status TEXT DEFAULT 'active', "
        "profile_visibility TEXT DEFAULT 'public', username TEXT, display_name TEXT, avatar_url TEXT)"
    )
    cur.execute("CREATE TABLE user_settings (user_id INTEGER, setting_key TEXT, setting_value TEXT)")
    cur.execute("CREATE TABLE pulse_follows (follower_user_id INTEGER, followed_user_id INTEGER)")
    cur.execute("CREATE TABLE pulse_friendships (user_id INTEGER, friend_user_id INTEGER)")
    cur.execute("CREATE TABLE pulse_friends (user_id INTEGER, friend_user_id INTEGER, status TEXT)")
    cur.execute("CREATE TABLE blocked_users (blocker_user_id INTEGER, blocked_user_id INTEGER)")
    # Distinct names as well as distinct ids, so a payload that returns the
    # wrong participant reads as a wrong name rather than an empty string.
    for uid, name in (
        (OWNER, "owner70101"),
        (STRANGER, "stranger70202"),
        (FOLLOWER, "follower70303"),
        (FRIEND, "friend70404"),
    ):
        cur.execute(
            "INSERT INTO users (user_id, username, display_name) VALUES (?, ?, ?)",
            (uid, name, name),
        )
    cur.execute("INSERT INTO pulse_follows VALUES (?, ?)", (FOLLOWER, OWNER))
    cur.execute("INSERT INTO pulse_friendships VALUES (?, ?)", (FRIEND, OWNER))
    conn.commit()
    return conn, cur


def _set_command_center(cur, user_id, value):
    cur.execute(
        "INSERT INTO user_settings (user_id, setting_key, setting_value) VALUES (?, 'message_requests', ?)",
        (user_id, value),
    )


def _set_native(cur, user_id, value, raw=None):
    """Write the native settings blob. ``raw`` injects a corrupt payload."""
    blob = raw if raw is not None else json.dumps({"privacy": {"allowDirectMessages": value}})
    cur.execute(
        "INSERT INTO user_settings (user_id, setting_key, setting_value) "
        "VALUES (?, 'pulse_native_preferences', ?)",
        (user_id, blob),
    )


class PreferenceResolution(unittest.TestCase):
    """Reading the two stores, and deciding which one binds."""

    def setUp(self):
        self.conn, self.cur = _db()
        self.addCleanup(self.conn.close)

    def test_no_stored_preference_reads_as_everyone(self):
        # The overwhelmingly common case: no row at all. It must not deny.
        self.assertEqual(message_privacy.resolve_preference(self.cur, OWNER), "everyone")

    def test_command_center_value_is_read(self):
        _set_command_center(self.cur, OWNER, "none")
        self.assertEqual(message_privacy.resolve_preference(self.cur, OWNER), "nobody")

    def test_native_value_is_read(self):
        _set_native(self.cur, OWNER, "followers")
        self.assertEqual(message_privacy.resolve_preference(self.cur, OWNER), "followers")

    def test_the_stricter_of_two_disagreeing_stores_wins(self):
        # Two writers, no shared ledger. A restriction set on either screen is
        # a real expression of intent and must not be discarded by the other
        # screen's untouched default.
        _set_command_center(self.cur, OWNER, "everyone")
        _set_native(self.cur, OWNER, "nobody")
        self.assertEqual(message_privacy.resolve_preference(self.cur, OWNER), "nobody")

    def test_the_stricter_wins_in_the_other_direction_too(self):
        # Same assertion with the stores swapped, so a test that only passes
        # because of argument order would fail here.
        _set_command_center(self.cur, OWNER, "none")
        _set_native(self.cur, OWNER, "everyone")
        self.assertEqual(message_privacy.resolve_preference(self.cur, OWNER), "nobody")

    def test_corrupt_native_json_cannot_widen_a_real_restriction(self):
        # An unparseable store must read as "said nothing", not as "everyone".
        # Treating it as permissive would let a corrupt blob silently reopen an
        # inbox the user had closed on the other screen.
        _set_command_center(self.cur, OWNER, "none")
        _set_native(self.cur, OWNER, None, raw="{not valid json")
        self.assertEqual(message_privacy.resolve_preference(self.cur, OWNER), "nobody")

    def test_an_unknown_vocabulary_word_cannot_widen_a_real_restriction(self):
        _set_command_center(self.cur, OWNER, "none")
        _set_native(self.cur, OWNER, "sometimes_maybe")
        self.assertEqual(message_privacy.resolve_preference(self.cur, OWNER), "nobody")

    def test_a_missing_settings_table_does_not_deny(self):
        # Degrade toward the behaviour that shipped, never toward an outage:
        # an unprovisioned optional table must not close every inbox.
        self.cur.execute("DROP TABLE user_settings")
        self.assertEqual(message_privacy.resolve_preference(self.cur, OWNER), "everyone")
        self.assertTrue(message_privacy.may_message(self.cur, OWNER, STRANGER))


class MayMessage(unittest.TestCase):
    """The shared predicate both the hint and the gate call."""

    def setUp(self):
        self.conn, self.cur = _db()
        self.addCleanup(self.conn.close)

    def test_everyone_allows_a_stranger(self):
        self.assertTrue(message_privacy.may_message(self.cur, OWNER, STRANGER))

    def test_nobody_denies_even_a_friend(self):
        _set_command_center(self.cur, OWNER, "none")
        self.assertFalse(message_privacy.may_message(self.cur, OWNER, FRIEND))

    def test_followers_denies_a_stranger(self):
        _set_command_center(self.cur, OWNER, "followers")
        self.assertFalse(message_privacy.may_message(self.cur, OWNER, STRANGER))

    def test_followers_allows_a_follower(self):
        _set_command_center(self.cur, OWNER, "followers")
        self.assertTrue(message_privacy.may_message(self.cur, OWNER, FOLLOWER))

    def test_followers_allows_a_friend_who_does_not_follow(self):
        # FRIEND has a friendship row but no follow row. Friendship is the
        # stronger relationship; reading only pulse_follows would deny it.
        _set_command_center(self.cur, OWNER, "followers")
        self.assertTrue(message_privacy.may_message(self.cur, OWNER, FRIEND))

    def test_friendship_is_checked_in_the_second_table_too(self):
        # pulse_friendships and pulse_friends coexist in this schema. An
        # account written through the other table must not read as a stranger.
        _set_command_center(self.cur, OWNER, "followers")
        self.cur.execute("DELETE FROM pulse_friendships")
        self.cur.execute("INSERT INTO pulse_friends VALUES (?, ?, 'active')", (FRIEND, OWNER))
        self.assertTrue(message_privacy.may_message(self.cur, OWNER, FRIEND))

    def test_the_preference_does_not_apply_to_yourself(self):
        _set_command_center(self.cur, OWNER, "none")
        self.assertTrue(message_privacy.may_message(self.cur, OWNER, OWNER))

    def test_the_direction_is_not_symmetric(self):
        # OWNER closing their inbox must not stop OWNER messaging others.
        _set_command_center(self.cur, OWNER, "none")
        self.assertFalse(message_privacy.may_message(self.cur, OWNER, STRANGER))
        self.assertTrue(message_privacy.may_message(self.cur, STRANGER, OWNER))


class ProfilePayloadHint(unittest.TestCase):
    """``can_message`` in the profile payload tracks the preference."""

    def setUp(self):
        self.conn, self.cur = _db()
        self.addCleanup(self.conn.close)

    def _can_message(self, viewer):
        perms = profile_viewer_permissions.viewer_permissions(self.cur, OWNER, viewer)
        return perms["can_message"]

    def test_control_an_unrestricted_profile_offers_messaging(self):
        # Without this, every assertion below could pass on a payload that
        # denies everything for an unrelated reason.
        self.assertTrue(self._can_message(STRANGER))

    def test_nobody_hides_the_button_from_a_stranger(self):
        _set_command_center(self.cur, OWNER, "none")
        self.assertFalse(self._can_message(STRANGER))

    def test_followers_hides_the_button_from_a_stranger_but_not_a_follower(self):
        _set_command_center(self.cur, OWNER, "followers")
        self.assertFalse(self._can_message(STRANGER))
        self.assertTrue(self._can_message(FOLLOWER))

    def test_the_native_store_also_drives_the_button(self):
        # The native screen is the one most users will actually touch; a fix
        # that only honoured the command-center key would look correct in
        # testing and do nothing on the phone.
        _set_native(self.cur, OWNER, "nobody")
        self.assertFalse(self._can_message(STRANGER))

    def test_a_private_profile_still_honours_a_closed_inbox(self):
        # Being accepted by a private account is necessary, not sufficient.
        # This branch returns early and previously never consulted the
        # preference at all.
        self.cur.execute("UPDATE users SET profile_visibility='private' WHERE user_id=?", (OWNER,))
        _set_command_center(self.cur, OWNER, "none")
        self.assertFalse(self._can_message(FRIEND))

    def test_a_private_profile_still_lets_an_accepted_friend_message_by_default(self):
        # The guard above must not have closed the normal private-profile case.
        self.cur.execute("UPDATE users SET profile_visibility='private' WHERE user_id=?", (OWNER,))
        self.assertTrue(self._can_message(FRIEND))

    def test_the_preference_does_not_leak_into_other_permissions(self):
        # Closing an inbox is not the same as closing a profile. A viewer who
        # cannot message must still be able to see public content, and must
        # still be able to report and block.
        _set_command_center(self.cur, OWNER, "none")
        perms = profile_viewer_permissions.viewer_permissions(self.cur, OWNER, STRANGER)
        self.assertFalse(perms["can_message"])
        self.assertTrue(perms["can_view_public_profile"])
        self.assertTrue(perms["can_view_public_media"])
        self.assertTrue(perms["can_report"])
        self.assertTrue(perms["can_block"])

    def test_the_owner_is_unaffected_by_their_own_setting(self):
        _set_command_center(self.cur, OWNER, "none")
        perms = profile_viewer_permissions.viewer_permissions(self.cur, OWNER, OWNER)
        self.assertTrue(perms["can_view_public_profile"])


class _KeepOpen:
    """A connection whose ``close()`` does nothing.

    ``create_conversation`` closes its connection in a ``finally``, which is
    correct in production and fatal here: closing an in-memory sqlite database
    destroys it, so the second call in a test would see an empty schema. Every
    other attribute passes straight through, including ``commit``/``rollback``,
    so the function's real transaction behaviour is still exercised.
    """

    def __init__(self, conn):
        self._conn = conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


class ConversationGate(unittest.TestCase):
    """The server refuses to *open* the thread. This is the half that matters.

    ``can_message`` is a rendering hint. If only the hint were fixed, a client
    that posted the request anyway — an older build, a replayed request, curl —
    would still get a conversation, which is precisely the "hidden button is
    not access control" failure ``profile_viewer_permissions`` warns about in
    its own docstring.

    These drive the real ``create_conversation`` against the real comm_v2 DDL
    (``models.ensure_schema``), not a reimplementation of it, so the test
    exercises the branch ordering as shipped.
    """

    def setUp(self):
        from pulse_communications_v2 import models as comm_models
        from pulse_communications_v2 import service as comm_service

        self.conn, self.cur = _db()
        self.addCleanup(self.conn.close)
        comm_models.ensure_schema(self.cur)
        self.conn.commit()

        self.service = comm_service
        # Bypass the bot import (which would run init_db against the dev
        # database) and the feature flag, leaving the gate under test intact.
        self._restore = (comm_service._open_db, comm_service._disabled)
        comm_service._open_db = lambda: (_KeepOpen(self.conn), self.cur)
        comm_service._disabled = lambda action: None
        self.addCleanup(self._put_back)

    def _put_back(self):
        self.service._open_db, self.service._disabled = self._restore

    def _open(self, sender, target):
        return self.service.create_conversation(
            sender, {"conversation_type": "direct", "target_user_id": target}
        )

    def _conversation_count(self):
        self.cur.execute("SELECT COUNT(*) FROM comm_v2_conversations")
        return int(self.cur.fetchone()[0])

    def test_a_stranger_can_open_a_thread_by_default(self):
        # Control case. Without this, every assertion below could pass because
        # conversation creation is broken for an unrelated reason.
        result = self._open(STRANGER, OWNER)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(1, self._conversation_count())

    def test_a_closed_inbox_refuses_a_stranger(self):
        _set_command_center(self.cur, OWNER, "none")
        result = self._open(STRANGER, OWNER)
        self.assertFalse(result.get("ok"), result)
        self.assertEqual("message_requests_closed", result.get("status"))
        self.assertEqual(403, result.get("http_status"))
        # The refusal must be a refusal, not a message into a created thread.
        self.assertEqual(0, self._conversation_count())

    def test_the_native_store_also_closes_the_gate(self):
        # The two settings screens are independent writers; the gate must read
        # the same pair the hint does, or the native screen would do nothing.
        _set_native(self.cur, OWNER, "nobody")
        result = self._open(STRANGER, OWNER)
        self.assertFalse(result.get("ok"), result)
        self.assertEqual("message_requests_closed", result.get("status"))
        self.assertEqual(0, self._conversation_count())

    def test_followers_only_admits_a_follower_and_refuses_a_stranger(self):
        _set_command_center(self.cur, OWNER, "followers")
        refused = self._open(STRANGER, OWNER)
        self.assertFalse(refused.get("ok"), refused)
        self.assertEqual(0, self._conversation_count())

        allowed = self._open(FOLLOWER, OWNER)
        self.assertTrue(allowed.get("ok"), allowed)
        self.assertEqual(1, self._conversation_count())

    def test_tightening_the_setting_does_not_sever_an_existing_thread(self):
        """The reason the gate sits below the existing-conversation branch.

        The control is "Message requests" in both UIs: it governs who may
        start a conversation. A settings toggle must not silently destroy
        access to live history the owner already consented to.
        """
        opened = self._open(STRANGER, OWNER)
        self.assertTrue(opened.get("ok"), opened)
        conversation_id = opened["conversation_id"]

        _set_command_center(self.cur, OWNER, "none")
        self.conn.commit()

        again = self._open(STRANGER, OWNER)
        self.assertTrue(again.get("ok"), again)
        self.assertEqual(conversation_id, again["conversation_id"])
        self.assertEqual(1, self._conversation_count())

    def test_the_owners_own_setting_never_locks_them_out(self):
        _set_command_center(self.cur, OWNER, "none")
        # OWNER reaching *out* is unaffected; the preference is about OWNER's
        # inbox, not OWNER's outbox.
        result = self._open(OWNER, STRANGER)
        self.assertTrue(result.get("ok"), result)


class LegacyStartConversationPath(unittest.TestCase):
    """`bot.pulse_start_conversation` is lower-traffic but still routed.

    comm_v2 carries live messaging; this legacy path backs
    ``/api/pulse/messages/start`` and is the kind of second door that makes a
    privacy gate decorative. Asserted statically: importing ``bot`` runs
    ``init_db()`` at module scope against a real database, which is far too
    expensive — and too destructive — for a unit test.

    Parsed with ``ast``, never grepped. The source now *mentions*
    ``private_chat_blocks`` in a comment explaining why it was removed, so a
    text search would match the documentation of the fix and report the
    defect as still present. Comments do not survive into the AST.
    """

    @classmethod
    def setUpClass(cls):
        import ast

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "bot.py"), encoding="utf-8") as handle:
            cls.tree = ast.parse(handle.read())
        matches = [
            node
            for node in ast.walk(cls.tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "pulse_start_conversation"
        ]
        # If this ever finds two, the assertions below might be inspecting the
        # wrong one and silently passing.
        assert len(matches) == 1, f"expected one definition, found {len(matches)}"
        cls.function = matches[0]

    def _string_constants(self, node):
        import ast

        return [
            child.value
            for child in ast.walk(node)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        ]

    def test_the_block_check_reads_the_table_the_block_button_writes(self):
        sql = " ".join(self._string_constants(self.function))
        self.assertIn("blocked_users", sql)

    def test_the_phantom_block_table_is_gone_from_the_whole_module(self):
        """No executable reference anywhere, not just in this function.

        It only ever appeared once, inside a ``try/except: pass`` — so the
        "no such table" error was swallowed on every request and the check
        had never denied anything. A silent no-op leaves no trace in logs,
        which is why it survived; this keeps it from coming back.
        """
        hits = [
            value
            for value in self._string_constants(self.tree)
            if "private_chat_blocks" in value
        ]
        self.assertEqual([], hits)

    def test_the_preference_gate_is_present_on_this_path_too(self):
        import ast

        called = {
            node.func.attr
            for node in ast.walk(self.function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertIn(
            "may_message",
            called,
            "/api/pulse/messages/start would be a way around the setting.",
        )


class DeadColumnsAreGone(unittest.TestCase):
    """The columns the old code read do not exist and must not come back.

    Pinned because the failure was invisible: reading an absent key returns
    ``None``, falls through to the permissive default, and nothing errors. A
    future edit that "restores" the old lookup would be silently inert again.
    """

    def setUp(self):
        self.conn, self.cur = _db()
        self.addCleanup(self.conn.close)

    def test_a_users_row_column_cannot_drive_the_preference(self):
        # Even if something later adds these columns, they are not the store
        # the settings screens write, so they must not be consulted.
        self.cur.execute("ALTER TABLE users ADD COLUMN message_privacy TEXT")
        self.cur.execute("ALTER TABLE users ADD COLUMN dm_privacy TEXT")
        self.cur.execute(
            "UPDATE users SET message_privacy='nobody', dm_privacy='nobody' WHERE user_id=?", (OWNER,)
        )
        # No user_settings row: the real control says nothing, so messaging is
        # open regardless of what the dead columns claim.
        self.assertTrue(message_privacy.may_message(self.cur, OWNER, STRANGER))

    def test_source_no_longer_reads_the_dead_columns(self):
        """No executable statement in the module may read the dead columns.

        Parsed with ``ast`` rather than grepped. A text search cannot tell a
        live ``account.get("message_privacy")`` apart from the same string
        inside the docstring that explains why it was removed — and this
        module's docstrings name both columns deliberately. Comments and
        docstrings are *documentation of the fix*; only code can regress it.
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(profile_viewer_permissions))
        # Drop every docstring node, so only executable code is inspected.
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]

        dead = {"message_privacy", "dm_privacy"}
        # `account.get("dm_privacy")` — a call whose argument names a dead
        # column. Catches the exact shape of the defect regardless of which
        # object it is read from.
        offenders = [
            arg.value
            for call in ast.walk(tree)
            if isinstance(call, ast.Call)
            for arg in call.args
            if isinstance(arg, ast.Constant) and arg.value in dead
        ]
        # ...and subscript form, `account["dm_privacy"]`.
        offenders += [
            node.slice.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value in dead
        ]
        self.assertEqual(
            [],
            offenders,
            "profile_viewer_permissions reads a column no writer has ever "
            "created; the preference would silently deny nothing again.",
        )


if __name__ == "__main__":
    unittest.main()
