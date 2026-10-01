#!/usr/bin/env python3
"""Production readiness smoke audit for critical PulseSoc surfaces."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, asdict
from datetime import UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "production_readiness_audit.md"
JSON_REPORT = ROOT / "reports" / "production_readiness_audit.json"
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service  # noqa: E402


SURFACES = {
    "Admin routes": [
        "/admin/dashboard",
        "/admin/security",
        "/admin/audit-logs",
        "/admin/performance",
        "/admin/system-audit",
        "/admin/messages-health",
        "/admin/notifications",
    ],
    "Messaging": [
        "/pulse/messages",
        "/pulse/messages-v2",
        "/api/pulse/communications/v2/conversations",
        "/api/pulse/communications/v2/realtime?after_id=0&limit=20",
    ],
    "Live streaming": [
        "/pulse/live",
        "/api/pulse/live/stream",
    ],
    "Notifications": [
        "/pulse/notifications",
        "/api/pulse/notifications/unread-count",
        "/api/pulse/badge-counts",
    ],
    "Composer": [
        "/pulse/create",
        "/pulse/camera",
    ],
    "Videos": [
        "/pulse/videos",
        "/api/pulse/videos",
    ],
    "Reels": [
        "/pulse/reels",
        "/api/pulse/reels/feed",
    ],
    "Premium": [
        "/pulse/premium",
        "/pulse/premium/undx",
        "/pulse/creator/dashboard",
        "/admin/premium-command",
    ],
    "Marketplace": [
        "/pulse/marketplace",
        "/pulse/merchant/dashboard",
        "/admin/marketplace-command",
        "/admin/merchant-applications",
    ],
}


@dataclass
class Check:
    surface: str
    route: str
    status: str
    http_status: int
    detail: str


def ensure_accounts() -> tuple[int, int]:
    bot.init_db()
    now = bot.datetime.now(UTC).isoformat(timespec="seconds")
    conn = db_service.connect()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users ORDER BY user_id LIMIT 1")
    row = cur.fetchone()
    if row:
        user_id = int(row["user_id"])
    else:
        cur.execute(
            "INSERT INTO users (username, display_name, email, signup_time, created_at, onboarding_complete, account_status) VALUES (?, ?, ?, ?, ?, 1, 'active')",
            ("prodready", "Production Ready", "prod-ready@example.test", now, now),
        )
        user_id = int(cur.lastrowid)

    cur.execute("SELECT id FROM admin_users WHERE status='active' AND lower(role) IN ('owner','super_admin') ORDER BY id LIMIT 1")
    admin = cur.fetchone()
    if admin:
        admin_id = int(admin["id"])
    else:
        cur.execute(
            "INSERT INTO admin_users (email, password_hash, full_name, role, status, created_at, updated_at, must_change_password) VALUES (?, ?, ?, 'owner', 'active', ?, ?, 0)",
            ("prod-ready-admin@example.test", bot.generate_password_hash("not-used"), "Production Ready Admin", now, now),
        )
        admin_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return user_id, admin_id


def classify(route: str, response) -> Check:
    body = response.get_data(as_text=True)
    status_code = int(response.status_code)
    status = "pass"
    detail = f"HTTP {status_code}"
    if status_code >= 500:
        status = "fail"
    elif "Traceback" in body or "PulseSoc hit a temporary system issue" in body or "Internal Server Error" in body:
        status = "fail"
        detail = "error screen or raw error content detected"
    elif status_code in {401, 403, 404, 405}:
        status = "warn"
    return status, status_code, detail


def run_checks() -> list[Check]:
    user_id, admin_id = ensure_accounts()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
        session["admin_user_id"] = admin_id
        session["csrf_token"] = "prod-ready-csrf"

    checks: list[Check] = []
    for surface, routes in SURFACES.items():
        for route in routes:
            try:
                response = client.get(route)
                status, status_code, detail = classify(route, response)
            except Exception as exc:
                status, status_code, detail = "fail", 0, f"raised {type(exc).__name__}: {exc}"
            checks.append(Check(surface, route, status, status_code, detail))
    return checks


def write_reports(checks: list[Check]) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    failed = [check for check in checks if check.status == "fail"]
    warned = [check for check in checks if check.status == "warn"]
    lines = [
        "# Production Readiness Audit",
        "",
        f"- Routes audited: {len(checks)}",
        f"- Issues found: {len(failed) + len(warned)}",
        f"- Deployment blockers: {len(failed)}",
        "- Issues fixed: PostgreSQL SELECT alias in `/admin/security` suspicious-domain HAVING clause is guarded by `scripts/postgres_compatibility_audit.py`.",
        "- Remaining risks: local test-client smoke checks do not exercise third-party providers, real browser media playback, or production-only data volumes.",
        "",
        "| Surface | Route | Result | HTTP | Detail |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check in checks:
        lines.append(f"| {check.surface} | `{check.route}` | {check.status.upper()} | {check.http_status} | {check.detail} |")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    JSON_REPORT.write_text(json.dumps([asdict(check) for check in checks], indent=2) + "\n", encoding="utf-8")
    print(f"report={REPORT.relative_to(ROOT)}")


def main() -> int:
    checks = run_checks()
    write_reports(checks)
    failed = [check for check in checks if check.status == "fail"]
    warned = [check for check in checks if check.status == "warn"]
    print(f"production readiness audit: pass={len(checks) - len(failed) - len(warned)} warn={len(warned)} fail={len(failed)}")
    for check in failed:
        print(f"FAIL {check.surface} {check.route}: {check.detail}")
    if failed:
        return 1
    print("production readiness audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
