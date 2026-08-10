import sqlite3
import unittest

from services import messenger_media_foundation


class VoiceAttachmentIdempotencyTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.cur = self.conn.cursor()
        self.cur.executescript(
            """
            CREATE TABLE pulse_conversations (id INTEGER PRIMARY KEY, status TEXT, deleted_at TEXT);
            CREATE TABLE pulse_conversation_participants (
                conversation_id INTEGER, user_id INTEGER, left_at TEXT
            );
            CREATE TABLE pulse_messages (
                id INTEGER PRIMARY KEY, conversation_id INTEGER, sender_user_id INTEGER
            );
            CREATE TABLE blocked_users (blocker_user_id INTEGER, blocked_user_id INTEGER);
            CREATE TABLE comm_v2_participants (
                conversation_id INTEGER, user_id INTEGER, membership_state TEXT, left_at TEXT
            );
            CREATE TABLE comm_v2_conversations (id INTEGER PRIMARY KEY, status TEXT, deleted_at TEXT);
            CREATE TABLE conversation_members (conversation_id INTEGER, user_id INTEGER);
            CREATE TABLE conversations (id INTEGER PRIMARY KEY);
            """
        )
        messenger_media_foundation.ensure_schema(self.cur, self.conn)
        self.cur.execute("INSERT INTO pulse_conversations VALUES (10, 'active', '')")
        self.cur.executemany(
            "INSERT INTO pulse_conversation_participants VALUES (10, ?, '')",
            [(1,), (2,)],
        )
        self.cur.execute("INSERT INTO pulse_messages VALUES (20, 10, 1)")
        self.cur.execute(
            """
            INSERT INTO message_attachments
                (id, message_id, conversation_id, conversation_model, sender_id,
                 media_type, mime_type, upload_status, deleted_at, created_at, updated_at)
            VALUES (30, NULL, 10, 'pulse', 1, 'voice', 'audio/mp4', 'uploaded', '', '', '')
            """
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_retry_attaches_exactly_once_to_the_same_message(self):
        payload = {"message_id": 20, "attachment_ids": [30]}
        first, status = messenger_media_foundation.attach_to_message(
            self.cur, self.conn, {"user_id": 1}, payload
        )
        self.assertEqual(status, 200)
        self.assertEqual(first["attached"], [30])

        second, status = messenger_media_foundation.attach_to_message(
            self.cur, self.conn, {"user_id": 1}, payload
        )
        self.assertEqual(status, 200)
        self.assertEqual(second["attached"], [30])
        row = self.cur.execute(
            "SELECT message_id, upload_status FROM message_attachments WHERE id=30"
        ).fetchone()
        self.assertEqual((row["message_id"], row["upload_status"]), (20, "attached"))

    def test_other_participant_cannot_reassign_the_voice_attachment(self):
        with self.assertRaises(messenger_media_foundation.MessengerMediaError) as raised:
            messenger_media_foundation.attach_to_message(
                self.cur, self.conn, {"user_id": 2}, {"message_id": 20, "attachment_ids": [30]}
            )
        self.assertEqual(raised.exception.error, "not_attachment_owner")


class VoiceMessageRouteContractTest(unittest.TestCase):
    def test_media_only_send_requires_media_url_and_attachment(self):
        source = open("bot.py", encoding="utf-8").read()
        route = source[source.index("def api_pulse_communications_send_message") :]
        route = route[: route.index("\n@webhook_app.route", 1)]
        self.assertIn('message_type in {"image", "gif", "video", "voice", "audio", "file"}', route)
        self.assertIn("and bool(media_url) and bool(attachment_ids)", route)
        self.assertIn("messenger_media_foundation.attach_to_message", route)

    def test_private_media_enrichment_never_returns_storage_keys(self):
        source = open("bot.py", encoding="utf-8").read()
        helper = source[source.index("def pulse_attach_private_message_media") :]
        helper = helper[: helper.index("\ndef ", 1)]
        self.assertIn("signed_or_private_url", helper)
        self.assertNotIn('"storage_key":', helper)


if __name__ == "__main__":
    unittest.main()
