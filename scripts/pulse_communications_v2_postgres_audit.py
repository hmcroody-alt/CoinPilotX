#!/usr/bin/env python3
"""Audit PostgreSQL portability requirements for Communications V2."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import db as db_service  # noqa: E402

TABLES = (
    "comm_v2_conversations",
    "comm_v2_participants",
    "comm_v2_messages",
    "comm_v2_attachments",
    "comm_v2_live_streams",
    "comm_v2_message_reactions",
    "comm_v2_read_receipts",
    "comm_v2_typing",
    "comm_v2_reports",
    "comm_v2_blocks",
    "comm_v2_moderation_events",
    "comm_v2_communities",
    "comm_v2_channels",
)


def expect(ok: bool, label: str) -> None:
    if not ok:
        raise AssertionError(f"{label} failed")
    print(f"ok - {label}")


def main() -> None:
    db_source = (ROOT / "services/db.py").read_text(encoding="utf-8")
    service_source = (ROOT / "pulse_communications_v2/service.py").read_text(encoding="utf-8")
    js_source = (ROOT / "static/js/pulse_messages_v2.js").read_text(encoding="utf-8")

    for table in TABLES:
        expect(f'"{table}": "id"' in db_source, f"{table} returns generated IDs on PostgreSQL")

    class FakeCursor:
        description = (("id",),)

        def __init__(self):
            self.sql = ""

        def execute(self, sql, params):
            self.sql = sql

        def fetchone(self):
            return (91,)

        def fetchall(self):
            return []

    fake = FakeCursor()
    cursor = db_service.CompatCursor(fake)
    cursor.execute("INSERT INTO comm_v2_conversations (public_id) VALUES (?)", ("audit",))
    expect("RETURNING id" in fake.sql, "PostgreSQL adapter adds RETURNING id for V2 inserts")
    expect(cursor.lastrowid == 91, "PostgreSQL adapter exposes V2 generated ID")

    list_start = service_source.find("def list_conversations(")
    list_end = service_source.find("\ndef send_message(", list_start)
    query = service_source[list_start:list_end] if list_start >= 0 and list_end > list_start else ""
    expect(bool(query), "conversation list query is discoverable")
    expect("SELECT DISTINCT c.*" not in query, "conversation list avoids PostgreSQL DISTINCT/ORDER BY conflict")
    expect("SELECT c.*" in query, "conversation list keeps the complete conversation payload")
    expect("Messenger is temporarily unavailable." in js_source, "V2 UI does not mislabel server errors as upload failures")
    expect("test the staged system" not in js_source, "published V2 UI does not use staged-system copy")
    expect("_SCHEMA_READY = False" in service_source and "_SCHEMA_LOCK = threading.Lock()" in service_source, "schema initialization is process-cached and thread-safe")
    expect("PULSE_COMM_V2_SCHEMA_READY" in service_source, "schema initialization timing is logged")
    expect("migration_table_columns" in service_source and "if missing:" in service_source, "schema migration scans each table before checking individual columns")

    print("pulse communications v2 PostgreSQL audit ok")


if __name__ == "__main__":
    main()
