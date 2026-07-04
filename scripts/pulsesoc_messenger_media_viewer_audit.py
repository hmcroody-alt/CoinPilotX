#!/usr/bin/env python3
"""Static audit for the PulseSoc Messenger full-screen media viewer."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    target = ROOT / path
    return target.read_text(encoding="utf-8") if target.exists() else ""


def exists(path: str) -> bool:
    return (ROOT / path).exists()


def check(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def main() -> int:
    viewer_js = read("static/js/pulse_messenger_media_viewer.js")
    viewer_css = read("static/css/pulse_messenger_media_viewer.css")
    messages_js = read("static/js/pulse_messages_v2.js")
    template = read("templates/pulse_messages_v2.html")
    routes = read("pulse_communications_v2/routes.py")
    service = read("pulse_communications_v2/service.py")
    report = read("reports/pulsesoc_messenger_media_viewer.md")

    image_fallback_raw_anchor = bool(
        re.search(r"if\s*\(\(item\.media_type[\s\S]{0,120}image\|gif[\s\S]{0,160}<a\s", messages_js)
    )

    checks = [
        check("media viewer JS exists", exists("static/js/pulse_messenger_media_viewer.js")),
        check("media viewer CSS exists", exists("static/css/pulse_messenger_media_viewer.css")),
        check("template loads viewer JS", "pulse_messenger_media_viewer.js" in template),
        check("template loads viewer CSS", "pulse_messenger_media_viewer.css" in template),
        check("image click handler exists", "data-messenger-media-viewer-trigger" in messages_js),
        check("raw image URL navigation prevented", "messengerImageAttachmentHtml" in messages_js and not image_fallback_raw_anchor),
        check("full-screen overlay exists", "pulse-messenger-media-viewer" in viewer_js and "setAttribute(\"role\", \"dialog\")" in viewer_js),
        check("close behavior exists", "function close()" in viewer_js and "data-pmmv-close" in viewer_js),
        check("previous/next media support exists", "data-pmmv-prev" in viewer_js and "data-pmmv-next" in viewer_js),
        check("zoom support exists", "MAX_ZOOM" in viewer_js and "wheel" in viewer_js and "touchDistance" in viewer_js),
        check("viewer actions wired", all(token in viewer_js for token in ["onReply", "onReact", "onForward", "onReport"])),
        check("message actions delegated", all(token in messages_js for token in ["startReply", "reactToMessage", "forwardMessage", "media_report"])),
        check("permission-protected report route exists", "/messages/<int:message_id>/report" in routes or "report_message(user" in service),
        check("protected media download route exists", "/api/messages/media/<int:attachment_id>/download" in read("bot.py")),
        check("message media payload fail-softs read-state locks", "COMM_V2_READ_STATE_DEFERRED" in service and "read_state_committed" in service),
        check("report exists", bool(report.strip())),
    ]

    failed = [item for item in checks if not item["ok"]]
    payload = {"ok": not failed, "checks": checks, "failed": failed}
    print(json.dumps(payload, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
