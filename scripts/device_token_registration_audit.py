#!/usr/bin/env python3
"""Runtime audit for PulseSoc device token registration and cleanup."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import notification_service, push_service  # noqa: E402


USER_ID = 985501
EXPO_TOKEN = "ExpoPushToken[token-registration-audit]"
WEB_ENDPOINT = "https://updates.push.services.mozilla.com/wpush/v2/token-registration-audit"


def expect(condition: bool, message: str, details: str = "") -> None:
    if not condition:
        raise AssertionError(f"{message}{': ' + details if details else ''}")
    print(f"ok - {message}")


def setup_user() -> None:
    bot.init_db()
    conn = bot.db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (USER_ID,))
    if cur.fetchone():
        cur.execute("UPDATE users SET account_status='active', email=? WHERE user_id=?", ("token-audit@example.test", USER_ID))
    else:
        cur.execute(
            "INSERT INTO users (user_id, username, display_name, email, account_status, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, 'active', ?, 1)",
            (USER_ID, "token_audit", "Token Audit", "token-audit@example.test", bot.datetime.utcnow().isoformat(timespec="seconds")),
        )
    cur.execute("DELETE FROM push_subscriptions WHERE user_id=? OR endpoint IN (?, ?)", (USER_ID, EXPO_TOKEN, WEB_ENDPOINT))
    cur.execute("DELETE FROM user_device_tokens WHERE user_id=? OR push_token IN (?, ?)", (USER_ID, EXPO_TOKEN, WEB_ENDPOINT))
    cur.execute("DELETE FROM pulse_notification_devices WHERE user_id=? OR endpoint IN (?, ?)", (USER_ID, EXPO_TOKEN, WEB_ENDPOINT))
    conn.commit()
    conn.close()


def device_rows() -> list[dict]:
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT platform, device_id, push_provider, enabled, revoked_at FROM user_device_tokens WHERE user_id=? ORDER BY id",
        (USER_ID,),
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def main() -> int:
    setup_user()

    native = notification_service.save_pulse_device(
        USER_ID,
        {
            "endpoint": EXPO_TOKEN,
            "token": EXPO_TOKEN,
            "provider": "expo",
            "platform": "ios",
            "device_id": "token-audit-ios",
            "device_type": "native",
            "app_version": "1.0.0",
        },
        "PulseSoc iOS QA",
    )
    expect(native.get("ok") is True, "Expo token registration succeeds", str(native))
    rows = device_rows()
    expect(len(rows) == 1, "Expo token mirrors into user_device_tokens exactly once", str(rows))
    expect(rows[0].get("push_provider") == "expo" and int(rows[0].get("enabled") or 0) == 1, "Expo token is active and provider-classified", str(rows))

    repeat = notification_service.save_pulse_device(
        USER_ID,
        {
            "endpoint": EXPO_TOKEN,
            "token": EXPO_TOKEN,
            "provider": "expo",
            "platform": "ios",
            "device_id": "token-audit-ios",
            "device_type": "native",
            "app_version": "1.0.1",
        },
        "PulseSoc iOS QA refresh",
    )
    expect(repeat.get("ok") is True, "Expo token refresh succeeds", str(repeat))
    expect(len(device_rows()) == 1, "Token refresh updates existing device instead of duplicating")

    web = notification_service.save_pulse_device(
        USER_ID,
        {
            "endpoint": WEB_ENDPOINT,
            "keys": {"p256dh": "audit-p256dh", "auth": "audit-auth"},
            "provider": "web_push",
            "platform": "web",
            "device_id": "token-audit-web",
            "device_type": "desktop",
        },
        "Mozilla/5.0 Chrome",
    )
    expect(web.get("ok") is True, "Web push token registration succeeds", str(web))
    rows = device_rows()
    providers = sorted(row.get("push_provider") for row in rows)
    expect(providers == ["expo", "webpush"], "Device registry keeps native and PWA providers separate", str(rows))

    conn = bot.db()
    cur = conn.cursor()
    push_service._ensure_user_device_tokens(cur)
    cur.execute("SELECT push_token FROM user_device_tokens WHERE user_id=? AND push_token LIKE 'ExpoPushToken%' LIMIT 1", (USER_ID,))
    expect(cur.fetchone() is not None, "Token is stored for delivery eligibility")
    conn.close()

    unsub = notification_service.unsubscribe_push(USER_ID, EXPO_TOKEN)
    expect(unsub.get("ok") is True and int(unsub.get("updated") or 0) >= 1, "Endpoint unsubscribe revokes push subscription", str(unsub))
    rows = device_rows()
    expo_rows = [row for row in rows if row.get("push_provider") == "expo"]
    expect(expo_rows and int(expo_rows[0].get("enabled") or 0) == 0 and expo_rows[0].get("revoked_at"), "Endpoint unsubscribe disables mirrored device token", str(rows))

    print("device_token_registration_audit: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"device_token_registration_audit: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
