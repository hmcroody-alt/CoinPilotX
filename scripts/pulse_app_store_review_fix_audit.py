#!/usr/bin/env python3
"""Guard the parts of the 1.0 App Store rejection repair that are still policy.

Scope
-----

Apple rejected 1.0 in June 2026 on four guidelines: Design 4.0 and Performance
2.1(a) (iPad layout clipped), Privacy 5.1.1(v) (no in-app account deletion), and
Payments 3.1.1 (Stripe checkout reachable inside the iOS app). `beb4e8d8` fixed
all four and 1.0 shipped 2026-07-01.

Two of those repairs are permanent product policy and are what this file guards:
the app stays iPhone-only, and the **web** payment surfaces stay closed inside a
native iOS request. Everything else here is an assertion about live web/server
code (`bot.py`, `templates/`, `static/`, `services/`).

What was removed from this file, and why
----------------------------------------

Every check that read `mobile/pulse-react-native/**` was deleted. That tree is
the legacy Expo 51 app, frozen since 2026-06-30; the shipping app is
`mobile-native/`. Both declare `com.pulsesoc.app` and both point at ascAppId
6777591572, so the reads looked plausible while validating an artifact nobody
builds. Three of them were worse than merely inert:

  * `ios.buildNumber > 26` passed because the legacy tree holds 27 - the build
    that already shipped. A frozen literal compared against a frozen file is a
    tautology, not a gate. The live successor is
    `tests/protection/test_ios_build_version_contract.py`, which reads the
    authoritative `Info.plist`; repointing this check would only duplicate it
    worse.
  * `ios.supportsTablet is False` is advisory in a bare workflow - it reaches the
    binary only via `expo prebuild`, which nobody runs here. Repointing it at
    `mobile-native/app.json` would have reproduced exactly the bug 34f1d7d3 just
    fixed, so the iPhone-only check now reads `TARGETED_DEVICE_FAMILY`.
  * The `PremiumScreen.tsx` / store-metadata assertions required that iOS offer
    no purchases at all. That was the 1.0 workaround for 3.1.1, and it has since
    been **reversed on purpose**: `mobile-native/src/payments/appleIapPremium.ts`
    ships StoreKit 2 with server-side verification through
    `/api/pulse/payments/apple/premium/verify`. Repointing them would have failed
    compliant code for being compliant.

The `bot.py` gates below are not part of that reversal. StoreKit is the only
sanctioned purchase channel; Stripe and web checkout inside a native iOS request
stay blocked, which is why `ios_native_app_request()` is still load-bearing.
"""

import re
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
    pbxproj = read("mobile-native/ios/PulseSoc.xcodeproj/project.pbxproj")
    families = {
        value.strip().strip('"')
        for value in re.findall(r"^\s*TARGETED_DEVICE_FAMILY = ([^;]+);", pbxproj, re.M)
    }
    if families != {"1"}:
        failures.append(
            "iOS build must stay iPhone-only until iPad layouts pass Design 4.0 QA; "
            f"TARGETED_DEVICE_FAMILY reads {sorted(families) or ['unset']}"
        )

    account = read("templates/account.html")
    require(account, 'name="terms_accepted"', "account forms", failures)
    require(account, "no-tolerance rules for objectionable content and abusive users", "account forms", failures)
    require(account, "{% if paid_digital_access_available %}\n            <a href=\"/pulse/premium\">PulseSoc Premium</a>", "native iOS account nav paid access gate", failures)
    require(account, "{% if not access.is_paid_pro and paid_digital_access_available %}", "native iOS dashboard paid CTA gate", failures)
    require(account, "{% if not paid_digital_access_available %}", "native iOS core fallback", failures)
    require(account, "Paid digital access is not available in this iOS build.", "native iOS paid unavailable copy", failures)
    require(account, 'name="email" type="email" autocomplete="email" placeholder="Email address" required', "email-based signup", failures)
    require(account, "A phone number is not required. You can add one later in Account Settings", "optional phone signup copy", failures)
    signup_form = account[account.index('{% if page == "signup" %}'): account.index('{% elif page == "login" %}')]
    if 'name="phone"' in signup_form or 'name="sms_opt_in"' in signup_form:
        failures.append("initial signup must not collect phone or SMS consent")

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
        "INSERT INTO pulse_reports",
        "Paid digital access is not available in this iOS build",
        "ios_paid_digital_unavailable_response(api=True)",
        '"ios_core_only": True',
        '"paid_digital_access_available": not ios_native',
        '"stripe_customer_id": "",',
        '"stripe_subscription_id": "",',
        'payload.pop(key, None)',
        'def api_billing_confirm_session():',
        'def api_payments_list_purchases():',
        'def api_payments_entitlements():',
        'context.setdefault("paid_digital_access_available", paid_digital_access_available)',
        "def pulse_premium_page():",
        'phone = ""',
        'sms_opt_in = False',
        'error="Enter your email address to create your account."',
    ]:
        require(bot, token, "bot.py", failures)
    billing_portal_marker = "def api_premium_billing_portal():"
    if billing_portal_marker not in bot:
        failures.append("missing route function api_premium_billing_portal")
    else:
        billing_portal_segment = bot[bot.index(billing_portal_marker): bot.index(billing_portal_marker) + 750]
        if "ios_native_app_request()" not in billing_portal_segment:
            failures.append("api_premium_billing_portal must detect native iOS")
        if "return ios_paid_digital_unavailable_response(api=True)" not in billing_portal_segment:
            failures.append("api_premium_billing_portal must use shared native iOS paid digital block")
    status_marker = "def subscription_status_payload(user):"
    if status_marker not in bot:
        failures.append("missing subscription_status_payload")
    else:
        status_segment = bot[bot.index(status_marker): bot.index(status_marker) + 5200]
        if "if ios_native:" not in status_segment or "payload.pop(key, None)" not in status_segment:
            failures.append("native iOS subscription status must strip provider and Stripe identifier keys")
        for token in [
            '"provider_connected",',
            '"provider_subscription_connected",',
            '"stripe_customer_id",',
            '"stripe_subscription_id",',
        ]:
            require(status_segment, token, "native iOS subscription status sanitizer", failures)
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

    # The blocking write moved out of bot.py into the shared social-graph
    # service, which writes `blocked_users` and `comm_v2_blocks` together - this
    # audit was still pinning the bare `INSERT INTO blocked_users` literal that
    # the route used to carry, and had been failing ever since.
    social_graph = read("services/pulse_social_graph_service.py")
    require(social_graph, "INSERT INTO blocked_users", "social graph block write", failures)

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

    if failures:
        print("PulseSoc App Store review fix audit FAILED")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("pulsesoc app store review fix audit ok")


if __name__ == "__main__":
    main()
