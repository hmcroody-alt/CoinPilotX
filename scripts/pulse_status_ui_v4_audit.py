"""Audit PulseSoc Status UI V4 one-identity / one-soundtrack contract."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATUS_JS = ROOT / "static" / "js" / "pulse_status_viewer.js"
STATUS_CSS = ROOT / "static" / "css" / "pulse_status_system.css"
BOT = ROOT / "bot.py"
REPORT = ROOT / "reports" / "pulse_status_ui_v4.md"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main() -> None:
    js = STATUS_JS.read_text(encoding="utf-8")
    css = STATUS_CSS.read_text(encoding="utf-8")
    bot = BOT.read_text(encoding="utf-8")
    report = REPORT.read_text(encoding="utf-8")

    require("pulse-status-v4-viewer" in js, "Status viewer receives V4 runtime class")
    require("pulse-status-v4-shell" in js, "Status shell receives V4 runtime class")
    require("pulse-status-legacy-identity" in js and 'data-status-legacy-identity' in css, "legacy footer identity is hidden")
    require("statusHeaderMeta(viewer, time)" in js, "top header owns one creator metadata line")
    require("viewer.querySelector(\"[data-status-viewer-body],[data-status-story-body]\")?.textContent?.trim()" not in js[js.find("const body ="):js.find("const artist =")], "music signature does not use status body as song title")
    require("viewer.querySelector(\"[data-status-viewer-author],[data-status-story-author]\")?.textContent?.trim()" not in js[js.find("const artist ="):js.find("const title =")], "music signature does not repeat creator as artist fallback")
    require("if (!hasAttachedMusic)" in js and "title.textContent = \"\"" in js, "music signature clears stale text when no music exists")
    require("pulse-status-music-signature-v4" in js and "is-music-expanded" in js, "music signature exists and expands on interaction")
    require(".pulse-status-music-signature-v4[hidden]" in css and "display: none !important" in css, "music signature honors hidden state when no music exists")
    require("pulse-status-music-expanded-actions" in js, "expanded music layer has secondary actions")
    require("item.music?.title||item.body" not in bot, "server render paths do not fall back to body as music title")
    require("Final V4 overrides must stay after legacy story rules" in css, "V4 CSS override block is last-mile cascade safe")
    require("prefers-reduced-motion" in css, "reduced-motion handling exists")
    require("one identity" in report.lower() and "one soundtrack" in report.lower(), "V4 report documents the contract")


if __name__ == "__main__":
    main()
