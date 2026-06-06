#!/usr/bin/env python3
"""Audit permanent Pulse avatar and cover persistence wiring."""

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
        '@webhook_app.route("/api/pulse/profile/avatar"',
        '@webhook_app.route("/api/pulse/profile/cover"',
        "UPDATE users SET avatar_url=?, avatar_thumbnail_url=?",
        "UPDATE users SET cover_url=?, banner_url=?",
        "SELECT avatar_url, avatar_thumbnail_url FROM users",
        "SELECT cover_url, banner_url, cover_position FROM users",
        "Profile picture save did not persist",
        "Cover photo save did not persist",
        "avatar_url_cache_busted",
        "cover_url_cache_busted",
        "pulse_mobile_user_payload",
        '"cover_url": user.get("cover_url") or user.get("banner_url") or ""',
        "shell_avatar_script",
        "load_account_by_id(user.get(\"user_id\"))",
    ]:
        require(token in source, f"missing persistence token: {token}", failures)

    require(source.count("cur.rowcount != 1") >= 2, "avatar and cover saves must verify DB rowcount", failures)
    require("_profile_cache_busted_url" in source, "profile cache-busting helper is missing", failures)
    require("COALESCE(cover_url,banner_url,'') AS cover_url" in source, "profile edit page must hydrate persisted cover", failures)

    if failures:
        print("Profile avatar/cover persistence audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Profile avatar/cover persistence audit passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
