#!/usr/bin/env python3
"""Audit the inactive Pulse Communications 2.0 foundation."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from pulse_communications_v2 import flags, models, permissions, service  # noqa: E402


def expect(ok: bool, label: str, details: str = "") -> None:
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def main() -> None:
    bot.init_db()
    client = bot.app.test_client()

    response = client.get("/api/pulse/comm/v2/health")
    data = response.get_json(silent=True) or {}
    expect(response.status_code == 200, "v2 health route returns 200", str(data))
    expect(data == {"enabled": False, "status": "disabled"}, "v2 health route is disabled by default", str(data))
    expect(flags.PULSE_COMMUNICATIONS_V2_ENABLED is False, "v2 feature flag defaults false")

    table_names = set(models.table_names())
    expect(len(table_names) == 8, "v2 table contract count", str(table_names))
    expect(all(name.startswith("comm_v2_") for name in table_names), "v2 table names are prefixed", str(table_names))
    legacy_names = {"conversations", "conversation_members", "private_messages", "pulse_conversations", "pulse_messages"}
    expect(not table_names.intersection(legacy_names), "v2 table names avoid legacy collisions", str(table_names))

    expect(service.create_conversation(1).get("status") == "disabled", "create_conversation no-op disabled")
    expect(service.list_conversations(1).get("status") == "disabled", "list_conversations no-op disabled")
    expect(service.send_message(1, 1).get("status") == "disabled", "send_message no-op disabled")
    expect(service.list_messages(1, 1).get("status") == "disabled", "list_messages no-op disabled")
    expect(service.create_community(1).get("status") == "disabled", "create_community no-op disabled")
    expect(service.create_channel(1, 1).get("status") == "disabled", "create_channel no-op disabled")

    expect(not permissions.can_view_conversation(1, 1), "can_view_conversation closed while disabled")
    expect(not permissions.can_send_message(1, 1), "can_send_message closed while disabled")
    expect(not permissions.can_manage_community(1, 1), "can_manage_community closed while disabled")
    expect(not permissions.can_moderate_channel(1, 1), "can_moderate_channel closed while disabled")

    for route in ("/pulse", "/pulse/messages", "/dashboard"):
        page = client.get(route)
        text = page.get_data(as_text=True)
        expect(page.status_code in {200, 302}, f"{route} still loads", f"status={page.status_code}")
        expect("/api/pulse/comm/v2" not in text, f"{route} does not expose v2 navigation")
        expect("Pulse Communications 2.0" not in text, f"{route} production UI unchanged")

    print("pulse communications v2 foundation audit ok")


if __name__ == "__main__":
    main()
