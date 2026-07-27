#!/usr/bin/env python3
"""Static gate for the native PulseSoc Music upload/library migration."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def main() -> int:
    backend = read("bot.py")
    api = read("mobile-native/src/api/music.ts")
    screen = read("mobile-native/src/screens/MusicScreen.tsx")
    app_nav = read("mobile-native/src/navigation/AppNavigator.tsx")
    types = read("mobile-native/src/navigation/types.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    dashboard_routing = read("mobile-native/src/navigation/dashboardRouting.ts")
    native_actions = read("mobile-native/src/navigation/nativeRouteActions.ts")
    notification_routing = read("mobile-native/src/navigation/notificationRouting.ts")
    action_alias = read("mobile-native/src/screens/DashboardActionAliasScreen.tsx")
    home_composer = read("mobile-native/src/components/HomePulseComposer.tsx")
    home_screen = read("mobile-native/src/screens/HomeScreen.tsx")
    status_creator = read("mobile-native/src/components/StatusCreator.tsx")

    for token in (
        '@webhook_app.route("/pulse/music"',
        '@webhook_app.route("/api/pulse/music/search"',
        '@webhook_app.route("/api/pulse/music/upload"',
        '@webhook_app.route("/api/pulse/music/<int:track_id>/event"',
        '@webhook_app.route("/api/pulse/music/<int:track_id>/report"',
        '@webhook_app.route("/api/pulse/music/artist/<int:artist_user_id>"',
        "rights_confirmed",
        "pulse_audio_tracks",
    ):
        require(token in backend, f"production music contract missing: {token}")

    for token in (
        "searchPulseMusic",
        "uploadPulseMusic",
        "selectPulseMusicForSurface",
        "consumePulseMusicSelection",
        "composerMusicTrackFromPulseMusic",
        "/api/pulse/music/search",
        "/api/pulse/music/upload",
        "/api/pulse/music/${encodeURIComponent(trackId)}/event",
        "/api/pulse/music/${encodeURIComponent(trackId)}/report",
        "rights_confirmed",
        "MUSIC_SELECTION_PREFIX",
    ):
        require(token in api, f"native music API missing: {token}")

    for token in (
        "export function MusicScreen",
        "DocumentPicker.getDocumentAsync",
        "expo-av",
        "Upload for Review",
        "I confirm that I own this music or have the legal right to upload it.",
        "Music Library",
        "Preview",
        "Save",
        "Share",
        "Report",
        "Use in Reel",
        "Use in Video",
        "Use in Status",
        "claimMediaPlayback",
        'kind: "music_preview"',
        "recordPulseMusicEvent",
        "reportPulseMusic",
        "uploadPulseMusic",
        "selectPulseMusicForSurface",
        "audio/mp4",
    ):
        require(token in screen, f"native music screen missing: {token}")

    require("WebView" not in screen, "Music screen must be native, not a WebView wrapper")
    require('Stack.Screen name="Music"' in app_nav and "MusicScreen" in app_nav, "Music stack screen is not registered")
    require("Music:" in types and "trackId?: string" in types and "openUpload?: boolean" in types, "Music route params missing")
    require('path: "pulse/music"' in linking and "Music:" in linking, "Deep link /pulse/music must route native")
    require("isMusicRoutePath" in dashboard_routing and 'navigation.navigate("Music"' in dashboard_routing, "Dashboard music routes must open native Music")
    require('routePath.startsWith("/pulse/music")' in native_actions and 'navigation.navigate("Music"' in native_actions, "Native route action missing /pulse/music")
    require('normalized.startsWith("/pulse/music")' in notification_routing and 'navigationRef.navigate("Music"' in notification_routing, "Notification routing missing /pulse/music")
    require('route.name === "DashboardMusicAlias"' in action_alias and 'navigation.replace("Music"' in action_alias, "Legacy music alias must redirect native")
    require("composerMode" in home_screen and "initialMode" in home_composer, "Home composer handoff missing")
    require('consumePulseMusicSelection("status")' in status_creator, "Status creator handoff missing")
    require('consumePulseMusicSelection(surface)' in home_composer and "composerMusicTrackFromPulseMusic" in home_composer, "Feed/Reel composer music handoff missing")

    print("PulseSoc native Music upload/library audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
