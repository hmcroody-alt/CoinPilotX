#!/usr/bin/env python3
"""Guard PulseSoc App Store Review fixes for the 1.0 rejection."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"Missing {path}")
    return target.read_text(encoding="utf-8")


def require(text, token, label, failures):
    if token not in text:
        failures.append(f"{label} missing: {token}")


def main():
    failures = []
    app = json.loads(read("mobile/pulse-react-native/app.json"))["expo"]
    if (app.get("ios") or {}).get("supportsTablet") is not False:
        failures.append("iOS tablet support must remain disabled until iPad layouts pass QA.")
    try:
        build_number = int(str((app.get("ios") or {}).get("buildNumber") or "0").split(".")[0])
    except ValueError:
        build_number = 0
    if build_number <= 26:
        failures.append("iOS build number must be higher than latest EAS/App Store build 26.")

    account = read("templates/account.html")
    require(account, 'name="terms_accepted"', "account forms", failures)
    require(account, "no-tolerance rules for objectionable content and abusive users", "account forms", failures)
    require(account, "{% if paid_digital_access_available %}\n            <a href=\"/pulse/premium\">PulseSoc Premium</a>", "native iOS account nav paid access gate", failures)
    require(account, "{% if not access.is_paid_pro and paid_digital_access_available %}", "native iOS dashboard paid CTA gate", failures)
    require(account, "{% if not paid_digital_access_available %}", "native iOS core fallback", failures)
    require(account, "Paid digital access is not available in this iOS build.", "native iOS paid unavailable copy", failures)

    terms = read("templates/terms.html")
    for token in [
        "no tolerance for objectionable content or abusive users",
        "content filtering, report controls, user blocking",
        "acts on objectionable content reports within 24 hours",
    ]:
        require(terms, token, "terms", failures)

    bot = read("bot.py")
    for token in [
        '@webhook_app.route("/api/pulse/block"',
        "INSERT INTO blocked_users",
        "INSERT INTO pulse_reports",
        "Paid digital access is not available in this iOS build",
        "External billing management is not available in this iOS build",
        "ios_paid_digital_unavailable_response(api=True)",
        '"ios_core_only": True',
        '"paid_digital_access_available": not ios_native',
        '"stripe_customer_id": "",',
        '"stripe_subscription_id": "",',
        'def api_billing_confirm_session():',
        'def api_payments_list_purchases():',
        'def api_payments_entitlements():',
        'context.setdefault("paid_digital_access_available", paid_digital_access_available)',
        "def pulse_premium_page():",
    ]:
        require(bot, token, "bot.py", failures)
    for route_name in [
        "checkout_page",
        "upgrade_success_page",
        "api_billing_confirm_session",
        "pulse_creator_monetization_page",
        "pulse_creator_ai_tool_api",
        "pulse_creator_dashboard_page",
        "pulse_creator_analytics_page",
        "pulse_premium_page",
        "pulse_courses_page",
        "pulse_course_create_page",
        "pulse_course_detail_page",
        "api_payments_order_verify",
        "api_payments_list_purchases",
        "api_payments_list_seller_orders",
    ]:
        marker = f"def {route_name}"
        if marker not in bot:
            failures.append(f"missing route function {route_name}")
            continue
        segment = bot[bot.index(marker): bot.index(marker) + 650]
        if "ios_native_app_request()" not in segment:
            failures.append(f"{route_name} must explicitly gate native iOS paid digital access")

    feed_engine = read("services/pulse_feed_engine.py")
    require(feed_engine, "NOT EXISTS (SELECT 1 FROM blocked_users bu", "feed engine block filtering", failures)

    home_js = read("static/js/pulse_home_core.js")
    for token in [
        '["Block user", { blockUser: author.public_player_id }]',
        'api("/api/pulse/block"',
        "node.dataset.authorPublicPlayerId === block.dataset.blockUser",
    ]:
        require(home_js, token, "feed block UI", failures)
    if "This menu action is queued for moderation tools." in home_js:
        failures.append("feed menu still exposes placeholder moderation actions")

    app_store = read("mobile/pulse-react-native/store-metadata/en-US/app-store.md")
    for token in [
        "Guideline",
        "Terms/EULA before signup/login",
        "Premium purchase surfaces are disabled in native iOS context",
        "Existing web subscriptions do not unlock paid digital premium surfaces inside this iOS build",
        "native Premium screen does not open Stripe",
        "iPhone-only",
        "physical-device screen recording",
    ]:
        require(app_store, token, "app store review notes", failures)
    if "premium creator tools into one mobile-first community" in app_store:
        failures.append("App Store metadata still advertises premium creator tools in the iOS app")

    store_config = read("mobile/pulse-react-native/store.config.json")
    if "premium creator tools into one mobile-first community" in store_config:
        failures.append("Store config still advertises premium creator tools in the iOS app")
    if "ipad-13-premium-inside" in store_config:
        failures.append("Store config still includes an iPad Premium screenshot")

    premium_screen = read("mobile/pulse-react-native/screens/main/PremiumScreen.tsx")
    for token in [
        'const isIos = Platform.OS === "ios";',
        'setStatus({ ok: true, plan: "iOS Core Access"',
        'await Linking.openURL("https://pulsesoc.com/pulse")',
        'isIos ? "Core Social Access" : "Premium"',
    ]:
        require(premium_screen, token, "native Premium screen iOS compliance", failures)

    moderation = read("mobile/pulse-react-native/store-metadata/moderation.md")
    require(moderation, "within 24 hours", "moderation metadata", failures)
    premium = read("mobile/pulse-react-native/store-metadata/premium-compliance.md")
    require(premium, "StoreKit purchase and restore flows", "premium compliance metadata", failures)

    if failures:
        print("PulseSoc App Store review fix audit FAILED")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("pulsesoc app store review fix audit ok")


if __name__ == "__main__":
    main()
