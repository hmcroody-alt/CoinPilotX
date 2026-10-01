#!/usr/bin/env python3
"""Audit Pulse Reels engagement production wiring."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    feed = (ROOT / "services" / "pulse_feed_engine.py").read_text(encoding="utf-8")
    for token, label in [
        ("preview_comments", "Reel payload includes actual comment previews"),
        ("pulse_reel_comment_payload", "Reel comments are enriched for replies and permissions"),
        ('/api/pulse/reels/<int:reel_id>", methods=["PATCH", "DELETE"]', "Reel owner edit/delete endpoint exists"),
        ('/api/pulse/reels/<int:reel_id>/pin', "Reel owner pin endpoint exists"),
        ('/api/pulse/reels/comments/<int:comment_id>/react', "Comment reaction endpoint exists"),
        ('/api/pulse/reels/comments/<int:comment_id>", methods=["PATCH", "DELETE"]', "Comment edit/delete endpoint exists"),
        ("comments_disabled", "Comments disabled flag is enforced"),
        ("reactions_disabled", "Reactions disabled flag is enforced"),
        ("pulse_reel_deleted", "Soft delete audit event is emitted"),
        ("pulse_reel_comment_reaction_updated", "Comment reaction event is emitted"),
        ("data-reel-comment-like", "Comment like control is wired"),
        ("data-reel-comment-reply", "Comment reply control is wired"),
        ("data-reel-comment-edit", "Comment edit control is wired"),
        ("data-reel-comment-delete", "Comment delete control is wired"),
        ("data-reel-toggle-comments", "Owner comment toggle is wired"),
        ("data-reel-toggle-reactions", "Owner reaction toggle is wired"),
    ]:
        require(token in source, label)
    for token, label in [
        ('"user_id": item.get("user_id")', "Comment payload exposes safe author id for permissions"),
        ('"edited_at": item.get("edited_at")', "Comment payload exposes edited state"),
    ]:
        require(token in feed, label)
    print("pulse reels engagement audit ok")


if __name__ == "__main__":
    main()
