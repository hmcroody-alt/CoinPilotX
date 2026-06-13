#!/usr/bin/env python3
"""Audit PulseSoc global video controls/status UX cleanup."""

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def check(condition: bool, label: str, failures: list[str]) -> None:
    if condition:
        print(f"PASS {label}")
    else:
        print(f"FAIL {label}")
        failures.append(label)


def main() -> int:
    failures: list[str] = []
    media = read("static/js/pulse_media_renderer.js")
    status = read("static/js/pulse_status_viewer.js")
    cinematic_css = read("static/css/pulse_cinematic_media.css")
    status_css = read("static/css/pulse_status_system.css")
    reels_css = read("static/css/pulse_reels_experience.css")

    check("function stripNativeVideoControls" in media, "global video control sanitizer exists", failures)
    check(
        "observeNativeVideoControls" in media
        and "MutationObserver" in media
        and "attributeFilter: [\"controls\"]" not in media,
        "control observer watches added videos without attribute churn",
        failures,
    )
    check("PulseSoc media control guard skipped" in media, "control guard cannot block shell boot", failures)
    check("showTapIcon" in media and "pulse-media-tap-icon" in cinematic_css, "small temporary tap icon is available", failures)
    check("data-pulse-video-player${loop}" in media and "data-pulse-video-player${controls}" not in media, "shared renderer does not emit native controls", failures)
    check(".reel-card.show-controls .reel-center-play" in media and "display: none !important" in media, "runtime guard suppresses Reels center overlay", failures)
    check(re.search(r"\.reel-card\.show-controls\s+\.reel-center-play[\s\S]{0,220}display:\s*none\s*!important", reels_css) is not None, "Reels CSS keeps center overlay hidden", failures)
    check("decorateStatusActions" in status and "pulse-status-action-icon" in status, "Status action buttons are decorated with icons", failures)
    check("pointerdown" in status and "closeStatusViewerNow" in status, "Status close uses immediate pointer handler", failures)
    check("z-index: 10090" in status_css and "width: 56px" in status_css, "Status close target is large and top-layered", failures)
    check(".pulse-status-story-actions .pulse-status-action" in status_css, "Status actions have compact reaction styling", failures)
    check("::-webkit-media-controls" in cinematic_css and "display: none !important" in cinematic_css, "native webkit controls are hidden by CSS", failures)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
