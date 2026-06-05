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
    client = bot.webhook_app.test_client()
    with client.session_transaction() as sess:
        sess["account_user_id"] = 1
    for route in ["/pulse/premium", "/pulse/premium/intelligence", "/pulse/premium/intelligence/portfolio", "/pulse/premium/portfolio", "/pulse/portfolio", "/pulse/saved", "/pulse/creator/dashboard", "/pulse/settings/security", "/pulse/messages", "/pulse/videos", "/pulse/roast-battle"]:
        res = client.get(route)
        expect(res.status_code in {200, 302, 403}, f"{route} resolves cleanly", str(res.status_code))
    print("pulse premium routes audit ok")

if __name__ == "__main__":
    main()
