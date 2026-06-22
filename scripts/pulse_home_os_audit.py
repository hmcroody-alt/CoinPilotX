#!/usr/bin/env python3
"""Audit the route-scoped PulseSoc futuristic Home OS presentation."""

from __future__ import annotations

import sys
from datetime import UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


FAILURES: list[str] = []


def require(condition: bool, label: str, details: str = "") -> None:
    if condition:
        print(f"PASS {label}")
    else:
        print(f"FAIL {label}{': ' + details if details else ''}")
        FAILURES.append(label)


def ensure_user() -> int:
    user_id = 94061320
    now = bot.datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete, alerts_enabled)
        VALUES (?, ?, ?, ?, ?, 1, 1)
        """,
        (user_id, "pulse_home_os_audit", "Pulse Home OS Audit", "pulse-home-os-audit@example.test", now),
    )
    conn.commit()
    conn.close()
    return user_id


def main() -> int:
    bot.init_db()
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    css_path = ROOT / "static/css/pulse_home_os.css"
    js_path = ROOT / "static/js/pulse_environment_engine.js"
    require(css_path.exists(), "Home OS stylesheet exists")
    require(js_path.exists(), "Galactic city runtime exists")
    css = css_path.read_text(encoding="utf-8")
    js = js_path.read_text(encoding="utf-8")

    for token in [
        "pulse_home_os.css?v=pulse-home-os-20260621a",
        "pulse_environment_engine.js?v=galactic-city-20260621a",
        "request.path == '/pulse'",
        "pulse-network-feature",
        "home-intel-overview",
        "home-intel-trending",
        "home-intel-live",
        "home-intel-safety",
    ]:
        require(token in source, f"Home shell contains {token}")

    for token in [
        "body.pulse-home-os",
        ".pulse-home-os .pulse-environment-engine",
        ".pulse-home-os .pulse-city-district-left",
        ".pulse-home-os .pulse-city-district-right",
        ".pulse-home-os .pulse-city-vehicle",
        ".pulse-home-os .pulse-city-billboard",
        "@keyframes pulseCityVehicle",
        ".pulse-home-os .pulse-desktop-layout",
        "body.pulse-home-os .pulse-desktop-center .pulse-home-hero.hero.card",
        "display: none !important",
        ".pulse-home-os .pulse-network-globe-card",
        "height: 342px",
        ".pulse-home-os .pulse-action-card-grid",
        ".pulse-home-os .feed > .post-card-modern",
        "@media (max-width: 1380px)",
        "body.pulse-home-os .pulse-desktop-topbar",
        "grid-template-columns: minmax(142px, 190px) minmax(0, 1fr) minmax(160px, 220px) !important",
        ".pulse-home-os .pulse-desktop-left {\n    display: none;",
        "grid-template-columns: minmax(0, 1fr) minmax(260px, 290px)",
        "@media (max-width: 1180px)",
        "@media (max-width: 900px)",
        "@media (max-width: 480px)",
        "@media (prefers-reduced-motion: reduce)",
        "max-width: 100vw",
    ]:
        require(token in css, f"Home OS CSS contains {token}")

    for token in [
        "data-pulse-environment",
        "pulse-city-district-left",
        "pulse-city-district-right",
        "pulse-city-vehicle",
        "pulse-city-billboard",
        "visibilitychange",
        "prefers-reduced-motion: reduce",
        "navigator.connection",
    ]:
        require(token in js, f"Galactic city runtime contains {token}")

    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = ensure_user()

    home = client.get("/pulse?boot_profile=core")
    home_html = home.get_data(as_text=True)
    require(home.status_code == 200, "Home route loads", str(home.status_code))
    require('class="pulse-home-os"' in home_html, "Home route receives Home OS scope")
    require("pulse_home_os.css?v=pulse-home-os-20260621a" in home_html, "Home loads cache-busted Home OS CSS")
    require("pulse_environment_engine.js?v=galactic-city-20260621a" in home_html, "Home loads cache-busted galactic city runtime")
    for token in ["/pulse/live", "/scam-shield", "/pulse/premium/intelligence", "id=\"pulseComposer\"", "id=\"feed\""]:
        require(token in home_html, f"Home workflow remains wired: {token}")

    trending = client.get("/pulse/trending?boot_profile=core")
    trending_html = trending.get_data(as_text=True)
    require(trending.status_code == 200, "Trending feed route loads", str(trending.status_code))
    require('class=""' in trending_html and 'class="pulse-home-os"' not in trending_html, "Home OS scope does not leak to Trending")

    if FAILURES:
        print(f"pulse home OS audit failed: {len(FAILURES)} issue(s)")
        return 1
    print("pulse home OS audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
