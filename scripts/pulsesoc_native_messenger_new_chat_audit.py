#!/usr/bin/env python3
"""Controlled contract audit for native Messenger New Chat recovery."""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


OWNER_ID = 986201
PEER_ID = 986202


def require(condition: bool, label: str, details: object = "") -> None:
    if not condition:
        raise AssertionError(f"{label} failed: {details}")
    print(f"ok - {label}")


def client_for(user_id: int):
    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
    return client


def seed_controlled_users() -> None:
    bot.init_db()
    conn = bot.db()
    conn.row_factory = bot.sqlite3.Row
    cur = conn.cursor()
    bot.ensure_pulse_messenger_schema(cur, conn)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    users = [
        (OWNER_ID, "native_new_chat_owner", "Native New Chat Owner", "native-new-chat-owner@example.test"),
        (PEER_ID, "native_new_chat_peer", "Native New Chat Peer", "native-new-chat-peer@example.test"),
    ]
    for user_id, username, display_name, email in users:
        cur.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
        if cur.fetchone():
            cur.execute(
                "UPDATE users SET username=?, display_name=?, email=?, account_status='active' WHERE user_id=?",
                (username, display_name, email, user_id),
            )
        else:
            cur.execute(
                "INSERT INTO users (user_id, username, display_name, email, account_status, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, 'active', ?, 1)",
                (user_id, username, display_name, email, now),
            )
    conn.commit()
    conn.close()


def open_direct(user_id: int, target_user_id: int) -> tuple[int, dict]:
    response = client_for(user_id).post(
        "/api/pulse/messages/direct/open",
        data=json.dumps({"target_user_id": target_user_id}),
        content_type="application/json",
    )
    return response.status_code, response.get_json(silent=True) or {}


def main() -> int:
    seed_controlled_users()
    owner = client_for(OWNER_ID)

    search = owner.get("/api/pulse/users/search?q=native_new_chat_peer")
    search_data = search.get_json(silent=True) or {}
    users = search_data.get("users") or []
    require(search.status_code == 200 and search_data.get("ok") is True, "production user search contract", search_data)
    require(any(int(item.get("user_id") or 0) == PEER_ID for item in users), "controlled peer appears in search", users)
    require(all("email" not in item for item in users), "search never exposes email", users)
    require(all(not item.get("is_self") for item in users), "native-visible results exclude self", users)

    with ThreadPoolExecutor(max_workers=4) as pool:
        concurrent = list(pool.map(lambda _: open_direct(OWNER_ID, PEER_ID), range(4)))
    require(all(status == 200 and data.get("ok") for status, data in concurrent), "concurrent direct opens succeed", concurrent)
    conversation_ids = {int(data.get("conversation_id") or 0) for _, data in concurrent}
    require(len(conversation_ids) == 1 and next(iter(conversation_ids)) > 0, "concurrent opens return one canonical conversation", concurrent)
    conversation_id = next(iter(conversation_ids))

    reverse_status, reverse_data = open_direct(PEER_ID, OWNER_ID)
    require(reverse_status == 200 and int(reverse_data.get("conversation_id") or 0) == conversation_id, "reverse participant order reuses canonical conversation", reverse_data)

    payload = {"body": "Native New Chat idempotency proof", "client_message_id": "native-new-chat-audit-message"}
    first = owner.post(f"/api/pulse/messages/{conversation_id}/send", data=json.dumps(payload), content_type="application/json")
    second = owner.post(f"/api/pulse/messages/{conversation_id}/send", data=json.dumps(payload), content_type="application/json")
    first_data = first.get_json(silent=True) or {}
    second_data = second.get_json(silent=True) or {}
    require(first.status_code == 200 and second.status_code == 200, "repeated first-message send succeeds", [first_data, second_data])
    require(int(first_data.get("message_id") or 0) == int(second_data.get("message_id") or 0) > 0, "client_message_id returns one canonical message", [first_data, second_data])

    conn = bot.db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM pulse_message_threads WHERE user_one_id=? AND user_two_id=?", tuple(sorted((OWNER_ID, PEER_ID))))
    require(int(cur.fetchone()[0]) == 1, "storage contains one direct thread")
    cur.execute("SELECT COUNT(*) FROM pulse_conversation_participants WHERE conversation_id=?", (conversation_id,))
    require(int(cur.fetchone()[0]) == 2, "storage contains one membership per participant")
    cur.execute("SELECT COUNT(*) FROM pulse_messages WHERE conversation_id=? AND sender_user_id=? AND client_message_id=?", (conversation_id, OWNER_ID, payload["client_message_id"]))
    require(int(cur.fetchone()[0]) == 1, "storage contains one canonical first message")
    conn.close()

    messenger_api = (ROOT / "mobile-native/src/api/messenger.ts").read_text(encoding="utf-8")
    new_chat = (ROOT / "mobile-native/src/screens/NewChatScreen.tsx").read_text(encoding="utf-8")
    inbox = (ROOT / "mobile-native/src/screens/MessengerScreen.tsx").read_text(encoding="utf-8")
    for token in ("/api/pulse/users/search", "/api/pulse/messages/direct/open", "directConversationRequests", "upsertCachedConversation"):
        require(token in messenger_api, f"native API wiring includes {token}")
    for token in ("new-chat-search-input", "No people found", "requestReauthentication", "Message ${item.display_name}"):
        require(token in new_chat, f"New Chat state includes {token}")
    require(inbox.count('navigation.navigate("NewChat")') == 2, "both visible New Chat entries share one route")

    print("PulseSoc native Messenger New Chat audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
