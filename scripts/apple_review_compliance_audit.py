#!/usr/bin/env python3
"""Audit App Store review compliance hooks for PulseSoc communication safety."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    file_path = ROOT / path
    return file_path.read_text(encoding="utf-8") if file_path.exists() else ""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    bot = read("bot.py")
    comm_routes = read("pulse_communications_v2/routes.py")
    comm_service = read("pulse_communications_v2/service.py")
    account = read("templates/account.html")
    terms = read("templates/terms.html")
    app_json = read("mobile/pulse-react-native/app.json")

    require("terms" in account.lower() or "eula" in account.lower(), "account entry must expose Terms/EULA before or near account access")
    require("no-tolerance" in terms.lower() or "objectionable" in terms.lower(), "terms must describe UGC moderation/no-tolerance policy")
    require("report_message" in comm_routes and "block_user" in comm_routes, "Messages must expose report and block APIs")
    require("comm_v2_reports" in comm_service and "comm_v2_blocks" in comm_service, "Report/block persistence must remain wired")
    require("Report Profile" in bot or "report profile" in bot.lower(), "Profile report action must remain present")
    require("Block" in bot or "blocked_user_id" in bot, "User blocking must remain present")
    require("supportsTablet" in app_json and "false" in app_json, "iOS app must remain iPhone-only for the submitted build")
    require("external" not in account.lower() or "ios" in account.lower(), "External purchase surfaces must stay gated on iOS")
    require("privacy" in terms.lower(), "Terms/privacy text must remain available for reviewers")

    print("apple_review_compliance_audit: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"apple_review_compliance_audit: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
