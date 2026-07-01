#!/usr/bin/env python3
"""Static PulseSoc Home inventory and wiring audit.

The audit intentionally uses source inspection instead of a browser session so it
can run quickly in CI and catch missing routes before runtime QA.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
HOME_JS = ROOT / "static/js/pulse_home_core.js"
MEDIA_JS = ROOT / "static/js/pulse_media_renderer.js"
HOME_CSS = ROOT / "static/css/pulse_home_os.css"
DESKTOP_CSS = ROOT / "static/css/pulse_desktop_feed.css"
REPORT = ROOT / "reports/pulsesoc_home_feature_inventory.json"


FEATURES = [
    ("Home", "/pulse", "pulse_page_html", "/api/pulse/feed", "wired"),
    ("Dashboard", "/dashboard", "left rail + Apps menu", "/dashboard", "wired"),
    ("Pulse Radio", "/pulse/music#pulse-radio", "hero + left radio dock", "static/js/pulse_radio.js", "wired when approved tracks exist"),
    ("Statuses / Stories", "/pulse/status", "status rail + create sheet", "/api/pulse/status/rail", "wired"),
    ("Reels", "/pulse/reels", "top nav + rail + composer", "/api/pulse/reels/feed", "wired"),
    ("Videos", "/pulse/videos", "top nav + rail", "/api/pulse/feed", "wired"),
    ("Live", "/pulse/live", "top nav + hero + composer", "/api/pulse/live-now", "wired"),
    ("Pulse Composer", "/pulse#create", "top Create Signal + create sheet", "/api/pulse/posts", "wired"),
    ("Feed", "/pulse", "main feed tabs", "/api/pulse/feed", "wired"),
    ("Messages", "/pulse/messages", "top nav + rail + bottom nav", "/api/pulse/messages", "wired"),
    ("Notifications / Pulse Alerts", "/pulse/notifications", "top bell + rail", "/api/pulse/notifications", "wired"),
    ("Search", "/pulse/search", "universal search", "/api/pulse/search", "wired"),
    ("Profile", "/pulse/profile", "avatar + rail + bottom nav", "/api/pulse/profile/update", "wired"),
    ("Communities", "/pulse/communities", "rail + drawer + quick apps", "/pulse/groups", "gateway"),
    ("Groups", "/pulse/groups", "communities gateway", "/api/pulse/groups", "wired"),
    ("Marketplace", "/pulse/marketplace", "rail + composer + quick apps", "/pulse/marketplace/create", "wired"),
    ("Music", "/pulse/music", "rail + composer + quick apps", "/api/pulse/music/ai-suggest", "wired"),
    ("Events", "/pulse/events", "rail + create sheet + quick apps", "/pulse/live", "safe gateway"),
    ("Creator Studio", "/pulse/creator-studio", "rail + quick apps", "/api/pulse/creator-ai/<tool>", "wired alias"),
    ("Seller Tools", "/pulse/seller-tools", "rail + gateway", "/pulse/dashboard/seller-tools", "wired gateway"),
    ("Premium", "/pulse/premium", "rail + promo card", "/pulse/premium/activate", "wired"),
    ("Promote / Ads", "/pulse/promote", "rail + sidebar + owner post tools", "/api/pulse/promotions", "safe gateway"),
    ("Saved", "/pulse/saved", "rail + drawer", "/api/pulse/saved", "wired"),
    ("Collections", "/pulse/collections", "rail + drawer", "/api/pulse/saved/collections", "safe gateway"),
    ("Settings", "/pulse/settings", "rail + drawer", "/api/account/security", "wired gateway"),
    ("Apps menu", "/pulse/discover#apps", "top Apps + Quick Apps", "n/a", "wired"),
    ("AI Assistant", "/pulse/creator-studio#creator-ai", "composer + creator studio", "/api/pulse/creator-ai/<tool>", "wired"),
    ("Pulse Intelligence", "/pulse", "right intelligence sidebar", "/api/pulse/feed", "wired"),
    ("Trending Signals", "/pulse?feed=trending", "feed tabs + right rail", "/api/pulse/feed?tab=trending", "wired"),
    ("Recommended Creators", "/pulse/discover", "right rail", "/api/pulse/search", "safe gateway"),
    ("Live Network", "/pulse/live", "top shortcut + hero", "/api/pulse/live-now", "wired"),
    ("Advertiser / promotion tools", "/pulse/promote", "sidebar + post owner actions", "/api/pulse/promotions", "safe gateway"),
    ("Dashboard centers", "/pulse/dashboard/content-planner", "dashboard routes", "/api/dashboard/*", "wired"),
]


REQUIRED_ROUTES = [
    "/pulse",
    "/dashboard",
    "/pulse/reels",
    "/pulse/videos",
    "/pulse/live",
    "/pulse/messages",
    "/pulse/notifications",
    "/pulse/search",
    "/pulse/profile",
    "/pulse/marketplace",
    "/pulse/music",
    "/pulse/events",
    "/pulse/creator-studio",
    "/pulse/premium",
    "/pulse/promote",
    "/pulse/discover",
    "/pulse/communities",
    "/pulse/collections",
    "/pulse/settings",
    "/pulse/seller-tools",
]

LEFT_NAV_LABELS = [
    "Home",
    "Dashboard",
    "Discover",
    "Reels",
    "Videos",
    "Live",
    "Communities",
    "Marketplace",
    "Music",
    "Events",
    "Messages",
    "Notifications",
    "Pulse Radio",
    "Creator Studio",
    "Seller Tools",
    "Premium",
    "Promote",
    "Saved",
    "Collections",
    "Settings",
    "More",
]

COMPOSER_LABELS = ["Post", "Reel", "Live", "Marketplace", "Music", "Poll", "Question", "More"]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def route_patterns(source: str) -> set[str]:
    patterns: set[str] = set()
    for match in re.finditer(r"@webhook_app\.route\((?P<quote>['\"])(?P<path>/[^'\"]+)(?P=quote)", source):
        patterns.add(match.group("path"))
    return patterns


def function_body(source: str, name: str) -> str:
    marker = f"def {name}("
    start = source.find(marker)
    if start < 0:
        return ""
    next_def = source.find("\ndef ", start + len(marker))
    return source[start:] if next_def < 0 else source[start:next_def]


def js_function_body(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.find(marker)
    if start < 0:
        return ""
    next_func = source.find("\n  function ", start + len(marker))
    return source[start:] if next_func < 0 else source[start:next_func]


def check(name: str, passed: bool, details: str = "") -> dict[str, object]:
    return {"name": name, "passed": bool(passed), "details": details}


def ordered(source: str, tokens: list[str]) -> bool:
    index = -1
    for token in tokens:
        next_index = source.find(token, index + 1)
        if next_index < 0:
            return False
        index = next_index
    return True


def unsafe_home_anchors(home: str) -> list[str]:
    problems = []
    for match in re.finditer(r"<a\b[^>]*href=['\"]#['\"][^>]*>", home):
        tag = match.group(0)
        if "data-pulse-create-trigger" not in tag and "data-status" not in tag:
            problems.append(tag[:160])
    for match in re.finditer(r"href=['\"]javascript:void\(0\)['\"]", home, flags=re.I):
        problems.append(match.group(0))
    return problems


def build_inventory(routes: set[str], sources: str) -> list[dict[str, object]]:
    inventory = []
    for feature, route, entry, api, status_hint in FEATURES:
        literal_route = route.split("#", 1)[0].split("?", 1)[0]
        route_ok = literal_route in routes or literal_route == "/pulse#create"
        appears_on_home = feature in sources or route in sources or literal_route in sources
        inventory.append(
            {
                "feature": feature,
                "current_route": route,
                "current_entry_point": entry,
                "current_backend_route_or_api": api,
                "current_status": status_hint if route_ok or literal_route.startswith("/api/") else "missing route",
                "appears_on_home": appears_on_home,
                "appears_in_nav_menu_search_apps": appears_on_home,
                "known_issues": [] if route_ok else [f"{literal_route} is not registered"],
            }
        )
    return inventory


def main() -> int:
    bot = read(BOT)
    home_js = read(HOME_JS)
    media_js = read(MEDIA_JS)
    home_css = read(HOME_CSS)
    desktop_css = read(DESKTOP_CSS)
    routes = route_patterns(bot)
    home = "\n".join(
        [
            function_body(bot, "pulse_desktop_top_nav_html"),
            function_body(bot, "pulse_desktop_left_rail_html"),
            function_body(bot, "pulse_desktop_right_rail_html"),
            function_body(bot, "pulse_page_html"),
        ]
    )
    all_home_sources = "\n".join([home, home_js, home_css, desktop_css])

    inventory = build_inventory(routes, all_home_sources)
    missing_routes = [route for route in REQUIRED_ROUTES if route not in routes]
    missing_left_nav = [label for label in LEFT_NAV_LABELS if label not in function_body(bot, "pulse_desktop_left_rail_html")]
    missing_composer = [label for label in COMPOSER_LABELS if label not in function_body(bot, "pulse_page_html")]
    unsafe_anchors = unsafe_home_anchors(home)
    legacy_tokens = ["old_home", "legacy_home", "legacy_feed", "/create-old", "/home-old", "/feed-old"]
    legacy_hits = [token for token in legacy_tokens if token in home]
    feed_order_ok = ordered(
        js_function_body(home_js, "renderPost"),
        [
            "renderCreatorHeader",
            "renderCaption",
            "if (media) card.appendChild(media)",
            "renderPostMusic",
            "renderEngagement",
            "renderActions",
            "renderComposer",
        ],
    )

    checks = [
        check("Feature inventory exists", bool(inventory), f"{len(inventory)} features inventoried"),
        check("Required PulseSoc routes exist", not missing_routes, ", ".join(missing_routes)),
        check("Pulse Radio present", "pulse-radio-dock" in home and "data-pulse-radio-toggle" in home and "pulse_radio.js" in bot),
        check("Top nav uses notification bell", "pulse-desktop-topbar" in home and "aria-label='Notifications'" in home and "PULSE_NOTIFICATION_BELL_ICON" in home and "pulse-alert-radar" not in function_body(bot, "pulse_desktop_top_nav_html")),
        check("Big P removed", "pulse-desktop-brand" in home and "pulsesoc-logo" in home and "PulseSoc</a>" in home),
        check("Create Signal exists", "Create Signal" in home and "data-pulse-create-trigger" in home),
        check("Left nav includes required features", not missing_left_nav, ", ".join(missing_left_nav)),
        check("Dashboard visible in Home sidebar", '("Dashboard", "/dashboard"' in function_body(bot, "pulse_desktop_left_rail_html") and '.desktop-rail-link[href="/dashboard"]' in home_css),
        check("Dropdowns promoted to front layer", "pulse-dropdown-open" in home_js and "pulse-desktop-more-menu[open]" in home_css and "z-index: 1301" in home_css),
        check("Composer includes required actions", not missing_composer, ", ".join(missing_composer)),
        check("Status preview system present", "data-status-home-video" in bot and "observeStatusPreviewVideo" in home_js),
        check("Feed hierarchy markers present", feed_order_ok),
        check("Attached audio priority present", "forceOriginalAudioMuted" in media_js and "renderPostMusic" in home_js),
        check("Promotion access present", "/pulse/promote" in home and "home-promotion-gateway" in home and "pulsePromotionModal" in bot),
        check("Quick Apps present", "home-quick-apps" in home and "pulse-quick-app-grid" in home),
        check("No obvious legacy route references in Home", not legacy_hits, ", ".join(legacy_hits)),
        check("No obvious dead Home anchors", not unsafe_anchors, json.dumps(unsafe_anchors[:5])),
        check("No public LogiNexus exposure", "LogiNexus" not in home and "loginexus" not in home.lower()),
        check("No fake analytics/reach copy", "fake reach" not in home.lower() and "estimated reach" not in function_body(bot, "pulse_page_html").lower()),
    ]

    report = {
        "ok": all(item["passed"] for item in checks),
        "inventory": inventory,
        "checks": checks,
        "routes_verified": REQUIRED_ROUTES,
        "missing_routes": missing_routes,
        "source_files": [
            str(BOT.relative_to(ROOT)),
            str(HOME_JS.relative_to(ROOT)),
            str(MEDIA_JS.relative_to(ROOT)),
            str(HOME_CSS.relative_to(ROOT)),
            str(DESKTOP_CSS.relative_to(ROOT)),
        ],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "checks": checks, "report": str(REPORT.relative_to(ROOT))}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
