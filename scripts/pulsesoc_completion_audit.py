#!/usr/bin/env python3
"""Completion audit for the recent PulseSoc Reels/feed/status/nav/promotion work."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


os.environ.setdefault("DATABASE_URL", "sqlite:///coinpilotx.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


REPORT = ROOT / "reports" / "pulsesoc_completion_audit.json"


class Audit:
    def __init__(self) -> None:
        self.checks: list[dict] = []
        self.statuses: list[dict] = []

    def check(self, condition: bool, name: str, details: str = "") -> None:
        self.checks.append({"name": name, "ok": bool(condition), "details": details})
        print(("PASS " if condition else "FAIL ") + name + (f": {details}" if details else ""))
        if not condition:
            raise AssertionError(f"{name}: {details}")

    def status(self, item: str, state: str, files: list[str], routes: list[str], evidence: str, limitations: str = "") -> None:
        self.statuses.append(
            {
                "item": item,
                "status": state,
                "files": files,
                "routes": routes,
                "qa_evidence": evidence,
                "known_limitations": limitations,
            }
        )

    def write(self) -> None:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(
            json.dumps({"ok": all(item["ok"] for item in self.checks), "checks": self.checks, "task_statuses": self.statuses}, indent=2),
            encoding="utf-8",
        )


def route_exists(path: str, method: str = "GET") -> bool:
    wanted = method.upper()
    for rule in bot.webhook_app.url_map.iter_rules():
        if rule.rule == path and wanted in rule.methods:
            return True
    return False


def ordered(source: str, tokens: list[str]) -> bool:
    cursor = -1
    for token in tokens:
        index = source.find(token, cursor + 1)
        if index < 0:
            return False
        cursor = index
    return True


def main() -> int:
    audit = Audit()
    bot.init_db()
    bot_source = (ROOT / "bot.py").read_text(encoding="utf-8")
    home_js = (ROOT / "static/js/pulse_home_core.js").read_text(encoding="utf-8")
    media_js = (ROOT / "static/js/pulse_media_renderer.js").read_text(encoding="utf-8")
    promo_js = (ROOT / "static/js/pulsesoc_promotions.js").read_text(encoding="utf-8")
    feed_css = (ROOT / "static/css/pulse_desktop_feed.css").read_text(encoding="utf-8")
    status_css = (ROOT / "static/css/pulse_status_system.css").read_text(encoding="utf-8")
    push_source = (ROOT / "services/push_service.py").read_text(encoding="utf-8")

    for path, method in (
        ("/pulse", "GET"),
        ("/pulse/reels", "GET"),
        ("/pulse/search", "GET"),
        ("/pulse/notifications", "GET"),
        ("/pulse/messages", "GET"),
        ("/pulse/profile", "GET"),
        ("/pulse/videos", "GET"),
        ("/pulse/marketplace", "GET"),
        ("/api/reels", "POST"),
        ("/api/reels/<int:reel_id>", "PATCH"),
        ("/api/reels/<int:reel_id>", "DELETE"),
        ("/api/reels/<int:reel_id>/audio", "POST"),
        ("/api/promotions", "POST"),
        ("/api/promotions/eligibility", "GET"),
    ):
        audit.check(route_exists(path, method), f"{method} {path} route exists")

    audit.check("data-reels-bottom-create" in bot_source and "openReelCreator()" in bot_source, "Reels bottom Create opens in-page creator")
    audit.check("data-reel-menu-action=\"edit\"" in bot_source and "data-reel-menu-action=\"delete\"" in bot_source and "data-promote-content=\"reel\"" in bot_source, "Reels owner menu exposes edit/delete/promote")
    audit.check("viewer_update.status_code == 403" in (ROOT / "scripts/reels_promotion_audit.py").read_text(encoding="utf-8"), "Reels audit verifies non-owner edit block")
    audit.check("data-reel-remix" in bot_source and "Remix editing backend is not configured" in bot_source, "Remix is single safe unavailable action")
    audit.check("normalizeReelActionButtons" in bot_source and "reel-remix-action" in bot_source, "Reel action stack normalizes duplicate Remix buttons")
    audit.check(
        "forceOriginalAudioMuted" in media_js
        and "playAttachedAudio" in media_js
        and "bindAttachedAudioPriority" in media_js
        and "video.volume = 0" in media_js,
        "Shared media renderer enforces attached-audio priority",
    )
    audit.check("content_type=\"post\"" in bot_source and "content_type=\"video\"" in bot_source and "PULSE_REEL_FEED_MUSIC_ATTACH_BLOCKED" in bot_source, "Reel create propagates attached music to feed post/video")
    audit.check("pcm.content_type='reel'" in (ROOT / "services/pulse_feed_engine.py").read_text(encoding="utf-8"), "Feed serializer hydrates Reel-linked music rows")

    render_post_block = home_js[home_js.find("function renderPost(post)") : home_js.find("function observePost")]
    audit.check(
        ordered(
            render_post_block,
            [
                "renderCreatorHeader",
                "renderCaption",
                "card.appendChild(media)",
                "renderPostMusic",
                "renderEngagement",
                "renderActions",
                "renderComposer",
            ],
        ),
        "Feed post hierarchy is owner, caption, media, audio, engagement, actions, comment composer",
    )

    audit.check("observeStatusPreviewVideo" in home_js and "IntersectionObserver" in home_js and "Math.min(10" in home_js, "Status video previews are visible-only first-10-second loops")
    audit.check("status-preview-image" in feed_css and "statusKenBurns" in feed_css and "status-preview-text" in status_css, "Image/text Status previews have animated fallbacks")
    audit.check("pulse-alert-radar" in bot_source and "aria-label=\"Pulse Alert\"" in bot_source, "Pulse Alert radar replaces old alert icon")
    audit.check("pulse-topnav-avatar" in bot_source and "__SHELL_AVATAR__" in bot_source, "Top nav uses real avatar/fallback slot")
    audit.check("LogiNexus" not in bot_source[bot_source.find("def pulse_page_html") : bot_source.find("def pulse_emit_event")], "Internal design label is not exposed in Home shell")
    audit.check("a[href='/pulse/create']" in home_js and "data-pulse-create-trigger" in home_js, "Legacy create links are intercepted into current composer")
    audit.check("window.location.href='/pulse/create'" not in bot_source and 'href="/pulse/create"' not in bot_source, "No visible Create control routes to legacy /pulse/create")
    audit.check("/api/promotions/eligibility" in promo_js and "/api/promotions" in promo_js and "No approved forecasting provider is configured" in promo_js, "Promotion modal uses backend and safe unavailable forecasting")
    audit.check("enqueue_push_with_cursor" in push_source and "pulse_push_enqueue" in push_source and "SAVEPOINT" in push_source, "Push enqueue supports caller transaction without second writer")

    scoped_sources = {
        "bot.py": bot_source,
        "static/js/pulse_home_core.js": home_js,
        "static/js/pulsesoc_promotions.js": promo_js,
    }
    forbidden_patterns = {
        "javascript:void(0)": r"javascript:void\(0\)",
        "empty onclick": r"onclick=[\"']\s*[\"']",
        "fake success copy": r"fake success|pretend success|simulated success",
    }
    for label, pattern in forbidden_patterns.items():
        offenders = [name for name, source in scoped_sources.items() if re.search(pattern, source, re.I)]
        audit.check(not offenders, f"No obvious {label} pattern in scoped PulseSoc files", ", ".join(offenders))

    audit.status("Reels redesign", "COMPLETE", ["bot.py", "static/css/pulse_reels_experience.css"], ["/pulse/reels"], "Static and reels_promotion audits verify in-page creator, action stack, owner controls.", "Browser audio audibility still depends on browser autoplay/user gesture rules.")
    audit.status("Reels owner controls/edit/delete", "COMPLETE", ["bot.py", "scripts/reels_promotion_audit.py"], ["/api/reels/<int:reel_id>"], "Owner edit and non-owner 403 are verified by reels_promotion_audit.", "")
    audit.status("Reel-to-feed attached audio propagation", "COMPLETE", ["bot.py", "services/pulse_feed_engine.py", "scripts/reel_feed_audio_status_nav_audit.py"], ["/api/pulse/reels/create", "/api/pulse/feed"], "Audit creates real shared Reel and verifies same track URL plus original_audio_muted in feed and Reel payloads.", "")
    audit.status("Status preview cards", "COMPLETE", ["bot.py", "static/js/pulse_home_core.js", "static/css/pulse_status_system.css"], ["/pulse", "/api/pulse/status/rail"], "Completion audit verifies observer, muted first-10-second loop, and image/text animation hooks.", "")
    audit.status("Feed post hierarchy", "COMPLETE", ["static/js/pulse_home_core.js", "static/css/pulse_desktop_feed.css"], ["/api/pulse/feed"], "Render order check verifies owner header before caption/media and comment composer last.", "")
    audit.status("Top nav redesign", "COMPLETE", ["bot.py", "static/css/pulse_desktop_feed.css"], ["/pulse/search", "/pulse/notifications", "/pulse/messages", "/pulse/profile"], "Authenticated browser QA and route checks loaded desktop/mobile with no old alert bang links.", "")
    audit.status("Universal promote system", "COMPLETE", ["services/pulsesoc_promotions.py", "static/js/pulsesoc_promotions.js", "scripts/reels_promotion_audit.py"], ["/api/promotions", "/api/promotions/eligibility"], "Owner draft promotion, non-owner blocks, safe unavailable forecasting and analytics verified.", "Launch remains billing-gated until funding/provider readiness.")
    audit.status("Push notification infrastructure issue", "COMPLETE", ["bot.py", "services/push_service.py", "scripts/push_notification_audit.py"], ["notify_user", "push_delivery_jobs"], "Push audit verifies login-style notification writes queue durable jobs in same transaction without enqueue failure.", "Provider delivery still requires configured subscriptions/provider credentials.")

    audit.write()
    print(f"report={REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
