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
        "Premium purchases are not available in this iOS build",
        "External billing management is not available in this iOS build",
    ]:
        require(bot, token, "bot.py", failures)

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
        "iPhone-only",
        "physical-device screen recording",
    ]:
        require(app_store, token, "app store review notes", failures)

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
