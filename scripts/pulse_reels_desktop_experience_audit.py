#!/usr/bin/env python3
"""Static audit for the premium desktop PulseSoc Reels experience."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/pulse_reels_experience.css").read_text(encoding="utf-8")

FAILURES: list[str] = []


def require(label: str, condition: bool) -> None:
    if condition:
        print(f"PASS {label}")
    else:
        print(f"FAIL {label}")
        FAILURES.append(label)


def main() -> int:
    require("desktop media shell is fullscreen", ".reels-shell[data-reels-adaptive-shell]" in CSS and "position: fixed !important" in CSS)
    require("desktop center video width targets 60-70 percent", "left: clamp(190px, 14vw, 270px)" in CSS and "right: clamp(310px, 24vw, 430px)" in CSS)
    require("video stage uses cover", ".reels-media-stage .pulse-media-wrap video" in CSS and "object-fit: cover !important" in CSS)
    require("desktop sidebar includes requested discovery labels", all(label in BOT for label in ["For You", "Following", "Trending", "New Creators", "AI Picks", "Local"]))
    require("secondary sidebar includes requested sections", all(label in BOT for label in ["Reels", "Live", "Status", "Messages"]))
    require("quick actions include create and go live", "data-open-reel-camera" in BOT and "data-open-live-camera" in BOT)
    require("right info panel is present", "reelsInfoPanel" in BOT and "reels-info-panel" in BOT)
    require("live comments and composer are mirrored into info panel", "syncReelsInfoPanel" in BOT and "reel-inline-comment" in BOT)
    require("up next panel uses real reel media", "renderUpNext" in BOT and "reelPoster(reel)" in BOT and "reelPreviewSrc(reel)" in BOT)
    require("up next items are clickable buttons", "data-jump-reel" in BOT and "reels-upnext-item" in BOT)
    require("reaction selected state is rendered", "viewerReaction=String(reel.viewer_reaction||reel.my_reaction||'')" in BOT and "aria-pressed=\"${reelReactActive?'true':'false'}\"" in BOT)
    require("reaction aria state syncs after API", "setAttribute('aria-pressed',d.removed?'false':'true')" in BOT)
    require("desktop comments update preview without reload", "updatePreviewAfterComment(currentCommentReel,comment)" in BOT)
    require("mobile reels rules are preserved", "Mobile Reels redesign" in CSS and "@media (max-width: 900px)" in CSS)

    if FAILURES:
        print("\nFAILURES:")
        for item in FAILURES:
            print(f"- {item}")
        return 1
    print("pulse desktop reels experience audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
