#!/usr/bin/env python3
"""Audit PulseSoc password-reset and health reliability boundaries."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BREVO_EMAIL_ENABLED", "0")
os.environ.setdefault("EMAIL_OPPORTUNISTIC_PROCESSOR_ENABLED", "0")
os.environ.setdefault("PULSE_MAIN_APP_SSE_ALLOWED", "0")

import bot  # noqa: E402


RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"{mark} {name}" + (f" - {detail}" if detail else ""))


def require(name: str, condition: bool, detail: str = "") -> None:
    record(name, bool(condition), detail)


def ensure_test_user() -> int:
    bot.init_db()
    email = "reliability-reset-audit@example.test"
    username = "reliability_reset_audit"
    now = datetime.now().isoformat()
    conn = bot.db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE lower(email)=lower(?) LIMIT 1", (email,))
    row = cur.fetchone()
    if row:
        conn.close()
        return int(row[0])
    columns = set(bot.table_columns(cur, "users"))
    values = {
        "username": username,
        "email": email,
        "password_hash": bot.generate_password_hash("AuditPass123!"),
        "created_at": now,
        "updated_at": now,
        "email_verified": 1,
        "account_status": "active",
        "login_enabled": 1,
        "access_enabled": 1,
        "full_name": "Reliability Audit",
        "name": "Reliability Audit",
    }
    available = {key: value for key, value in values.items() if key in columns}
    placeholders = ",".join(["?"] * len(available))
    cur.execute(
        f"INSERT INTO users ({','.join(available.keys())}) VALUES ({placeholders})",
        tuple(available.values()),
    )
    conn.commit()
    user_id = int(cur.lastrowid)
    conn.close()
    return user_id


def latest_reset_row(user_id: int):
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        "SELECT token, token_hash, delivery_status FROM password_reset_tokens WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def csrf_post(client, path: str, data: dict):
    with client.session_transaction() as sess:
        sess["csrf_token"] = "audit-csrf"
    data = {**data, "csrf_token": "audit-csrf"}
    return client.post(path, data=data, follow_redirects=False)


def main() -> int:
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    routes_source = (ROOT / "pulse_communications_v2" / "routes.py").read_text(encoding="utf-8")
    require("global exception handler exists", "@webhook_app.errorhandler(Exception)" in source)
    require("safe reset helper exists", "def safe_password_reset_request" in source)
    require("web forgot-password uses safe helper", 'safe_password_reset_request(email, source="web")' in source)
    require("mobile recovery uses safe helper", 'safe_password_reset_request(email, source="mobile_api")' in source)
    require("hashed reset lookup exists", "WHERE token_hash=?" in source)
    require("reset token hash column is migrated", "idx_password_reset_tokens_hash" in source and "token_hash" in source)
    require("live health endpoint exists", '"/health/live"' in routes_source)
    require("ready health endpoint exists", '"/health/ready"' in routes_source)
    require("deep health is admin-only", '"/admin/health/deep"' in routes_source and "Admin access required" in routes_source)

    client = bot.webhook_app.test_client()
    for path in ("/health", "/health/live", "/health/ready"):
        response = client.get(path)
        require(f"{path} returns JSON without crashing", response.status_code == 200 and response.is_json, f"status={response.status_code}")

    unknown = csrf_post(client, "/forgot-password", {"email": "missing-reliability-audit@example.test"})
    require("forgot-password unknown email is generic 200", unknown.status_code == 200 and b"If that email has an account" in unknown.data, f"status={unknown.status_code}")

    user_id = ensure_test_user()
    valid = csrf_post(client, "/forgot-password", {"email": "reliability-reset-audit@example.test"})
    require("forgot-password valid email survives provider config", valid.status_code == 200 and b"If that email has an account" in valid.data, f"status={valid.status_code}")
    row = latest_reset_row(user_id)
    require("new reset record exists", bool(row))
    if row:
        token, token_hash, delivery_status = row
        require("new reset token is hashed", bool(token_hash) and not token, f"delivery_status={delivery_status or ''}")

    invalid = csrf_post(client, "/reset-password/not-a-real-token", {"password": "AuditPass123!"})
    require("invalid reset token handled cleanly", invalid.status_code == 200 and b"invalid or expired" in invalid.data.lower(), f"status={invalid.status_code}")

    mobile_recover = client.post("/api/mobile/auth/recover", json={"email": "missing-reliability-audit@example.test"})
    require("mobile recover survives unknown email", mobile_recover.status_code == 200 and mobile_recover.is_json, f"status={mobile_recover.status_code}")

    failed = [item for item in RESULTS if not item[1]]
    report = {
        "ok": not failed,
        "passed": len(RESULTS) - len(failed),
        "failed": len(failed),
        "failures": [{"name": name, "detail": detail} for name, ok, detail in RESULTS if not ok],
    }
    print(json.dumps(report, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
