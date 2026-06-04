#!/usr/bin/env python3
"""Mobile regression audit for Pulse Communications 2.0 layout."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["PULSE_COMMUNICATIONS_V2_ENABLED"] = "true"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def expect(ok: bool, label: str, details: str = "") -> None:
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_user() -> int:
    bot.init_db()
    conn = bot.db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=? LIMIT 1", (983201,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (user_id, username, display_name, email, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, ?, 1)",
            (983201, "mobile_v2_audit", "Mobile V2 Audit", "mobile-v2-audit@example.test", bot.datetime.utcnow().isoformat(timespec="seconds")),
        )
    conn.commit()
    conn.close()
    return 983201


def main() -> None:
    css = (ROOT / "static/css/pulse_messages_v2.css").read_text(encoding="utf-8")
    js = (ROOT / "static/js/pulse_messages_v2.js").read_text(encoding="utf-8")
    html = (ROOT / "templates/pulse_messages_v2.html").read_text(encoding="utf-8")

    mobile_block_start = css.index("@media (max-width: 768px)")
    mobile_block = css[mobile_block_start:]
    for token in [
        'data-mobile-mode="list"',
        'data-mobile-mode="thread"',
        "height: 100dvh;",
        "position: sticky;",
        ".message-menu-trigger",
        ".comm-modal-panel",
    ]:
        expect(token in mobile_block, f"mobile CSS keeps {token}")
    expect("@media (min-width: 941px)" in css, "desktop changes are isolated behind min-width media query")
    expect("comm-intel" not in html and "Coming Soon" not in html and "Coming soon" not in html, "mobile page has no placeholder side panels")
    expect("data-open-new-chat" in html and "data-open-new-group" in html, "mobile page exposes chat creation")
    expect("@media (max-width: 940px)" in css, "tablet breakpoint remains present")
    expect("setInterval(" not in js, "mobile client has no repeated polling interval")

    user_id = ensure_user()
    client = bot.app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
    response = client.get("/pulse/messages-v2", headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"})
    body = response.get_data(as_text=True)
    expect(response.status_code == 200, "mobile messages-v2 route loads", f"status={response.status_code}")
    expect("data-comm-v2-enabled=\"true\"" in body, "mobile page receives v2 flag")
    expect("/static/css/pulse_messages_v2.css" in body and "/static/js/pulse_messages_v2.js" in body, "mobile page includes v2 assets")

    print("pulse communications v2 mobile regression audit ok")


if __name__ == "__main__":
    main()
