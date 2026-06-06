#!/usr/bin/env python3
"""Audit critical Pulse stabilization contracts."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"Missing {path}")
    return target.read_text(encoding="utf-8")


def require(text, needle, label, failures):
    if needle not in text:
        failures.append(f"Missing {label}: {needle}")


def main():
    failures = []
    bot = read("bot.py")
    media = read("static/js/pulse_media_renderer.js")
    status = read("static/js/pulse_status_viewer.js")
    messages_css = read("static/css/pulse_messages_v2.css")

    require(media, 'muted playsinline', "muted default video render", failures)
    require(media, 'playVisibleVideo(video, false)', "muted hover preview", failures)
    require(media, 'playVisibleVideo(vid, false)', "muted in-view preview", failures)
    require(media, 'pulse:media-sound-change', "shared sound preference event", failures)
    require(media, 'conn.row_factory = sqlite3.Row', "profile row factory should not be in media", [])
    require(status, 'window.PulseMediaRenderer.renderMedia', "status shared renderer", failures)
    require(status, 'autoplay muted playsinline', "status fallback muted autoplay", failures)
    require(bot, "conn.row_factory = sqlite3.Row\n    cur = conn.cursor()\n    now = datetime.utcnow().isoformat(timespec=\"seconds\")\n    cur.execute(\n        \"UPDATE users SET avatar_url=?", "avatar persistence row handling", failures)
    require(bot, "thumbnail_url_cache_busted", "avatar thumbnail cache busting", failures)
    require(bot, "video.defaultMuted=true;video.setAttribute('muted','');", "reels muted default", failures)
    if "@media(max-width:" not in messages_css and "@media (max-width:" not in messages_css:
        failures.append("Missing messages mobile media query.")

    if failures:
        print("Pulse stabilization audit FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Pulse stabilization audit passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
