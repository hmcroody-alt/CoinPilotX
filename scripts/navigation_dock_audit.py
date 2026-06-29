#!/usr/bin/env python3
"""Audit PulseSoc universal navigation dock wiring."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
HOME_JS = ROOT / "static/js/pulse_home_core.js"
HOME_CSS = ROOT / "static/css/pulse_home_os.css"
REPORT = ROOT / "reports/navigation_dock_audit.json"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def check(name: str, passed: bool, details: str = "") -> dict[str, object]:
    return {"name": name, "passed": bool(passed), "details": details}


def function_body(source: str, name: str) -> str:
    marker = f"def {name}("
    start = source.find(marker)
    if start < 0:
        return ""
    next_def = source.find("\ndef ", start + len(marker))
    return source[start:] if next_def < 0 else source[start:next_def]


def main() -> int:
    bot = read(BOT)
    js = read(HOME_JS)
    css = read(HOME_CSS)
    page = function_body(bot, "pulse_page_html")
    shell = function_body(bot, "pulse_social_shell")
    runtime = function_body(bot, "pulse_universal_dock_runtime_script")
    dock_actions = ["home", "reels", "create", "messages", "profile"]
    expected_dock_items = ["Home", "Reels", "Create", "Messages", "Profile"]
    checks = [
        check("Dock exists on Home", "mobile-bottom-nav" in page and "data-pulse-dock-item" in page),
        check("Dock exists on shared PulseSoc shell", "mobile-bottom-nav" in shell and "data-pulse-dock-item" in shell),
        check("Dock has five primary actions", all(item in page for item in expected_dock_items) and all(f'"{action}"' in page or f"'{action}'" in page for action in dock_actions)),
        check("Shared shell has five primary actions", all(item in shell for item in expected_dock_items) and all(f'"{action}"' in shell or f"'{action}'" in shell for action in dock_actions)),
        check("Dock is enabled on desktop", "pulse-universal-dock" in css and "@media (min-width: 901px)" in css and "display: grid !important" in css),
        check("Dock is enabled on tablet/mobile", "@media (max-width: 520px)" in css and "env(safe-area-inset-bottom" in css),
        check("Create opens in-page composer", "data-pulse-create-trigger='1'" in page and "window.openPulseComposer = openPulseComposer" in js),
        check("Shared shell Create opens in-place sheet", "data-pulse-create-trigger='1'" in shell and "create_sheet_html" in shell and "openCreateSheet" in runtime),
        check("Reels Create opens real creator", 'href="/pulse/reels?open=create"' in shell and "adoptIncomingCreateIntent" in bot and "reelUploadModal" in bot),
        check("Messages and Profile route to real pages", '"/pulse/messages"' in shell and '"/pulse/profile"' in shell and '"/pulse/messages"' in page and '"/pulse/profile"' in page),
        check("Create uses context-aware safe sheet outside Home", "if (typeof window.openPulseComposer === \"function\") window.openPulseComposer(type);" in runtime and "else openCreateSheet();" in runtime),
        check("No legacy create route in Home dock", 'href="/pulse/create"' not in page and "javascript:void(0)" not in page),
        check("No legacy create route in shared shell dock", 'href="/pulse/create"' not in shell and "javascript:void(0)" not in shell),
        check("Hide on downward scroll", "window.addEventListener(\"scroll\", requestDockUpdate, { passive: true })" in js and "setDockHidden(delta > 0" in js),
        check("Reveal on upward scroll", "setDockHidden(delta > 0 && y > 120)" in js and "setHidden(delta > 0 && y > 120)" in runtime),
        check("Disabled while composer open", "composer?.classList.contains(\"is-expanded\")" in js),
        check("Disabled while keyboard visible", "visualViewport" in js and "keyboardOpen" in js),
        check("Disabled while sheets/modals open", "#createSheet.open" in js and "#createSheet.open" in runtime and ".pulse-media-lightbox.open" in js and ".pulse-promotion-modal.open" in js),
        check("Active route state exists", "aria-current" in js and "data-dock-action=\"create\"" in css),
        check("Unread badge remains backend/live wired", "data-chat-unread" in page and "nav-badge" in page),
        check("Profile uses real avatar or initials", "shell_avatar_html" in page and "shell_initials" in bot and 'or "P"' not in function_body(bot, "pulse_page_html")),
        check("Dock transition quality", "cubic-bezier(.22, .61, .36, 1)" in css and "opacity .22s ease" in css),
        check("Reduced motion supported", "prefers-reduced-motion: reduce" in css and "transition: none !important" in css),
        check("Create sheet is above dock", ".create-sheet" in css and "z-index: 1190" in css and ".create-sheet.open" in css),
    ]
    report = {"ok": all(item["passed"] for item in checks), "checks": checks}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "checks": checks, "report": str(REPORT.relative_to(ROOT))}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
