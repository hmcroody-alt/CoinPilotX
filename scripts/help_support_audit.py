#!/usr/bin/env python3
"""Audit the PulseSoc Help Center wiring without printing secrets."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")


def main():
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    app_template = (ROOT / "templates" / "app.html").read_text(encoding="utf-8")
    support_template = (ROOT / "templates" / "support.html").read_text(encoding="utf-8")
    router = (ROOT / "services" / "command_router.py").read_text(encoding="utf-8")

    require('@webhook_app.route("/help"' in bot, "/help route is missing")
    require('@webhook_app.route("/pulse/help"' in bot, "/pulse/help route is missing")
    require('@webhook_app.route("/support"' in bot, "/support route is missing")
    require("INSERT INTO support_tickets" in bot, "support ticket persistence is missing")
    require("support_ticket_created" in bot, "support ticket audit/product event is missing")
    require('href="/help"' in app_template, "app Help shortcut is not a real link")
    require('data-command="/help">Help' not in app_template, "dead /help command button still present")
    require("PulseSoc Help Center" in support_template, "Help Center heading is missing")
    require('method="post"' in support_template and 'name="message"' in support_template, "ticket form is missing")
    require('name="csrf_token"' in support_template and "verify_csrf()" in bot, "support ticket form is missing CSRF protection")
    for href in [
        "/pulse/settings/notifications",
        "/pulse/messages",
        "/security",
        "/pulse/settings/privacy",
        "/crypto-scam-scanner",
        "/terms",
        "/privacy",
    ]:
        require(href in support_template, f"expected help destination missing: {href}")
    require('"/help": "help"' in router, "command router /help alias is missing")
    require("Open /help for account support" in router, "command router help response is not useful")
    forbidden = ["COMMAND_CENTER_INTERNAL_TOKEN", "DATABASE_URL", "PRIVATE_KEY", "SECRET_KEY"]
    rendered_copy = support_template + router
    for token in forbidden:
        require(token not in rendered_copy, f"Help surface references sensitive config name: {token}")

    print("PASS: Help/support routes, command shortcut, ticket form, and safe destinations are wired.")


if __name__ == "__main__":
    main()
