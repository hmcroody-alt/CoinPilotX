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
    require("<!doctype html>" in html.lower(), "/pulse returns valid HTML document")
    require("</body>" in html.lower() and "</html>" in html.lower(), "/pulse HTML is complete")
    require(len(html) > 20000, "/pulse HTML is not blank", f"length={len(html)}")
    require("PulseSoc" in html and "pulse_media_renderer.js" in html, "/pulse includes PulseSoc shell scripts")
    require("Traceback" not in html and "Internal Server Error" not in html, "/pulse contains no server traceback")
    require("global-media-ui-20260613c" in html, "/pulse references the current media renderer cache key")

    refs = same_origin_static_refs(html)
    require(bool(refs), "/pulse exposes static JS/CSS asset references")
    for url in refs:
        asset = client.get(url)
        body = asset.get_data()
        require(asset.status_code == 200, f"asset loads {url}", str(asset.status_code))
        require(len(body) > 0, f"asset is non-empty {url}")

    if FAILURES:
        print("\nFAILURES:")
        for failure in FAILURES:
            print(f"- {failure}")
        return 1
    print("pulse homepage loading audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
