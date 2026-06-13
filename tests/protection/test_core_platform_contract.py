#!/usr/bin/env python3
"""Protect critical PulseSoc route and security contracts."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
APP_TEMPLATE = (ROOT / "templates/app.html").read_text(encoding="utf-8")


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"ok - {label}")


def main() -> None:
    for route in [
        "/pulse",
        "/pulse/reels",
        "/pulse/videos",
        "/pulse/live",
        "/pulse/messages-v2",
        "/pulse/notifications",
        "/pulse/premium",
        "/api/pulse/reels/feed",
        "/api/pulse/videos",
        "/api/pulse/status/rail",
        "/api/stripe/webhook",
    ]:
        expect(route in BOT, f"critical route present: {route}")

    expect("stripe.Webhook.construct_event" in BOT, "Stripe webhook uses signature verification")
    expect("pulse_processed_stripe_events" in BOT or "stripe_event" in BOT.lower(), "Stripe event idempotency remains represented")
    expect("chat_unread_count" in BOT or "chat_unread_count" in APP_TEMPLATE, "chat unread count contract remains present")
    expect("alert_unread_count" in BOT or "alert_unread_count" in APP_TEMPLATE, "alert unread count contract remains present")
    expect("support@pulsesoc.com" in BOT or "support@pulsesoc.com" in APP_TEMPLATE, "PulseSoc support address remains present")
    expect("coinpilotx.app" in BOT.lower(), "legacy coinpilotx.app support remains for migration safety")
    print("core platform protection contract ok")


if __name__ == "__main__":
    main()
