#!/usr/bin/env python3
"""Static behavior gate for the final native Reels closure milestone."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def main() -> int:
    failures: list[str] = []
    screen = read("mobile-native/src/screens/ReelsScreen.tsx")
    api = read("mobile-native/src/api/reels.ts")
    feed_api = read("mobile-native/src/api/feed.ts")
    offline = read("mobile-native/src/core/reelsOfflinePolicy.ts")
    audio = read("mobile-native/src/core/reelsAudioSession.ts")
    sync = read("mobile-native/src/core/eventSync.ts")
    backend = read("bot.py")

    def require(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    require("getReelComments" in api and "countCommentTree" in api, "canonical nested comment loading is missing")
    require("parent_comment_id" in feed_api and "replies: normalizeComments(item.replies" in feed_api, "reply tree normalization is missing")
    require("loadReelCommentDraft" in api and "saveReelCommentDraft" in api and "clearReelCommentDraft" in api, "device-local draft persistence is incomplete")
    require("insertReply" in screen and "CommentThread" in screen, "nested reply rendering and insertion are missing")
    require("beginEditComment" in screen and "submitEditComment" in screen, "comment author editing is missing")
    require("Only the comment author can edit this comment." in backend, "server-side edit ownership guard is missing")
    require("Only the comment author or Reel owner can delete this comment." in backend, "server-side delete/moderation guard is missing")
    require("pulse_reel_comment_created" in backend and "pulse_reel_comment_updated" in backend and "pulse_reel_comment_deleted" in backend, "canonical comment events are incomplete")
    require("pulse_reel_comment_reaction_updated" in backend, "canonical comment reaction event is missing")
    require('registerSyncInvalidation("reels"' in screen and "refreshComments(commentReel)" in screen, "open comments do not refresh after Reels invalidation")
    require("activeReelId.current" in screen, "active Reel identity is not preserved across canonical refresh")
    require("seen.has(id)" in sync, "duplicate event suppression is missing")

    for action in (
        "reaction", "replace_reaction", "remove_reaction", "save", "unsave",
        "follow", "unfollow", "report", "block", "delete_own_comment", "delete_own_reel", "join_live",
    ):
        require(f'{action}: "ONLINE REQUIRED"' in offline, f"offline policy missing for {action}")
    require('comment: "LOCAL DRAFT ONLY"' in offline and 'reply: "LOCAL DRAFT ONLY"' in offline, "offline comment/reply draft policy is missing")
    require('share: "MANUAL RETRY"' in offline, "offline share retry policy is missing")
    require("never" in offline and "second native queue" in offline, "offline policy does not explicitly prohibit a second mutation queue")

    require("configureReelsAudioSession" in screen, "Reels does not configure its native audio session")
    require("InterruptionModeIOS.DoNotMix" in audio and "playsInSilentModeIOS: true" in audio, "iOS Reels audio-session policy is incomplete")
    require("staysActiveInBackground: false" in audio, "Reels audio may continue unexpectedly in background")
    require("LogiNexus" not in screen + api + offline + audio, "internal design language leaked into native Reels")
    require("react-native-webview" not in (screen + api).lower(), "native Reels introduced a WebView dependency")

    if failures:
        print("Native Reels final readiness audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS: native Reels ownership, replies, draft recovery, realtime refresh, offline policy, and audio policy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
