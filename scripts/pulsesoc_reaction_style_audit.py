#!/usr/bin/env python3
"""Audit unified PulseSoc post/status reaction styling and wiring."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOME_JS = ROOT / "static/js/pulse_home_core.js"
STATUS_JS = ROOT / "static/js/pulse_status_viewer.js"
FEED_CSS = ROOT / "static/css/pulse_desktop_feed.css"
STATUS_CSS = ROOT / "static/css/pulse_status_system.css"
BOT = ROOT / "bot.py"
REPORT = ROOT / "reports/pulsesoc_reaction_style_audit.json"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def check(name: str, passed: bool, details: str = "") -> dict[str, object]:
    return {"name": name, "passed": bool(passed), "details": details}


def main() -> int:
    home_js = read(HOME_JS)
    status_js = read(STATUS_JS)
    feed_css = read(FEED_CSS)
    status_css = read(STATUS_CSS)
    bot_py = read(BOT)
    checks = [
        check("Shared reaction class exists", ".pulse-action-button" in feed_css and ".pulse-action-button" in status_css),
        check("Post action rows use shared class", '"post-action-button pulse-action-button"' in home_js),
        check("Status actions use shared class", '"pulse-status-action", "pulse-action-button", "reel-action", "reel-action-button"' in status_js),
        check("Inline status viewer uses shared class", "button.classList.add('pulse-status-action','pulse-action-button','reel-action','reel-action-button')" in bot_py),
        check("Video feed actions use shared class", '"post-action-button pulse-action-button"' in bot_py and 'data-video-repost' in bot_py and 'data-save-video' in bot_py),
        check("Post action data actions exist", all(token in home_js for token in ['action: "like"', 'action: "comment"', 'action: "repost"', 'action: "share"', 'action: "save"'])),
        check("Status data actions exist", "button.dataset.action" in status_js and 'key === "love" ? "like" : key' in status_js and 'button.dataset.action = "mute"' in status_js),
        check("Backend status data actions exist", all(token in bot_py for token in ["button.dataset.action=action", "'like'", "'comment'", "'share'", "'save'", "'more'"])),
        check("Backend video data actions exist", all(token in bot_py for token in ['data-action="like"', 'data-action="comment"', 'data-action="repost"', 'data-action="share"', 'data-action="save"'])),
        check("Active states styled", '[data-action="like"][aria-pressed="true"]' in feed_css and '[data-action="save"][aria-pressed="true"]' in feed_css),
        check("Status active states styled", '[data-action="like"][aria-pressed="true"]' in status_css and '[data-action="save"][aria-pressed="true"]' in status_css),
        check("Status save toggles pressed state", "statusViewerSave.setAttribute('aria-pressed','true')" in bot_py and 'key === "love" || key === "save"' in status_js),
        check("Video save/repost toggles pressed state", "btn.setAttribute('aria-pressed','true')" in bot_py and "repost.setAttribute('aria-pressed','true')" in bot_py),
        check("Handlers still referenced", all(token in home_js for token in ["reactToPost", "sendComment", "shareUrl", "/save", "/repost"])),
        check("No duplicate Remix introduced", home_js.count("Remix") <= 1),
        check("No obvious dead action hrefs", all(token not in home_js + bot_py for token in ["javascript:void(0)", "onclick=\"\""])),
    ]
    report = {"ok": all(item["passed"] for item in checks), "checks": checks}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "checks": checks, "report": str(REPORT.relative_to(ROOT))}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
