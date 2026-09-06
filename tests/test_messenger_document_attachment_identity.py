"""One document, one identity, one membership check.

Stage 30/114 of the Private Conversations mission asks for end-to-end proof
that a document shared inside a Private Office conversation is an ORDINARY
Messenger attachment: same table, same id, same authorization. The risk is not
that someone writes a second attachment store on purpose -- it is that the
Office needs one extra field, gets a small side table, and six months later
there are two answers to "may this person open this file" that disagree.

So this suite walks the whole lifecycle against a real database rather than a
mock -- init, upload, attach, read, download-target -- and asserts two things at
every step: the attachment id is the only handle that exists, and a
non-participant is refused. The refusal is checked at each stage separately
because the interesting bug is not "everything is open", it is "one of the five
entry points forgot".

The conversation here is a comm_v2 conversation, which is what a Private Office
thread actually is; the Office's own tables classify that conversation and hold
no media identity of their own. That is asserted directly at the end.
"""

import os
import sqlite3
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "")

from services import messenger_media_foundation as foundation  # noqa: E402

OWNER = 9101
MEMBER = 9102
STRANGER = 9103
CONVERSATION_ID = 77

PDF_MIME = "application/pdf"
PDF_BYTES = b"%PDF-1.7\n% a small but honest pdf\n"


class _Stream:
    """The minimal shape ``_spool_upload`` consumes."""

    def __init__(self, payload: bytes):
        self._buffer = BytesIO(payload)

    def read(self, size=-1):
        return self._buffer.read(size)


class _FileStorage:
    def __init__(self, filename: str, mimetype: str, payload: bytes):
        self.filename = filename
        self.mimetype = mimetype
        self.stream = _Stream(payload)


class DocumentAttachmentIdentity(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._previous_local_dir = os.environ.get("MESSENGER_MEDIA_LOCAL_DIR")
        os.environ["MESSENGER_MEDIA_LOCAL_DIR"] = str(root / "private_uploads")

        self.conn = sqlite3.connect(str(root / "media.db"))
        self.conn.row_factory = sqlite3.Row
        self.cur = self.conn.cursor()
        foundation.ensure_schema(self.cur, self.conn)

        # The canonical conversation tables, plus the empty legacy tables the
        # membership fallback queries. They are created empty on purpose: a
        # non-member has to be refused by reaching the end of the chain, not by
        # a missing-table error that would mask a real regression.
        self.cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS comm_v2_conversations (
                id INTEGER PRIMARY KEY, status TEXT, deleted_at TEXT);
            CREATE TABLE IF NOT EXISTS comm_v2_participants (
                conversation_id INTEGER, user_id INTEGER,
                membership_state TEXT, left_at TEXT);
            CREATE TABLE IF NOT EXISTS pulse_conversations (
                id INTEGER PRIMARY KEY, status TEXT, deleted_at TEXT);
            CREATE TABLE IF NOT EXISTS pulse_conversation_participants (
                conversation_id INTEGER, user_id INTEGER, left_at TEXT);
            CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS conversation_members (
                conversation_id INTEGER, user_id INTEGER);
            CREATE TABLE IF NOT EXISTS blocked_users (
                blocker_user_id INTEGER, blocked_user_id INTEGER);
            CREATE TABLE IF NOT EXISTS comm_v2_messages (
                id INTEGER PRIMARY KEY, conversation_id INTEGER, sender_user_id INTEGER);
            """
        )
        # Attaching binds the file to a message that already exists in the
        # canonical ledger and belongs to the sender -- the foundation checks
        # this itself, which is precisely why the Office cannot invent its own
        # message ids for attachments.
        for message_id in (5150, 5151, 5153):
            self.cur.execute(
                "INSERT INTO comm_v2_messages (id, conversation_id, sender_user_id) VALUES (?, ?, ?)",
                (message_id, CONVERSATION_ID, OWNER),
            )
        self.cur.execute(
            "INSERT INTO comm_v2_conversations (id, status, deleted_at) VALUES (?, 'active', NULL)",
            (CONVERSATION_ID,),
        )
        for user_id in (OWNER, MEMBER):
            self.cur.execute(
                """
                INSERT INTO comm_v2_participants
                    (conversation_id, user_id, membership_state, left_at)
                VALUES (?, ?, 'active', NULL)
                """,
                (CONVERSATION_ID, user_id),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        if self._previous_local_dir is None:
            os.environ.pop("MESSENGER_MEDIA_LOCAL_DIR", None)
        else:
            os.environ["MESSENGER_MEDIA_LOCAL_DIR"] = self._previous_local_dir
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------

    def _init_pdf(self, user_id=OWNER, filename="statement.pdf"):
        result, status = foundation.init_upload(
            self.cur, self.conn, {"user_id": user_id},
            {
                "conversation_id": CONVERSATION_ID,
                "media_type": "file",
                "mime_type": PDF_MIME,
                "filename": filename,
                "size_bytes": len(PDF_BYTES),
            },
        )
        self.conn.commit()
        return result, status

    def _upload(self, attachment_id, user_id=OWNER, filename="statement.pdf"):
        result, status = foundation.upload_file(
            self.cur, self.conn, {"user_id": user_id}, attachment_id,
            _FileStorage(filename, PDF_MIME, PDF_BYTES),
        )
        self.conn.commit()
        return result, status

    def _tables(self):
        self.cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {row["name"] for row in self.cur.fetchall()}

    # -- the lifecycle ---------------------------------------------------

    def test_a_document_travels_the_ordinary_attachment_lifecycle(self):
        created, status = self._init_pdf()
        self.assertEqual(status, 201, created)
        attachment_id = int(created["attachment_id"])
        self.assertGreater(attachment_id, 0)

        uploaded, status = self._upload(attachment_id)
        self.assertEqual(status, 200, uploaded)

        self.cur.execute(
            "SELECT * FROM message_attachments WHERE id=?", (attachment_id,))
        row = self.cur.fetchone()
        self.assertEqual(row["upload_status"], "uploaded")
        self.assertEqual(row["media_type"], "file")
        self.assertEqual(row["mime_type"], PDF_MIME)
        self.assertEqual(int(row["size_bytes"]), len(PDF_BYTES))
        self.assertEqual(int(row["conversation_id"]), CONVERSATION_ID)
        self.assertEqual(int(row["sender_id"]), OWNER)

        # The bytes landed in private local storage, not in a public directory
        # and not in the database.
        stored = foundation._local_path(row["storage_key"])
        self.assertTrue(stored.exists())
        self.assertEqual(stored.read_bytes(), PDF_BYTES)
        self.assertTrue(
            foundation.local_private_root() in stored.parents,
            f"{stored} escaped the private upload root",
        )
        self.assertFalse(str(row["public_url"] or ""))

        attached, status = foundation.attach_to_message(
            self.cur, self.conn, {"user_id": OWNER},
            {"message_id": 5150, "attachments": [attachment_id]},
        )
        self.conn.commit()
        self.assertEqual(status, 200, attached)

        self.cur.execute(
            "SELECT message_id, upload_status FROM message_attachments WHERE id=?",
            (attachment_id,))
        row = self.cur.fetchone()
        self.assertEqual(int(row["message_id"]), 5150)
        self.assertEqual(row["upload_status"], "attached")

        # A second participant reads it by the same id -- there is no per-viewer
        # copy, alias or Office-side handle.
        payload, status = foundation.get_attachment(
            self.cur, {"user_id": MEMBER}, attachment_id, include_url=False)
        self.assertEqual(status, 200)
        self.assertEqual(int(payload["attachment_id"]), attachment_id)
        self.assertEqual(payload["download_url"], f"/api/messages/media/{attachment_id}/download")

        target = foundation.attachment_download_target(
            self.cur, {"user_id": MEMBER}, attachment_id)
        self.assertEqual(target["kind"], "local")
        self.assertEqual(target["mime_type"], PDF_MIME)
        self.assertEqual(target["disposition"], "attachment")

    def test_the_attachment_id_is_the_only_identity_in_the_payload(self):
        created, _ = self._init_pdf()
        attachment_id = int(created["attachment_id"])
        self._upload(attachment_id)
        payload, _ = foundation.get_attachment(
            self.cur, {"user_id": OWNER}, attachment_id, include_url=False)

        # Every identifying field either IS the attachment id or points at the
        # conversation/message it belongs to. A second identity would show up
        # here first, as a uuid, token or slug the client is invited to use.
        self.assertEqual(int(payload["attachment_id"]), attachment_id)
        self.assertIn(str(attachment_id), payload["download_url"])
        for key, value in payload.items():
            if not isinstance(value, str) or key in {"download_url"}:
                continue
            self.assertNotIn(
                "/media/", value,
                f"payload field {key} offers a second media handle: {value!r}")

    def test_a_non_participant_is_refused_at_every_entry_point(self):
        created, _ = self._init_pdf()
        attachment_id = int(created["attachment_id"])
        self._upload(attachment_id)
        foundation.attach_to_message(
            self.cur, self.conn, {"user_id": OWNER},
            {"message_id": 5151, "attachments": [attachment_id]})
        self.conn.commit()

        stranger = {"user_id": STRANGER}
        checks = {
            "init_upload": lambda: self._init_pdf(user_id=STRANGER),
            "upload_file": lambda: self._upload(attachment_id, user_id=STRANGER),
            "get_attachment": lambda: foundation.get_attachment(
                self.cur, stranger, attachment_id, include_url=False),
            "download_target": lambda: foundation.attachment_download_target(
                self.cur, stranger, attachment_id),
            "attach_to_message": lambda: foundation.attach_to_message(
                self.cur, self.conn, stranger,
                {"message_id": 5152, "attachments": [attachment_id]}),
        }
        for name, call in checks.items():
            with self.subTest(entry_point=name):
                with self.assertRaises(foundation.MessengerMediaError) as caught:
                    call()
                self.assertEqual(caught.exception.status_code, 403, name)

    def test_a_participant_who_is_not_the_sender_cannot_mutate_the_upload(self):
        """Reading is a membership question; writing is an ownership question."""
        created, _ = self._init_pdf()
        attachment_id = int(created["attachment_id"])
        with self.assertRaises(foundation.MessengerMediaError) as caught:
            self._upload(attachment_id, user_id=MEMBER)
        self.assertEqual(caught.exception.error, "not_attachment_owner")
        self.assertEqual(caught.exception.status_code, 403)

    def test_the_whole_lifecycle_creates_no_second_attachment_store(self):
        before = self._tables()
        created, _ = self._init_pdf()
        attachment_id = int(created["attachment_id"])
        self._upload(attachment_id)
        foundation.attach_to_message(
            self.cur, self.conn, {"user_id": OWNER},
            {"message_id": 5153, "attachments": [attachment_id]})
        self.conn.commit()
        after = self._tables()

        self.assertEqual(
            sorted(after - before), [],
            "the document lifecycle created new tables")
        self.assertNotIn("private_office_attachments", after)
        self.assertFalse(
            [name for name in after if name.startswith("private_office")],
            "the Office must not own an attachment table")

        # And exactly one row describes this file.
        self.cur.execute(
            "SELECT COUNT(*) AS n FROM message_attachments WHERE storage_key IS NOT NULL AND storage_key != ''")
        self.assertEqual(int(self.cur.fetchone()["n"]), 1)


if __name__ == "__main__":
    unittest.main()
