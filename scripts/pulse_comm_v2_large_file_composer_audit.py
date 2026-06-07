#!/usr/bin/env python3
"""Guard Communications V2 large attachments and mobile composer ergonomics."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(f"{label} failed")
    print(f"ok - {label}")


def main() -> int:
    bot = read("bot.py")
    media_service = read("services/media_service.py")
    upload_progress = read("services/upload_progress_service.py")
    js = read("static/js/pulse_messages_v2.js")
    css = read("static/css/pulse_messages_v2.css")
    html = read("templates/pulse_messages_v2.html")
    comm_service = read("pulse_communications_v2/service.py")

    expect("/api/pulse/communications/v2/attachments/upload" in bot, "comm v2 upload route bypasses generic small POST cap")
    expect("PULSE_COMM_V2_MAX_REQUEST_MB" in bot and "COMM_V2_FILE_MAX_MB" in bot, "request cap is configurable for very large comm files")

    for token in ("COMM_V2_VIDEO_MAX_MB", "COMM_V2_FILE_MAX_MB", "COMM_V2_IMAGE_MAX_MB", "COMM_V2_AUDIO_MAX_MB"):
        expect(token in media_service, f"media service has {token}")
        expect(token in upload_progress, f"progress validator has {token}")
    expect('"1024"' in media_service and '"1024"' in upload_progress, "large file/video default is one gigabyte")

    expect("pulse-voice-note-" in media_service and "COMM_V2_VOICE_HEADER_ACCEPTED" in media_service, "recorded comm voice notes avoid false format rejection")
    expect('audio/mp4' in js and 'audio/webm;codecs=opus' in js, "voice recorder chooses mobile-friendly audio formats")

    expect("video: 1024 * 1024 * 1024" in js, "client video limit allows very large videos")
    expect("file: 1024 * 1024 * 1024" in js, "client file limit allows very large files")
    expect("You can send without typing a message" in js, "attachment-only send is explained in UI")
    expect("!body.trim() && !state.attachmentQueue.length && !hasVoice" in js, "empty send guard permits attachments or voice without text")
    expect("if not body and not media_ids" in comm_service, "backend permits media-only messages")

    expect("Communications V2 mobile composer polish" in css, "mobile composer polish block exists")
    expect("grid-template-columns: 48px minmax(0, 1fr) 48px 56px" in css, "mobile composer uses easy four-control row")
    expect("bottom: calc(82px + env(safe-area-inset-bottom))" in css, "mobile error/status stays above composer")
    expect("grid-template-columns: repeat(3, minmax(0, 1fr))" in css, "mobile attachment sheet is tap-friendly")
    expect("data-attachment-option=\"camera\"" in html and "data-voice-start" in html, "camera and voice controls are wired")

    print("PASS pulse_comm_v2_large_file_composer_audit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
