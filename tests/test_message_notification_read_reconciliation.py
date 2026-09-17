"""The server half of "a read message's notification goes away".

A delivered iOS notification can only be removed by the device that holds it,
and only if it can first establish that the message behind it has been read.
That question turns out to be harder than it looks, and these tests pin the two
decisions that make the answer trustworthy.

DECISION 1 -- the watermark is not the authority, `read_at` is.

`comm_v2_participants.last_read_message_id` is a high-water mark stamped from
`MAX(id)` at the moment of reading. Ids come from a sequence that can hand out
100 before 99 and commit them in the other order, so a message can become
visible *below* a watermark that has already passed it. `mark_read`'s own
receipt writer anti-joins on the receipt row rather than bounding by the
watermark for exactly this reason.

It matters far more here than it does there. A skipped receipt row is
self-healing and invisible. A notification dismissed because its id happened to
fall under a watermark is a message the user is never told about -- the alert is
gone, the badge was recomputed without it, and nothing in the product will ever
mention it again. `test_read_state_does_not_trust_the_watermark` is the guard,
and it is the single most important test in this file.

DECISION 2 -- read receipts are a *publishing* setting, not a recording one.

`_read_receipts_allowed` used to gate the whole receipt write. A member who
switched read receipts off therefore produced no read record at all, and
"has this user read message N" became permanently unanswerable for them -- so
their notifications could never be reconciled, which is the population most
likely to have privacy turned up and least likely to want a pile of stale
alerts naming who messaged them.

The gate now covers `seen_at` alone. That is the sender-visible column: the
"Seen" tick in `_message_payload`/`_message_payloads` is rendered from it and
from nothing else. `read_at` is reader-private -- no query in this codebase
returns it to anybody but its owner -- and is always written. The tests below
assert both halves, because a change that fixed reconciliation by quietly
publishing read state to senders would be a worse bug than the one it fixed.

Run: python3 -m pytest tests/test_message_notification_read_reconciliation.py
"""

import json
import os
import re
import sqlite3
import unittest

os.environ.setdefault("DATABASE_URL", "")

from pulse_communications_v2 import service  # noqa: E402
from pulse_communications_v2.models import ensure_schema  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICE_SOURCE = open(
    os.path.join(ROOT, "pulse_communications_v2", "service.py"), encoding="utf-8"
).read()

READER = 11
SENDER = 12
STRANGER = 13
CONVERSATION = 5
STAMP = "2026-01-01T00:00:00+00:00"


class _KeepOpenConnection:
    """Service functions close what they are handed; the fixture needs the
    in-memory database to survive more than one call."""

    def __init__(self, conn):
        self._conn = conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


class ReadStateFixture(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.cur = self.conn.cursor()
        ensure_schema(self.cur)
        self.cur.execute(
            "CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, "
            "display_name TEXT, avatar_url TEXT, email TEXT)"
        )
        for user_id, name in ((READER, "reader"), (SENDER, "sender"), (STRANGER, "stranger")):
            self.cur.execute(
                "INSERT INTO users (user_id, username, display_name) VALUES (?,?,?)",
                (user_id, name, name),
            )
            self.set_receipts(user_id, True)
        self.cur.execute(
            "INSERT INTO comm_v2_conversations (id, public_id, conversation_type, privacy, created_at, updated_at) "
            "VALUES (?, 'conv-read-state', 'direct', 'private', ?, ?)",
            (CONVERSATION, STAMP, STAMP),
        )
        for user_id in (READER, SENDER):
            self.join(user_id)
        self._real_open_db = service._open_db
        self._real_dispatch = service._dispatch_command_center_async
        self.shared = _KeepOpenConnection(self.conn)
        service._open_db = lambda: (self.shared, self.cur)
        service._dispatch_command_center_async = lambda *a, **k: True
        self.conn.commit()

    def tearDown(self):
        service._open_db = self._real_open_db
        service._dispatch_command_center_async = self._real_dispatch
        self.conn.close()

    # -- fixture helpers ------------------------------------------------

    def set_receipts(self, user_id: int, enabled: bool):
        self.cur.execute("DELETE FROM comm_v2_user_settings WHERE user_id=?", (user_id,))
        self.cur.execute(
            "INSERT INTO comm_v2_user_settings (user_id, read_receipts_enabled, updated_at) VALUES (?,?,?)",
            (user_id, 1 if enabled else 0, STAMP),
        )

    def join(self, user_id: int, conversation_id: int = CONVERSATION):
        self.cur.execute(
            "INSERT INTO comm_v2_participants (conversation_id, user_id, role, membership_state, joined_at, created_at, updated_at) "
            "VALUES (?,?,'member','active',?,?,?)",
            (conversation_id, user_id, STAMP, STAMP, STAMP),
        )

    def say(self, body: str, sender: int = SENDER, message_id: int | None = None) -> int:
        if message_id is None:
            self.cur.execute(
                "INSERT INTO comm_v2_messages (conversation_id, sender_user_id, message_type, body, created_at, updated_at) "
                "VALUES (?,?,'text',?,?,?)",
                (CONVERSATION, sender, body, STAMP, STAMP),
            )
        else:
            self.cur.execute(
                "INSERT INTO comm_v2_messages (id, conversation_id, sender_user_id, message_type, body, created_at, updated_at) "
                "VALUES (?,?,?,'text',?,?,?)",
                (message_id, CONVERSATION, sender, body, STAMP, STAMP),
            )
        return int(self.cur.lastrowid)

    def receipt(self, message_id: int, user_id: int = READER) -> dict:
        self.cur.execute(
            "SELECT delivered_at, seen_at, read_at FROM comm_v2_read_receipts WHERE message_id=? AND user_id=?",
            (message_id, user_id),
        )
        row = self.cur.fetchone()
        return {k: (row[k] or "") for k in ("delivered_at", "seen_at", "read_at")} if row else {}

    def watermark(self, user_id: int = READER) -> int:
        self.cur.execute(
            "SELECT last_read_message_id FROM comm_v2_participants WHERE conversation_id=? AND user_id=?",
            (CONVERSATION, user_id),
        )
        row = self.cur.fetchone()
        return int((row["last_read_message_id"] if row else 0) or 0)

    def classify(self, message_ids, user_id: int = READER) -> dict:
        result = service.message_notification_read_state(user_id, message_ids)
        self.assertTrue(result.get("ok"), result)
        return result


class ReadRecordingIsNotAPublishingDecision(ReadStateFixture):
    """DECISION 2. Turning read receipts off must hide the tick, not erase the
    server's own record of what its user has read."""

    def test_read_at_is_recorded_even_when_read_receipts_are_off(self):
        self.set_receipts(READER, False)
        message_id = self.say("hello")
        self.assertTrue(service.mark_read(READER, CONVERSATION).get("ok"))
        receipt = self.receipt(message_id)
        self.assertTrue(
            receipt.get("read_at"),
            "a reader with receipts off produced no read record, so their delivered "
            "notifications can never be reconciled -- this is the whole defect",
        )

    def test_seen_at_stays_unset_when_read_receipts_are_off(self):
        self.set_receipts(READER, False)
        message_id = self.say("hello")
        service.mark_read(READER, CONVERSATION)
        self.assertEqual(
            self.receipt(message_id).get("seen_at"),
            "",
            "the sender-visible column was written for a reader who switched "
            "receipts off -- that is a privacy regression, not a fix",
        )

    def test_the_sender_is_still_told_nothing(self):
        """The assertion that actually matters to a user: what the OTHER side sees.

        Checking the column is checking the mechanism. This checks the rendered
        claim, so a future change that derives the tick from `read_at` instead
        fails here even though the column assertion above would still pass.
        """
        self.set_receipts(READER, False)
        message_id = self.say("hello")
        service.mark_read(READER, CONVERSATION)
        self.cur.execute("SELECT * FROM comm_v2_messages WHERE id=?", (message_id,))
        payload = service._message_payload(self.cur, dict(self.cur.fetchone()), SENDER)
        self.assertNotEqual(
            payload["delivery_status"],
            "seen",
            "the sender was shown a Seen tick by a reader who had receipts switched off",
        )

    def test_a_conversation_level_optout_is_honoured_too(self):
        """`_read_receipts_allowed` has two gates. The global one is easy to test
        and easy to be the only one anybody checks."""
        self.set_receipts(READER, True)
        self.cur.execute(
            "INSERT INTO comm_v2_conversation_settings (conversation_id, user_id, privacy_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (CONVERSATION, READER, json.dumps({"read_receipts": False}), STAMP, STAMP),
        )
        # Positive control: the row above has to actually reach the gate, or the
        # assertions below are testing the global setting a second time.
        self.assertFalse(
            service._read_receipts_allowed(self.cur, READER, CONVERSATION),
            "the per-conversation override did not reach _read_receipts_allowed; "
            "this test would otherwise pass while setting nothing",
        )
        message_id = self.say("hello")
        service.mark_read(READER, CONVERSATION)
        receipt = self.receipt(message_id)
        self.assertEqual(receipt.get("seen_at"), "", "a per-conversation opt-out still published a tick")
        self.assertTrue(receipt.get("read_at"), "the private record must still exist")

    def test_turning_receipts_back_on_still_produces_a_seen_tick(self):
        """The regression the fix could easily have caused.

        Before the split, receipts-off wrote nothing, so re-reading with them on
        found rows with no `read_at` and stamped both columns. Now `read_at` is
        already set, and a write bounded to `read_at=''` would skip those rows
        forever: the sender would never get a tick for anything read during the
        quiet period, with no way to recover it.
        """
        self.set_receipts(READER, False)
        message_id = self.say("hello")
        service.mark_read(READER, CONVERSATION)
        self.assertEqual(self.receipt(message_id).get("seen_at"), "")

        self.set_receipts(READER, True)
        service.mark_read(READER, CONVERSATION)
        self.assertTrue(
            self.receipt(message_id).get("seen_at"),
            "turning receipts back on left already-read messages permanently tickless",
        )

    def test_read_at_records_the_first_read_not_the_latest(self):
        """Stated as a test because reconciliation reads it as a boolean and a
        future caller might be tempted to read it as a time."""
        message_id = self.say("hello")
        service.mark_read(READER, CONVERSATION)
        first = self.receipt(message_id)["read_at"]
        self.cur.execute(
            "UPDATE comm_v2_read_receipts SET read_at=? WHERE message_id=? AND user_id=?",
            ("2020-01-01T00:00:00+00:00", message_id, READER),
        )
        service.mark_read(READER, CONVERSATION)
        self.assertEqual(
            self.receipt(message_id)["read_at"],
            "2020-01-01T00:00:00+00:00",
            "read_at was refreshed; it is documented as first-read and the O(N) "
            "write it costs to keep current is the reason",
        )
        self.assertTrue(first)


class ReadStateClassification(ReadStateFixture):
    def test_an_unread_message_is_unread(self):
        message_id = self.say("hello")
        result = self.classify([message_id])
        self.assertEqual(result["unread"], [message_id])
        self.assertEqual(result["read"], [])

    def test_a_read_message_is_read(self):
        message_id = self.say("hello")
        service.mark_read(READER, CONVERSATION)
        result = self.classify([message_id])
        self.assertEqual(result["read"], [message_id])
        self.assertEqual(result["unread"], [])

    def test_read_state_does_not_trust_the_watermark(self):
        """THE test. A message whose id falls below `last_read_message_id` but
        which carries no receipt is UNREAD, and must be reported as such.

        This is the out-of-order-commit case made deterministic: ids 1..3 and 10
        exist and are read, so the watermark is 10; id 7 then becomes visible.
        Postgres produces this by handing 7 out before 10 and committing it
        after. Here it is produced directly, because the failure has nothing to
        do with concurrency -- it is that `id <= watermark` is not a read test.

        If this ever fails, the notification for message 7 is being deleted from
        the user's phone and they will never learn it arrived.
        """
        for index in (1, 2, 3):
            self.say(f"early {index}", message_id=index)
        late = self.say("late", message_id=10)
        service.mark_read(READER, CONVERSATION)
        self.assertEqual(self.watermark(), late, "fixture did not establish the watermark")

        straggler = self.say("committed out of order", message_id=7)
        self.assertLess(straggler, self.watermark())
        self.assertEqual(self.receipt(straggler), {}, "fixture accidentally gave the straggler a receipt")

        result = self.classify([straggler])
        self.assertEqual(
            result["unread"],
            [straggler],
            "a message below the read watermark with no receipt was reported READ; "
            "the device will dismiss its notification and the message is lost",
        )
        self.assertEqual(result["read"], [])

    def test_your_own_message_is_read(self):
        """Nobody needs telling about the words they just typed, and no receipt
        row is ever written for a sender's own message."""
        message_id = self.say("mine", sender=READER)
        result = self.classify([message_id])
        self.assertEqual(result["read"], [message_id])

    def test_a_deleted_message_is_obsolete(self):
        message_id = self.say("unsend me")
        self.cur.execute("UPDATE comm_v2_messages SET deleted_at=? WHERE id=?", (STAMP, message_id))
        result = self.classify([message_id])
        self.assertEqual(result["obsolete"], [message_id])
        self.assertEqual(result["unread"], [])

    def test_a_message_hidden_for_this_user_is_obsolete(self):
        message_id = self.say("delete for me")
        self.cur.execute(
            "INSERT INTO comm_v2_message_deletions (message_id, conversation_id, user_id, deleted_at) VALUES (?,?,?,?)",
            (message_id, CONVERSATION, READER, STAMP),
        )
        self.assertEqual(self.classify([message_id])["obsolete"], [message_id])

    def test_a_deleted_conversation_is_obsolete(self):
        message_id = self.say("hello")
        self.cur.execute("UPDATE comm_v2_conversations SET deleted_at=? WHERE id=?", (STAMP, CONVERSATION))
        self.assertEqual(self.classify([message_id])["obsolete"], [message_id])

    def test_leaving_the_conversation_makes_it_obsolete(self):
        message_id = self.say("hello")
        self.cur.execute(
            "UPDATE comm_v2_participants SET membership_state='left', left_at=? WHERE conversation_id=? AND user_id=?",
            (STAMP, CONVERSATION, READER),
        )
        self.assertEqual(
            self.classify([message_id])["obsolete"],
            [message_id],
            "a notification for a conversation the user has left must be removable",
        )

    def test_an_unrecognised_id_is_unknown_not_obsolete(self):
        """Unknown means keep. A push can outrun its own row across a replica,
        and an id that has not arrived yet must not read as an id that is gone.
        """
        result = self.classify([999_999])
        self.assertEqual(result["unknown"], [999_999])
        self.assertEqual(result["obsolete"], [])
        self.assertEqual(result["read"], [])

    def test_another_members_read_state_is_not_visible(self):
        """Account isolation. The reader has read it; the sender has not, and
        asking as the sender must not inherit the reader's answer."""
        message_id = self.say("hello", sender=STRANGER)
        self.join(STRANGER)
        service.mark_read(READER, CONVERSATION)
        self.assertEqual(self.classify([message_id], user_id=READER)["read"], [message_id])
        self.assertEqual(
            self.classify([message_id], user_id=SENDER)["unread"],
            [message_id],
            "one member's read state answered another member's question",
        )

    def test_a_non_member_gets_obsolete_not_content(self):
        message_id = self.say("hello")
        result = self.classify([message_id], user_id=STRANGER)
        self.assertEqual(result["obsolete"], [message_id])
        self.assertEqual(result["read"], [])

    def test_every_requested_id_comes_back_exactly_once(self):
        """The client dismisses from `read`+`obsolete` and keeps the rest. An id
        that falls through every bucket is a notification nothing will ever
        decide about; one in two buckets is a contradiction."""
        read_id = self.say("read me")
        service.mark_read(READER, CONVERSATION)
        unread_id = self.say("not yet")
        gone_id = self.say("gone")
        self.cur.execute("UPDATE comm_v2_messages SET deleted_at=? WHERE id=?", (STAMP, gone_id))
        requested = [read_id, unread_id, gone_id, 888_888]
        result = self.classify(requested)
        returned = result["read"] + result["unread"] + result["obsolete"] + result["unknown"]
        self.assertEqual(sorted(returned), sorted(requested))
        self.assertEqual(len(returned), len(set(returned)))

    def test_duplicate_and_junk_ids_are_survivable(self):
        message_id = self.say("hello")
        result = self.classify([message_id, message_id, 0, -4, None, "seven", "  "])
        self.assertEqual(result["unread"], [message_id])
        self.assertEqual(result["unknown"], [])

    def test_an_empty_request_is_an_empty_answer(self):
        result = self.classify([])
        self.assertEqual((result["read"], result["unread"], result["obsolete"], result["unknown"]), ([], [], [], []))

    def test_the_batch_is_capped(self):
        """iOS holds at most 64 delivered notifications. A request for ten
        thousand ids is not a device asking honestly, and answering it means one
        `IN (...)` with ten thousand placeholders."""
        message_id = self.say("hello")
        oversized = list(range(500_000, 500_000 + service.MESSAGE_READ_STATE_BATCH_LIMIT + 50)) + [message_id]
        result = self.classify(oversized)
        total = len(result["read"]) + len(result["unread"]) + len(result["obsolete"]) + len(result["unknown"])
        self.assertLessEqual(total, service.MESSAGE_READ_STATE_BATCH_LIMIT)


class ReadStateLeaksNothing(ReadStateFixture):
    def test_the_response_carries_no_message_content(self):
        """Stage 11. The caller already has the ids; it must not be handed a
        second copy of the words, the names, or the thread titles."""
        body = "the quick brown fox jumped over the lazy dog"
        message_id = self.say(body)
        self.cur.execute("UPDATE users SET display_name=? WHERE user_id=?", ("Marlene Bookbinder", SENDER))
        rendered = json.dumps(self.classify([message_id]))
        for secret in (body, "Marlene", "Bookbinder", "conv-read-state"):
            self.assertNotIn(secret, rendered, f"the read-state response leaked {secret!r}")

    def test_the_log_line_carries_counts_not_ids(self):
        """A log that names conversation ids and message ids reconstructs who is
        talking to whom from a log aggregator. Counts answer the operational
        question -- is reconciliation doing anything -- without that."""
        source = SERVICE_SOURCE[SERVICE_SOURCE.index("def message_notification_read_state(") :]
        source = source[: source.index("\ndef ", 1)]
        logged = source[source.index("COMM_V2_NOTIFICATION_READ_STATE") :]
        logged = logged[: logged.index(")\n    return")]
        for forbidden in ("conversation_id", "message_id", "body", "sender_name"):
            self.assertNotIn(forbidden, logged, f"the read-state log line names {forbidden}")
        self.assertIn("len(", logged, "the log line should be reporting sizes")


class PushPayloadContract(unittest.TestCase):
    """Stage 1. The fields a device needs to tell its own message notifications
    apart from everything else in Notification Center, without reading one."""

    def setUp(self):
        block = SERVICE_SOURCE[SERVICE_SOURCE.index("push_metadata = {") :]
        self.block = block[: block.index("\n                }") ]

    def test_the_contract_is_versioned(self):
        self.assertEqual(service.MESSAGE_PUSH_SCHEMA_VERSION, 2)
        for key in ('"schema_version"', '"schemaVersion"'):
            self.assertIn(key, self.block)

    def test_the_payload_names_its_recipient(self):
        """Without this a device that has switched accounts cannot tell whose
        notification it is holding, and the only safe answer is to keep it --
        which is how read alerts accumulate."""
        for key in ('"recipient_user_id"', '"recipientUserId"'):
            self.assertIn(key, self.block)
        self.assertIn("int(recipient_id)", self.block)

    def test_the_payload_declares_its_type_explicitly(self):
        for key in ('"notification_type"', '"notificationType"'):
            self.assertIn(key, self.block)

    def test_the_payload_carries_a_send_time(self):
        for key in ('"sent_at"', '"sentAt"'):
            self.assertIn(key, self.block)

    def test_the_old_fields_are_still_there(self):
        """Backward compatibility. A deployed client reads `type`, `push_type`,
        `conversation_id` and `message_id`; removing any of them turns a
        payload-contract upgrade into a routing outage on every phone that has
        not updated."""
        for key in ('"type": "message"', '"push_type": "chat_message"', '"conversation_id"', '"message_id"'):
            self.assertIn(key, self.block)

    def test_the_logical_key_is_not_sold_as_a_dismissal_handle(self):
        """`notification_key` is PulseSoc's name for the alert. iOS assigns its
        own identifier to the delivered request and that is the only thing
        `dismissNotificationAsync` accepts. Conflating them produces code that
        looks right and dismisses nothing."""
        self.assertIn('"notification_key"', self.block)
        context = SERVICE_SOURCE[: SERVICE_SOURCE.index("push_metadata = {")]
        note = context[context.rindex("# The contract a device needs") :]
        self.assertTrue(
            re.search(r"NOT the identifier iOS assigns", note),
            "the distinction between the logical key and the OS request identifier "
            "is the one thing a reader of this payload has to be told",
        )


class WatermarkIsNotUsedForDismissal(unittest.TestCase):
    """A source-level guard beside the behavioural one.

    `test_read_state_does_not_trust_the_watermark` proves today's code is right.
    It does not stop someone replacing the receipt join with the watermark
    comparison, because that rewrite would fail only in the narrow case the test
    constructs -- and the obvious "simplification" is to delete the odd-looking
    fixture along with it.
    """

    @staticmethod
    def _executable_source(name: str) -> str:
        """The function with its docstring and comments removed.

        Both talk about the watermark at length -- explaining why it is not used
        is most of the point -- so a naive substring check reads its own
        rationale as a violation.
        """
        source = SERVICE_SOURCE[SERVICE_SOURCE.index(f"def {name}(") :]
        source = source[: source.index("\ndef ", 1)]
        body = source[source.index('"""') + 3 :]
        body = body[body.index('"""') + 3 :]
        return "\n".join(line for line in body.splitlines() if not line.strip().startswith("#"))

    def test_the_classifier_never_reads_last_read_message_id(self):
        source = self._executable_source("message_notification_read_state")
        self.assertIn("read_at", source, "the extractor found the wrong slice of the file")
        self.assertNotIn(
            "last_read_message_id",
            source,
            "the read-state classifier is consulting the watermark. It is not a read "
            "test: a message can become visible below a watermark that has already "
            "passed it, and dismissing its notification loses the message.",
        )
        self.assertIn("read_at", source)


class BothEmittersSpeakTheContract(unittest.TestCase):
    """Stage 1, second half. There are TWO live message-push senders.

    `pulse_communications_v2.service.send_message` is the one everybody thinks
    of. `bot.pulse_finalize_message_delivery` is the other, with five call
    sites, and it emitted a payload that carried the ids but never declared a
    type or named a recipient -- so a device holding an alert from that path
    could not answer "is this a message?" or "is this mine?" without reading the
    title, and therefore had to leave it alone.

    A fix applied to one sender and not the other is the worst outcome
    available: message alerts would vanish or persist depending on which code
    path happened to deliver them, which reads as intermittent rather than as a
    bug in a specific place. These tests read bot.py as text -- importing it
    costs ~200x on a cold bytecode cache and pulls in the whole Flask app for
    two dictionary literals.
    """

    @classmethod
    def setUpClass(cls):
        source = open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read()
        start = source.index("def pulse_finalize_message_delivery(")
        cls.function = source[start : source.index("\ndef ", start + 1)]
        block = cls.function[cls.function.index("metadata = {") :]
        cls.block = block[: block.index("\n        }")]

    def test_the_legacy_sender_declares_the_message_type(self):
        for key in ('"notification_type": "message"', '"notificationType": "message"'):
            self.assertIn(key, self.block, "the legacy sender does not declare its type")

    def test_the_legacy_sender_names_its_recipient(self):
        for key in ('"recipient_user_id": recipient_id', '"recipientUserId": recipient_id'):
            self.assertIn(key, self.block, "the legacy sender still cannot say who the alert is for")

    def test_the_legacy_sender_is_versioned_from_the_one_source(self):
        """Not a hardcoded 2.

        A literal here would be a second place to remember on the next bump, and
        the failure when somebody forgot would be a client quietly declining
        payloads from one of the two senders.
        """
        for key in ('"schema_version": push_schema_version', '"schemaVersion": push_schema_version'):
            self.assertIn(key, self.block)
        self.assertIn("push_schema_version = _message_push_schema_version()", self.function)

    def test_the_version_helper_survives_a_broken_route_pack(self):
        """comm_v2 is registered as an optional route pack, so it can be absent.

        A module-scope import would make a broken comm_v2 take the legacy chat
        sender down with it -- turning a subsystem outage into a messaging
        outage. Returning 0 is the honest answer; the payload still carries the
        recipient and the explicit type, which is what the client decides on.
        """
        source = open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read()
        helper = source[source.index("def _message_push_schema_version("):]
        helper = helper[: helper.index("\ndef ", 1)]
        self.assertIn("except Exception:", helper)
        self.assertIn("return 0", helper)
        self.assertIn("from pulse_communications_v2 import service", helper)

    def test_the_legacy_sender_keeps_its_old_fields(self):
        """Backward compatibility: a phone that has not updated still routes on
        these. Adding the contract must not be a breaking change."""
        for key in ('"type": "chat_message"', '"conversation_id"', '"message_id"', '"deepLink"'):
            self.assertIn(key, self.block)


class CrossLanguageConstants(unittest.TestCase):
    """The two numbers that exist in Python and in TypeScript.

    Both drift silently. A schema version the client does not recognise makes it
    decline payloads and stop dismissing anything, with no error anywhere. A
    batch limit the client exceeds gets clamped server-side, and the ids that
    fall off the end come back in no bucket at all -- which, without the
    client's backfill, would read as "none of those are unread".

    Read from the TypeScript source rather than duplicated here, so this test
    and its jest counterpart cannot be satisfied independently.
    """

    @classmethod
    def setUpClass(cls):
        cls.ts = open(
            os.path.join(ROOT, "mobile-native", "src", "notifications", "messageNotificationContract.ts"),
            encoding="utf-8",
        ).read()

    def _ts_const(self, name):
        match = re.search(rf"export const {name} = (\d+);", self.ts)
        self.assertIsNotNone(match, f"{name} is not declared in messageNotificationContract.ts")
        return int(match.group(1))

    def test_the_schema_version_agrees(self):
        self.assertEqual(self._ts_const("MESSAGE_PUSH_SCHEMA_VERSION"), service.MESSAGE_PUSH_SCHEMA_VERSION)

    def test_the_read_state_batch_limit_agrees(self):
        self.assertEqual(self._ts_const("MESSAGE_READ_STATE_BATCH_LIMIT"), service.MESSAGE_READ_STATE_BATCH_LIMIT)

    def test_the_client_recognises_the_type_string_the_server_emits(self):
        """Both senders say `notification_type: "message"`. The client's positive
        test is `declaredType !== "message"`. A rename on either side is the
        silent failure this pins."""
        self.assertIn('declaredType !== "message"', self.ts)
        self.assertIn('"notification_type": "message"', SERVICE_SOURCE)

    def test_the_client_still_accepts_both_senders_legacy_type_strings(self):
        """v1 alerts delivered before this shipped are on the shade right now.

        comm_v2 used `type: "message"`; the bot.py sender used
        `type: "chat_message"`. The client has to recognise BOTH or half the
        upgrade-day backlog never clears.
        """
        match = re.search(r"LEGACY_MESSAGE_TYPES[^=]*= (\[[^\]]*\])", self.ts)
        self.assertIsNotNone(match, "LEGACY_MESSAGE_TYPES is not declared as a literal array")
        legacy = match.group(1)
        for emitted in ('"message"', '"chat_message"'):
            self.assertIn(emitted, legacy)


if __name__ == "__main__":
    unittest.main()
