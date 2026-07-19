#!/usr/bin/env python3
"""Audit the native Messenger voice message bubble cleanup."""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
CHAT = ROOT / "mobile-native" / "src" / "screens" / "ChatScreen.tsx"
API = ROOT / "mobile-native" / "src" / "api" / "messenger.ts"
DOMAIN = ROOT / "mobile-native" / "src" / "pulseCommand" / "domain.ts"
REPORT = ROOT / "reports" / "pulsesoc_voice_message_bubble_fix.md"


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    chat = CHAT.read_text()
    api = API.read_text()
    domain = DOMAIN.read_text()
    failures: list[str] = []

    require('body: input.voice ? "" : input.name' in chat, "voice upload must not send generated filename as message body", failures)
    require("function displayMessageBody" in chat and "if (isVoiceLikeMessage(message)) return \"\";" in chat, "voice/audio message body must be suppressed in chat bubble", failures)
    require("minWidth: 224" in chat and "paddingVertical: 9" in chat, "voice card should use compact dimensions", failures)
    require("Array.from({ length: 18 })" in chat, "voice waveform should use compact bar count", failures)
    require("body: input.name" not in chat, "old filename body assignment must be removed", failures)

    generated_pattern = r"pulsesoc\[-_ \]voice\[-_\]\\d\+\\\.\(m4a\|mp4\|aac\|mp3\|wav\|webm\|ogg\)"
    require(re.search(generated_pattern, api) is not None, "API normalization must detect generated voice filenames", failures)
    require("normalizeMessengerBody" in api and "normalizeMessengerPreview" in api, "API must normalize voice bodies and previews", failures)
    require("messageType = safeText(item.message_type)" in api, "message type must be known before body normalization", failures)

    voice_preview_index = domain.find('return "Voice message";')
    body_preview_index = domain.find("if (message.body) return message.body;")
    require(voice_preview_index != -1 and body_preview_index != -1 and voice_preview_index < body_preview_index, "voice previews must resolve before raw body text", failures)
    require(REPORT.exists(), "voice message bubble report must exist", failures)

    if failures:
        print("PulseSoc voice message bubble audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc voice message bubble audit passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
