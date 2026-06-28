#!/usr/bin/env python3
"""Audit PulseSoc Verification Center and dashboard center wiring."""

from __future__ import annotations

import os
import sys
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("COINPILOTX_INIT_DB_ON_IMPORT", "0")

import bot  # noqa: E402
from services import pulsesoc_dashboard_centers  # noqa: E402


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    bot.init_db()
    source = (ROOT / "bot.py").read_text()
    service = (ROOT / "services" / "pulsesoc_dashboard_centers.py").read_text()

    required_tokens = [
        "/dashboard/account/verification",
        "/admin/command-center/account/verification",
        "/admin/verification/badges",
        "/admin/verification/appeals",
        "/dashboard/intelligence/ai-advisor",
        "/dashboard/economy/seller-tools",
        "/dashboard/economy/subscriptions",
        "/dashboard/economy/premium",
        "/dashboard/creator/content-planner",
        "/dashboard/creator/post-scheduler",
        "/dashboard/creator/draft-studio",
        "/dashboard/creator/ai-creator-assistant",
        "/pulse/dashboard/post-scheduler",
        "/pulse/dashboard/draft-studio",
        "/pulse/dashboard/ai-creator-assistant",
        "/api/dashboard/account/verification/document",
        "/admin/verification/document/",
        "data-start-verification-track",
        "data-verification-document-submit",
        "api_admin_verification_action",
    ]
    combined = source + "\n" + service
    for token in required_tokens:
        require(token in combined, f"missing route or UI token: {token}", failures)

    service_tokens = [
        "verification_badges",
        "status='approved'",
        "revoked_at IS NULL",
        "Admins cannot approve or review themselves.",
        "can_review",
        "verification_audit_logs",
        "record_private_document",
        "verification_document_for_admin",
        "Crypto information is for education and alerts only. It is not financial advice.",
        "AI endpoint available",
        "Publish Now",
    ]
    for token in service_tokens:
        require(token in service or token in source, f"missing backend safety token: {token}", failures)

    require("LogiNexus" not in source[source.find("def _verification_center_html"):source.find("def dashboard_network_shell")], "public dashboard renderers expose LogiNexus", failures)
    require("generic Open" not in source, "generic Open copy found", failures)
    require("Secure private document storage is not configured on this route" not in source, "verification upload still returns not configured", failures)
    require("501" not in source[source.find("def api_dashboard_verification_document"):source.find("@webhook_app.route(\"/api/dashboard/ai-advisor/goals\"")], "verification upload endpoint still returns 501", failures)

    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    try:
        pulsesoc_dashboard_centers.ensure_tables(conn)
        user = {"user_id": 1, "email": "audit@example.com", "username": "audit", "email_verified": 1, "plan": "free"}
        state = pulsesoc_dashboard_centers.build_verification_center(conn, user)
        require(bool(state.get("tracks")), "verification tracks missing from backend state", failures)
        require(not pulsesoc_dashboard_centers.active_badges(conn, 1), "badge rendered without approved backend state", failures)
        result = pulsesoc_dashboard_centers.create_verification_request(conn, user, "identity")
        require(result.get("ok"), "user verification submission failed", failures)
        request_id = int((result.get("request") or {}).get("id") or 0)
        readonly = {"id": 999, "role": "support_readonly", "email": "readonly@example.com"}
        decision = pulsesoc_dashboard_centers.admin_decision(conn, readonly, request_id, "approve")
        require(not decision.get("ok"), "readonly admin can approve verification", failures)
        self_admin = {"id": 1, "role": "admin", "email": "audit@example.com"}
        self_decision = pulsesoc_dashboard_centers.admin_decision(conn, self_admin, request_id, "approve")
        require(not self_decision.get("ok"), "admin can approve own verification", failures)
    finally:
        conn.close()

    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = 1
    for route in [
        "/dashboard/account/verification",
        "/dashboard/intelligence/ai-advisor",
        "/dashboard/economy/seller-tools",
        "/dashboard/economy/subscriptions",
        "/dashboard/economy/premium",
        "/dashboard/creator/content-planner",
        "/dashboard/creator/post-scheduler",
        "/dashboard/creator/draft-studio",
        "/dashboard/creator/ai-creator-assistant",
        "/pulse/dashboard/post-scheduler",
        "/pulse/dashboard/draft-studio",
        "/pulse/dashboard/ai-creator-assistant",
    ]:
        response = client.get(route)
        require(response.status_code != 404, f"{route} returned 404", failures)
        html = response.get_data(as_text=True)
        require("LogiNexus" not in html, f"{route} exposes internal LogiNexus name", failures)
        lowered = html.lower()
        safe_fake_copy = "does not fake" in lowered or "not fabricated" in lowered or "never faked" in lowered or "no fake" in lowered
        require("fake" not in lowered or safe_fake_copy, f"{route} may contain unsafe fake-state language", failures)
        if "dashboard/creator" in route or "dashboard/economy" in route or "ai-advisor" in route or "verification" in route:
            require("checklist" in lowered or "completion" in lowered, f"{route} missing checklist/completion UI", failures)
        require("href=\"#\"" not in lowered, f"{route} contains dead # link", failures)

    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    try:
        pulsesoc_dashboard_centers.ensure_tables(conn)
        upload_user = {"user_id": 1, "email": "audit@example.com", "username": "audit", "email_verified": 1}
        upload_request = pulsesoc_dashboard_centers.create_verification_request(conn, upload_user, "business")
        upload_request_id = int((upload_request.get("request") or {}).get("id") or request_id or 0)
    finally:
        conn.close()
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 600
    upload_response = client.post(
        "/api/dashboard/account/verification/document",
        data={"request_id": str(upload_request_id), "document_type": "business_document", "file": (BytesIO(png), "audit.png")},
        content_type="multipart/form-data",
    )
    require(upload_response.status_code == 200, f"private verification upload failed: {upload_response.status_code} {upload_response.get_data(as_text=True)[:160]}", failures)
    upload_json = upload_response.get_json(silent=True) or {}
    require(upload_json.get("ok") is True, "private verification upload did not return ok", failures)
    require("storage_path" not in upload_json and "url" not in upload_json, "private upload response exposes storage path or URL", failures)

    admin_response = client.get("/admin/command-center/account/verification")
    require(admin_response.status_code in {302, 401, 403}, "non-admin can access verification review queue", failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("verification_center_audit: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
