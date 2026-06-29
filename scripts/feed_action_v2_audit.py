#!/usr/bin/env python3
"""Audit regular PulseSoc feed post action V2 structure and wiring."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOME_JS = ROOT / "static/js/pulse_home_core.js"
REACTION_JS = ROOT / "static/js/pulse_reaction_system.js"
REACTION_CSS = ROOT / "static/css/pulse_reaction_system.css"
BOT = ROOT / "bot.py"
REPORT = ROOT / "reports/feed_action_v2_audit.json"


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
    home_js = read(HOME_JS)
    reaction_js = read(REACTION_JS)
    reaction_css = read(REACTION_CSS)
    bot = read(BOT)

    render_post = section(home_js, "function renderPost(post)", "function observePost")
    render_actions = section(home_js, "function renderActions(card, post)", "function renderComposer")
    render_composer = section(home_js, "function renderComposer(card, post)", "function renderPost(post)")
    server_feed_rewrite = section(bot, "html = (", "rendered_html = (")
    dead_links = re.findall(r"(?:href=['\"]#['\"]|javascript:void\\(0\\))", home_js + reaction_js)

    order_tokens = [
        "renderCreatorHeader(card, post, author, authorName, label)",
        "renderCaption(card, post)",
        "renderPostMusic(card, post)",
        "if (media) card.appendChild(media)",
        "renderEngagement(card, post)",
        "renderActions(card, post)",
        "renderComposer(card, post)",
    ]
    order_positions = [render_post.find(token) for token in order_tokens]
    required_actions = ["Like", "Comment", "Repost", "Share", "Save", "More"]
    required_action_attrs = ["data-post-like", "data-post-comment", "data-post-repost", "data-post-share", "data-save-post", "data-post-menu"]

    checks = [
        check("Feed action V2 row exists", '"pulse-feed-actions-v2 pulse-reaction-bar"' in home_js and 'row.dataset.contentType = "feed"' in home_js),
        check("Feed action chips exist", "function feedActionChip" in home_js and '"pulse-action-chip pulse-reaction-button"' in home_js),
        check("Required feed action labels exist", all(f'"{label}"' in render_actions for label in required_actions)),
        check("Required feed action handlers remain wired", all(token in home_js for token in required_action_attrs)),
        check("More chip uses existing post menu handler", 'feedActionChip("•••", "More", { postMenu: post.id, action: "more" }' in home_js and 'event.target.closest("[data-post-menu]")' in home_js),
        check("Owner promote chip is owner-only", "if (post.can_delete)" in render_actions and "promote.dataset.promoteContent" in render_actions and "data-promote-content" in reaction_js + bot),
        check("Server-rendered /pulse feed receives V2 row", "pulse-feed-actions-v2 pulse-reaction-bar" in server_feed_rewrite and "quick-action" in server_feed_rewrite),
        check("Server-rendered /pulse actions are wired", all(token in server_feed_rewrite for token in ["data-react=\\\"like\\\"", "data-open-comments", "data-post-repost", "data-share", "data-save-post", "data-post-menu"])),
        check("Server-rendered /pulse composer is V2", "pulse-comment-composer-v2 post-comment-composer" in server_feed_rewrite and "data-action=\\\"comment-media\\\"" in server_feed_rewrite and "data-send-comment" in server_feed_rewrite),
        check("Server-rendered /pulse order supports tags music media", "data-post-music-anchor" in server_feed_rewrite and "return html.replace(`<span data-post-music-anchor" in server_feed_rewrite),
        check("Server-rendered /pulse repost has rollback", "const repost=e.target.closest('[data-post-repost]')" in server_feed_rewrite and "/api/pulse/posts/${repost.dataset.postRepost}/repost" in server_feed_rewrite and "repost.classList.toggle('active',was)" in server_feed_rewrite),
        check("Server-rendered /pulse save honors backend toggle state", "const active=!(d.removed||d.saved===false)" in server_feed_rewrite and "finally{save.disabled=false}" in server_feed_rewrite),
        check("Action chip has no legacy feed toolbar classes", "post-action-row" not in render_actions and "post-action-button" not in render_actions and "reel-action-button" not in render_actions),
        check("Old post-action-row CSS cannot control V2 row", ".pulse-feed-actions-v2" in reaction_css and ".pulse-feed-actions-v2 .pulse-action-chip" in reaction_css),
        check("No duplicate icon label meta structure in feed chips", "reel-action-label" not in render_actions and "reel-action-meta" not in render_actions and "post-action-icon" not in render_actions),
        check("Counts have stable update targets", all(token in home_js for token in ["dataset.postLikeCount", "dataset.postCommentCount", "dataset.postRepostCount", "dataset.postShareCount"])),
        check("Like count updates only after backend response", "data-post-like-count" in home_js and "const data = await api(`/api/pulse/posts/${postId}/react`" in home_js),
        check("Comment count updates after comment backend success", "data-post-comment-count" in home_js and "await api(`/api/pulse/posts/${postId}/comments`" in home_js),
        check("Comment composer V2 exists", '"pulse-comment-composer-v2 post-comment-composer"' in home_js and "pulse-comment-avatar" in home_js and "pulse-comment-action" in home_js),
        check("Composer buttons remain wired", all(token in render_composer for token in ["commentMedia: post.id", "commentEmoji: post.id", "commentSend: post.id"])),
        check("Composer has compact CSS", all(token in reaction_css for token in [".pulse-comment-composer-v2", "min-height: 46px", "grid-template-columns: 36px minmax(0, 1fr) 38px 38px 38px"])),
        check("Feed action CSS uses compact glass chips", all(token in reaction_css for token in [".pulse-action-chip", "min-height: 44px", "backdrop-filter: blur(16px) saturate(160%)", "border-radius: 999px"])),
        check("Mobile feed action layout avoids page overflow", all(token in reaction_css for token in ["@media (max-width: 620px)", "grid-auto-flow: column", "overflow-x: auto"])),
        check("Very small mobile avoids label/icon collisions", all(token in reaction_css for token in ["@media (max-width: 380px)", "clip-path: inset(50%)"])),
        check("Accessible pressed states exist", 'aria-pressed", "false"' in home_js and 'setAttribute("aria-pressed"' in home_js),
        check("No href hash or javascript void in feed action runtime", not dead_links, ", ".join(dead_links[:3])),
        check("Feed render order matches required structure", all(pos >= 0 for pos in order_positions) and order_positions == sorted(order_positions), str(order_positions)),
        check("Media is not rendered before creator identity", render_post.find("renderCreatorHeader") < render_post.find("if (media) card.appendChild(media)")),
        check("Shared hydrator recognizes V2 feed classes", ".pulse-feed-actions-v2" in reaction_js and ".pulse-action-chip" in reaction_js and ".pulse-comment-action" in reaction_js),
    ]

    report = {"ok": all(item["passed"] for item in checks), "checks": checks}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "checks": checks, "report": str(REPORT.relative_to(ROOT))}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
