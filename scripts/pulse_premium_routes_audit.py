#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bot  # noqa: E402

def expect(ok, label, detail=""):
    if not ok:
        raise AssertionError(f"{label}: {detail}")
    print(f"ok - {label}")

def main():
    bot.init_db()
    original_portal = bot.create_subscription_billing_portal_session
    bot.create_subscription_billing_portal_session = lambda user: (None, "Audit billing portal unavailable")
    client = bot.webhook_app.test_client()
    try:
        with client.session_transaction() as sess:
            sess["account_user_id"] = 1
        for route in ["/pulse/premium", "/pulse/premium/success?session_id=cs_audit", "/pulse/premium/cancel", "/billing/portal", "/pulse/premium/intelligence", "/pulse/premium/intelligence/portfolio", "/pulse/premium/portfolio", "/pulse/portfolio", "/pulse/saved", "/pulse/creator/dashboard", "/pulse/settings/security", "/pulse/messages", "/pulse/videos", "/pulse/roast-battle"]:
            res = client.get(route)
            expect(res.status_code in {200, 302, 403}, f"{route} resolves cleanly", str(res.status_code))
        status = client.get("/api/premium/status")
        expect(status.status_code in {200, 401}, "/api/premium/status resolves cleanly", str(status.status_code))
    finally:
        bot.create_subscription_billing_portal_session = original_portal
    print("pulse premium routes audit ok")

if __name__ == "__main__":
    main()
