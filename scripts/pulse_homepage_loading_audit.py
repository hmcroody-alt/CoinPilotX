#!/usr/bin/env python3
"""Fail if the authenticated PulseSoc homepage renders blank or broken assets."""

from __future__ import annotations

import re
import sys
from datetime import UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


FAILURES: list[str] = []
STATIC_REF_RE = re.compile(r"""(?:src|href)=["'](?P<url>/static/[^"']+)["']""")


def require(condition: bool, label: str, details: str = "") -> None:
    if condition:
        print(f"PASS {label}")
    else:
        print(f"FAIL {label}{': ' + details if details else ''}")
        FAILURES.append(label)


def ensure_user() -> int:
    user_id = 94061301
    now = bot.datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete, alerts_enabled)
        VALUES (?, ?, ?, ?, ?, 1, 1)
        """,
        (user_id, "pulse_home_loading_audit", "Pulse Home Loading Audit", "pulse-home-loading-audit@example.test", now),
    )
    conn.commit()
    conn.close()
    return user_id


def same_origin_static_refs(html: str) -> list[str]:
    urls: list[str] = []
    for match in STATIC_REF_RE.finditer(html):
        url = match.group("url")
        if url not in urls:
            urls.append(url)
    return urls


def main() -> int:
    bot.init_db()
    user_id = ensure_user()
    client = bot.webhook_app.test_client()

    anonymous = client.get("/pulse", follow_redirects=False)
    require(anonymous.status_code in {302, 303}, "anonymous /pulse redirects to login", str(anonymous.status_code))
    require("/login" in (anonymous.headers.get("Location") or ""), "anonymous redirect points at login")

    with client.session_transaction() as session:
        session["account_user_id"] = user_id

    response = client.get("/pulse", headers={"User-Agent": "PulseSocHomepageAudit/1.0"})
    html = response.get_data(as_text=True)
    require(response.status_code == 200, "authenticated /pulse returns HTTP 200", html[:240])
    cache_control = response.headers.get("Cache-Control", "")
    require("no-store" in cache_control and "max-age=0" in cache_control, "/pulse HTML is not cacheable", cache_control)
    require(response.headers.get("Pragma") == "no-cache", "/pulse HTML sends legacy no-cache header")
    require("<!doctype html>" in html.lower(), "/pulse returns valid HTML document")
    require("</body>" in html.lower() and "</html>" in html.lower(), "/pulse HTML is complete")
    require(len(html) > 20000, "/pulse HTML is not blank", f"length={len(html)}")
    require("PulseSoc" in html and "pulse_home_core.js" in html, "/pulse includes the compact core controller")
    require("Traceback" not in html and "Internal Server Error" not in html, "/pulse contains no server traceback")
    require('data-pulse-boot-profile="core"' in html, "/pulse defaults to the core-safe boot profile")
    require("pulse_media_renderer.js" not in html, "core-safe /pulse suppresses the global media runtime")
    require("pulse_status_viewer.js" not in html, "core-safe /pulse suppresses the global status runtime")
    require("static/notifications.js" not in html, "core-safe /pulse suppresses the global notification runtime")
    require("data-pulse-shell-runtime" not in html, "core-safe /pulse suppresses the oversized inline controller")
    core_script = client.get("/static/js/pulse_home_core.js?v=pulse-home-core-20260614a")
    core_script_text = core_script.get_data(as_text=True)
    require(core_script.status_code == 200, "compact core controller loads")
    require("AbortController" in core_script_text and "data-retry-pulse-feed" in core_script_text, "compact core controller has timeout and retry protection")

    normal = client.get("/pulse?boot_profile=normal")
    normal_html = normal.get_data(as_text=True)
    require(normal.status_code == 200, "full diagnostic boot profile loads")
    require('data-pulse-boot-profile="normal"' in normal_html, "full diagnostic boot profile is labeled")
    require("global-media-ui-20260614a" in normal_html, "full profile references the current media renderer cache key")
    require("status-cinematic-hud-20260621a" in normal_html, "full profile references the current status viewer cache key")
    require('pulse_media_renderer.js?v=global-media-ui-20260614a" defer' in normal_html, "full profile defers the media runtime")
    require('pulse_status_viewer.js?v=status-cinematic-hud-20260621a" defer' in normal_html, "full profile defers the status runtime")
    require("pulseBootTask" in normal_html, "full profile retains the advanced controller")

    for profile, absent_asset in (
        ("status_off", "pulse_status_viewer.js"),
        ("media_off", "pulse_media_renderer.js"),
        ("notifications_off", "static/notifications.js"),
    ):
        profiled = client.get(f"/pulse?boot_profile={profile}")
        profiled_html = profiled.get_data(as_text=True)
        require(profiled.status_code == 200, f"diagnostic boot profile loads {profile}")
        require(f'data-pulse-boot-profile="{profile}"' in profiled_html, f"diagnostic boot profile is labeled {profile}")
        require(absent_asset not in profiled_html, f"diagnostic boot profile suppresses {absent_asset}")
    shell_only = client.get("/pulse?boot_profile=shell_only")
    shell_only_html = shell_only.get_data(as_text=True)
    require(shell_only.status_code == 200, "diagnostic shell-only profile loads")
    require('data-pulse-boot-profile="shell_only"' in shell_only_html, "diagnostic shell-only profile is labeled")
    require("data-pulse-shell-runtime" not in shell_only_html, "diagnostic shell-only profile suppresses inline runtime")

    refs = same_origin_static_refs(normal_html)
    require(bool(refs), "/pulse exposes static JS/CSS asset references")
    for url in refs:
        asset = client.get(url)
        body = asset.get_data()
        require(asset.status_code == 200, f"asset loads {url}", str(asset.status_code))
        require(len(body) > 0, f"asset is non-empty {url}")
        if "pulse_media_renderer.js" in url:
            text = body.decode("utf-8", errors="replace")
            require("HYDRATE_INITIAL_LIMIT" in text, "media renderer bounds initial hydration")
            require("processInChunks" in text, "media renderer chunks hydration work")
            require("DOMContentLoaded\", () => runIdle(() => hydrate(document)" in text, "media renderer defers global DOMContentLoaded hydration")
        if "pulse_status_viewer.js" in url:
            text = body.decode("utf-8", errors="replace")
            require("statusCloseHardened" in text, "status close hardening is idempotent")
            require("attributeFilter" not in text, "status viewer observer cannot self-trigger on style attributes")

    for worker_url in ("/sw.js", "/static/service-worker.js"):
        worker = client.get(worker_url)
        worker_text = worker.get_data(as_text=True)
        require(worker.status_code == 200, f"service worker loads {worker_url}", str(worker.status_code))
        require("coinpilotx-cache-v18-pulse-home-bandwidth" in worker_text, f"service worker cache version is current {worker_url}")
        require("no-store" in worker.headers.get("Cache-Control", ""), f"service worker is not cacheable {worker_url}")

    if FAILURES:
        print("\nFAILURES:")
        for failure in FAILURES:
            print(f"- {failure}")
        return 1
    print("pulse homepage loading audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
