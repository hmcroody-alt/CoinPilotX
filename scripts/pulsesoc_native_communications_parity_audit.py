#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    path = ROOT / rel
    if not path.exists():
        raise AssertionError(f"missing required file: {rel}")
    return path.read_text(encoding="utf-8", errors="replace")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def require_all(source: str, needles: list[str], label: str, failures: list[str]) -> None:
    for needle in needles:
        require(needle in source, f"{label} missing `{needle}`", failures)


def main() -> int:
    failures: list[str] = []

    report = read("reports/pulsesoc_native_communications_parity_migration.md")
    v2_routes = read("pulse_communications_v2/routes.py")
    bot = read("bot.py")
    native_messenger = read("mobile-native/src/api/messenger.ts")
    native_calls = read("mobile-native/src/api/calls.ts")
    native_call_room = read("mobile-native/src/calls/useNativeCallRoom.ts")
    incoming_layer = read("mobile-native/src/calls/IncomingCallLayer.tsx")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")

    require_all(
        v2_routes,
        [
            "@comm_v2_blueprint.get(f\"{API_PREFIX}/conversations\")",
            "@comm_v2_blueprint.post(f\"{API_PREFIX}/conversations/<path:conversation_ref>/messages\")",
            "@comm_v2_blueprint.get(f\"{API_PREFIX}/realtime\")",
            "@comm_v2_blueprint.get(f\"{API_PREFIX}/realtime/stream\")",
            "@comm_v2_blueprint.post(f\"{API_PREFIX}/attachments/upload\")",
            "@comm_v2_blueprint.post(f\"{API_PREFIX}/conversations/<path:conversation_ref>/voice/start\")",
            "@comm_v2_blueprint.post(f\"{API_PREFIX}/conversations/<path:conversation_ref>/video/start\")",
            "@comm_v2_blueprint.post(\"/api/calls/start\")",
            "@comm_v2_blueprint.post(\"/api/calls/<path:call_id>/join-token\")",
            "@comm_v2_blueprint.get(\"/api/calls/active\")",
        ],
        "comm v2 route contract",
        failures,
    )

    require_all(
        bot,
        [
            '@webhook_app.route("/api/pulse/messages/conversations", methods=["GET"])',
            '@webhook_app.route("/api/pulse/messages/<int:conversation_id>/messages", methods=["GET"])',
            '@webhook_app.route("/api/pulse/messages/<int:conversation_id>/send", methods=["POST"])',
            '@webhook_app.route("/api/pulse/messages/<int:conversation_id>/sync", methods=["GET", "POST"])',
            '@webhook_app.route("/api/pulse/messages/<int:conversation_id>/seen", methods=["POST"])',
            '@webhook_app.route("/api/pulse/messages/<int:conversation_id>/typing", methods=["POST"])',
            '@webhook_app.route("/api/pulse/messages/<int:message_id>/react", methods=["POST"])',
            '@webhook_app.route("/api/pulse/messages/<int:message_id>/delete", methods=["POST", "DELETE"])',
            '@webhook_app.route("/api/pulse/messages/<int:message_id>/report", methods=["POST"])',
            '@webhook_app.route("/api/pulse/messages/media/upload", methods=["POST"])',
        ],
        "legacy production messaging route contract",
        failures,
    )

    require_all(
        native_messenger,
        [
            'pulseApi<ConversationListResponse>("/api/pulse/messages/conversations")',
            "pulseApi<ConversationResponse>(`/api/pulse/messages/${conversationId}/messages${suffix}`)",
            "pulseApi<ConversationResponse>(`/api/pulse/messages/${conversationId}/sync?after_id=${afterId}&limit=80`)",
            "pulseApi<{ ok: boolean; message?: string; data?: MessengerMessage; message_id?: number }>(`/api/pulse/messages/${conversationId}/send`",
            "client_message_id",
            "pulseApi<{ ok: boolean; last_read_message_id?: number }>(`/api/pulse/messages/${conversationId}/seen`",
            "pulseApi<{ ok: boolean; typing: boolean }>(`/api/pulse/messages/${conversationId}/typing`",
            "pulseApi<MediaUploadResult>(\"/api/pulse/messages/media/upload\"",
            "normalizeMessages",
            "message_id: id",
            "conversation_id: id",
        ],
        "native messenger API reuse",
        failures,
    )

    require_all(
        native_calls,
        [
            "/api/pulse/comm/v2/conversations/${encodeURIComponent(String(conversationId))}/${callType === \"video\" ? \"video\" : \"voice\"}/start",
            'pulseApi<PulseCall>("/api/calls/start"',
            "`/api/calls/${encodeURIComponent(callId)}/accept`",
            "`/api/calls/${encodeURIComponent(callId)}/decline`",
            "`/api/calls/${encodeURIComponent(callId)}/end`",
            "`/api/calls/${encodeURIComponent(callId)}/join-token`",
            "`/api/calls/${encodeURIComponent(callId)}/status`",
            'pulseApi<ActiveCallsResponse>("/api/calls/active")',
            "`/api/calls/${encodeURIComponent(callId)}/${action}`",
            "normalizeCall",
            "call_id: id",
        ],
        "native call API reuse",
        failures,
    )

    require_all(
        native_call_room,
        [
            'await import("@livekit/react-native")',
            'await import("livekit-client")',
            "registerGlobals({ autoConfigureAudioSession: true })",
            "room.connect(join.livekit_url, join.token",
            "setMicrophoneEnabled",
            "setCameraEnabled",
            "switchCamera",
        ],
        "native LiveKit device adapter",
        failures,
    )

    require("Voice in progress" not in incoming_layer and "Video in progress" not in incoming_layer, "active-call mini popup copy must be removed globally", failures)
    require("callBubbleMain" not in incoming_layer, "active-call mini popup main Pressable must not mount", failures)
    require("callBubbleEnd" not in incoming_layer, "active-call mini popup End button must not mount", failures)
    require("showFloatingCall" not in incoming_layer, "route-specific mini popup visibility policy should not remain", failures)
    require_all(
        incoming_layer,
        [
            "setFloatingCall(connected || null)",
            'navigationRef.navigate("Call"',
        ],
        "active call state and canonical call route",
        failures,
    )

    require_all(
        routing,
        [
            'navigationRef.navigate("Call"',
            'navigationRef.navigate("Chat"',
            'navigationRef.navigate("Tabs", { screen: "Messenger" })',
        ],
        "notification/deep-link routing",
        failures,
    )

    require_all(
        report,
        [
            "Implementation Matrix",
            "Data Model Changes",
            "None in this gate",
            "Safe to replace WebView messaging: NO",
            "Safe to replace WebView calls: NO",
            "No native-only conversation, room, call, presence, push, or attachment backend was introduced.",
            "The mission explicitly requires the production communication implementation matrix before deeper native implementation.",
        ],
        "migration report",
        failures,
    )

    if failures:
        print("PulseSoc native communications parity audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc native communications parity audit passed.")
    print("Validated mapped production messaging/call contracts, native API reuse, data-safety gate, and global call-popup removal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
