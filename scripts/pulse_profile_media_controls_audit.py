#!/usr/bin/env python3
"""Audit the always-available Pulse profile picture and cover controls."""

from pathlib import Path

BOT = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")

for token in [
    "Change cover",
    "Change picture",
    "avatarPreviewButton",
    "coverPreview",
    "avatarInput.addEventListener('change',async",
    "coverInput.addEventListener('change',async",
    "Uploading profile picture...",
    "Uploading cover picture...",
    "/api/pulse/profile/avatar",
    "/api/pulse/profile/cover",
    "/api/pulse/profile/avatar/remove",
    "/api/pulse/profile/cover/remove",
    "Take Profile Picture",
    "Take Cover Picture",
]:
    assert token in BOT, f"missing profile media control: {token}"
    print(f"PASS: {token}")

assert "profile-file-input" in BOT and "accept='image/png,image/jpeg,image/webp'" in BOT
print("pulse profile media controls audit ok")
