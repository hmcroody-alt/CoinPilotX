"""Native Unicode emoji foundation — DB round-trip + API contract (Stages 14-16).

The emoji mission's core storage rule is that the backend persists the Unicode
value itself — never an image URL, vendor ID, or shortcode. These tests pin that
rule end-to-end against the canonical v2 messaging stack (``comm_v2_*``), which
is the only messaging API the mobile client speaks:

1.  Message bodies containing multi-codepoint emoji survive write -> read
    byte-identically (ZWJ family, skin tone, flag/regional indicators, VS16).
2.  ``set_reaction`` stores the raw emoji cluster exactly and keeps one row per
    (message, user): switching replaces, "none" clears, counts stay accurate.
3.  ``bot.pulse_normalize_emoji_reaction`` (the v1 endpoint's validator) accepts
    every required emoji unchanged, maps legacy aliases, and rejects non-emoji —
    the grapheme-aware payload contract of Stage 16.

Runs against a temp sqlite file so nothing touches coinpilotx.db. Command-center
and push side effects are stubbed: they run after the commit under test and are
network-bound, so stubbing them keeps the suite hermetic without weakening any
assertion about what was persisted.

Required round-trip set (from the mission spec):
    😂  U+1F602                      single codepoint
    ❤️  U+2764 U+FE0F               VS16 presentation
    👨‍👩‍👧‍👦  4 people + 3 ZWJ            multi-codepoint family
    👍🏿  U+1F44D U+1F3FF             Fitzpatrick skin tone
    🇭🇹  U+1F1ED U+1F1F9             regional-indicator pair (flag)
"""

import itertools
import os
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB_PATH = os.environ.get("PULSESOC_EMOJI_TEST_DB", "")
if not _DB_PATH:
    _HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="emoji_roundtrip_")
    os.close(_HANDLE)
    os.environ["PULSESOC_EMOJI_TEST_DB"] = _DB_PATH
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bot_importable() -> bool:
    try:
        import bot  # noqa: F401

        return True
    except Exception:
        return False


_HAVE_REAL_BOT = _bot_importable()

if not _HAVE_REAL_BOT:
    # Sandbox fallback: bot.py needs third-party packages (stripe, flask, …)
    # that a hermetic environment may not have. The code under test here is
    # pulse_communications_v2 (models.ensure_schema + service), which only
    # needs three things from bot: db(), sqlite3, and (optionally) the
    # additive-column helpers. Provide exactly those, nothing else — the
    # service and schema code paths stay 100% real.
    import sqlite3 as _sqlite3
    import types as _types

    def _shim_db():
        url = os.environ.get("DATABASE_URL") or ""
        path = url.split("sqlite:///", 1)[1] if "sqlite:///" in url else url
        conn = _sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, display_name TEXT, avatar_url TEXT)"
        )
        return conn

    _shim = _types.ModuleType("bot")
    _shim.db = _shim_db
    _shim.sqlite3 = _sqlite3
    sys.modules["bot"] = _shim

from pulse_communications_v2 import service  # noqa: E402

REQUIRED_EMOJI = ["😂", "❤️", "👨‍👩‍👧‍👦", "👍🏿", "🇭🇹"]

USER_A = 9501
USER_B = 9502

_CONVERSATION_IDS = itertools.count(9100)


def _use_module_database():
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"


def _seed_direct_conversation(cur, conn):
    """A 1:1 conversation with both users active, plus one seed message."""
    conversation_id = next(_CONVERSATION_IDS)
    now = service._now()
    cur.execute(
        """
        INSERT INTO comm_v2_conversations
        (id, public_id, conversation_type, privacy, status, created_at, updated_at)
        VALUES (?, ?, 'direct', 'private', 'active', ?, ?)
        """,
        (conversation_id, f"conv_{uuid.uuid4().hex[:12]}", now, now),
    )
    for user_id in (USER_A, USER_B):
        cur.execute(
            """
            INSERT INTO comm_v2_participants
            (conversation_id, user_id, role, membership_state, left_at, created_at, updated_at)
            VALUES (?, ?, 'member', 'active', '', ?, ?)
            """,
            (conversation_id, user_id, now, now),
        )
    cur.execute(
        """
        INSERT INTO comm_v2_messages
        (public_id, conversation_id, sender_user_id, message_type, body, delivery_status, created_at, updated_at)
        VALUES (?, ?, ?, 'text', 'seed', 'sent', ?, ?)
        """,
        (f"msg_{uuid.uuid4().hex[:12]}", conversation_id, USER_A, now, now),
    )
    cur.execute("SELECT id FROM comm_v2_messages WHERE conversation_id=? ORDER BY id DESC LIMIT 1", (conversation_id,))
    message_id = int(dict(cur.fetchone())["id"])
    conn.commit()
    return conversation_id, message_id


class EmojiUnicodeRoundTripTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _use_module_database()
        # Build the schema once through the real bootstrap path.
        conn, cur = service._open_db()
        conn.close()

    def setUp(self):
        _use_module_database()
        self._real_dispatch = service._dispatch_command_center_async
        self._real_side_effects = service._dispatch_message_side_effects
        service._dispatch_command_center_async = lambda *a, **k: False
        service._dispatch_message_side_effects = lambda *a, **k: {}
        self.conn, self.cur = service._open_db()
        self.conversation_id, self.message_id = _seed_direct_conversation(self.cur, self.conn)

    def tearDown(self):
        service._dispatch_command_center_async = self._real_dispatch
        service._dispatch_message_side_effects = self._real_side_effects
        try:
            self.conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ bodies

    def test_message_body_round_trips_every_required_emoji_byte_identically(self):
        body = "Test " + " ".join(REQUIRED_EMOJI)
        result = service.send_message(USER_A, self.conversation_id, {"body": body})
        self.assertTrue(result.get("ok"), result)
        stored = result["message"]["body"]
        self.assertEqual(stored, body)
        # And straight back off disk, not just the in-process echo.
        self.cur.execute("SELECT body FROM comm_v2_messages WHERE id=?", (int(result["message"]["message_id"] or result["message"].get("id") or 0),))
        row = self.cur.fetchone()
        if row is not None:
            self.assertEqual(dict(row)["body"], body)

    # ---------------------------------------------------------------- reactions

    def test_each_required_emoji_reaction_is_stored_exactly(self):
        for emoji in REQUIRED_EMOJI:
            result = service.set_reaction(USER_A, self.message_id, emoji)
            self.assertTrue(result.get("ok"), result)
            self.assertEqual(result["message"]["my_reaction"], emoji)
            self.cur.execute(
                "SELECT reaction_type FROM comm_v2_message_reactions WHERE message_id=? AND user_id=?",
                (self.message_id, USER_A),
            )
            rows = [dict(r)["reaction_type"] for r in self.cur.fetchall()]
            self.assertEqual(rows, [emoji], f"expected exactly one stored row equal to {emoji!r}, got {rows!r}")

    def test_switching_reaction_replaces_rather_than_accumulates(self):
        service.set_reaction(USER_A, self.message_id, "😂")
        service.set_reaction(USER_A, self.message_id, "👍🏿")
        self.cur.execute(
            "SELECT reaction_type FROM comm_v2_message_reactions WHERE message_id=? AND user_id=?",
            (self.message_id, USER_A),
        )
        rows = [dict(r)["reaction_type"] for r in self.cur.fetchall()]
        self.assertEqual(rows, ["👍🏿"])

    def test_counts_are_accurate_across_two_users(self):
        service.set_reaction(USER_A, self.message_id, "❤️")
        result = service.set_reaction(USER_B, self.message_id, "❤️")
        reactions = {r["reaction_type"]: r["count"] for r in result["message"]["reactions"]}
        self.assertEqual(reactions.get("❤️"), 2)

    def test_none_clears_the_viewer_reaction(self):
        service.set_reaction(USER_A, self.message_id, "🇭🇹")
        result = service.set_reaction(USER_A, self.message_id, "none")
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["message"]["my_reaction"], "")
        self.cur.execute(
            "SELECT COUNT(*) AS n FROM comm_v2_message_reactions WHERE message_id=? AND user_id=?",
            (self.message_id, USER_A),
        )
        self.assertEqual(int(dict(self.cur.fetchone())["n"]), 0)


class EmojiReactionContractTest(unittest.TestCase):
    """Stage 16: grapheme-aware validation on the v1 normalizer in bot.py."""

    @classmethod
    def setUpClass(cls):
        _use_module_database()
        if _HAVE_REAL_BOT:
            import bot  # heavy import, shared across the class

            cls.normalize = staticmethod(bot.pulse_normalize_emoji_reaction)
            return
        # Sandbox fallback: execute the real function source extracted from
        # bot.py — it depends only on stdlib unicodedata.
        import ast
        import unicodedata

        with open(os.path.join(REPO_ROOT, "bot.py"), encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source)
        node = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "pulse_normalize_emoji_reaction"
        )
        namespace = {"unicodedata": unicodedata}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
        cls.normalize = staticmethod(namespace["pulse_normalize_emoji_reaction"])

    def test_required_emoji_pass_through_unchanged(self):
        for emoji in REQUIRED_EMOJI:
            self.assertEqual(self.normalize(emoji), emoji)

    def test_legacy_aliases_map_to_unicode(self):
        self.assertEqual(self.normalize("heart"), "❤️")
        self.assertEqual(self.normalize("like"), "👍")
        self.assertEqual(self.normalize("laugh"), "😂")

    def test_non_emoji_is_rejected(self):
        for bad in ("hello", "", None, "<script>", "abc123"):
            self.assertEqual(self.normalize(bad), "")


if __name__ == "__main__":
    unittest.main()
