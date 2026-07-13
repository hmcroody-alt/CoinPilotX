#!/usr/bin/env python3
"""Focused native Reels contract, interaction, playback, and safety audit."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")

def require(value: bool, message: str, failures: list[str]) -> None:
    if not value:
        failures.append(message)

def main() -> int:
    failures: list[str] = []
    backend = read("bot.py")
    api = read("mobile-native/src/api/reels.ts")
    screen = read("mobile-native/src/screens/ReelsScreen.tsx")
    card = read("mobile-native/src/components/ReelPlayerCard.tsx")
    config = read("mobile-native/src/api/config.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    navigator = read("mobile-native/src/navigation/AppNavigator.tsx")

    routes = [
        '/pulse/reels", methods=["GET"]', '/api/pulse/reels/feed", methods=["GET"]',
        '/api/pulse/reels/<int:reel_id>/react", methods=["POST"]',
        '/api/pulse/reels/<int:reel_id>/comments", methods=["GET", "POST"]',
        '/api/pulse/reels/comments/<int:comment_id>/react", methods=["POST"]',
        '/api/pulse/reels/<int:reel_id>/save", methods=["POST"]',
        '/api/pulse/reels/<int:reel_id>/share", methods=["POST"]',
        '/api/pulse/reels/<int:reel_id>/follow-creator", methods=["POST"]',
        '/api/pulse/reels/<int:reel_id>/audio", methods=["POST"]',
    ]
    for route in routes:
        require(route in backend, f"production route missing: {route}", failures)

    for endpoint in ["/api/pulse/reels/feed", "/api/pulse/reels/${reelId}/react", "/api/pulse/reels/${reelId}/comments", "/api/pulse/reels/comments/${commentId}/react", "/api/pulse/reels/${reelId}/save", "/api/pulse/reels/${reelId}/share", "/api/pulse/reels/${reelId}/follow-creator"]:
        require(endpoint in api, f"native route reuse missing: {endpoint}", failures)

    require('includeComments: false' in screen, "feed must not load visible preview comments", failures)
    require("visible={Boolean(commentReel)}" in screen and "onOpenComments" in card, "comments must open only from explicit action", failures)
    require("preview_comments" not in card, "Reel canvas must not render preview comments", failures)
    require("ReactionPicker" in screen and "onLongPress={() => onOpenReactions(reel)}" in card, "long-press reaction selector missing", failures)
    for reaction in ['key: "like"', 'key: "love"', 'key: "fire"', 'key: "funny"', 'key: "wow"', 'key: "smart"']:
        require(reaction in screen, f"production reaction missing: {reaction}", failures)
    require("previousReaction" in screen and "result.removed" in screen, "reaction replacement/removal reconciliation missing", failures)
    require("musicMicro" in card and "MusicDetail" in screen, "minimal attached-music UI missing", failures)
    require("Audio.Sound.createAsync" in card and "original_audio_muted" in card, "attached audio playback/mixing guard missing", failures)
    require("live_session_id" in api and "-Math.abs(liveId)" in api, "production Live records must survive numeric native normalization", failures)
    require('navigation.navigate("LiveDetail"' in screen, "Join Live must reuse native Live viewer", failures)
    require("AppState.addEventListener" in screen and "active={index === activeIndex" in screen, "background/sheet playback pause missing", failures)
    require("pagingEnabled" in screen and "snapToInterval={viewportHeight}" in screen and "windowSize={3}" in screen, "full-screen conservative paging missing", failures)
    require("PULSESOC_QA_REELS_FIXTURES" in config and "localhost" in config, "QA fixtures must be localhost-only", failures)
    require("PULSESOC_QA_REELS_FIXTURES" in api and "reelsQaFixtures" in api, "deterministic Reels fixtures missing", failures)
    require('target: "reel"' in screen and 'mode: "reel"' in screen, "existing native creator route not reused", failures)
    require("pulse/reels/:reelId" in linking and 'name="ReelDetail"' in navigator, "Reel detail/deep-link route changed", failures)
    require("LogiNexus" not in screen and "LogiNexus" not in card, "internal branding leaked into Reels UI", failures)
    require("react-native-webview" not in (api + screen + card).lower(), "native Reels introduced WebView", failures)

    if failures:
        print("Native Reels futuristic deep-wiring audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS: native Reels routes, playback, paging, hidden comments, reactions, music, Live, creator, fixtures, and compatibility")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
