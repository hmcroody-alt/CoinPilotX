#!/usr/bin/env python3
"""Audit the shared PulseSoc content viewer/reaction system across surfaces."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
HOME_JS = ROOT / "static/js/pulse_home_core.js"
STATUS_JS = ROOT / "static/js/pulse_status_viewer.js"
MEDIA_JS = ROOT / "static/js/pulse_media_renderer.js"
REACTION_JS = ROOT / "static/js/pulse_reaction_system.js"
REACTION_CSS = ROOT / "static/css/pulse_reaction_system.css"
FEED_CSS = ROOT / "static/css/pulse_desktop_feed.css"
STATUS_CSS = ROOT / "static/css/pulse_status_system.css"
REELS_CSS = ROOT / "static/css/pulse_reels_experience.css"
REPORT = ROOT / "reports/pulsesoc_unified_reactions_audit.json"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def check(name: str, passed: bool, details: str = "") -> dict[str, object]:
    return {"name": name, "passed": bool(passed), "details": details}


def reaction_dead_links(*sources: str) -> list[str]:
    matches: list[str] = []
    pattern = re.compile(r"<[^>]*(?:pulse-reaction-button|reel-action|pulse-action-button)[^>]*(?:href=['\"]#['\"]|javascript:void\(0\))", re.I)
    for source in sources:
        matches.extend(match.group(0)[:180] for match in pattern.finditer(source))
    return matches


def main() -> int:
    bot = read(BOT)
    home_js = read(HOME_JS)
    status_js = read(STATUS_JS)
    media_js = read(MEDIA_JS)
    reaction_js = read(REACTION_JS)
    reaction_css = read(REACTION_CSS)
    feed_css = read(FEED_CSS)
    status_css = read(STATUS_CSS)
    reels_css = read(REELS_CSS)
    dead_links = reaction_dead_links(bot, home_js, status_js, media_js)
    pulse_css_after_status = re.search(r"pulse_status_system\.css[^<]+<link rel=\"stylesheet\" href=\"/static/css/pulse_reaction_system\.css", bot, re.S) is not None
    pulse_css_after_reels = re.search(r"pulse_reels_experience\.css[^<]+<link rel=\"stylesheet\" href=\"/static/css/pulse_cinematic_media\.css[^<]+<link rel=\"stylesheet\" href=\"/static/css/pulse_home_os\.css[^<]+<link rel=\"stylesheet\" href=\"/static/css/pulse_reaction_system\.css", bot, re.S) is not None
    checks = [
        check("Shared reaction CSS exists", ".pulse-reaction-button" in reaction_css and ".pulse-reaction-bar" in reaction_css),
        check("Shared content viewer JS exists", "window.LogiNexusContentViewer" in reaction_js and "decorateViewer" in reaction_js and "hydrate(root" in reaction_js),
        check("Shared reaction JS exists", "window.PulseReactionSystem" in reaction_js and "decorate(button" in reaction_js and "hydrate(root" in reaction_js),
        check("Pulse pages load shared reaction CSS", "pulse_reaction_system.css?v=status-v3-20260629b" in bot),
        check("Pulse pages load shared reaction JS", "pulse_reaction_system.js?v=feed-actions-v2-20260629b" in bot),
        check("Pulse pages load updated Status viewer JS", "pulse_status_viewer.js?v=status-v3-20260629b" in bot),
        check("Feed posts use dedicated V2 reaction classes", "pulse-feed-actions-v2 pulse-reaction-bar" in home_js and "pulse-action-chip pulse-reaction-button" in home_js),
        check("Video posts use shared reaction classes", "post-action-button pulse-action-button pulse-reaction-button reel-action reel-action-button" in bot and "post-action-row pulse-reaction-bar" in bot and "data-video-repost" in bot and "data-save-video" in bot),
        check("Statuses use shared reaction classes", "pulse-status-action-v3" in status_js and "lnx-action-control" in status_js and "PulseReactionSystem?.decorate" in status_js),
        check("Standalone Status viewer uses shared reaction selectors", all(token in status_js for token in ["[data-status-viewer-react]", "[data-status-viewer-comment]", "[data-status-viewer-share]", "[data-status-viewer-save]", "[data-status-viewer-more]", "[data-status-viewer-mute]"])),
        check("Inline status viewer uses shared reaction classes", "button.classList.add('pulse-status-action','pulse-action-button','pulse-reaction-button','reel-action','reel-action-button')" in bot),
        check("Reels use shared reaction classes", "reels-action-rail reel-actions pulse-reaction-bar" in bot and "button.classList.add('reel-action-button','pulse-reaction-button')" in bot),
        check("Reels mobile enhancer uses shared reaction classes", "reel-action reel-action-button pulse-reaction-button" in media_js and "PulseReactionSystem?.hydrate?.(card)" in media_js),
        check("Shared creator/music/counter primitives exist", all(token in reaction_css + reaction_js for token in ["pulse-creator-header", "pulse-music-card", "pulse-counter-row", "pulse-glass-overlay"])),
        check("Content viewer modes are internal and shared", all(token in reaction_js for token in ['dataset.contentViewerAction', '? "video" : "feed"', 'dataset.contentViewerMode = "reel"', 'dataset.contentViewerMode = "status"'])),
        check("Feed and video dynamic renders hydrate shared system", "PulseReactionSystem?.hydrate?.(card)" in home_js and "PulseReactionSystem?.hydrate?.(videosGrid)" in bot),
        check("Feed actions use compact shared glass controls", ".pulse-feed-actions-v2 .pulse-action-chip" in reaction_css and "min-height: 44px" in reaction_css and "display: inline-grid" in reaction_css),
        check("Action rail uses same compact control dimensions", all(token in reaction_css for token in [".pulse-status-story-actions .lnx-action-control", ".reels-action-rail .lnx-action-control", "width: 46px", "height: 46px"])),
        check("Shared animations cover pop ripple and save flash", all(token in reaction_css + reaction_js for token in ["lnxActionPop", "lnxShareRipple", "lnxGoldFlash", "is-rippling", "is-save-flashing", "is-popping"])),
        check("Shared active states include like/save/remix", all(token in reaction_css for token in ['data-action="like"', 'data-action="save"', 'data-action="remix"', 'pulseReactionPulse'])),
        check("Shared V2 design tokens exist", all(token in reaction_css for token in ["--lnx-viewer-radius", "--lnx-glass-blur", "--lnx-action-size", "--lnx-motion-fast"])),
        check("Music controllers are unified", all(token in reaction_css + reaction_js + status_js for token in ["lnx-music-controller", "pulse-status-now-copy", "pulse-status-now-play"]) and ("lnxMusicWave" in reaction_css + reaction_js or "pulseStatusWave" in reaction_css)),
        check("Comment composer is compact", all(token in reaction_css for token in [".pulse-comment-composer-v2", "height: 38px", "min-height: 46px"])),
        check("Desktop Status viewer stage is centered", all(token in reaction_css for token in ["--lnx-status-stage-width", "object-fit: contain", ".pulse-status-action-rail-v3"])),
        check("Canonical desktop action icons are present", all(token in reaction_js + status_js + home_js for token in ['like: "❤"', 'comment: "💬"', 'share: "↗"', 'save: "🔖"', 'more: "•••"']) and 'feedActionChip("↻", "Repost"' in home_js),
        check("Old surface CSS is overridden by shared layer", pulse_css_after_status and pulse_css_after_reels),
        check("No duplicate Remix protection exists", "rail.querySelector('[data-reel-repost]')" in bot and "button.remove()" in bot and "data-reel-more" in bot),
        check("No reaction dead hrefs", not dead_links, "; ".join(dead_links[:3])),
        check("No javascript void reaction links", "javascript:void(0)" not in home_js + status_js + media_js + reaction_js),
        check("Like handlers still exist", all(token in home_js + status_js + bot for token in ["data-post-like", "data-status-story-react", "data-reel-react"])),
        check("Comment/reply handlers still exist", all(token in home_js + status_js + bot for token in ["data-post-comment", "data-status-story-reply", "data-open-comments"])),
        check("Share handlers still exist", all(token in home_js + status_js + bot for token in ["data-post-share", "data-status-story-share", "data-share-reel"])),
        check("Save handlers still exist", all(token in home_js + status_js + bot for token in ["data-save-post", "data-status-story-save", "data-reel-save"])),
        check("More menu handlers still exist", all(token in home_js + status_js + bot for token in ["data-post-menu", "data-status-story-more", "data-reel-more"])),
        check("Owner and non-owner menu guards still exist", all(token in home_js + bot for token in ["post.can_delete", "canDelete", "ownerTools", "canManage"])),
        check("Minimum tap target is enforced without oversized chips", "--lnx-action-size: 42px" in reaction_css and "min-height: 38px" in reaction_css and "width: 46px" in reaction_css),
        check("Reduced motion is supported", "prefers-reduced-motion" in reaction_css),
        check("Internal design name is not rendered in public HTML", "LogiNexus" not in bot[bot.find("def pulse_page_html") : bot.find("def pulse_emit_event")] and "LogiNexus" not in bot[bot.find("def pulse_social_shell") : bot.find("def pulse_reels_page")]),
        check("No duplicate shared class tokens", "pulse-reaction-button pulse-reaction-button" not in bot + home_js + status_js + media_js),
    ]
    report = {"ok": all(item["passed"] for item in checks), "checks": checks}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "checks": checks, "report": str(REPORT.relative_to(ROOT))}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
