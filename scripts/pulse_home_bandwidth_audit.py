#!/usr/bin/env python3
"""Guard Pulse Home against heavy first-load and oversized network UI regressions."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    bot = read("bot.py")
    home_js = read("static/js/pulse_home_core.js")
    home_css = read("static/css/pulse_home_os.css")
    sw = read("static/sw.js")
    service_worker = read("static/service-worker.js")

    for source_name, source in {
        "bot.py": bot,
        "static/js/pulse_home_core.js": home_js,
    }.items():
        require("feedPageLimit" in source or "pulseFeedPageLimit" in source, f"{source_name} must use adaptive feed limits")
        require("live-now?limit=3" in source, f"{source_name} must keep Pulse Network live refresh light")
        require("live-now?limit=6" not in source, f"{source_name} must not request six live cards on Home")

    require("feed-post-v3-media-overlay-20260629a" in bot, "Home core script version must be cache-busted")
    require("pulse-home-os-20260629-universal-dock" in bot, "Home CSS version must be cache-busted")
    require("setInterval(()=>{if(!document.hidden)pollLive()},6000)" not in bot, "Inline live polling must not run every six seconds")
    require("},25000);" not in bot, "Heartbeat interval must not use the old tight cadence")
    require("setInterval(()=>{if(!document.hidden&&!pulsePrefersReducedData)checkForNewPosts()},60000)" in bot, "New post polling must be throttled and data-saver aware")
    require("},60000);" in bot, "Heartbeat cadence should be one minute")

    require("Pulse Home bandwidth/layout guard" in home_css, "Home CSS must include compact layout guard")
    require("content-visibility: auto" in home_css, "Pulse Network module should use browser containment")
    require("min-height: 168px !important" in home_css, "Mobile Pulse Network card must stay compact")
    require("height: auto !important" in home_css, "Pulse Network card must override fixed oversized heights")
    require("prefers-reduced-data" in home_css, "CSS must respect reduced-data mode")
    require("scroll-snap-type: x proximity" in home_css, "Mobile composer mode row must remain reachable without overflow")
    require("min-width: 104px !important" in home_css, "Mobile composer mode buttons must use compact touch targets")

    for source_name, source in {
        "static/sw.js": sw,
        "static/service-worker.js": service_worker,
    }.items():
        require("coinplotx-cache-v20-pulse-offline-dashboard" in source, f"{source_name} must bump cache version")
        require("coinpilotx-cache-v17-command-center-assets" not in source, f"{source_name} must not keep old cache name")

    print("Pulse Home bandwidth audit passed.")


if __name__ == "__main__":
    main()
