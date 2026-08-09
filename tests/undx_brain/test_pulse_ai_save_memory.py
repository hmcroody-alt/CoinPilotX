from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.undx_agent import bootstrap  # noqa: E402

bootstrap.install()

from services import pulse_ai_service as svc  # noqa: E402


class SaveMemoryTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        handle.close()
        self.path = handle.name
        original_bot = svc._bot

        def connect():
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
            return conn

        svc._bot = lambda: types.SimpleNamespace(db=connect, sqlite3=sqlite3)
        self.addCleanup(setattr, svc, "_bot", original_bot)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))
        conn, _ = svc._open_db()
        conn.close()

    def enable(self, owner: int) -> None:
        result = svc.update_settings(owner, {"remember_preferences": True})
        self.assertTrue(result["settings"]["remember_preferences"])

    def rows(self, statement: str, params: tuple = ()) -> list[dict]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute(statement, params).fetchall()]
        finally:
            conn.close()

    def test_memory_creation_requires_explicit_consent(self):
        result = svc.save_memory(41, "timezone", "America/New_York")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "memory_disabled")
        self.assertEqual(self.rows("SELECT * FROM pulse_ai_user_memory"), [])

    def test_invalid_owners_never_default_or_write(self):
        for owner in (None, 0, -1, True, False, "41", 41.0, [], {}):
            with self.subTest(owner=repr(owner)):
                result = svc.save_memory(owner, "timezone", "UTC")
                self.assertEqual(result["error"], "invalid_user_id")
        self.assertEqual(self.rows("SELECT * FROM pulse_ai_user_memory"), [])

    def test_create_deduplicate_update_provenance_and_safe_events(self):
        self.enable(41)
        created = svc.save_memory(41, "timezone", "America/New_York")
        repeated = svc.save_memory(41, "timezone", "America/New_York")
        updated = svc.save_memory(41, "timezone", "America/Los_Angeles")

        self.assertEqual(created["status"], "created")
        self.assertEqual(repeated["status"], "existing")
        self.assertEqual(updated["status"], "updated")
        self.assertEqual(created["memory_id"], repeated["memory_id"])
        self.assertEqual(created["memory_id"], updated["memory_id"])

        active = self.rows(
            "SELECT * FROM pulse_ai_user_memory WHERE user_id=? AND memory_key=? AND status='active'",
            (41, "timezone"),
        )
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["memory_value"], "America/Los_Angeles")

        provenance = self.rows(
            "SELECT * FROM pulse_ai_memory_provenance WHERE user_id=? AND memory_id=? ORDER BY id",
            (41, created["memory_id"]),
        )
        self.assertEqual(len(provenance), 2)
        self.assertEqual(provenance[0]["provenance"], "user_verified")
        self.assertEqual(provenance[0]["confidence"], 1.0)
        self.assertEqual(provenance[0]["sensitivity"], "user_scoped")
        self.assertEqual(provenance[0]["deletion_policy"], "user_delete")
        self.assertEqual(
            json.loads(provenance[1]["correction_history_json"])[0]["previous"],
            "America/New_York",
        )

        events = self.rows(
            "SELECT event_type, metadata_json FROM pulse_ai_learning_events WHERE user_id=? ORDER BY id",
            (41,),
        )
        memory_events = [row for row in events if row["event_type"].startswith("memory_")]
        self.assertEqual([row["event_type"] for row in memory_events], ["memory_created", "memory_updated"])
        self.assertNotIn("America/New_York", json.dumps(memory_events))
        self.assertNotIn("America/Los_Angeles", json.dumps(memory_events))

    def test_other_owner_cannot_modify_export_correct_or_delete_memory(self):
        self.enable(41)
        self.enable(42)
        created = svc.save_memory(41, "language", "English")
        foreign = svc.save_memory(42, "language", "French")

        self.assertNotEqual(created["memory_id"], foreign["memory_id"])
        self.assertEqual(svc.correct_memory(42, created["memory_id"], {"value": "French"})["error"], "memory_not_found")
        self.assertEqual(svc.delete_memory(42, created["memory_id"])["error"], "memory_not_found")
        self.assertEqual([item["memory_value"] for item in svc.export_memory(41)["items"]], ["English"])
        self.assertEqual([item["memory_value"] for item in svc.export_memory(42)["items"]], ["French"])

        corrected = svc.correct_memory(41, created["memory_id"], {"value": "Spanish"})
        self.assertTrue(corrected["ok"])
        self.assertEqual([item["memory_value"] for item in svc.export_memory(41)["items"]], ["Spanish"])
        deleted = svc.delete_memory(41, created["memory_id"])
        self.assertTrue(deleted["ok"])
        self.assertEqual(svc.export_memory(41)["count"], 0)
        self.assertEqual(svc.export_memory(42)["count"], 1)

    def test_empty_key_and_value_are_rejected(self):
        self.enable(41)
        self.assertEqual(svc.save_memory(41, "", "value")["error"], "invalid_memory_key")
        self.assertEqual(svc.save_memory(41, "key", "")["error"], "invalid_memory_value")

    def test_required_write_failure_rolls_back_the_whole_memory(self):
        self.enable(41)
        original = svc._record_learning_event
        svc._record_learning_event = lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("forced learning event failure")
        )
        self.addCleanup(setattr, svc, "_record_learning_event", original)

        with self.assertRaisesRegex(RuntimeError, "forced learning event failure"):
            svc.save_memory(41, "timezone", "UTC")

        self.assertEqual(self.rows("SELECT * FROM pulse_ai_user_memory"), [])
        self.assertEqual(self.rows("SELECT * FROM pulse_ai_memory_provenance"), [])


if __name__ == "__main__":
    unittest.main()
