#!/usr/bin/env python3
"""Contract audit for native PulseSoc voice-message capture and delivery."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"Missing {label}: {needle}")


def main() -> int:
    api = (ROOT / "mobile-native/src/api/messenger.ts").read_text(encoding="utf-8")
    chat = (ROOT / "mobile-native/src/screens/ChatScreen.tsx").read_text(encoding="utf-8")
    backend = (ROOT / "services/messenger_media_foundation.py").read_text(encoding="utf-8")

    for needle, label in (
        ('import { File } from "expo-file-system"', "native filesystem metadata"),
        ("resolveLocalMessengerFileSize(input.uri, input.sizeBytes)", "pre-init byte resolution"),
        ("size_bytes: sizeBytes", "declared byte contract"),
        ('"/api/messages/media/init"', "production init route"),
        ('"/api/messages/media/upload"', "production upload route"),
        ('"/api/messages/media/complete"', "production completion route"),
        ("attachment_ids: [attachmentId]", "durable attachment delivery"),
    ):
        require(api + chat, needle, label)

    for needle, label in (
        ("isMeteringEnabled: true", "native recording metering"),
        ("LIVE VOICE PULSE", "voice capture presentation"),
        ("Discard voice recording", "discard control"),
        ("Stop and send voice message", "explicit send control"),
        ("Voice message failed", "voice-specific error copy"),
        ("VOICE PULSE", "playback presentation"),
        ("Audio.RecordingOptionsPresets.HIGH_QUALITY", "native high-quality recorder"),
    ):
        require(chat, needle, label)

    for needle, label in (
        ('"audio/mp4": {"media_type": "voice"', "M4A voice support"),
        ('MessengerMediaError("invalid_size", "File size is required."', "server-authoritative size validation"),
        ("def init_upload", "production upload foundation"),
        ("def upload_file", "production private upload"),
        ("def complete_upload", "production completion"),
    ):
        require(backend, needle, label)

    if "uploadMessengerMediaLegacy(" in chat:
        raise AssertionError("Chat must not fall back to the legacy attachment uploader.")

    print("PulseSoc native voice-message audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
