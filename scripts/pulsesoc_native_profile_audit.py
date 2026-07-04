#!/usr/bin/env python3
"""Static audit for the PulseSoc native Profile foundation."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    path = ROOT / relative
    if not path.exists():
        raise AssertionError(f"Missing required file: {relative}")
    return path.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    report = read("reports/pulsesoc_native_profile_progress.md")
    progress = read("reports/pulsesoc_native_progress.md")
    api = read("mobile-native/src/api/profile.ts")
    header = read("mobile-native/src/components/ProfileHeader.tsx")
    profile = read("mobile-native/src/screens/ProfileScreen.tsx")
    edit = read("mobile-native/src/screens/ProfileEditScreen.tsx")
    card = read("mobile-native/src/components/PostCard.tsx")
    home = read("mobile-native/src/screens/HomeScreen.tsx")
    post_detail = read("mobile-native/src/screens/PostDetailScreen.tsx")
    messenger = read("mobile-native/src/screens/MessengerScreen.tsx")
    navigator = read("mobile-native/src/navigation/AppNavigator.tsx")
    types = read("mobile-native/src/navigation/types.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    routing = read("mobile-native/src/navigation/notificationRouting.ts")

    for phrase in (
        "does not touch production WebView paths",
        "Server APIs stay authoritative",
        "Existing Web/Backend Implementation Inspected",
        "No native-only profile authorization",
        "Device-Only Behavior Not Verified",
        "not marked as passed without device access",
    ):
        require(phrase in report, f"profile report must document reuse/safety/device truth: {phrase}")

    for route in (
        "/api/pulse/profile/me",
        "/api/pulse/profile/update",
        "/api/pulse/profile/avatar",
        "/api/pulse/profile/cover",
        "/api/pulse/profile/avatar/remove",
        "/api/pulse/profile/cover/remove",
        "/api/pulse/premium/profile-theme",
    ):
        require(route in api, f"profile API must reuse backend route: {route}")

    for token in (
        "getMyProfile",
        "updateProfile",
        "uploadProfileAvatar",
        "uploadProfileCover",
        "removeProfileAvatar",
        "removeProfileCover",
        "getProfileTheme",
        "updateProfileTheme",
        "listPublicProfilePosts",
        "loadCachedProfile",
        "cacheProfile",
        "profileWebUrl",
    ):
        require(token in api, f"profile API helper missing: {token}")

    for token in (
        "ProfileHeader",
        "avatar_url",
        "cover_url",
        "Verified",
        "Premium",
        "Followers",
        "Following",
        "Edit Profile",
        "Share Profile",
    ):
        require(token in header, f"profile header behavior missing: {token}")

    for token in (
        "FlatList",
        "RefreshControl",
        "getMyProfile",
        "listPublicProfilePosts",
        "loadCachedProfile",
        "ProfileHeader",
        "PostCard",
        "Posts",
        "Media",
        "About",
        "profileWebUrl",
    ):
        require(token in profile, f"profile screen behavior missing: {token}")

    for token in (
        "expo-image-picker",
        "requestMediaLibraryPermissionsAsync",
        "launchImageLibraryAsync",
        "updateProfile",
        "uploadProfileAvatar",
        "uploadProfileCover",
        "removeProfileAvatar",
        "removeProfileCover",
        "updateProfileTheme",
        "Display name is required.",
        "Photo permission was not granted.",
        "Save",
        "Cancel",
    ):
        require(token in edit, f"profile edit behavior missing: {token}")

    require("onAuthorPress" in card, "post card must expose author/profile navigation")
    require("ProfileDetail" in home, "home feed author navigation must target ProfileDetail")
    require("ProfileDetail" in post_detail, "post detail author navigation must target ProfileDetail")
    require("ProfileDetail" in messenger and "other_public_player_id" in messenger, "messenger profile navigation must use existing public id when present")

    for token in (
        "ProfileDetail",
        "ProfileEdit",
        "ProfileScreen",
        "ProfileEditScreen",
    ):
        require(token in navigator, f"navigator profile route missing: {token}")

    require("ProfileDetail: { profileKey?: string" in types, "navigation types must include ProfileDetail params")
    require("ProfileEdit: undefined" in types, "navigation types must include ProfileEdit")
    require("pulse/profile/:profileKey" in linking and "pulse/profile/edit" in linking, "linking must include profile routes")
    require('"ProfileDetail"' in routing and '"ProfileEdit"' in routing and "pulse\\/profile" in routing, "notification routing must open native profile routes")

    for phrase in (
        "Profile:",
        "Native Reels Player + Reel Detail",
        "Why This Comes Next",
        "Risk: Medium-high",
        "Complexity: Medium-high",
        "Safest Implementation Plan",
    ):
        require(phrase in progress, f"native progress report must include completed profile and next recommendation: {phrase}")

    mobile_native = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "mobile-native/src").rglob("*.ts*")
        if "node_modules" not in path.parts
    )
    require("WebView" not in mobile_native and "react-native-webview" not in mobile_native.lower(), "native Profile must not introduce WebView")

    print("PulseSoc native Profile audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
