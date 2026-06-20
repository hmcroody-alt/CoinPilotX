#!/usr/bin/env python3
"""Runtime audit for PulseSoc two-user message delivery wiring.

This uses Flask test clients with separate sessions so it can verify the
delivery path without requiring external browsers, APNs, FCM, or Expo tokens.
It writes a small local QA message to the configured local database.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot


API = "/api/pulse/communications/v2"


class PushTraceCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        if "PUSH_TRACE stage=" in message:
            self.lines.append(message)


def _client_for(user_id: int):
    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = int(user_id)
        session["user_id"] = int(user_id)
    return client


def _ok(name: str, response) -> dict:
    data = response.get_json(silent=True) or {}
    if response.status_code >= 400 or data.get("ok") is False:
        raise AssertionError(f"{name} failed status={response.status_code} payload={json.dumps(data, default=str)[:1000]}")
    return data


def _conversation_id(opened: dict, client) -> int:
    direct_id = int(
        opened.get("conversation_id")
        or (opened.get("conversation") or {}).get("conversation_id")
        or (opened.get("conversation") or {}).get("id")
        or 0
    )
    if direct_id:
        return direct_id
    listed = _ok("list_conversations", client.get(f"{API}/conversations"))
    for item in listed.get("conversations") or []:
        if item.get("conversation_type") == "direct":
            return int(item.get("conversation_id") or item.get("id") or 0)
    raise AssertionError("No direct conversation available for two-user audit")


def main() -> int:
    user_a = 1
    user_b = 2
    client_a = _client_for(user_a)
    client_b = _client_for(user_b)

    capture = PushTraceCapture()
    capture.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(capture)
    try:
        opened = _ok("open_direct", client_a.post(f"{API}/direct/open", json={"target_user_id": user_b}))
        conversation_id = _conversation_id(opened, client_a)
        body = f"Two-user delivery audit {uuid.uuid4().hex[:10]}"
        sent = _ok(
            "send_message",
            client_a.post(
                f"{API}/conversations/{conversation_id}/messages",
                json={"body": body, "client_message_id": f"qa-{uuid.uuid4().hex}"},
            ),
        )
        message = sent.get("message") or {}
        message_id = int(message.get("message_id") or message.get("id") or sent.get("message_id") or 0)
        if not message_id:
            raise AssertionError("Send response did not include message_id")

        recipient_messages = _ok("recipient_messages", client_b.get(f"{API}/conversations/{conversation_id}/messages"))
        bodies = [item.get("body") or item.get("text") for item in recipient_messages.get("messages") or []]
        if body not in bodies:
            raise AssertionError("Recipient did not load the sent message")

        recipient_realtime = _ok("recipient_realtime", client_b.get(f"{API}/realtime?since=0&limit=80"))
        recipient_types = [item.get("type") or item.get("event_type") for item in recipient_realtime.get("events") or []]
        if "message_created" not in recipient_types and "message_notification" not in recipient_types:
            raise AssertionError(f"Recipient realtime missing message event: {recipient_types}")

        _ok("typing_start", client_b.post(f"{API}/conversations/{conversation_id}/typing", json={"is_typing": True}))
        sender_typing = _ok("sender_realtime_after_typing", client_a.get(f"{API}/realtime?since=0&limit=80"))
        sender_typing_types = [item.get("type") or item.get("event_type") for item in sender_typing.get("events") or []]
        if "typing_started" not in sender_typing_types:
            raise AssertionError(f"Sender realtime missing typing event: {sender_typing_types}")

        _ok("recipient_read", client_b.post(f"{API}/conversations/{conversation_id}/read", json={}))
        sender_read = _ok("sender_realtime_after_read", client_a.get(f"{API}/realtime?since=0&limit=80"))
        sender_read_types = [item.get("type") or item.get("event_type") for item in sender_read.get("events") or []]
        if "message_read" not in sender_read_types:
            raise AssertionError(f"Sender realtime missing read event: {sender_read_types}")

        _ok("reaction", client_b.post(f"{API}/messages/{message_id}/reactions", json={"reaction": "spark"}))
    finally:
        logging.getLogger().removeHandler(capture)

    side_effects = [line for line in capture.lines if "stage=message_side_effects_start" in line]
    policies = [line for line in capture.lines if "stage=recipient_policy" in line]
    push_starts = [line for line in capture.lines if "stage=send_push_start" in line]
    if not side_effects or '"recipient_count": 1' not in side_effects[-1]:
        raise AssertionError(f"Expected deduplicated recipient_count=1, got {side_effects[-1:]}")
    if len(policies) != 1:
        raise AssertionError(f"Expected one recipient policy evaluation, got {len(policies)}")
    if len(push_starts) > 1:
        raise AssertionError(f"Expected at most one push provider attempt, got {len(push_starts)}")

    print(json.dumps({
        "ok": True,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "recipient_realtime_types": recipient_types[:8],
        "sender_typing_types": sender_typing_types[:8],
        "sender_read_types": sender_read_types[:8],
        "recipient_policy_count": len(policies),
        "send_push_start_count": len(push_starts),
    }, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"pulse_messages_two_user_delivery_audit: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
