#!/usr/bin/env python3
"""Audit mobile Pulse feed density stays compact and touch-safe."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "static" / "css" / "pulse_desktop_feed.css"


def require(condition, message, details=""):
    if not condition:
        raise AssertionError(f"{message}{': ' + details if details else ''}")
    print(f"ok - {message}")


def main():
    css = CSS.read_text()
    require("@media (max-width: 1023px)" in css, "mobile/tablet feed density breakpoint exists")
    require("@media (max-width: 768px)" in css, "phone-specific Pulse layout breakpoint exists")
    require("max-width: 100vw !important" in css and "overflow-x: hidden !important" in css, "phone layout blocks horizontal overflow")
    require("max-width: calc(100vw - 16px) !important" in css, "mobile cards cannot exceed viewport width")
    require(".mobile-topbar" in css and "min-height: 48px !important" in css, "mobile header is compact")
    require(".mobile-brand img" in css and "width: 26px !important" in css, "mobile brand area is reduced")
    require(".hero h1" in css and "clamp(22px, 7.2vw, 30px)" in css, "mobile hero title is capped")
    require(".composer.card" in css and "padding: 13px !important" in css, "mobile composer avoids giant vertical padding")
    require(".pulse-publisher-card .smart-composer-bar textarea" in css and "min-height: 44px !important" in css, "phone composer textarea is compact")
    require(".composer-primary-actions" in css and "overflow-x: auto" in css, "mobile action toolbar can scroll instead of stacking tall")
    require(".pulse-status-strip" in css and "overflow-x: auto !important" in css, "status cards scroll horizontally on mobile")
    require(".pulse-status-card" in css and "flex: 0 0 112px !important" in css and "max-height: 148px !important" in css, "status cards are compact")
    require(".pulse-status-home-preview .pulse-status-card-media" in css and "object-fit: cover" in css, "status rail media fills mobile story cards")
    require("data-live-now-hub" not in (ROOT / "bot.py").read_text().split("def pulse_page_html", 1)[-1].split("@webhook_app.route(\"/pulse\"", 1)[0], "middle Home live hub is not injected into mobile feed")
    require(".post.card" in css and "contain-intrinsic-size: 280px !important" in css, "phone post cards reserve compact height")
    require("-webkit-line-clamp: 4 !important" in css, "mobile long text is clamped aggressively")
    require(".post h2" in css and "-webkit-line-clamp: 2" in css, "mobile post titles are clamped")
    require(".reaction-pill" in css and "min-height: 28px !important" in css, "mobile reactions are compact")
    require(".quick-action" in css and "min-height: 30px !important" in css, "mobile actions remain touch-safe but compact")
    require(".pulse-fab" in css and "width: 40px !important" in css and "bottom: calc(76px + env(safe-area-inset-bottom)) !important" in css, "floating create button is smaller and above bottom nav")
    require("width: 100% !important" in css and "max-width: 100% !important" in css, "mobile cards stay edge-to-edge without horizontal scroll")
    print("mobile feed density audit ok")


if __name__ == "__main__":
    main()
