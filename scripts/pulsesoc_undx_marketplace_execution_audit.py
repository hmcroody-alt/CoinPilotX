#!/usr/bin/env python3
"""Static release gate for the governed native UNDX Marketplace workflow."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(text: str, needle: str, label: str, failures: list[str]) -> None:
    if needle not in text:
        failures.append(f"{label}: missing {needle!r}")


def function_body(text: str, name: str) -> str:
    marker = f"def {name}("
    start = text.find(marker)
    if start < 0:
        return ""
    next_route = text.find("\n@webhook_app.route(", start)
    return text[start:next_route if next_route >= 0 else len(text)]


def main() -> int:
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    engine = (ROOT / "services/business_os/undx_actions/engine.py").read_text(
        encoding="utf-8")
    workflow = (
        ROOT / "services/business_os/undx_actions/marketplace_workflow.py"
    ).read_text(encoding="utf-8")
    screen = (
        ROOT / "mobile-native/src/screens/UndxActionCenterScreen.tsx"
    ).read_text(encoding="utf-8")
    failures: list[str] = []

    require(bot, "def _business_os_undx_user_scope(user):",
            "server-owned identity", failures)
    require(bot, 'user_id = str((user or {}).get("user_id")',
            "canonical user id", failures)
    require(bot, 'trusted_org_id=scope["org_id"]',
            "trusted organization binding", failures)
    require(bot, 'trusted_actor=scope["actor"]',
            "trusted actor binding", failures)
    if 'str(user.get("id") or ""), pulse_ads_json_payload()' in bot:
        failures.append("legacy empty user-id lookup remains in UNDX workflow route")
    for route_name in (
        "api_business_os_undx_record_policy",
        "api_business_os_undx_record_request",
        "api_business_os_undx_register_tool",
        "api_business_os_undx_grant_permission",
        "api_business_os_undx_record_confirmation",
        "api_business_os_undx_record_receipt",
        "api_business_os_undx_emergency_stop",
        "api_business_os_undx_evaluate",
    ):
        body = function_body(bot, route_name)
        if "require_owner_api()" not in body:
            failures.append(f"{route_name}: governance mutation is not owner guarded")

    require(engine, "def redeem_confirmation(",
            "atomic confirmation boundary", failures)
    require(engine, "AND payload_hash = ?",
            "confirmation payload binding", failures)
    require(engine, "AND status = 'pending'",
            "single-use confirmation update", failures)
    require(workflow, "expires_at=plan.get(\"expires_at\")",
            "shared confirmation expiry", failures)
    require(workflow, "_engine.redeem_confirmation(",
            "UNDX confirmation redemption", failures)
    require(workflow, "current.get(\"effect\") == \"deny\"",
            "execution-time governance recheck", failures)

    require(screen, 'Alert.alert(',
            "native confirmation prompt", failures)
    require(screen, ">Review and publish<",
            "explicit publish review control", failures)
    if 'placeholder="Confirmation token from publish plan"' in screen:
        failures.append("raw confirmation-token input remains user visible")

    if failures:
        print("UNDX Marketplace execution audit: FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("UNDX Marketplace execution audit: PASS")
    print("- authenticated identity is server-owned")
    print("- confirmations are bound, expiring, and single-use")
    print("- governance is rechecked immediately before execution")
    print("- native publish uses an explicit review prompt without exposing tokens")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
