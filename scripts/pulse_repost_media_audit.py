#!/usr/bin/env python3
"""Audit Reel/Pulse reposts preserve original media by reference."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"
FEED = ROOT / "services" / "pulse_feed_engine.py"


def require(condition, message, failures):
    if not condition:
        failures.append(message)


def main():
    bot = BOT.read_text(encoding="utf-8")
    feed = FEED.read_text(encoding="utf-8")
    failures = []

    for token in [
        "_repost_originals",
        "repost_of_post_id",
        '"original_post": repost_original',
        '"repost": {',
        "display_media = repost_original.get(\"media\")",
        "display_body = \"\\n\\n\".join",
    ]:
        require(token in feed, f"feed repost hydration missing: {token}", failures)

    for token in [
        '@webhook_app.route("/api/pulse/reels/<int:reel_id>/repost"',
        '@webhook_app.route("/api/pulse/posts/<int:post_id>/repost"',
        "pulse_reel_reposted",
        "post_reposted",
        "repost_owner_public_id = pulse_identity_for_user",
        "original_public_id = pulse_identity_for_user",
    ]:
        require(token in bot, f"repost route wiring missing: {token}", failures)

    require("media_ids_json" not in bot[bot.find("def api_pulse_reel_repost_by_id"):bot.find("@webhook_app.route(\"/api/pulse/reels/<int:reel_id>/share\"")], "reel repost should not copy media ids", failures)
    require("media_ids_json" not in bot[bot.find("def api_pulse_post_repost"):bot.find("@webhook_app.route(\"/api/pulse/posts/<int:post_id>/comments\"")], "post repost should not copy media ids", failures)

    if failures:
        print("Pulse repost media audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Pulse repost media audit passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
