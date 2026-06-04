#!/usr/bin/env python3
"""Audit the Communications V2 creation-action UI contract."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["PULSE_COMMUNICATIONS_V2_ENABLED"] = "true"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def expect(ok: bool, label: str) -> None:
    if not ok:
        raise AssertionError(f"{label} failed")
    print(f"ok - {label}")


def main() -> None:
    js = (ROOT / "static/js/pulse_messages_v2.js").read_text(encoding="utf-8")
    html = (ROOT / "templates/pulse_messages_v2.html").read_text(encoding="utf-8")

    expect("pulse_messages_v2.js?v=" in html, "V2 JavaScript is cache-versioned")
    expect("pulse_messages_v2.css?v=" in html, "V2 CSS is cache-versioned")
    expect('return openModal("new-room")' in js, "new room button opens its modal")
    expect("peopleCache: new Map()" in js, "search selections use the people cache")
    expect("addGroupMember(Number(person.dataset.personId" in js, "group search results select members")
    expect('"Opening chat..."' in js and "openDm(Number(person.dataset.personId" in js, "direct search results open chats")
    expect('"Creating room..."' in js and 'api("/rooms"' in js, "room creation action calls the room API")
    expect("data-modal-status" in html and "Pulse Communications V2 action failed" in js, "creation failures are visible")
    expect('event.key === "Escape"' in js and 'event.key !== "Enter"' in js, "modal keyboard controls are wired")

    client = bot.app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = 983101
    page = client.get("/pulse/messages-v2")
    body = page.get_data(as_text=True)
    expect(page.status_code == 200, "V2 page loads")
    expect("data-open-new-chat" in body and "data-open-new-group" in body and "data-open-new-room" in body, "all creation buttons render")
    expect(body.count("data-modal-status") == 3, "every creation modal renders action feedback")

    print("pulse communications v2 UI actions audit ok")


if __name__ == "__main__":
    main()
