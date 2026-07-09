#!/usr/bin/env python3
"""Static guard for PulseSoc Native Home foundation Phase 1."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "mobile-native" / "src" / "screens" / "HomeScreen.tsx"
COMPOSER = ROOT / "mobile-native" / "src" / "components" / "HomePulseComposer.tsx"
POST_CARD = ROOT / "mobile-native" / "src" / "components" / "PostCard.tsx"
FEED_API = ROOT / "mobile-native" / "src" / "api" / "feed.ts"
PROGRESS = ROOT / "reports" / "pulsesoc_native_home_foundation_progress.md"
VISIBLE_QA = ROOT / "reports" / "pulsesoc_native_home_visible_qa.md"
MASTER = ROOT / "reports" / "pulsesoc_native_progress.md"


REQUIRED_HOME_TOKENS = [
    "PulseNetworkHero",
    "StatusRail",
    "HomePulseComposer",
    "registerSyncInvalidation",
    "listStatuses",
    "loadCachedStatuses",
    "openDashboardRoute",
    "Pulse Radio",
    "Safety scan",
    "Refresh",
    "Add Status",
    "StatusDetail",
    "CameraStudio",
    "PostDetail",
    "ProfileDetail",
    "SafetyHub",
    "GrowthCenter",
]

REQUIRED_FEED_TABS = [
    "For You",
    "Following",
    "Friends",
    "Communities",
    "Trending",
    "Crypto",
    "Scam Alerts",
    "Arena Highlights",
    "Roast Clips",
    "Questions",
    "My Posts",
]

REQUIRED_COMPOSER_TOKENS = [
    "Pulse Composer",
    "Post",
    "Reel",
    "Live",
    "Photo",
    "Video",
    "Music",
    "Feeling",
    "Location",
    "Mention",
    "Topic",
    "Public",
    "Publish Signal",
    "MAX_BODY = 3000",
    "createPost",
    "useNativeMediaUpload",
    "MediaUploadPreview",
    "uploadResultMediaId",
]

REQUIRED_POST_CARD_TOKENS = [
    "onComment",
    "onFollow",
    "onReport",
    "onHide",
    "onBlock",
    "onMute",
    "Comment",
    "Follow",
    "Report",
    "Hide",
    "Block",
    "Mute",
]

REQUIRED_REPORT_TOKENS = [
    "Pulse Network hero",
    "Status rail",
    "Pulse Composer",
    "Feed category tabs",
    "Visible QA",
]


def read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"Missing required file: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def require_tokens(label: str, text: str, tokens: list[str]) -> list[str]:
    missing = [token for token in tokens if token not in text]
    if missing:
        raise AssertionError(f"{label} missing tokens: {', '.join(missing)}")
    return tokens


def main() -> None:
    home = read(HOME)
    composer = read(COMPOSER)
    post_card = read(POST_CARD)
    feed_api = read(FEED_API)
    progress = read(PROGRESS)
    visible_qa = read(VISIBLE_QA)
    master = read(MASTER)

    require_tokens("HomeScreen", home, REQUIRED_HOME_TOKENS)
    require_tokens("Home feed tabs", home, REQUIRED_FEED_TABS)
    require_tokens("HomePulseComposer", composer, REQUIRED_COMPOSER_TOKENS)
    require_tokens("PostCard interactions", post_card, REQUIRED_POST_CARD_TOKENS)
    require_tokens("Feed author routing", feed_api, ["author_public_player_id", "public_player_id: author.public_player_id || item.author_public_player_id"])
    require_tokens("Home progress report", progress, REQUIRED_REPORT_TOKENS)
    require_tokens("Visible QA report", visible_qa, ["What Roody visibly saw", "Built-in QA browser", "Home hero", "Feed tabs"])
    require_tokens("Master progress report", master, ["Native Home Experience Foundation", "Home foundation"])

    print("PulseSoc Native Home foundation audit passed.")
    print(f"Verified feed tabs: {len(REQUIRED_FEED_TABS)}")
    print("Verified Home hero, Status rail, Composer, feed tabs, feed actions, reports, and master progress update.")


if __name__ == "__main__":
    main()
