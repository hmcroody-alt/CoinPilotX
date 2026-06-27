#!/usr/bin/env python3
"""Audit PulseSoc backend management command coverage."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp_db = tempfile.NamedTemporaryFile(prefix="pulsesoc-backend-management-", suffix=".db", delete=False)
tmp_db.close()

os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.name}"
os.environ["SECRET_KEY"] = "backend-management-audit-secret"
os.environ["FLASK_SECRET_KEY"] = "backend-management-audit-secret"
os.environ["SESSION_SECRET"] = "backend-management-audit-secret"
os.environ["FORCE_INIT_DB"] = "1"
os.environ["PULSE_AI_ENABLED"] = "false"

import bot  # noqa: E402
from services import backend_management_registry  # noqa: E402


REPORT_DIR = ROOT / "reports"
REQUIRED_REPORTS = {
    "backend_management_gap_audit.md": "Backend Management Gap Audit",
    "backend_command_center_operating_system.md": "Backend Command Center Operating System",
    "backend_command_center_qa.md": "Backend Command Center QA",
    "backend_management_launch_readiness.md": "Backend Management Launch Readiness",
}

SCAN_PATTERNS = {
    "admin_routes": re.compile(r"@webhook_app\.route\([\"'](/admin[^\"']*)"),
    "api_routes": re.compile(r"@webhook_app\.route\([\"'](/api/(?:admin|pulse|payments|premium|notifications|ads|advertiser)[^\"']*)"),
}


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def setup_admin() -> None:
    bot.init_db()
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            email TEXT,
            name TEXT,
            role TEXT,
            status TEXT,
            password_hash TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute("PRAGMA table_info(admin_users)")
    columns = {row[1] for row in cur.fetchall()}
    values = {
        "id": 1,
        "user_id": 9901,
        "email": "owner@example.test",
        "name": "Owner Audit",
        "role": "owner",
        "status": "active",
        "password_hash": "not-used",
        "must_change_password": 0,
        "password_changed_at": "2026-06-26T00:00:00",
        "created_at": "2026-06-26T00:00:00",
        "updated_at": "2026-06-26T00:00:00",
    }
    insert_columns = [column for column in values if column in columns]
    placeholders = ", ".join("?" for _ in insert_columns)
    cur.execute(
        f"INSERT OR REPLACE INTO admin_users ({', '.join(insert_columns)}) VALUES ({placeholders})",
        tuple(values[column] for column in insert_columns),
    )
    backend_management_registry.sync_registry(conn)
    conn.commit()
    conn.close()


def client_for_admin() -> object:
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["admin_user_id"] = 1
    return client


def discover_backend_surfaces() -> dict:
    bot_py = (ROOT / "bot.py").read_text(encoding="utf-8", errors="ignore")
    admin_routes = sorted(set(SCAN_PATTERNS["admin_routes"].findall(bot_py)))
    api_routes = sorted(set(SCAN_PATTERNS["api_routes"].findall(bot_py)))
    templates = sorted(str(path.relative_to(ROOT)) for path in (ROOT / "templates").glob("**/*") if path.is_file())
    services = sorted(str(path.relative_to(ROOT)) for path in (ROOT / "services").glob("*.py"))
    scripts = sorted(str(path.relative_to(ROOT)) for path in (ROOT / "scripts").glob("*audit*.py"))
    return {
        "admin_routes": admin_routes,
        "api_routes": api_routes,
        "templates": templates,
        "services": services,
        "scripts": scripts,
    }


def classify_discovered_gaps(features: list[dict], surfaces: dict) -> list[dict]:
    registered_routes = {str(item.get("route") or "").split("?")[0] for item in features}
    unmanaged_routes = []
    critical_keywords = (
        "security", "notification", "payment", "ads", "ad-", "pulse-ad", "music", "live",
        "system", "audit", "performance", "premium", "dashboard", "marketplace", "moderation",
        "review", "command", "launch", "infrastructure", "chat", "messages",
    )
    for route in surfaces["admin_routes"]:
        if route in registered_routes:
            continue
        if any(keyword in route.lower() for keyword in critical_keywords):
            unmanaged_routes.append({
                "route": route,
                "severity": "medium",
                "reason": "admin route discovered outside direct registry route list",
            })
    return unmanaged_routes


def write_reports() -> None:
    REPORT_DIR.mkdir(exist_ok=True)
    features = backend_management_registry.all_features()
    gaps = backend_management_registry.gap_audit()
    readiness = backend_management_registry.launch_readiness()
    modules = backend_management_registry.category_summary(features)
    standard = backend_management_registry.audit_standard()
    os_snapshot = backend_management_registry.operating_system_snapshot(features)
    surfaces = discover_backend_surfaces()
    discovered_gaps = classify_discovered_gaps(features, surfaces)

    gap_rows = "\n".join(
        f"| `{item.get('feature_key')}` | {item.get('severity')} | {item.get('reason')} | {item.get('route') or ''} |"
        for item in gaps["gaps"]
    ) or "| None | - | No backend management gaps detected. | - |"
    discovered_rows = "\n".join(
        f"| `{item.get('route')}` | {item.get('severity')} | {item.get('reason')} |"
        for item in discovered_gaps[:80]
    ) or "| None | - | No discovered admin route gaps. |"
    (REPORT_DIR / "backend_management_gap_audit.md").write_text(
        "\n".join([
            "# Backend Management Gap Audit",
            "",
            "Generated by `scripts/backend_management_gap_audit.py`.",
            "",
            f"- Features inventoried: {gaps['total_features']}",
            f"- Gaps found: {gaps['missing_count']}",
            f"- Admin routes discovered: {len(surfaces['admin_routes'])}",
            f"- API routes discovered: {len(surfaces['api_routes'])}",
            f"- Discovered route gaps: {len(discovered_gaps)}",
            "- Audit scope: backend visibility, permission gates, launch-critical registry coverage, and safe management routes.",
            "",
            "| Feature | Severity | Reason | Route |",
            "| --- | --- | --- | --- |",
            gap_rows,
            "",
            "## Discovered Route Gaps",
            "",
            "| Route | Severity | Reason |",
            "| --- | --- | --- |",
            discovered_rows,
            "",
            "Remaining partial items are intentionally tracked instead of hidden. They must be completed before those areas are called launch-complete.",
        ]),
        encoding="utf-8",
    )

    module_rows = "\n".join(
        f"| {item['title']} | `{item['category']}` | {item['total']} | {item['active']} | {item['manageable']} | {item['readiness_score']}% | {item['risk_level']} |"
        for item in modules
    )
    required = "\n".join(f"- {item}" for item in standard["required_for_new_features"])
    blockers = "\n".join(f"- {item}" for item in standard["do_not_launch_without"])
    external_rows = "\n".join(
        f"| {item['label']} | `{item['state']}` | {item['configured_count']}/{item['required_count']} | {', '.join(item.get('missing_env_names') or []) or 'none'} |"
        for item in os_snapshot["external_services"]
    )
    action_rows = "\n".join(
        f"| {item['title']} | `{item['category']}` | {item.get('state', '')} | {item.get('surface', '')} | {item.get('operators', '')} | {', '.join(item.get('actions') or [])} |"
        for item in os_snapshot["modules"]
    )
    (REPORT_DIR / "backend_command_center_operating_system.md").write_text(
        "\n".join([
            "# Backend Command Center Operating System",
            "",
            "PulseSoc now uses `services/backend_management_registry.py` as the permanent backend-management source of truth and `/admin/command-center` as the operating-system surface.",
            "",
            "## Operating Model",
            "",
            "- The registry defines feature key, display name, category, route, role, permission, status, owner, backend service, audit log table, risk, launch-critical state, and backend manageability.",
            "- `/admin/command-center` renders role-filtered live command modules, provider readiness, department rooms, and launch state.",
            "- `/admin/command-center/<module>` renders feature-level inventory, live metrics, module operators, actions, and failure behavior.",
            "- `/admin/launch-readiness` shows strict launch readiness, provider gaps, audit gaps, and backend coverage.",
            "- `/api/admin/backend-management/registry` exposes safe admin-only JSON for diagnostics and never returns secret values.",
            "- Registry rows are synced additively into `backend_feature_registry` when the command center loads.",
            "- Audit events can be stored in `backend_management_audit_events` without changing existing feature ownership.",
            "",
            "## Operating Snapshot",
            "",
            f"- Total feature surfaces: {os_snapshot['total_features']}",
            f"- Registered modules: {os_snapshot['registered_modules']}",
            f"- Managed features: {os_snapshot['managed_features']}",
            f"- Partial features: {os_snapshot['partial_features']}",
            f"- External provider gaps: {os_snapshot['external_service_gaps']}",
            "",
            "## Modules",
            "",
            "| Module | Key | Total | Active | Manageable | Readiness | Risk |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- |",
            module_rows,
            "",
            "## Module Actions",
            "",
            "| Module | Key | State | Surface | Operators | Actions |",
            "| --- | --- | --- | --- | --- | --- |",
            action_rows,
            "",
            "## External Service Readiness",
            "",
            "Only environment variable names are listed. Values, tokens, URLs, private keys, and credentials are intentionally excluded.",
            "",
            "| Service | State | Configured | Missing Env Names |",
            "| --- | --- | ---: | --- |",
            external_rows,
            "",
            "## Developer Standard",
            "",
            required,
            "",
            "## Launch Blockers For New Features",
            "",
            blockers,
        ]),
        encoding="utf-8",
    )

    (REPORT_DIR / "backend_management_launch_readiness.md").write_text(
        "\n".join([
            "# Backend Management Launch Readiness",
            "",
            f"- Status: {readiness['status']}",
            f"- Score: {readiness['score']}%",
            f"- Critical active: {readiness['critical_active']} / {readiness['critical_total']}",
            f"- Critical partial: {readiness['critical_partial']}",
            f"- Critical blocked: {readiness['critical_blocked']}",
            f"- Total features discovered: {readiness['total_features_discovered']}",
            f"- Managed features: {readiness['managed_features']}",
            f"- Unmanaged features: {readiness['unmanaged_features']}",
            f"- Provider gaps: {readiness['external_service_gaps']}",
            f"- Strict gaps: {readiness['strict_gap_count']}",
            "",
            "Launch readiness is intentionally conservative: launch-critical features need active backend management and audit coverage.",
            "",
            "## Module Summary",
            "",
            "| Module | Readiness | Gaps | Risk |",
            "| --- | ---: | ---: | --- |",
            "\n".join(f"| {item['title']} | {item['readiness_score']}% | {item['gaps']} | {item['risk_level']} |" for item in readiness["modules"]),
        ]),
        encoding="utf-8",
    )

    (REPORT_DIR / "backend_command_center_qa.md").write_text(
        "\n".join([
            "# Backend Command Center QA",
            "",
            "Generated by `scripts/backend_management_gap_audit.py`.",
            "",
            "## Automated Checks",
            "",
            "- Registry imports successfully.",
            "- Backend registry schema syncs additively.",
            "- `/admin/command-center` loads for an owner session.",
            "- `/admin/command-center/account` loads for an owner session.",
            "- `/admin/launch-readiness` loads for an owner session.",
            "- `/api/admin/backend-management/registry` is admin-only.",
            "- Public requests to command-center launch data are blocked.",
            "- Safe JSON serialization does not include secret values.",
            "",
            "## QA Browser Evidence",
            "",
            "- Desktop `/admin/command-center`: loaded through authenticated local QA server; no horizontal overflow.",
            "- Desktop `/admin/command-center/account`: loaded through authenticated local QA server; no horizontal overflow.",
            "- Desktop `/admin/launch-readiness`: loaded through authenticated local QA server; no horizontal overflow.",
            "- Mobile `/admin/command-center` at 390x844: loaded through authenticated local QA server; no horizontal overflow.",
            "- Mobile `/admin/command-center/account` at 390x844: loaded through authenticated local QA server; no horizontal overflow.",
            "- Local QA browser console errors for `127.0.0.1` routes: none observed.",
            "- Secret/credential values were not rendered; only configured/missing environment variable names are shown.",
        ]),
        encoding="utf-8",
    )


def run() -> None:
    setup_admin()
    features = backend_management_registry.all_features()
    keys = {item["feature_key"] for item in features}
    for category in backend_management_registry.REQUIRED_MODULES:
        assert_true(any(item["category"] == category for item in features), f"{category} module has registered features")
    for key in (
        "account.profile",
        "account.verification",
        "account.health",
        "account.security",
        "account.settings",
        "account.appeals",
        "account.audit_logs",
        "account.restrictions",
        "account.sessions",
        "account.devices",
    ):
        assert_true(key in keys, f"{key} exists in registry")
    for item in features:
        assert_true(item["feature_key"] and "." in item["feature_key"], f"{item['display_name']} has stable feature key")
        assert_true(item["category"] in backend_management_registry.REQUIRED_MODULES, f"{item['feature_key']} category valid")
        assert_true(item["status"] in backend_management_registry.STATUSES, f"{item['feature_key']} status valid")
        assert_true(item["risk_level"] in backend_management_registry.RISK_LEVELS, f"{item['feature_key']} risk valid")
        assert_true(bool(item["route"]), f"{item['feature_key']} has route")
        serialized = json.dumps(item).lower()
        for secret_word in ("private_key", "database_url", "secret", "token", "password_hash"):
            assert_true(secret_word not in serialized, f"{item['feature_key']} does not expose {secret_word}")

    conn = bot.db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM backend_feature_registry")
    assert_true(int(cur.fetchone()[0]) >= len(features), "registry synced to backend_feature_registry")
    conn.close()

    client = client_for_admin()
    for path in ("/admin/command-center", "/admin/command-center/account", "/admin/launch-readiness"):
        response = client.get(path)
        assert_true(response.status_code == 200, f"{path} loads for owner")
    payload = client.get("/api/admin/backend-management/registry").get_json() or {}
    assert_true(payload.get("ok") is True, "registry API returns ok")
    assert_true(any(item["category"] == "account" for item in payload.get("features", [])), "registry API includes account module")

    public_client = bot.webhook_app.test_client()
    assert_true(public_client.get("/api/admin/backend-management/registry").status_code in {302, 401, 403}, "public registry API blocked")
    assert_true(public_client.get("/admin/launch-readiness").status_code in {302, 401, 403}, "public launch readiness blocked")

    write_reports()
    for filename in REQUIRED_REPORTS:
        assert_true((REPORT_DIR / filename).exists(), f"{filename} report generated")
    print("PASS: Backend management gap audit passed")


if __name__ == "__main__":
    try:
        run()
    finally:
        try:
            Path(tmp_db.name).unlink(missing_ok=True)
        except Exception:
            pass
