#!/usr/bin/env python3
"""Verify PulseSoc public surfaces do not expose admin navigation."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


ADMIN_MARKERS = (
    "/admin/global-command",
    "Admin</span>",
    "Global Command",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def pulse_html(session_values: dict[str, int | str]) -> tuple[int, str]:
    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session.update(session_values)
    response = client.get("/pulse")
    return response.status_code, response.get_data(as_text=True)


def main() -> int:
    status, html = pulse_html({"account_user_id": 1})
    require(status == 200, f"/pulse did not render for normal user: {status}")
    for marker in ADMIN_MARKERS:
        require(marker not in html, f"Normal PulseSoc Home leaked admin marker: {marker}")

    status, stale_html = pulse_html({"account_user_id": 1, "admin_user_id": 999999999})
    require(status == 200, f"/pulse did not render for stale admin session: {status}")
    for marker in ADMIN_MARKERS:
        require(marker not in stale_html, f"Stale admin session leaked admin marker: {marker}")

    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = 1
    admin_response = client.get("/admin/global-command")
    require(admin_response.status_code in {302, 401, 403}, f"Admin route was not protected: {admin_response.status_code}")

    css = (ROOT / "static" / "css" / "pulse_home_os.css").read_text(encoding="utf-8")
    require('desktop-rail-link[href="/admin/global-command"]' not in css, "Home CSS still whitelists admin nav.")

    print("PulseSoc admin visibility audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
