"""Seasonal leaderboard scoring foundations."""

from __future__ import annotations


def score_entry(metrics=None):
    metrics = metrics or {}
    return int(
        int(metrics.get("posts") or 0) * 8
        + int(metrics.get("helpful_comments") or 0) * 5
        + int(metrics.get("verified_scam_reports") or 0) * 20
        + int(metrics.get("lessons") or 0) * 18
        + int(metrics.get("livestream_minutes") or 0) * 0.6
        + int(metrics.get("marketplace_sales") or 0) * 12
    )


def leaderboard_name(kind="creators"):
    return {
        "creators": "Top Creators",
        "teachers": "Top Teachers",
        "scam_hunters": "Top Scam Hunters",
        "livestreamers": "Top Livestreamers",
        "learners": "Top Learners",
        "sellers": "Top Marketplace Sellers",
    }.get(kind, "Top Contributors")
