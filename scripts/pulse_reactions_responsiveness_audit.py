#!/usr/bin/env python3
"""Static contract audit for PulseSoc reaction responsiveness wiring."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(name: str, condition: bool, failures: list[str]) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"{status}: {name}")
    if not condition:
        failures.append(name)


def main() -> int:
    bot = read("bot.py")
    engine = read("services/pulse_feed_engine.py")
    feed_css = read("static/css/pulse_desktop_feed.css")
    messages = read("static/js/pulse_messages_v2.js")
    live = read("static/js/pulse_live_studio.js")
    live_runtime = read("static/js/pulse_live_studio_runtime.js")

    failures: list[str] = []
    require("shared reaction pop animation exists", "pulseReactionPop" in feed_css and "pulseReactionFloat" in feed_css, failures)
    require("reduced motion disables reaction float", "prefers-reduced-motion" in feed_css and ".pulse-reaction-float" in feed_css, failures)
    require("feed post reactions animate immediately", "animatePulseReaction(r,pulseReactionEmoji(r.dataset.react))" in bot, failures)
    require("feed post reactions rollback old counts", "oldCounts" in bot and "oldTotal" in bot, failures)
    require("status story reactions use optimistic capture handler", "data-status-story-react" in bot and "oldText=count?.textContent" in bot, failures)
    require("video detail reaction count is rendered", "data-video-like-count" in bot and "data-video-reaction-count" in bot, failures)
    require("video detail reaction rolls back", "oldCount=Number(like.dataset.videoReactionCount" in bot and "like.dataset.videoReactionCount=oldCount" in bot, failures)
    require("reel reactions block rapid duplicate taps", "button.dataset.busy='1'" in bot and "delete button.dataset.busy" in bot, failures)
    require("reel comment reactions optimistic", "data-reel-comment-like" in bot and "oldText=likeComment.textContent" in bot, failures)
    require("message reactions call authorized backend route", "fetch(`/api/pulse/messages/${id}/react`" in messages, failures)
    require("message reactions rollback local state", "state.messages[index] = previous" in messages, failures)
    require("message reaction summaries support backend object shape", "Object.entries(item.reactions || {})" in messages, failures)
    require("message reactions normalize backend emoji values", '"🔥": "fire"' in messages and "normalizeReaction(data.my_reaction" in messages, failures)
    require("live studio reactions animate before fetch", "renderReactionBurst(root, [{ emoji: reaction" in live and "const response = await fetch" in live, failures)
    require("live runtime reactions animate before fetch", "renderReactionBurst(root, [{ emoji: reaction" in live_runtime and "const response = await fetch" in live_runtime, failures)
    require("post reaction API remains protected", '@webhook_app.route("/api/pulse/posts/<int:post_id>/react"' in bot and "Login required." in bot, failures)
    require("backend accepts UI reaction set", all(f'"{name}"' in engine for name in ["like", "love", "wow", "rocket", "clap", "hundred", "target", "shield"]), failures)
    require("video reaction API remains protected", '@webhook_app.route("/api/pulse/videos/<int:video_id>/react"' in bot and "api_account_user()" in bot, failures)
    require("message reaction API checks conversation access", "pulse_conversation_participants" in bot and "message_id=? AND user_id=?" in bot, failures)

    if failures:
        print("\nReaction responsiveness audit failed:")
        for item in failures:
            print(f"- {item}")
        return 1
    print("\nReaction responsiveness audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
