#!/usr/bin/env python3
"""Controlled static gate for the PulseSoc native Profile V2 mission."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def main() -> int:
    backend = read("bot.py")
    api = read("mobile-native/src/api/profile.ts")
    header = read("mobile-native/src/components/ProfileHeader.tsx")
    screen = read("mobile-native/src/screens/ProfileScreen.tsx")
    edit = read("mobile-native/src/screens/ProfileEditScreen.tsx")
    tests = read("mobile-native/ios/PulseSocNativeUITests/PulseSocNativeCameraStudioQATests.swift")
    report = read("reports/pulsesoc_native_profile_progress.md")

    for token in (
        "def pulse_native_profile_payload",
        '@webhook_app.route("/api/pulse/profile/<path:profile_key>"',
        '"viewer_follows"',
        '"post_count"',
        '"media_count"',
        '"follower_count"',
        '"following_count"',
        'add_columns_if_missing(cur, "pulse_profile_themes"',
        '"layout_key"',
        '"modules_json"',
        '"motion_level"',
    ):
        require(token in backend, f"backend Profile V2 contract missing: {token}")

    require("ios_paid_digital_unavailable_response" not in backend[backend.index('def pulse_premium_profile_theme_api'):backend.index('def creator_ai_payload')], "native theme access must not be treated as a purchase")
    for theme in ("deep_space", "neon_galaxy", "cyber_city", "solar_pulse", "aurora", "quantum", "crystal", "dark_matter", "nova", "minimal_black"):
        require(theme in backend and theme in edit, f"server/client theme missing: {theme}")
    for layout in ("classic", "creator", "professional", "minimal", "artist", "music", "gaming", "developer", "business", "streamer"):
        require(layout in backend and layout in edit, f"server/client layout missing: {layout}")

    for token in ("getPublicProfile", "toggleProfileFollow", "/api/pulse/profile/", "/api/pulse/follows/toggle"):
        require(token in api, f"native Profile API missing: {token}")
    for token in ("createLogiNexusAmbientPulse", "useLogiNexusReducedMotion", "deep_space", "Edit Profile", "Customize", "Message", "Following", "Identity", "Media", "Music", "Trust"):
        require(token in header, f"living Profile header missing: {token}")
    for token in ("getPublicProfile", "openDirectConversation", "toggleProfileFollow", 'navigate("Chat"', "Showing saved profile"):
        require(token in screen, f"Profile wiring missing: {token}")
    require("testLivingProfileAndCustomizationOnIPhone" in tests, "Xcode Profile UI test missing")
    require("No native-only identity" in report and "1 test / 0 failures" in report, "Profile report must record boundaries and simulator result")

    native_source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "mobile-native/src").rglob("*.ts*"))
    require("react-native-webview" not in native_source.lower(), "Profile V2 must stay native")
    print("PulseSoc native Profile V2 audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
