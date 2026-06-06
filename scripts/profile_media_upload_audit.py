#!/usr/bin/env python3
"""Audit Pulse profile media upload validation and durable storage checks."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
MEDIA_SERVICE = ROOT / "services" / "media_service.py"


def require(condition, message, failures):
    if not condition:
        failures.append(message)


def main():
    bot = BOT.read_text(encoding="utf-8")
    media = MEDIA_SERVICE.read_text(encoding="utf-8")
    failures = []

    for token in [
        'ext not in {"jpg", "jpeg", "png", "webp"}',
        "not file_storage.mimetype.lower().startswith(\"image/\")",
        'context_type="pulse_avatar"',
        'context_type="pulse_cover"',
        "_profile_media_is_cdn_safe",
        "durable CDN media",
        "Please upload again.",
    ]:
        require(token in bot, f"profile upload protection missing: {token}", failures)

    for token in [
        "MEDIA_UPLOAD_MAX_IMAGE_MB",
        "_media_header_ok",
        "PULSE_MEDIA_UPLOAD_HEADER_REJECTED",
        "ALLOWED_TYPES",
        "save_upload",
    ]:
        require(token in media, f"shared media upload guard missing: {token}", failures)

    blocked_extensions = {".exe", ".sh", ".js", ".php", ".bat"}
    allowed_profile_extensions = {"jpg", "jpeg", "png", "webp"}
    require(not any(ext.lstrip(".") in allowed_profile_extensions for ext in blocked_extensions), "blocked executable extension is allowed", failures)

    if failures:
        print("Profile media upload audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Profile media upload audit passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
