#!/usr/bin/env python3
"""Safe Stripe webhook recovery audit.

The audit never prints live Stripe keys or webhook secrets. It verifies route
coverage, raw-body signature handling with a throwaway local secret, idempotency
markers, and optional public endpoint health responses.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


LOCAL_SECRET = "whsec_local_recovery_audit_only"
REQUIRED_ROUTES = {
    "/stripe/webhook",
    "/api/stripe/webhook",
    "/stripe-webhook",
    "/webhook/stripe",
    "/webhooks/stripe",
}
REQUIRED_EVENTS = {
    "checkout.session.completed",
    "invoice.paid",
    "invoice.payment_failed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "payment_intent.succeeded",
    "payment_intent.payment_failed",
    "charge.refunded",
    "payout.paid",
    "payout.failed",
}


def sign(payload: bytes, secret: str = LOCAL_SECRET) -> str:
    timestamp = str(int(time.time()))
    signed_payload = f"{timestamp}.".encode("utf-8") + payload
    digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def public_probe(url: str) -> tuple[int, str]:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "PulseSocWebhookAudit/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            return int(response.status), response.read(400).decode("utf-8", "replace")
    except Exception as exc:
        return 0, exc.__class__.__name__


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="append", default=[], help="Optional public webhook URL to health-check with GET.")
    args = parser.parse_args()

    os.environ["STRIPE_WEBHOOK_SECRET"] = LOCAL_SECRET
    os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_local_audit_only")

    import bot  # noqa: E402

    failures: list[str] = []
    warnings: list[str] = []

    bot.init_db()
    routes = {str(rule.rule) for rule in bot.webhook_app.url_map.iter_rules()}
    for route in REQUIRED_ROUTES:
        if route not in routes:
            failures.append(f"missing webhook route: {route}")

    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    for event_type in REQUIRED_EVENTS:
        if event_type not in source:
            failures.append(f"missing event handler token: {event_type}")
    for token in ["request.data", "stripe.Webhook.construct_event", "record_webhook_event", "stripe_event_processed"]:
        if token not in source:
            failures.append(f"missing raw/idempotency token: {token}")

    payload = json.dumps(
        {
            "id": f"evt_recovery_audit_{int(time.time())}",
            "object": "event",
            "type": "payment_intent.payment_failed",
            "livemode": False,
            "data": {
                "object": {
                    "id": "pi_recovery_audit",
                    "object": "payment_intent",
                    "amount": 499,
                    "currency": "usd",
                    "customer": "cus_recovery_audit",
                    "metadata": {"audit": "true"},
                }
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    client = bot.webhook_app.test_client()
    response = client.post("/api/stripe/webhook", data=payload, headers={"Stripe-Signature": sign(payload), "Content-Type": "application/json"})
    if response.status_code < 200 or response.status_code >= 300:
        failures.append(f"valid signed local webhook returned HTTP {response.status_code}")

    invalid = client.post("/api/stripe/webhook", data=payload, headers={"Stripe-Signature": "t=1,v1=bad", "Content-Type": "application/json"})
    if invalid.status_code < 400:
        failures.append(f"invalid signature returned HTTP {invalid.status_code}")

    for url in args.probe:
        status, body = public_probe(url)
        if status < 200 or status >= 300:
            warnings.append(f"public probe non-2xx: {url} -> {status or body}")
        else:
            print(f"ok - public health probe {url} -> {status}")

    print("STRIPE WEBHOOK RECOVERY AUDIT")
    print(f"routes checked: {len(REQUIRED_ROUTES)}")
    print(f"events checked: {len(REQUIRED_EVENTS)}")
    print(f"local signed fixture status: {response.status_code}")
    print(f"invalid signature fixture status: {invalid.status_code}")
    print(f"warnings: {len(warnings)}")
    for warning in warnings:
        print(f"WARN {warning}")
    print(f"failures: {len(failures)}")
    for failure in failures:
        print(f"FAIL {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
