#!/usr/bin/env python3
"""Audit PulseSoc Status Viewer V3 structure and wiring."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
STATUS_JS = ROOT / "static/js/pulse_status_viewer.js"
REACTION_JS = ROOT / "static/js/pulse_reaction_system.js"
REACTION_CSS = ROOT / "static/css/pulse_reaction_system.css"
STATUS_CSS = ROOT / "static/css/pulse_status_system.css"
REPORT = ROOT / "reports/status_viewer_v3_audit.json"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def check(name: str, passed: bool, details: str = "") -> dict[str, object]:
    return {"name": name, "passed": bool(passed), "details": details}


def dead_links(*sources: str) -> list[str]:
    pattern = re.compile(
        r"<[^>]*(?:pulse-status|status-viewer|status-story)[^>]*(?:href=['\"]#['\"]|javascript:void\(0\))",
        re.I,
    )
    matches: list[str] = []
    for source in sources:
        matches.extend(match.group(0)[:220] for match in pattern.finditer(source))
    return matches


def main() -> int:
    bot = read(BOT)
    status_js = read(STATUS_JS)
    reaction_js = read(REACTION_JS)
    reaction_css = read(REACTION_CSS)
    status_css = read(STATUS_CSS)
    css = reaction_css + "\n" + status_css
    js = status_js + "\n" + reaction_js
    dead = dead_links(bot, status_js)
    checks = [
        check("Status V3 runtime class exists", "pulse-status-v3-viewer" in status_js and "pulse-status-v3-shell" in status_js),
        check("Pages load Status V3 asset version", "pulse_status_viewer.js?v=status-v3-20260629b" in bot and "pulse_status_viewer.js?v=feed-actions-v2" not in bot),
        check("Segmented progress exists", "pulse-status-segmented-progress-v3" in status_js and "pulse-status-story-segment" in status_js and "is-current" in status_js),
        check("Progress pauses and resumes", "pauseStory()" in status_js and "resumeStory()" in status_js and "storyRuntime.paused" in status_js),
        check("Creator header exists", "pulse-status-creator-header-v3" in status_js and "data-status-v3-author" in status_js and "syncStatusV3Metadata" in status_js),
        check("Full media remains primary layer", ".pulse-status-v3-shell .pulse-status-story-media" in css and "inset: 0" in css and "object-fit: contain" in css),
        check("Right rail uses shared action classes", "pulse-status-action-rail-v3" in status_js and "lnx-action-control" in status_js and "pulse-reaction-button" in status_js),
        check("Required rail actions exist", all(token in status_js for token in ["data-status-story-react", "data-status-story-comment", "data-status-story-repost", "data-status-story-share", "data-status-story-save", "data-status-story-more", "data-status-story-mute"])),
        check("Repost is safely unavailable, not fake", "Status repost is not available yet." in status_js and "aria-disabled" in status_js and "data-statusUnavailableReason" not in status_js),
        check("Old status buttons are not the active layout", "pulse-status-action-rail-v3" in css and ".pulse-status-action-rail-v3 .pulse-status-action-v3" in css),
        check("Music mini-player V3 exists", "pulse-status-music-mini-v3" in status_js and "data-status-mini-player" in status_js and "data-status-music-toggle" in status_js),
        check("Music waveform/progress exists", "pulseStatusWave" in reaction_css and "data-status-now-progress" in status_js),
        check("Sound toggle uses active audio source", "viewerSoundMedia" in status_js and "hasAttachedAudio" in status_js and "statusActiveAudio" in status_js),
        check("Attached audio keeps original video muted", "forceOriginalAudioMuted" in status_js and "status-attached" in status_js and "original_audio_muted" in status_js),
        check("Close button exists and stops playback", "data-status-story-close" in bot and "data-status-viewer-close" in bot and "closeStatusViewerNow" in status_js and "media.pause" in status_js),
        check("Bottom creator strip exists", "pulse-status-bottom-strip-v3" in status_js and "statusV3BottomStrip" in status_js),
        check("Reply composer exists outside rail", "pulse-status-reply-composer-v3" in status_js and "shell.appendChild(composer)" in status_js),
        check("Desktop centering rules exist", "@media (min-width: 900px)" in reaction_css and "--lnx-status-stage-width" in reaction_css and "max-width: min(100%, 560px)" in reaction_css),
        check("Mobile safe-area rules exist", "@media (max-width: 620px)" in reaction_css and "env(safe-area-inset-bottom)" in reaction_css and "100dvh" in reaction_css),
        check("No large shield overlay introduced", "large shield" not in css.lower() and "status-shield" not in css.lower()),
        check("No dead Status links", not dead, "; ".join(dead[:3])),
        check("No javascript void Status links", "javascript:void(0)" not in bot + status_js),
        check("Functional handlers remain wired", all(token in bot + status_js for token in ["react", "reply", "share", "save", "more", "toggleViewerSound", "navigateStory", "closeStatusViewer"])),
        check("Shared content viewer mode remains internal", 'dataset.contentViewerMode = "status"' in reaction_js or 'dataset.contentViewerMode = "status"' in status_js),
    ]
    report = {"ok": all(item["passed"] for item in checks), "checks": checks}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "report": str(REPORT.relative_to(ROOT)), "checks": checks}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
