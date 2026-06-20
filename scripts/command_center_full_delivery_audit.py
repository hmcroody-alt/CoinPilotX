#!/usr/bin/env python3
"""Audit PulseSoc full communication delivery wiring without external services."""

from __future__ import annotations

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def contains(path: str, *needles: str) -> None:
    text = read(path)
    for needle in needles:
        require(needle in text, f"{path} missing {needle!r}")


def main() -> int:
    routes = read("pulse_communications_v2/routes.py")
    service = read("pulse_communications_v2/service.py")
    realtime = read("services/realtime_engine.py")
    worker_realtime = read("services/command_center_worker/realtime_transport.py")
    client = read("static/js/pulse_messages_v2.js")
    global_rt = read("static/js/pulse_realtime.js")

    contains(
        "pulse_communications_v2/routes.py",
        "/realtime/stream",
        "text/event-stream",
        "X-Accel-Buffering",
    )
    contains(
        "services/realtime_engine.py",
        "Condition",
        "wait_events",
        "poll_events_for_channels",
        "_event_condition.notify_all()",
    )
    contains(
        "pulse_communications_v2/service.py",
        "client_temp_id",
        "sender_display_name",
        "sender_avatar",
        "delivery_state",
        "recipient_ids",
        "send_push_alert",
        "message_read",
        "typing_started",
        "Pulse Shield",
        "cc:user:",
        "cc:conversation:",
        "_chat_unread_count_for_user",
        "SELECT DISTINCT user_id FROM comm_v2_participants",
    )
    contains(
        "static/js/pulse_messages_v2.js",
        "connectRealtimeStream",
        "client_message_id",
        "client_temp_id",
        "_pending",
        "delivery_status: \"sending\"",
        "delivery_status: \"failed\"",
    )
    contains(
        "static/js/pulse_realtime.js",
        "scopedSseAllowed",
        "/api/pulse/communications/v2/realtime/stream",
        "command_center",
    )
    contains(
        "services/command_center_worker/realtime_transport.py",
        "cc:user:",
        "cc:conversation:",
    )

    require("PulseRealtime.connect();" not in client, "Messages must connect to the scoped stream URL, not the legacy default stream")
    require("state.seen = new Set" not in global_rt, "PulseRealtime must not reassign const state.seen")
    require(re.search(r"event:\s*pulse", routes), "SSE stream must emit pulse events")
    require("pulse_unread_count(int(recipient_id))" not in service, "Message realtime payload must not use Alerts unread count as chat unread count")
    require("notification_service.pulse_unread_count(int(user_id))" not in service, "Realtime poll response must not expose Alerts count as chat badge count")
    require("realtime:user:" in worker_realtime and "cc:user:" in worker_realtime, "Worker must keep legacy realtime and cc fanout topics")
    require(service.count("_dispatch_push_alert_async(") == 1, "Communications V2 must not send a second push outside create_pulse_notification")
    require(
        'realtime_engine.publish_event(f"comm_v2:user:{int(recipient_id)}", event_type, user_payload)' in service,
        "Typing/read events must fan out to participant user channels",
    )

    print("command_center_full_delivery_audit: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"command_center_full_delivery_audit: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
