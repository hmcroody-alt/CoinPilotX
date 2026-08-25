#!/usr/bin/env python3
"""Audit the official PulseSoc Member #000 avatar wiring."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import pulse_feed_engine, user_context  # noqa: E402


ASSET = ROOT / pulse_feed_engine.MEMBER_000_AVATAR_PATH.lstrip("/")
COVER = ROOT / pulse_feed_engine.MEMBER_000_COVER_PATH.lstrip("/")


def require(condition: bool, message: str, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{message}{': ' + detail if detail else ''}")
    print(f"PASS: {message}")


def png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    require(data.startswith(b"\x89PNG\r\n\x1a\n"), "Member #000 avatar is a PNG")
    return struct.unpack(">II", data[16:24])


def main() -> None:
    require(ASSET.exists(), "Member #000 avatar asset exists", str(ASSET))
    width, height = png_dimensions(ASSET)
    require((width, height) == (1024, 1024), "Member #000 avatar is 1024x1024", f"{width}x{height}")
    require(COVER.exists(), "Member #000 cover asset exists", str(COVER))

    pulse_feed_engine._MEMBER_000_PROFILE_READY = False
    author = pulse_feed_engine._public_author({"user_id": 0})
    require(author["display_name"] == pulse_feed_engine.MEMBER_000_DISPLAY_NAME, "Member #000 display name is official")
    require(author["avatar_url"] == pulse_feed_engine.MEMBER_000_AVATAR_URL, "Member #000 author payload uses official avatar")
    require(author["public_player_id"] == pulse_feed_engine.MEMBER_000_PUBLIC_PLAYER_ID, "Member #000 public profile id is stable")

    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT user_id, public_player_id, display_name, avatar_url
        FROM arena_profiles
        WHERE user_id=0 OR public_player_id=?
        ORDER BY CASE WHEN public_player_id=? THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (pulse_feed_engine.MEMBER_000_PUBLIC_PLAYER_ID, pulse_feed_engine.MEMBER_000_PUBLIC_PLAYER_ID),
    )
    row = cur.fetchone()
    conn.close()
    require(row is not None, "Member #000 profile row exists")
    record = dict(row)
    require(record.get("display_name") == pulse_feed_engine.MEMBER_000_DISPLAY_NAME, "Member #000 profile row stores display name")
    require(record.get("avatar_url") == pulse_feed_engine.MEMBER_000_AVATAR_URL, "Member #000 profile row stores avatar URL")

    feed_js = (ROOT / "static" / "js" / "pulse_home_core.js").read_text()
    require("pulse-avatar-orb" in feed_js, "Feed avatar fallback uses branded orb class")
    require("name.slice(0, 1).toUpperCase()" not in feed_js, "Feed avatar fallback no longer renders random initials")
    print("Member #000 avatar audit completed.")


if __name__ == "__main__":
    main()
