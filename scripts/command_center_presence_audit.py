#!/usr/bin/env python3
"""Audit the PulseSoc Command Center presence engine."""

from __future__ import annotations

import json
import os
import py_compile
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT_TOKEN = "presence-audit-token"


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def compile_targets() -> list[str]:
    targets = [
        ROOT / "bot.py",
        ROOT / "services" / "command_center_client.py",
        ROOT / "services" / "command_center_worker" / "app.py",
        ROOT / "services" / "command_center_worker" / "presence.py",
        Path(__file__).resolve(),
    ]
    compiled = []
    for target in targets:
        py_compile.compile(str(target), doraise=True)
        compiled.append(str(target.relative_to(ROOT)))
    return compiled


def iso_minutes_ago(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds").replace("+00:00", "Z")


def configure_env(db_path: Path) -> None:
    os.environ["PULSESOC_SERVICE_NAME"] = "command-center-worker"
    os.environ["PULSESOC_SERVICE_ROLE"] = "worker"
    os.environ["COMMAND_CENTER_WORKER_ENABLED"] = "true"
    os.environ["COMMAND_CENTER_INTERNAL_TOKEN"] = AUDIT_TOKEN
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ.pop("REDIS_URL", None)


def audit_worker_presence() -> dict:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "presence_audit.db"
        configure_env(db_path)
        sys.path.insert(0, str(ROOT))
        from services.command_center_worker.app import app
        from services.command_center_worker import presence

        presence.ensure_presence_schema()
        client = app.test_client()

        invalid = client.post(
            "/internal/command-center/presence/update",
            headers={"Authorization": f"Bearer {AUDIT_TOKEN}"},
            json={"user_id": 123, "status": "busy", "source": "audit", "device_label": "audit-device"},
        )
        expect(invalid.status_code == 400, "invalid status should be rejected")

        missing_token = client.post(
            "/internal/command-center/presence/update",
            json={"user_id": 123, "status": "online", "source": "audit", "device_label": "audit-device"},
        )
        expect(missing_token.status_code == 401, "missing token should be rejected")

        accepted = client.post(
            "/internal/command-center/presence/update",
            headers={"Authorization": f"Bearer {AUDIT_TOKEN}"},
            json={"user_id": 123, "status": "online", "source": "audit", "device_label": "audit-device"},
        )
        expect(accepted.status_code == 200, f"valid presence update returned {accepted.status_code}")
        accepted_json = accepted.get_json() or {}
        expect(accepted_json.get("accepted") is True, "valid presence update was not accepted")
        expect((accepted_json.get("presence") or {}).get("status") == "online", "accepted presence status mismatch")

        lookup = client.get(
            "/internal/command-center/presence/123",
            headers={"Authorization": f"Bearer {AUDIT_TOKEN}"},
        )
        expect(lookup.status_code == 200, f"presence lookup returned {lookup.status_code}")
        lookup_json = lookup.get_json() or {}
        expect(lookup_json.get("user_id") == 123, "presence lookup user_id mismatch")
        expect(lookup_json.get("status") == "online", "presence lookup status mismatch")

        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE user_presence SET status='online', last_active_at=?, updated_at=? WHERE user_id=123", (iso_minutes_ago(6), iso_minutes_ago(6)))
        conn.execute(
            """
            INSERT INTO user_presence (user_id, status, last_seen_at, last_active_at, source, device_label, updated_at)
            VALUES (124, 'away', ?, ?, 'audit', 'audit-device', ?)
            """,
            (iso_minutes_ago(16), iso_minutes_ago(16), iso_minutes_ago(16)),
        )
        conn.commit()
        conn.close()

        cleanup = presence.cleanup_stale_presence()
        expect(cleanup.get("ok") is True, "stale cleanup did not complete")
        away = presence.get_presence(123)
        offline = presence.get_presence(124)
        expect(away.get("status") == "away", "stale online user should become away after 5 minutes")
        expect(offline.get("status") == "offline", "stale away user should become offline after 15 minutes")

        serialized = json.dumps({"accepted": accepted_json, "lookup": lookup_json, "cleanup": cleanup}, sort_keys=True)
        expect(AUDIT_TOKEN not in serialized, "internal token leaked in audit payloads")
        expect(str(db_path) not in serialized, "database path leaked in audit payloads")

        return {
            "invalid_status": invalid.status_code,
            "missing_token": missing_token.status_code,
            "accepted": accepted.status_code,
            "lookup_status": lookup_json.get("status"),
            "cleanup": cleanup,
            "away_status": away.get("status"),
            "offline_status": offline.get("status"),
        }


def audit_main_app_disabled_mode() -> dict:
    os.environ["COMMAND_CENTER_ENABLED"] = "false"
    from services import command_center_client

    result = command_center_client.enqueue_presence_event(123, "online", source="audit", device_label="audit")
    expect(result.get("ok") is True, "disabled main-app presence dispatch should be safe")
    expect(result.get("dispatched") is False, "disabled main-app presence dispatch should not call worker")
    expect(result.get("reason") == "disabled", "disabled main-app presence dispatch reason mismatch")
    bot_source = (ROOT / "bot.py").read_text(encoding="utf-8")
    expect("services.command_center_worker" not in bot_source, "main app should not import worker package")
    return result


def main() -> int:
    compiled = compile_targets()
    worker = audit_worker_presence()
    disabled_mode = audit_main_app_disabled_mode()
    report = {
        "ok": True,
        "compiled": compiled,
        "worker_imports": True,
        "presence_module_imports": True,
        "invalid_status_rejected": worker["invalid_status"] == 400,
        "missing_token_rejected": worker["missing_token"] == 401,
        "valid_token_accepts_update": worker["accepted"] == 200,
        "get_presence_returns_expected_state": worker["lookup_status"] == "online",
        "stale_cleanup_transitions": {
            "online_to_away": worker["away_status"] == "away",
            "away_to_offline": worker["offline_status"] == "offline",
        },
        "worker_disabled_mode_does_not_break_main_app": disabled_mode.get("reason") == "disabled",
        "secrets_exposed": False,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
