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

from services import pulse_ai_knowledge, pulse_ai_service as svc  # noqa: E402


OWNER = 1
OTHER = 2
KEY = "professional.military_status"
VALUE = "Active-duty member of the United States military."


class MilitaryOwnerMemoryTests(unittest.TestCase):
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
        svc.update_settings(OWNER, {"remember_preferences": True})
        svc.update_settings(OTHER, {"remember_preferences": True})

    def connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self) -> dict:
        return svc.save_memory(OWNER, KEY, VALUE, source="user_verified")

    def prompt(self, owner: int, question: str, remember: bool = True) -> str:
        conn = self.connection()
        try:
            memories = svc._user_memory(
                conn.cursor(), owner, {"remember_preferences": 1 if remember else 0}, question
            )
        finally:
            conn.close()
        return pulse_ai_knowledge.build_messages(
            question, history=[], knowledge_items=[], user_memory=memories, env={}
        )[0]["content"]

    def test_relevant_owner_prompt_uses_only_the_verified_fact(self):
        self.save()
        prompt = self.prompt(OWNER, "What should you remember about my military background?")
        self.assertIn(KEY, prompt)
        self.assertIn(VALUE, prompt)
        self.assertIn("remain unknown", prompt)
        for unsupported_value in (
            "United States Army",
            "Captain",
            "Fort Bragg",
            "deployed overseas",
            "Top Secret",
            "combat veteran",
            "weapons expert",
        ):
            self.assertNotIn(unsupported_value, prompt)

    def test_unrelated_owner_prompts_do_not_cross_the_provider_boundary(self):
        self.save()
        for question in ("Write me a recipe.", "Explain CSS.", "What's 2+2?"):
            with self.subTest(question=question):
                prompt = self.prompt(OWNER, question)
                self.assertNotIn(KEY, prompt)
                self.assertNotIn(VALUE, prompt)

    def test_other_user_disabled_memory_and_deleted_memory_are_absent(self):
        created = self.save()
        for prompt in (
            self.prompt(OTHER, "Summarize my professional background."),
            self.prompt(OWNER, "Summarize my professional background.", remember=False),
        ):
            self.assertNotIn(KEY, prompt)
            self.assertNotIn(VALUE, prompt)

        self.assertTrue(svc.delete_memory(OWNER, created["memory_id"])["ok"])
        deleted_prompt = self.prompt(OWNER, "Am I active duty?")
        self.assertNotIn(KEY, deleted_prompt)
        self.assertNotIn(VALUE, deleted_prompt)

    def test_disabled_chat_history_cannot_contaminate_the_prompt(self):
        self.save()
        history = [{"role": "user", "body": "unsupported military history"}]
        self.assertEqual(
            svc._history_for_prompt(history, {"use_pulse_ai_chat_history": 0}),
            [],
        )
        prompt = self.prompt(OWNER, "Am I active duty?")
        self.assertNotIn("unsupported military history", prompt)

    def test_standard_lifecycle_is_owner_scoped_and_restorable(self):
        created = self.save()
        duplicate = self.save()
        self.assertEqual(created["memory_id"], duplicate["memory_id"])
        self.assertEqual(duplicate["status"], "existing")

        exported = svc.export_memory(OWNER)
        military = [item for item in exported["items"] if item["memory_key"] == KEY]
        self.assertEqual(len(military), 1)
        self.assertEqual(military[0]["memory_value"], VALUE)
        self.assertEqual([item for item in svc.export_memory(OTHER)["items"] if item["memory_key"] == KEY], [])

        corrected_value = "Member of the United States military."
        self.assertTrue(
            svc.correct_memory(OWNER, created["memory_id"], {"value": corrected_value})["ok"]
        )
        self.assertEqual(
            [item["memory_value"] for item in svc.export_memory(OWNER)["items"] if item["memory_key"] == KEY],
            [corrected_value],
        )
        self.assertEqual(
            svc.correct_memory(OTHER, created["memory_id"], {"value": "leak"})["error"],
            "memory_not_found",
        )

        self.assertTrue(svc.delete_memory(OWNER, created["memory_id"])["ok"])
        self.assertEqual([item for item in svc.export_memory(OWNER)["items"] if item["memory_key"] == KEY], [])
        restored = self.save()
        self.assertNotEqual(restored["memory_id"], created["memory_id"])
        self.assertEqual(restored["status"], "created")

    def test_provenance_and_learning_metadata_are_private_and_minimal(self):
        created = self.save()
        conn = self.connection()
        try:
            provenance = dict(
                conn.execute(
                    "SELECT * FROM pulse_ai_memory_provenance WHERE memory_id=? AND user_id=?",
                    (created["memory_id"], OWNER),
                ).fetchone()
            )
            events = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM pulse_ai_learning_events WHERE user_id=? AND event_type='memory_created'",
                    (OWNER,),
                )
            ]
        finally:
            conn.close()
        self.assertEqual(provenance["provenance"], "user_verified")
        self.assertEqual(provenance["confidence"], 1.0)
        self.assertEqual(provenance["sensitivity"], "user_scoped")
        self.assertEqual(provenance["deletion_policy"], "user_delete")
        self.assertEqual(len(events), 1)
        self.assertNotIn(VALUE, json.dumps(events))

    def test_private_memory_is_not_seeded_into_knowledge_or_training_sources(self):
        self.save()
        public_material = json.dumps(
            {
                "features": pulse_ai_knowledge.DEFAULT_FEATURE_REGISTRY,
                "knowledge": pulse_ai_knowledge.DEFAULT_KNOWLEDGE_ITEMS,
            }
        )
        self.assertNotIn(KEY, public_material)
        self.assertNotIn(VALUE, public_material)
        for path in (
            "data/pulse_ai",
            "backend/undx/config",
        ):
            for root, _, files in os.walk(path):
                for name in files:
                    if name.endswith((".json", ".yaml", ".yml", ".txt")):
                        with open(os.path.join(root, name), encoding="utf-8") as handle:
                            text = handle.read()
                        self.assertNotIn(KEY, text)
                        self.assertNotIn(VALUE, text)


if __name__ == "__main__":
    unittest.main()
