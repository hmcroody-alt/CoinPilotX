#!/usr/bin/env python3
"""Audit the PulseSoc Home Pulse Network globe contract."""

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
    user_id = 94061319
    now = bot.datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete, alerts_enabled)
        VALUES (?, ?, ?, ?, ?, 1, 1)
        """,
        (user_id, "pulse_network_globe_audit", "Pulse Network Globe Audit", "pulse-network-globe-audit@example.test", now),
    )
    conn.commit()
    conn.close()
    return user_id


def main() -> int:
    bot.init_db()
    css = (ROOT / "static/css/pulse_status_system.css").read_text(encoding="utf-8")
    core = (ROOT / "static/js/pulse_home_core.js").read_text(encoding="utf-8")
    source = (ROOT / "bot.py").read_text(encoding="utf-8")

    for token in [
        "pulse_network_globe_html",
        "data-pulse-network-globe",
        "data-network-action='detail'",
        "href='/pulse/live'",
        "href='/scam-shield'",
        "pulse-network-globe-20260619a",
    ]:
        require(token in source, f"server renders globe contract token {token}")

    for token in [
        "@keyframes pulseNetworkRotate",
        "90s linear infinite",
        "pulseNetworkScan",
        "prefers-reduced-motion: reduce",
        "animation-play-state: paused",
        "contain: layout paint",
        "calc(100vw - 34px)",
    ]:
        require(token in css, f"globe CSS includes {token}")

    for token in [
        "pulseNetworkUpdate",
        "pulseNetworkPaused",
        "visibilitychange",
        "prefers-reduced-motion: reduce",
        "/api/pulse/live-now?limit=6",
        "data.discovery_signal",
        "data.intelligence",
    ]:
        require(token in core, f"compact Home runtime wires {token}")

    globe_source = "\n".join(line for line in (source + "\n" + core + "\n" + css).splitlines() if "pulse-network" in line or "pulseNetwork" in line)
    for forbidden in ["latitude", "longitude", "geolocation", "navigator.geolocation", "exact_location"]:
        require(forbidden not in globe_source, f"globe does not expose {forbidden}")

    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = ensure_user()

    response = client.get("/pulse", headers={"User-Agent": "PulseNetworkGlobeAudit/1.0"})
    html = response.get_data(as_text=True)
    require(response.status_code == 200, "authenticated Home loads", str(response.status_code))
    for token in [
        "pulse-network-globe-card",
        "Pulse Network",
        "Aggregate community signals only",
        "/pulse/live",
        "/scam-shield",
        "/pulse/premium/intelligence",
    ]:
        require(token in html, f"Home contains globe token {token}")

    live_now = client.get("/api/pulse/live-now?limit=2")
    require(live_now.status_code == 200, "live-now aggregate endpoint is reachable for globe")

    if FAILURES:
        print(f"pulse network globe audit failed: {len(FAILURES)} issue(s)")
        return 1
    print("pulse network globe audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
