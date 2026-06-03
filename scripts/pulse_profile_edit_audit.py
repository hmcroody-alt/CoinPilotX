#!/usr/bin/env python3
"""Audit Pulse profile edit controls and backend protection."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot.py"


def expect(ok: bool, label: str) -> None:
    if not ok:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main() -> None:
    source = BOT.read_text(encoding="utf-8")
    for token in [
        '@webhook_app.route("/pulse/profile/edit"',
        '@webhook_app.route("/api/pulse/profile/update"',
        '@webhook_app.route("/api/pulse/profile/avatar"',
        '@webhook_app.route("/api/pulse/profile/cover"',
        "require_account()",
        "api_account_user()",
        "avatarFile",
        "coverFile",
        "displayName",
        "username",
        "bio",
        "socialLinks",
        "expertiseTags",
        "profileVisibility",
        "profileEditState",
        "That username is already taken.",
    ]:
        expect(token in source, f"Profile edit includes {token}")

    expect("WHERE user_id=?" in source, "Profile update is scoped to the signed-in user")
    expect("Display name is required." in source, "Profile save validates display name")


if __name__ == "__main__":
    main()
