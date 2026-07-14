#!/usr/bin/env python3
"""Behavior-oriented guard for native Reels recovery, sync, and comment ownership."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def main() -> int:
    failures: list[str] = []
    screen = read("mobile-native/src/screens/ReelsScreen.tsx")
    api = read("mobile-native/src/api/reels.ts")
    sync = read("mobile-native/src/core/eventSync.ts")
    navigator = read("mobile-native/src/navigation/AppNavigator.tsx")
    backend = read("bot.py")

    def require(ok: bool, message: str) -> None:
        if not ok:
            failures.append(message)

    for state in ["loading", "connecting", "offline", "server_busy", "maintenance", "rate_limited", "auth_expired", "empty"]:
        require(f'{state}:' in screen, f"dedicated recovery state missing: {state}")
    require("RETRY_DELAYS = [1_000, 2_000, 5_000, 10_000]" in screen, "bounded automatic retry schedule missing")
    require("loadCachedReelsSnapshot" in screen and "cacheAge(cachedAt)" in screen, "cached-first feed with age missing")
    require("GalaxyField" in screen and "skeletonCard" in screen, "living galaxy loading surface missing")
    require("errorPill" not in screen, "raw red error pill still renders")
    require('ListEmptyComponent={<ReelsRecovery' in screen, "empty/error content does not use the recovery state machine")
    require("PULSESOC_REELS_RECOVERY" in screen and 'endpoint: "/api/pulse/reels/feed"' in screen, "sanitized recovery diagnostics missing")
    require('registerSyncInvalidation("reels"' in screen, "Reels does not subscribe to shared sync invalidation")
    require('| "reels"' in sync and 'result.push("reels", "activity")' in sync, "shared sync layer does not classify Reel events")
    require('"status", "reels"' in navigator, "global event polling does not request Reels invalidations")
    require("normalizeEvents" in sync and "seen.has(id)" in sync, "duplicate realtime-event suppression missing")
    require("REELS_CACHE_META_KEY" in api and "loadCachedReelsSnapshot" in api, "timestamped lane cache missing")
    require("editReelComment" in api and "deleteReelComment" in api and "reportReelComment" in api, "production comment ownership/moderation endpoints not reused")
    require("comment.can_delete" in screen and "currentUserId" in screen, "comment delete action is not ownership guarded")
    require('/api/pulse/reels/comments/<int:comment_id>", methods=["PATCH", "DELETE"]' in backend, "authoritative comment edit/delete route missing")
    require("LogiNexus" not in screen, "internal design name leaked into user-facing Reels")
    require("react-native-webview" not in (screen + api).lower(), "Reels recovery introduced a WebView")

    if failures:
        print("Native Reels realtime/offline closure audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS: Reels cached-first recovery, state machine, retry, sync invalidation, diagnostics, and ownership guards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
