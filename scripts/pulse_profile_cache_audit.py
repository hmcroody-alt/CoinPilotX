#!/usr/bin/env python3
"""Audit Pulse profile media cache refresh behavior."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"


def require(condition, message, failures):
    if not condition:
        failures.append(message)


def main():
    source = BOT.read_text(encoding="utf-8")
    failures = []

    for token in [
        "_profile_cache_busted_url",
        "avatar_url_cache_busted",
        "cover_url_cache_busted",
        "thumbnail_url_cache_busted",
        "cache:'no-store'",
        "SELECT display_name, username, avatar_url, avatar_thumbnail_url, cover_url, banner_url, updated_at FROM users",
        "SELECT display_name, username, avatar_url, avatar_filter, COALESCE(cover_url,banner_url,'') AS cover_url",
        "document.querySelectorAll('.mobile-topbar .avatar",
        "savedUrl=d.cover_url_cache_busted||d.cover_url",
        "savedUrl=d.avatar_url_cache_busted||d.avatar_url",
    ]:
        require(token in source, f"cache refresh token missing: {token}", failures)

    require("load_account_by_id(user[\"user_id\"])" in source, "profile/me must return fresh DB user", failures)
    require("load_account_by_id(user.get(\"user_id\"))" in source, "Pulse shell must load fresh DB user", failures)

    if failures:
        print("Pulse profile cache audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Pulse profile cache audit passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
