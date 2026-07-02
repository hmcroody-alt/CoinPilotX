#!/usr/bin/env python3
"""Audit that PulseSoc Live watching resolves through Reels only."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "live_reels_only_watch_audit.json"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def record(results: list[dict], name: str, passed: bool, detail: str) -> None:
    results.append({"name": name, "passed": bool(passed), "detail": detail})


def main() -> int:
    results: list[dict] = []
    bot = read("bot.py")
    home_js = read("static/js/pulse_home_core.js")
    notifications_js = read("static/notifications.js")
    feed_engine = read("services/pulse_feed_engine.py")
    live_feed = read("services/live_feed_service.py")
    notification_system = read("services/pulsesoc_notification_system.py")

    record(
        results,
        "shared_open_live_in_reels_function",
        "window.openLiveInReels" in home_js
        and "window.openLiveInReels" in notifications_js
        and "/pulse/reels?live=" in home_js
        and "/pulse/reels?live=" in notifications_js,
        "Home and shared shell JS expose openLiveInReels(liveId).",
    )
    record(
        results,
        "public_live_watch_route_redirects",
        bool(re.search(r'def\s+pulse_live_room_page\(live_id\):\s*\n\s*return\s+redirect\(pulse_live_watch_url\(live_id\),\s*code=302\)', bot)),
        "/pulse/live/<id> redirects to the Reels live target.",
    )
    record(
        results,
        "watch_url_helper_exists",
        "def pulse_live_watch_url" in bot and 'return f"/pulse/reels?live={live_id}{suffix}"' in bot,
        "Server helper centralizes Live watch URLs.",
    )
    record(
        results,
        "reels_feed_accepts_live_id",
        "focus_live_id=safe_int(request.args.get(\"live\"), 0)" in bot and "focus_live_id=focus_live_id" in bot,
        "Reels feed API accepts live=<id> and prioritizes it.",
    )
    record(
        results,
        "reels_page_loads_live_query",
        "loadReels(params.get('tab')||(liveId?'live':'for_you'),{liveId})" in bot,
        "Reels page chooses the Live lane when opened with live=<id>.",
    )
    record(
        results,
        "home_live_cards_are_gateways",
        "pulse-live-gateway-card" in home_js and "Join Live in Reels" in home_js and "if (media && !isLiveGateway)" in home_js,
        "Home feed Live cards render a gateway and do not mount feed media players.",
    )
    record(
        results,
        "live_payloads_route_to_reels",
        "/pulse/reels?live=" in feed_engine and "/pulse/reels?live=" in live_feed,
        "Feed/live service payloads use Reels watch URLs.",
    )
    record(
        results,
        "live_notifications_route_to_reels",
        "/pulse/reels?live=" in notifications_js and "/pulse/reels?live=" in notification_system,
        "Notification fallback and central helpers route Live alerts to Reels.",
    )
    record(
        results,
        "live_start_notifications_route_to_reels",
        "live_watch_url = pulse_live_watch_url(live_id)" in bot and '"mobile_deep_link": f"pulse://reels?live={live_id}"' in bot,
        "Live start follower notifications use the Reels URL and mobile deep link.",
    )

    disallowed_patterns = [
        (r"href=['\"]/pulse/live/\{", "templated href to old public live watch route"),
        (r"href=['\"]/pulse/live/\d", "literal href to old public live watch route"),
        (r"`/pulse/live/\$\{", "JS template to old public live watch route"),
        (r"f\"/pulse/live/\{live_id\}\"", "Python f-string to old public live watch route"),
        (r"pulse://live/\{live_id\}", "old native live deep link"),
    ]
    haystack = "\n".join([bot, home_js, notifications_js, feed_engine, live_feed, notification_system])
    violations = []
    for pattern, label in disallowed_patterns:
        if re.search(pattern, haystack):
            violations.append(label)
    record(
        results,
        "no_old_live_watch_links",
        not violations,
        "No old public watch links remain." if not violations else "; ".join(sorted(set(violations))),
    )
    record(
        results,
        "no_fake_join_live_links",
        not re.search(r"Join Live[^\\n]{0,120}(href=['\"]#|javascript:void\(0\))", haystack, re.IGNORECASE),
        "Join Live links do not use # or javascript:void(0).",
    )

    failed = [item for item in results if not item["passed"]]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({"ok": not failed, "checks": results}, indent=2) + "\n", encoding="utf-8")
    if failed:
        print("Live Reels-only watch audit failed:")
        for item in failed:
            print(f"- {item['name']}: {item['detail']}")
        return 1
    print(f"Live Reels-only watch audit passed ({len(results)} checks).")
    print(f"Report: {REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
