#!/usr/bin/env python3
"""Audit PulseSoc regular feed post V3 structure, styling, and wiring."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
HOME_JS = ROOT / "static/js/pulse_home_core.js"
FEED_CSS = ROOT / "static/css/pulse_desktop_feed.css"
REACTION_CSS = ROOT / "static/css/pulse_reaction_system.css"
REPORT = ROOT / "reports/feed_post_v3_audit.json"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def check(name: str, passed: bool, details: str = "") -> dict[str, object]:
    return {"name": name, "passed": bool(passed), "details": details}


def section(source: str, start: str, end: str) -> str:
    start_idx = source.find(start)
    end_idx = source.find(end, start_idx + 1)
    if start_idx < 0 or end_idx < 0:
        return ""
    return source[start_idx:end_idx]


def main() -> int:
    bot = read(BOT)
    home_js = read(HOME_JS)
    feed_css = read(FEED_CSS)
    reaction_css = read(REACTION_CSS)
    css = feed_css + "\n" + reaction_css
    render_post = section(home_js, "function renderPost(post)", "function observePost")
    render_actions = section(home_js, "function renderActions(card, post)", "function renderComposer")
    dead_links = re.findall(r"(?:href=['\"]#['\"]|javascript:void\\(0\\))", render_post + render_actions)
    order_tokens = [
        "renderCreatorHeader(card, post, author, authorName, label)",
        "renderCaption(card, post)",
        "renderPostMusic(card, post)",
        "if (media) card.appendChild(media)",
        "renderEngagement(card, post)",
        "renderActions(card, post)",
        "renderComposer(card, post)",
    ]
    positions = [render_post.find(token) for token in order_tokens]
    checks = [
        check("Feed V3 card class exists", "pulse-feed-post-v3" in home_js and ".feed > .pulse-feed-post-v3" in feed_css),
        check("Pulse page loads Feed V3 CSS asset", "pulse_desktop_feed.css?v=feed-post-v3-20260629a" in bot and "pulse_desktop_feed.css?v=feed-actions-v2" not in bot),
        check("Required feed hierarchy order is preserved", all(pos >= 0 for pos in positions) and positions == sorted(positions), str(positions)),
        check("Music is ordered before media in CSS", ".feed > .pulse-feed-post-v3 .post-music-player { order: 4" in feed_css and ".feed > .pulse-feed-post-v3 > .post-card-media { order: 5" in feed_css),
        check("Media is edge-to-edge inside the post", "width: calc(100% + clamp(16px, 2.6vw, 28px))" in feed_css and "margin-inline: calc(clamp(8px, 1.3vw, 14px) * -1)" in feed_css),
        check("Nested media borders are removed", ".feed > .pulse-feed-post-v3 .post-card-media-frame" in feed_css and "border: 0 !important" in feed_css),
        check("Reaction summary V3 exists", "post-engagement-summary-v3" in home_js and "👍" in home_js and "😡" in home_js),
        check("Reaction summary excludes view metric in V3 renderer", '"views"' not in section(home_js, "const metrics = [", "metrics.forEach")),
        check("Bottom action bar V3 exists", "pulse-feed-actions-v3 pulse-feed-actions-v2 pulse-reaction-bar" in home_js and ".pulse-feed-actions-v3" in feed_css),
        check("Feed actions are bottom row only", "pulse-feed-actions-v3" in home_js and "pulse-status-action-rail-v3" not in render_post and "reels-action-rail" not in render_post),
        check("Required actions remain wired", all(token in home_js for token in ["data-post-like", "data-post-comment", "data-post-repost", "data-post-share", "data-save-post", "data-post-menu"])),
        check("Long press reaction picker exists", "pulse-feed-reaction-picker-v3" in home_js and "data-long-press-reactions" not in home_js and "longPressReactions" in home_js),
        check("Reaction picker uses backend reaction types", all(token in home_js for token in ['"like"', '"love"', '"funny"', '"wow"', '"brutal"', '"scam_alert"'])),
        check("Reaction picker uses real react endpoint", "reactToPost(postId, likeButton, reactionChoice.dataset.feedReactionChoice" in home_js and "reaction_type: selectedReaction" in home_js),
        check("Media long press no longer opens menu", "document.querySelector(`[data-post-sheet=\"${longPressStart.postId}\"]`)?.classList.add(\"open\")" not in home_js),
        check("Comment composer compact V3 grid exists", ".feed > .pulse-feed-post-v3 .post-comment-composer" in feed_css and "grid-template-columns: 38px minmax(0, 1fr) 40px 40px 46px" in feed_css),
        check("Comment send remains visible", ".feed > .pulse-feed-post-v3 .post-comment-composer [data-comment-send]" in feed_css and "display: grid !important" in feed_css),
        check("Responsive targets exist", all(token in feed_css for token in ["@media (max-width: 620px)", "@media (max-width: 390px)", "@media (min-width: 1280px)"])),
        check("Reduced motion supported", "prefers-reduced-motion" in feed_css),
        check("No dead action links", not dead_links, ", ".join(dead_links[:3])),
        check("Internal design name is not user-facing", "LogiNexus" not in render_post and "LogiNexus" not in feed_css),
    ]
    report = {"ok": all(item["passed"] for item in checks), "checks": checks}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "report": str(REPORT.relative_to(ROOT)), "checks": checks}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
