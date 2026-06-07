#!/usr/bin/env python3
"""Verify native Expo push token storage and delivery routing safely."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp = tempfile.NamedTemporaryFile(prefix="pulse-native-push-", suffix=".db", delete=False)
tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{tmp.name}"

from services import push_service  # noqa: E402
from services import user_context  # noqa: E402


def expect(condition, message, failures):
    if not condition:
        failures.append(message)


class FakeResponse:
    ok = True
    content = b'{"data":{"status":"ok"}}'

    @staticmethod
    def json():
        return {"data": {"status": "ok"}}


def main():
    failures = []
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            endpoint TEXT UNIQUE,
            subscription_json TEXT,
            p256dh TEXT,
            auth TEXT,
            user_agent TEXT,
            device_type TEXT,
            browser TEXT,
            active INTEGER DEFAULT 1,
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT,
            last_seen_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()

    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse()

    push_service.requests.post = fake_post
    token = "ExponentPushToken[audit-token]"
    saved = push_service.save_subscription(
        101,
        {
            "endpoint": token,
            "provider": "expo",
            "token": token,
            "subscription": {"expo_push_token": token},
            "device_type": "native",
        },
        user_agent="PulseSoc iOS QA",
        device_type="native",
        browser="Expo",
    )
    expect(saved.get("ok"), "native Expo token should save as a push subscription", failures)

    result = push_service.send_push(
        101,
        "PulseSoc QA",
        "Native push delivery audit.",
        {"url": "pulse://pulse/notifications"},
        push_type="mobile_qa",
    )
    expect(result.get("ok"), "native Expo push should send through Expo provider", failures)
    expect(result.get("status") == "sent", "native Expo push status should be sent", failures)
    expect(len(calls) == 1, "Expo push endpoint should be called once", failures)
    if calls:
        payload = calls[0]["json"] or {}
        expect(calls[0]["url"] == "https://exp.host/--/api/v2/push/send", "Expo push URL should be used", failures)
        expect(payload.get("to") == token, "Expo push token should be used as recipient", failures)
        expect(payload.get("sound") == "default", "native test push should request default sound", failures)
        expect(payload.get("priority") == "high", "native test push should request high priority", failures)
        expect(payload.get("channelId") == "default", "native test push should use default channel", failures)
        expect((payload.get("data") or {}).get("url") == "pulse://pulse/notifications", "deep link should be included", failures)

    Path(tmp.name).unlink(missing_ok=True)
    if failures:
        raise SystemExit("Pulse native push delivery audit failed:\n- " + "\n- ".join(failures))
    print("Pulse native push delivery audit ok")


if __name__ == "__main__":
    main()
