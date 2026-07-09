#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


CHECKS = [
    (
        "backend post hide route",
        ROOT / "bot.py",
        [
            '@webhook_app.route("/api/pulse/posts/<int:post_id>/hide", methods=["POST"])',
            "pulse_feed_engine.hide_post",
            "notify_user(",
            '"pulse_post_hidden"',
        ],
    ),
    (
        "backend user mute route",
        ROOT / "bot.py",
        [
            '@webhook_app.route("/api/pulse/users/mute", methods=["POST"])',
            "pulse_feed_engine.mute_user",
            "notify_user(",
            '"pulse_user_muted"',
        ],
    ),
    (
        "server-authoritative hide and mute tables",
        ROOT / "services" / "pulse_feed_engine.py",
        [
            "CREATE TABLE IF NOT EXISTS pulse_post_hides",
            "CREATE TABLE IF NOT EXISTS pulse_user_mutes",
            "NOT EXISTS (SELECT 1 FROM pulse_post_hides",
            "NOT EXISTS (SELECT 1 FROM pulse_user_mutes",
            "def hide_post(",
            "def mute_user(",
        ],
    ),
    (
        "native Home hide and mute mutations",
        ROOT / "mobile-native" / "src" / "screens" / "HomeScreen.tsx",
        [
            "hidePost(post.id)",
            "mutePostAuthor(post)",
            'event_type: "pulse_post_hidden"',
            'event_type: "pulse_user_muted"',
            "onHide={handleHide}",
            "onMute={handleMute}",
        ],
    ),
    (
        "native feed API hide and mute wrappers",
        ROOT / "mobile-native" / "src" / "api" / "feed.ts",
        [
            "export async function hidePost",
            "`/api/pulse/posts/${postId}/hide`",
            "export async function mutePostAuthor",
            '"/api/pulse/users/mute"',
        ],
    ),
    (
        "accessible comment submit path",
        ROOT / "mobile-native" / "src" / "screens" / "PostDetailScreen.tsx",
        [
            'testID="post-detail-comment-input"',
            'testID="post-detail-submit-comment"',
            'accessibilityRole="button"',
            'accessibilityLabel="Submit comment"',
            "accessibilityState={{ disabled:",
        ],
    ),
]


def main() -> int:
    failures: list[str] = []
    for label, path, needles in CHECKS:
        text = path.read_text(encoding="utf-8")
        missing = [needle for needle in needles if needle not in text]
        if missing:
            failures.append(f"{label}: missing {', '.join(missing)}")
    if failures:
        print("PulseSoc native Home release blocker audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PulseSoc native Home release blocker audit passed.")
    print(f"Validated {len(CHECKS)} release-blocker hardening areas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
