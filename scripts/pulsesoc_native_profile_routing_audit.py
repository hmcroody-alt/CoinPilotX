#!/usr/bin/env python3
"""Static behavior gate for native user search -> profile routing parity."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    backend = read("bot.py")
    search_api = read("mobile-native/src/api/search.ts")
    profile_api = read("mobile-native/src/api/profile.ts")
    target_api = read("mobile-native/src/api/profileTarget.ts")
    search_screen = read("mobile-native/src/screens/SearchScreen.tsx")
    profile_screen = read("mobile-native/src/screens/ProfileScreen.tsx")
    notification_routing = read("mobile-native/src/navigation/notificationRouting.ts")
    native_route_actions = read("mobile-native/src/navigation/nativeRouteActions.ts")
    linking = read("mobile-native/src/navigation/linking.ts")
    types = read("mobile-native/src/navigation/types.ts")
    home = read("mobile-native/src/screens/HomeScreen.tsx")
    reels = read("mobile-native/src/screens/ReelsScreen.tsx")
    status = read("mobile-native/src/screens/StatusScreen.tsx")
    post_detail = read("mobile-native/src/screens/PostDetailScreen.tsx")
    live = read("mobile-native/src/screens/LiveScreen.tsx")
    events = read("mobile-native/src/screens/EventsScreen.tsx")
    marketplace = read("mobile-native/src/screens/MarketplaceScreen.tsx")

    for token in (
        "NativeProfileTarget",
        "resolveProfileTarget",
        "profileTargetFromUrl",
        "profileTargetFromAuthor",
        "profileNavigationParams",
        "profileCacheKey",
        "profileWebPath",
        "/pulse/@",
        "/pulse/id/",
    ):
        require(token in target_api, f"shared profile resolver missing {token}")

    for token in ("user_id?:", "public_player_id?:", "public_pulse_id?:", "username?:"):
        require(token in search_api, f"search result contract missing {token}")
    require("isProfileResult(item)" in search_screen, "SearchScreen must detect profile results before generic routing")
    require('navigation.navigate("ProfileDetail", params)' in search_screen, "SearchScreen must navigate to native ProfileDetail")
    require("routeNotificationTarget(item.url" in search_screen, "SearchScreen must keep non-profile route fallback")

    require('url": pulse_profile_canonical_path' in backend, "backend search must return canonical profile route")
    require('"user_id": creator.get("user_id")' in backend, "backend search creators must include canonical user_id")
    require('"public_player_id": creator.get("public_player_id")' in backend, "backend search creators must include public_player_id")
    require("def pulse_user_id_from_profile_key" in backend, "backend must expose strict native profile resolver")
    strict_resolver = backend[backend.index("def pulse_user_id_from_profile_key"):backend.index("def pulse_profile_canonical_path")]
    require("display_name" not in strict_resolver and "full_name" not in strict_resolver, "strict native resolver must not resolve by display/full name")
    require("api_pulse_public_profile" in backend and "pulse_user_id_from_profile_key(cur, profile_key)" in backend, "profile API must use strict resolver")
    for status_code in ("403", "404", "410"):
        require(status_code in backend[backend.index("def api_pulse_public_profile"):backend.index('@webhook_app.route("/api/pulse/profile/me"')], f"profile API must distinguish status {status_code}")

    require("getPublicProfile(profileTarget)" in profile_screen, "ProfileScreen must fetch with resolved profile target")
    require("profileErrorState" in profile_screen, "ProfileScreen must use explicit error-state mapping")
    require("Retry native profile" in profile_screen, "ProfileScreen must prefer retry over web fallback")
    require("Open web fallback" in profile_screen, "ProfileScreen may expose controlled web fallback")
    require("Profile unavailable" not in profile_screen, "false Profile unavailable copy must be removed from native screen")
    require("requested PulseSoc service was not found" not in profile_screen, "internal service error must not be user-facing")
    require("profileCacheKey" in profile_api and "cacheProfileAliases" in profile_api, "profile cache must use canonical aliases")
    require("request_unreachable" in profile_api and "403" in profile_api and "410" in profile_api and "429" in profile_api, "profile errors must be mapped distinctly")

    for module_name, module_source in {
        "notification routing": notification_routing,
        "native route actions": native_route_actions,
        "deep linking": linking,
    }.items():
        require("profileTargetFromUrl" in module_source, f"{module_name} must reuse profile URL resolver")
        require("profileNavigationParams" in module_source, f"{module_name} must reuse profile navigation params")

    for token in ("userId?: number", "publicPlayerId?: string", "profileId?: string"):
        require(token in types, f"ProfileDetail params must carry {token}")

    for module_name, module_source in {
        "home feed": home,
        "post detail": post_detail,
        "status": status,
        "reels": reels,
        "live": live,
        "events": events,
        "marketplace": marketplace,
    }.items():
        require("profileNavigationParams" in module_source, f"{module_name} entry point must use shared profile navigation")
    require("post.author?.display_name || post.author_name" not in home, "home profile lookup must not fall back to display name")

    print("PulseSoc native profile routing audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
