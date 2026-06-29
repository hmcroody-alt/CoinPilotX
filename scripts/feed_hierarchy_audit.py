#!/usr/bin/env python3
"""Audit PulseSoc feed card hierarchy and composer toolbar scope."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
HOME_JS = ROOT / "static/js/pulse_home_core.js"
MEDIA_JS = ROOT / "static/js/pulse_media_renderer.js"
FEED_CSS = ROOT / "static/css/pulse_desktop_feed.css"
REPORT = ROOT / "reports/feed_hierarchy_audit.json"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def body(source: str, marker: str) -> str:
    start = source.find(marker)
    if start < 0:
        return ""
    next_func = source.find("\n  function ", start + len(marker))
    return source[start:] if next_func < 0 else source[start:next_func]


def ordered(source: str, tokens: list[str]) -> bool:
    pos = -1
    for token in tokens:
        pos = source.find(token, pos + 1)
        if pos < 0:
            return False
    return True


def check(name: str, passed: bool, details: str = "") -> dict[str, object]:
    return {"name": name, "passed": bool(passed), "details": details}


def main() -> int:
    bot = read(BOT)
    js = read(HOME_JS)
    media = read(MEDIA_JS)
    feed_css = read(FEED_CSS)
    render_post = body(js, "function renderPost(")
    render_music = body(js, "function renderPostMusic(")
    page = bot[bot.find("def pulse_page_html("):bot.find("@webhook_app.route(\"/pulse\"", bot.find("def pulse_page_html("))]
    expected = [
        "renderCreatorHeader",
        "renderCreatorDrawer",
        "renderMenu",
        "renderCaption",
        "renderPostMusic",
        "if (media) card.appendChild(media)",
        "renderEngagement",
        "renderActions",
        "renderComposer",
    ]
    composer_shortcut_removed = (
        "AI Assistant" not in page
        and "data-composer-ai>" not in page
        and "data-composer-ai " not in page
        and "data-composer-ai=" not in page
        and "data-composer-ai>" not in js
        and "data-composer-ai " not in js
        and "data-composer-ai=" not in js
    )
    checks = [
        check("Feed hierarchy owner/caption/audio/media/actions order", ordered(render_post, expected)),
        check("Attached audio card before media", render_post.find("renderPostMusic") < render_post.find("if (media) card.appendChild(media)")),
        check("Media is not rendered before owner header", render_post.find("if (media) card.appendChild(media)") > render_post.find("renderCreatorHeader")),
        check("CSS does not visually move video media before header", ".post-card-modern.post-card-video > .post-card-media {\norder:" not in feed_css and "order: -2" not in feed_css),
        check("Attached audio preserves original mute rule", "Original audio muted" in render_music and "forceOriginalAudioMuted" in render_music and "forceOriginalAudioMuted" in media),
        check("Post actions remain wired", all(token in js for token in ["data-post-like", "data-post-comment", "data-post-repost", "data-post-share", "data-save-post"])),
        check("Comment composer remains bottom", "renderComposer(card, post)" in render_post and render_post.rfind("renderComposer") > render_post.rfind("renderActions")),
        check("Composer AI shortcut removed", composer_shortcut_removed),
        check("Composer core buttons remain", all(label in page for label in ["Photo", "Video", "Music", "Feeling", "Location", "Mention", "Topic", "Public"])),
    ]
    report = {"ok": all(item["passed"] for item in checks), "checks": checks}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "checks": checks, "report": str(REPORT.relative_to(ROOT))}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
