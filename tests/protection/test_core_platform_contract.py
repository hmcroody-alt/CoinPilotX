#!/usr/bin/env python3
"""Protect critical PulseSoc route and security contracts."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
APP_TEMPLATE = (ROOT / "templates/app.html").read_text(encoding="utf-8")
WEBHOOK_VERIFIER = (ROOT / "services/stripe_webhook_verification.py").read_text(encoding="utf-8")


def _webhook_handler_body() -> str:
    """The source of bot.py's stripe_webhook() view, and nothing else.

    Scoping the security assertions to the handler itself is the point: a check
    that searches all of bot.py cannot tell the difference between "the webhook
    verifies signatures" and "the string appears somewhere in 117k lines".
    """
    marker = "\ndef stripe_webhook():"
    start = BOT.find(marker)
    if start == -1:
        return ""
    rest = BOT[start + len(marker):]
    end = rest.find("\ndef ")
    return rest if end == -1 else rest[:end]


def expect(condition: bool, label: str) -> None:
    # Counted so scripts/protection/run_protection_suite.py can prove this file
    # actually executed. A suite that exits 0 having checked nothing is the
    # failure mode the runner exists to catch.
    expect.calls = getattr(expect, "calls", 0) + 1
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

    # Signature verification lives in services/stripe_webhook_verification.py so
    # that one verifier can serve several Stripe event destinations (each of
    # which signs with its own secret). Checking bot.py for the literal
    # construct_event call would therefore report a false failure, and was never
    # a strong check anyway -- it would have passed on any stray occurrence of
    # that string anywhere in a 117k-line file, including a comment. So follow
    # the actual call path instead: the handler must delegate to the verifier,
    # must refuse to continue when verification fails, and the verifier must be
    # the thing that calls into Stripe's HMAC check.
    expect("stripe.Webhook.construct_event" in WEBHOOK_VERIFIER,
           "Stripe signature verification uses Stripe's own construct_event")
    handler = _webhook_handler_body()
    expect(bool(handler), "the Stripe webhook handler is present in bot.py")
    expect("stripe_webhook_verification.verify(" in handler,
           "Stripe webhook handler delegates to the shared signature verifier")
    verify_at = handler.index("stripe_webhook_verification.verify(")
    expect("400" in handler[verify_at:verify_at + 1200],
           "Stripe webhook handler rejects a payload that fails verification")
    # Nothing may act on the event before it has been verified. record_webhook_event
    # persisting first would mean an unsigned payload could reach the database.
    for forbidden in ("record_webhook_event", "enqueue_event", "record_stripe_event"):
        position = handler.find(forbidden)
        expect(position == -1 or position > verify_at,
               f"Stripe webhook verifies the signature before calling {forbidden}")
    expect("pulse_processed_stripe_events" in BOT or "stripe_event" in BOT.lower(), "Stripe event idempotency remains represented")
    expect("chat_unread_count" in BOT or "chat_unread_count" in APP_TEMPLATE, "chat unread count contract remains present")
    expect("alert_unread_count" in BOT or "alert_unread_count" in APP_TEMPLATE, "alert unread count contract remains present")
    expect("support@pulsesoc.com" in BOT or "support@pulsesoc.com" in APP_TEMPLATE, "PulseSoc support address remains present")
    expect("coinpilotx.app" in BOT.lower(), "legacy coinpilotx.app support remains for migration safety")
    print(f"PROTECTION_TESTS_RUN={getattr(expect, 'calls', 0)}")
    print("core platform protection contract ok")


if __name__ == "__main__":
    main()
