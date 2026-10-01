#!/usr/bin/env python3
"""Behavior audit for stale PulseSoc calls and the native call P0 contract."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("COINPILOTX_DISABLE_LOCAL_ENV", "1")
os.environ.setdefault("COINPILOTX_INIT_DB_ON_IMPORT", "0")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from services import pulsesoc_communications_engine as engine

    with tempfile.TemporaryDirectory(prefix="pulsesoc-call-p0-") as folder:
        db_path = Path(folder) / "calls.db"
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        cursor = connection.cursor()
        for statement in engine.CALL_TABLES:
            cursor.execute(statement)
        cursor.executescript(
            """
            CREATE TABLE users (user_id INTEGER PRIMARY KEY, display_name TEXT, username TEXT, avatar_url TEXT);
            CREATE TABLE comm_v2_participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER,
                user_id INTEGER,
                role TEXT,
                membership_state TEXT,
                left_at TEXT
            );
            """
        )
        cursor.executemany(
            "INSERT INTO users(user_id,display_name,username,avatar_url) VALUES(?,?,?,?)",
            [(1, "Current Tester", "current", ""), (2, "Vilson", "vilson", "")],
        )
        cursor.execute("INSERT INTO comm_v2_participants(conversation_id,user_id,role,membership_state,left_at) VALUES(10,1,'member','active','')")
        cursor.execute("INSERT INTO comm_v2_participants(conversation_id,user_id,role,membership_state,left_at) VALUES(10,2,'member','active','')")
        old = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat(timespec="seconds")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        calls = [
            ("stale-vilson", "connected", old),
            ("left-participant", "connected", now),
            ("valid-active", "connected", now),
        ]
        for public_id, status, timestamp in calls:
            cursor.execute(
                """INSERT INTO communication_calls
                   (public_id,conversation_id,room_name,provider,call_type,call_scope,status,created_by_user_id,created_at,updated_at)
                   VALUES(?,10,?,'livekit','audio','direct',?,2,?,?)""",
                (public_id, f"room-{public_id}", status, timestamp, timestamp),
            )
            call_id = cursor.lastrowid
            participant_status = "left" if public_id == "left-participant" else "joined"
            cursor.execute(
                """INSERT INTO communication_call_participants
                   (call_id,user_id,role,status,joined_at,last_seen_at,created_at,updated_at)
                   VALUES(?,1,'callee',?,?,?,?,?)""",
                (call_id, participant_status, timestamp, timestamp, timestamp, timestamp),
            )
            cursor.execute(
                """INSERT INTO communication_call_participants
                   (call_id,user_id,role,status,joined_at,last_seen_at,created_at,updated_at)
                   VALUES(?,2,'caller','joined',?,?,?,?)""",
                (call_id, timestamp, timestamp, timestamp, timestamp),
            )
        connection.commit()
        connection.close()

        original_open = engine._open_db
        original_emit = engine._emit_call_sync_event

        def open_test_db():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn, conn.cursor()

        engine._open_db = open_test_db
        engine._emit_call_sync_event = lambda *args, **kwargs: {"ok": True}
        try:
            result = engine.active_calls(1)
        finally:
            engine._open_db = original_open
            engine._emit_call_sync_event = original_emit

        active_ids = {item.get("public_id") for item in result.get("calls") or []}
        assert active_ids == {"valid-active"}, f"unexpected active calls: {active_ids}"
        assert result.get("expired_marked") == 1, result

        verify = sqlite3.connect(db_path)
        verify.row_factory = sqlite3.Row
        stale = verify.execute("SELECT status,end_reason FROM communication_calls WHERE public_id='stale-vilson'").fetchone()
        participant = verify.execute(
            """SELECT p.status,p.left_at FROM communication_call_participants p
               JOIN communication_calls c ON c.id=p.call_id
               WHERE c.public_id='stale-vilson' AND p.user_id=1"""
        ).fetchone()
        verify.close()
        assert stale["status"] == "expired", dict(stale)
        assert stale["end_reason"] == "stale_connected_timeout", dict(stale)
        assert participant["status"] == "left" and participant["left_at"], dict(participant)

    calls_api = (ROOT / "mobile-native/src/api/calls.ts").read_text(encoding="utf-8")
    incoming = (ROOT / "mobile-native/src/calls/IncomingCallLayer.tsx").read_text(encoding="utf-8")
    chat = (ROOT / "mobile-native/src/screens/ChatScreen.tsx").read_text(encoding="utf-8")
    assert "/api/pulse/communications/v2/conversations/" in calls_api
    assert "/api/pulse/comm/v2/conversations/${encodeURIComponent(String(conversationId))}/" not in calls_api
    assert "ACTIVE PULSESOC CALL" not in incoming and "floatingCall" not in incoming
    assert "KeyboardAvoidingView" in chat and "keyboardHeight" not in chat
    print("PulseSoc native call P0 behavior audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
