#!/usr/bin/env python3
"""Audit the native UNDX chat conversion.

This keeps the native UNDX surface aligned with production Messenger behavior:
one canonical assistant conversation, normal ChatScreen presentation, and no
standalone command-form UI.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    pulse_ai_screen = read("mobile-native/src/screens/PulseAiScreen.tsx")
    chat_screen = read("mobile-native/src/screens/ChatScreen.tsx")
    messenger_screen = read("mobile-native/src/screens/MessengerScreen.tsx")
    messenger_api = read("mobile-native/src/api/messenger.ts")
    control_center = read("mobile-native/src/components/ConversationControlCenter.tsx")
    app_navigator = read("mobile-native/src/navigation/AppNavigator.tsx")

    forbidden_command_form = [
        "Ask UNDX",
        "Digital Intelligence Companion",
        "PulseCommandHeader",
        "askPulseAi",
        "separate `Ask`",
    ]
    for text in forbidden_command_form:
        require(text not in pulse_ai_screen, f"PulseAiScreen still contains command-form text/import: {text}", failures)

    require("PULSE_AI_CONVERSATION_ID = -9001001" in messenger_api, "Canonical Pulse AI conversation id is missing.", failures)
    require('PULSE_AI_DISPLAY_NAME = "UNDX"' in messenger_api, "Canonical UNDX display name is missing.", failures)
    require("/api/pulse-ai/conversation" in messenger_api, "Native API does not load production /api/pulse-ai/conversation.", failures)
    require("/api/pulse-ai/message" in messenger_api, "Native API does not send through production /api/pulse-ai/message.", failures)
    require("item.id > 0 || item.id === PULSE_AI_CONVERSATION_ID" in messenger_api, "Conversation normalization still rejects the negative canonical assistant id.", failures)

    require("-900001" not in messenger_screen + chat_screen + messenger_api, "Old fake UNDX conversation id -900001 is still present.", failures)
    require('navigation.navigate("Chat", { conversationId: PULSE_AI_CONVERSATION_ID' in messenger_screen, "Messenger row does not open canonical UNDX in ChatScreen.", failures)
    require('navigation.navigate("Tabs", { screen: "PulseAI" })' not in messenger_screen, "Messenger still routes UNDX row to the old PulseAI tab surface.", failures)

    require("assistantConversation = conversationId === PULSE_AI_CONVERSATION_ID" in chat_screen, "ChatScreen does not detect the canonical assistant conversation.", failures)
    require("getPulseAiConversation" in chat_screen, "ChatScreen does not load UNDX conversation history.", failures)
    require("sendPulseAiMessage" in chat_screen, "ChatScreen does not send UNDX messages through the assistant adapter.", failures)
    require("Message UNDX…" in chat_screen, "UNDX composer placeholder is missing.", failures)
    require("!assistantConversation ? <SignalIconButton accessibilityLabel=\"Start audio call\"" in chat_screen, "UNDX audio call button is not hidden in ChatScreen.", failures)
    require("!assistantConversation ? <SignalIconButton accessibilityLabel=\"Start video call\"" in chat_screen, "UNDX video call button is not hidden in ChatScreen.", failures)
    require("assistantConversation ? \"UNDX · READY\"" in chat_screen, "UNDX composer state does not identify assistant readiness.", failures)
    require("UNDX supports text conversation in native chat right now" in chat_screen, "UNDX attachment/voice text-first boundary is missing.", failures)

    require("assistantConversation?: boolean" in control_center, "ConversationControlCenter lacks assistant mode prop.", failures)
    require("createAssistantControlData" in control_center, "ConversationControlCenter lacks assistant-only control profile.", failures)
    require("voice_call: false" in control_center and "video_call: false" in control_center, "UNDX control profile does not disable calls.", failures)
    require("onStartCall?: (callType" in control_center, "ConversationControlCenter call handler is not optional.", failures)
    require("UNDX is pinned in Messenger" in control_center, "Assistant pin behavior is not safely handled.", failures)

    require("PulseSoc Intelligence" in app_navigator, "PulseAI tab subtitle was not normalized away from Companion branding.", failures)

    if failures:
        print("PulseSoc native UNDX chat conversation audit FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc native UNDX chat conversation audit passed")
    print("- canonical conversation id: -9001001")
    print("- production routes: /api/pulse-ai/conversation and /api/pulse-ai/message")
    print("- native surface: ChatScreen")
    print("- old command-form PulseAiScreen removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
