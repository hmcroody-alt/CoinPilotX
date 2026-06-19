#!/usr/bin/env python3
"""Audit PulseSoc simplified navigation and search wiring contracts."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


BOT = ROOT / "bot.py"
CSS = ROOT / "static/css/pulse_desktop_feed.css"
HOME_JS = ROOT / "static/js/pulse_home_core.js"
SEARCH_BRIDGE_JS = ROOT / "static/js/pulse_search_bridge.js"


def expect(ok: bool, label: str, details: str = "") -> None:
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"PASS: {label}")


def main() -> None:
    source = BOT.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    home_js = HOME_JS.read_text(encoding="utf-8")
    search_bridge = SEARCH_BRIDGE_JS.read_text(encoding="utf-8")

    top_nav = source[source.find("def pulse_desktop_top_nav_html"):source.find("def pulse_desktop_left_rail_html")]
    expect('("Home", "/pulse")' in top_nav, "Desktop top nav keeps Home")
    expect('("Reels", "/pulse/reels")' in top_nav, "Desktop top nav keeps Reels")
    expect('("Videos", "/pulse/videos")' in top_nav, "Desktop top nav keeps Videos")
    expect('("Live", "/pulse/live")' in top_nav, "Desktop top nav keeps Live")
    expect('("Messages", "/pulse/messages")' in top_nav, "Desktop top nav keeps Messages")
    expect("pulse-create-strong" in top_nav and "/pulse/create" in top_nav, "Desktop top nav keeps strong Create")
    expect("pulse-desktop-more-menu" in top_nav and "pulse-desktop-more-panel" in top_nav, "Desktop top nav exposes Apps overflow")
    for secondary in ["Music", "Roast Battle", "Marketplace", "Creator Studio", "Portfolio", "Premium", "Profile", "Alerts", "Friends", "Groups", "Teachers", "Saved"]:
        expect(secondary in top_nav, f"Apps menu contains {secondary}")

    expect("grid-template-columns: minmax(190px, 260px) minmax(0, 1fr) minmax(310px, 430px)" in css, "Desktop topbar reserves search/Create space")
    expect(".pulse-desktop-more-panel" in css and "backdrop-filter: blur(20px)" in css, "Apps menu has sci-fi overflow styling")
    expect(".pulse-desktop-search:focus" in css and "box-shadow: 0 0 0 3px" in css, "Global search has focused glow state")

    expect('("Create", "/pulse/create", "＋")' in source, "Mobile bottom nav includes Create")
    expect('("Reels", "/pulse/reels", "▶")' in source, "Mobile bottom nav includes Reels")
    expect("grid-template-columns:repeat(5,minmax(0,1fr))" in source, "Mobile bottom nav uses five columns")
    expect(".pulse-fab{display:none!important}" in source, "Mobile duplicate floating create is hidden")

    search_route = source[source.find('@webhook_app.route("/api/pulse/search"'):source.find("def pulse_status_payload")]
    for group in ["posts", "creators", "videos", "reels", "statuses", "marketplace", "music", "groups", "rooms", "comments"]:
        expect(f'"{group}"' in search_route or f"'{group}'" in search_route, f"Global search includes {group}")
    expect("security_guard.rate_limited" in search_route, "Global search is rate limited")
    expect("pulse_search_users" in search_route and "allow_email=False" in search_route, "Creator search keeps emails masked")
    expect("COALESCE(v.visibility,'public')='public' OR v.owner_user_id=?" in search_route, "Video search respects private owner access")
    expect("COALESCE(visibility, 'public') = 'public'" in search_route, "Status search only exposes public statuses")
    expect("marketplace_listings" in search_route and "approval_status" in search_route, "Marketplace search only exposes approved listings")
    expect("music_service.search_tracks" in search_route, "Global search includes approved music service")

    for text in ["runCorePulseSearch", "debounceCorePulseSearch", "pulseSearchSave", "pulseSearchState.controller", "pulse-search-loading", "pulse-search-empty", "pulse-search-error"]:
        expect(text in home_js, f"Static Home core wires {text}")
    expect("videos" in home_js and "marketplace" in home_js and "music" in home_js, "Static global search renders new result groups")
    for text in ["window.__pulseSearchBridgeBound", "/api/pulse/search", "AbortController", "pulse-search-empty", "pulse-search-error"]:
        expect(text in search_bridge, f"Standalone global search bridge wires {text}")
    expect("pulse_search_bridge.js" in source, "Home shell loads standalone global search bridge")
    expect('action=\'/pulse/search\'' in source or 'action="/pulse/search"' in source, "Global search forms have server fallback action")
    expect('@webhook_app.route("/pulse/search"' in source, "Server-rendered PulseSoc search fallback route exists")
    expect("data-marketplace-search" in source and "/api/pulse/marketplace/search" in source, "Marketplace section search is wired")

    bot.init_db()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = 950781

    home = client.get("/pulse").get_data(as_text=True)
    expect("pulse-desktop-more-menu" in home, "Runtime Home renders Apps menu")
    expect(home.count("class='pulse-desktop-nav'") == 1, "Runtime Home renders one desktop nav")
    expect("nav-label'>Create</span>" in home and "nav-label'>Music</span>" not in home, "Runtime mobile nav is simplified")

    search = client.get("/api/pulse/search?q=pulse&limit=3")
    expect(search.status_code == 200, "Runtime global search endpoint succeeds")
    payload = search.get_json() or {}
    expect(payload.get("ok") is True, "Runtime global search returns ok")
    results = payload.get("results") or {}
    for group in ["posts", "creators", "videos", "reels", "statuses", "marketplace", "music", "groups", "rooms", "comments"]:
        expect(group in results, f"Runtime global search returns {group} bucket")
    serialized = str(payload).lower()
    expect("@" not in serialized or "email" not in serialized, "Runtime global search does not expose email fields")
    page = client.get("/pulse/search?q=zzzz-no-result-qa")
    page_html = page.get_data(as_text=True)
    expect(page.status_code == 200, "Runtime PulseSoc search page succeeds")
    expect("No PulseSoc results found" in page_html or "Search PulseSoc" in page_html, "Runtime PulseSoc search page renders fallback UX")

    marketplace = client.get("/api/pulse/marketplace/search?q=pulse&limit=3")
    expect(marketplace.status_code == 200, "Runtime marketplace search endpoint succeeds")
    expect((marketplace.get_json() or {}).get("ok") is True, "Runtime marketplace search returns ok")

    print("pulse navigation and search audit ok")


if __name__ == "__main__":
    main()
