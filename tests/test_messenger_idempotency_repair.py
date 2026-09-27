"""The repair script must refuse every group it cannot prove is safe to collapse.

`scripts/messenger_idempotency_repair.py` soft-deletes duplicate Messenger sends
so the unique index can install. It is the only tool in the repo that writes to
the table holding real people's messages, and the judgement it automates -- "these
two rows are one send, retried" -- is exactly the judgement the audit script
refuses to make.

So the tests that matter are the refusals. The first version of this script
classified a group whose members had *different bodies* as repairable: `plan()`
dropped the body from each member before handing them to `_classify`, which then
compared `None` to `None`, found one distinct value, and cleared the group. The
check that exists to stop two distinct messages being collapsed into one was
inert, and no output revealed it -- both rows printed the same `body_len`, because
the bodies were the same length.

These tests therefore hold: that each disqualifying condition actually disqualifies,
that a clean group is still repairable (a script that refuses everything is safe
and useless), that applying stamps exactly the losers and destroys nothing, and
that no message body reaches the output.
"""

import importlib.util
import os
import sqlite3
import tempfile
import unittest

os.environ.setdefault("DATABASE_URL", "")

from pulse_communications_v2.models import ensure_schema  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "messenger_idempotency_repair.py")


def _load_script():
    spec = importlib.util.spec_from_file_location("messenger_idempotency_repair", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


repair = _load_script()


class _RepairCase(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.cursor()
        ensure_schema(cur)
        self.conn.commit()
        self._next_id = 1
        # The script resolves its target through DATABASE_URL, so a run here
        # would otherwise leak a path into every later test in this process.
        self._saved_url = os.environ.get("DATABASE_URL")

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)
        if self._saved_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._saved_url

    def _send(self, conversation, sender, client_id, body, message_type="text", deleted=None):
        message_id = self._next_id
        self._next_id += 1
        self.conn.execute(
            "INSERT INTO comm_v2_messages (id, public_id, conversation_id, sender_user_id,"
            " client_message_id, body, message_type, created_at, deleted_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                message_id,
                f"pub-{message_id}",
                conversation,
                sender,
                client_id,
                body,
                message_type,
                "2026-09-01T00:00:00Z",
                deleted,
            ),
        )
        self.conn.commit()
        return message_id

    def _duplicate(self, body="hello", message_type="text"):
        """One send stored twice -- the shape the script exists to collapse."""
        first = self._send(10, 5, "native-abc", body, message_type)
        second = self._send(10, 5, "native-abc", body, message_type)
        return first, second

    def _plan(self):
        return repair.plan(self.path)

    def _only_group(self):
        result = self._plan()
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(len(result["groups"]), 1, result["groups"])
        return result["groups"][0]

    def _live_ids(self):
        rows = self.conn.execute(
            "SELECT id FROM comm_v2_messages WHERE COALESCE(deleted_at,'') = '' ORDER BY id"
        ).fetchall()
        return [row["id"] for row in rows]

    def _all_ids(self):
        rows = self.conn.execute("SELECT id FROM comm_v2_messages ORDER BY id").fetchall()
        return [row["id"] for row in rows]


class RefusalTest(_RepairCase):
    """Every condition that must send the group back to a human."""

    def test_members_with_different_bodies_are_never_collapsed(self):
        # The regression. Both bodies are the same length on purpose: a check
        # that compares body_len instead of body passes this group as repairable.
        self._send(10, 5, "native-abc", "one")
        self._send(10, 5, "native-abc", "two")
        group = self._only_group()
        self.assertEqual(group["verdict"], "needs_human")
        self.assertEqual(group["retire_ids"], [])
        self.assertIn(
            "members have different bodies -- these may be distinct messages",
            group["reasons"],
        )

    def test_members_with_different_message_types_are_never_collapsed(self):
        self._send(10, 5, "native-abc", "hello", message_type="text")
        self._send(10, 5, "native-abc", "hello", message_type="image")
        group = self._only_group()
        self.assertEqual(group["verdict"], "needs_human")
        self.assertEqual(group["retire_ids"], [])
        self.assertIn("members have different message types", group["reasons"])

    def test_a_loser_carrying_a_reaction_is_never_collapsed(self):
        _, loser = self._duplicate()
        self.conn.execute(
            "INSERT INTO comm_v2_message_reactions (message_id, conversation_id, user_id,"
            " reaction_type, created_at) VALUES (?,?,?,?,?)",
            (loser, 10, 9, "like", "2026-09-01T00:00:00Z"),
        )
        self.conn.commit()
        group = self._only_group()
        self.assertEqual(group["verdict"], "needs_human")
        self.assertEqual(group["retire_ids"], [])
        self.assertIn(f"id={loser} has 1 reactions", group["reasons"])

    def test_a_loser_with_a_reply_pointing_at_it_is_never_collapsed(self):
        _, loser = self._duplicate()
        self.conn.execute(
            "UPDATE comm_v2_messages SET reply_to_message_id=? WHERE id=?",
            (loser, self._send(10, 7, "native-reply", "answering that")),
        )
        self.conn.commit()
        group = next(g for g in self._plan()["groups"] if g["row_count"] > 1)
        self.assertEqual(group["verdict"], "needs_human")
        self.assertIn(f"id={loser} has 1 replies", group["reasons"])

    def test_a_loser_a_conversation_still_points_at_is_never_collapsed(self):
        _, loser = self._duplicate()
        self.conn.execute(
            "INSERT INTO comm_v2_conversations (id, conversation_type, created_at,"
            " last_message_id) VALUES (?,?,?,?)",
            (10, "direct", "2026-09-01T00:00:00Z", loser),
        )
        self.conn.commit()
        group = self._only_group()
        self.assertEqual(group["verdict"], "needs_human")
        self.assertIn(f"id={loser} has 1 conversation_pointers", group["reasons"])

    def test_a_loser_someone_has_read_up_to_is_never_collapsed(self):
        _, loser = self._duplicate()
        self.conn.execute(
            "INSERT INTO comm_v2_participants (conversation_id, user_id, role, joined_at,"
            " last_read_message_id) VALUES (?,?,?,?,?)",
            (10, 7, "member", "2026-09-01T00:00:00Z", loser),
        )
        self.conn.commit()
        group = self._only_group()
        self.assertEqual(group["verdict"], "needs_human")
        self.assertIn(f"id={loser} has 1 read_pointers", group["reasons"])

    def test_a_refused_group_survives_an_apply_untouched(self):
        self._send(10, 5, "native-abc", "one")
        self._send(10, 5, "native-abc", "two")
        before = self._live_ids()
        result = repair.apply(self.path)
        self.assertEqual(result["applied"], 0)
        self.assertEqual(self._live_ids(), before)


class RepairableTest(_RepairCase):
    """A script that refuses everything is safe and useless."""

    def test_one_send_stored_twice_is_repairable(self):
        first, second = self._duplicate()
        group = self._only_group()
        self.assertEqual(group["verdict"], "repairable")
        self.assertEqual(group["reasons"], [])
        self.assertEqual(group["survivor_id"], first)
        self.assertEqual(group["retire_ids"], [second])

    def test_the_survivor_is_the_lowest_id(self):
        first, second = self._duplicate()
        self.assertLess(first, second)
        self.assertEqual(self._only_group()["survivor_id"], first)

    def test_an_attachment_on_a_loser_does_not_block_the_group(self):
        # Both copies of one send point at the same upload, so retiring the
        # loser strands nothing. Attachments are reported, never disqualifying.
        first, second = self._duplicate()
        for message_id in (first, second):
            self.conn.execute(
                "INSERT INTO comm_v2_attachments (message_id, conversation_id,"
                " media_upload_id, media_type, created_at) VALUES (?,?,?,?,?)",
                (message_id, 10, 60, "image", "2026-09-01T00:00:00Z"),
            )
        self.conn.commit()
        group = self._only_group()
        self.assertEqual(group["verdict"], "repairable")
        self.assertEqual(group["retire_ids"], [second])

    def test_a_lone_send_is_not_a_group(self):
        self._send(10, 5, "native-solo", "hello")
        self.assertEqual(self._plan()["groups"], [])

    def test_blank_and_null_client_ids_are_not_duplicates_of_each_other(self):
        # Legacy and server-authored rows carry no client id. They are not
        # claims of identity, and the index predicate excludes them too.
        self._send(10, 5, "", "hello")
        self._send(10, 5, "", "hello")
        self._send(10, 5, None, "hello")
        self._send(10, 5, None, "hello")
        self.assertEqual(self._plan()["groups"], [])


class ApplyTest(_RepairCase):
    def test_apply_stamps_the_loser_and_keeps_the_survivor(self):
        first, second = self._duplicate()
        result = repair.apply(self.path)
        self.assertEqual(result["applied"], 1)
        self.assertEqual(self._live_ids(), [first])

    def test_apply_destroys_nothing(self):
        first, second = self._duplicate()
        repair.apply(self.path)
        self.assertEqual(self._all_ids(), [first, second])
        row = self.conn.execute(
            "SELECT body, deleted_at FROM comm_v2_messages WHERE id=?", (second,)
        ).fetchone()
        self.assertEqual(row["body"], "hello")
        self.assertTrue(row["deleted_at"])

    def test_a_second_run_finds_nothing_left_to_do(self):
        self._duplicate()
        self.assertEqual(repair.apply(self.path)["applied"], 1)
        again = repair.apply(self.path)
        self.assertEqual(again["applied"], 0)
        self.assertEqual(again["duplicate_groups"], 0)

    def test_an_already_retired_row_keeps_its_original_timestamp(self):
        # Re-stamping a row retired months ago would rewrite when it happened.
        first, second = self._duplicate()
        third = self._send(10, 5, "native-abc", "hello", deleted="2026-01-01T00:00:00Z")
        repair.apply(self.path)
        row = self.conn.execute(
            "SELECT deleted_at FROM comm_v2_messages WHERE id=?", (third,)
        ).fetchone()
        self.assertEqual(row["deleted_at"], "2026-01-01T00:00:00Z")
        self.assertEqual(self._live_ids(), [first])

    def test_an_already_retired_row_does_not_count_towards_a_group(self):
        first = self._send(10, 5, "native-abc", "hello")
        self._send(10, 5, "native-abc", "hello", deleted="2026-01-01T00:00:00Z")
        self.assertEqual(self._plan()["groups"], [])
        self.assertEqual(self._live_ids(), [first])

    def test_planning_alone_writes_nothing(self):
        first, second = self._duplicate()
        self.assertEqual(self._plan()["rows_to_retire"], 1)
        self.assertEqual(self._live_ids(), [first, second])


class OutputTest(_RepairCase):
    def test_no_message_body_reaches_the_plan(self):
        self._duplicate(body="a private thing someone typed")
        for group in self._plan()["groups"]:
            for member in group["members"]:
                self.assertNotIn("body", member)
                self.assertEqual(member["body_len"], len("a private thing someone typed"))

    def test_a_blocked_group_labels_no_row_as_retiring(self):
        # The label states what this run will do. A group that needs a human
        # has no retire_ids, so nothing in it may print as "retire".
        self._send(10, 5, "native-abc", "one")
        self._send(10, 5, "native-abc", "two")
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            repair._print_human(self._plan(), applied=False)
        printed = buffer.getvalue()
        self.assertIn("[needs_human]", printed)
        self.assertNotIn("retire id=", printed)

    def test_apply_without_a_backup_flag_writes_nothing(self):
        first, second = self._duplicate()
        import contextlib
        import io
        import sys

        argv = sys.argv
        sys.argv = [
            "messenger_idempotency_repair.py",
            "--database-url",
            self.path,
            "--apply",
        ]
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                code = repair.main()
        finally:
            sys.argv = argv
        self.assertEqual(code, 2)
        self.assertIn("--i-have-a-backup", buffer.getvalue())
        self.assertEqual(self._live_ids(), [first, second])


if __name__ == "__main__":
    unittest.main()
