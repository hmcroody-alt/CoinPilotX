#!/usr/bin/env python3
"""Audit the mobile-first PulseSoc profile structure and actions."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    start = SOURCE.index("def pulse_profile_page_for_user")
    end = SOURCE.index('@webhook_app.route("/pulse/post/', start)
    profile = SOURCE[start:end]

    for token in [
        "data-pulse-profile-page",
        "pulse-profile-cover",
        "pulse-profile-avatar",
        "pulse-profile-stats",
        "Edit Profile",
        "Share Profile",
        "Settings",
        "data-follow-public",
        "data-message-public",
        "data-profile-more-toggle",
        "View All Badges",
        "profile-badge-row",
        "Posts</a>",
        "Reels</a>",
        "Videos</a>",
        "Photos</a>",
        "About</a>",
        "embed=profile",
        "pulseProfileEmbedStyles",
        "show_intro=False",
    ]:
        expect(token in profile, f"mobile profile includes {token}")

    expect(profile.index("id='profilePosts'") < profile.index("id='profileAbout'"), "posts appear before secondary profile information")
    expect("grid-template-columns:repeat(4,minmax(0,1fr))" in profile, "profile stats use a compact four-column row")
    expect(".pulse-profile-page) .pulse-fab" in profile and "display:none!important" in profile, "mobile create button cannot cover profile controls")
    expect("profile-role-badge:nth-of-type(n+4)" in profile, "mobile profile limits visible badges")
    expect("Profile Controls</h2>" not in profile, "legacy profile controls card is removed")
    expect("Add Friend</button><button data-message" not in profile, "legacy oversized visitor action stack is removed")


if __name__ == "__main__":
    main()
