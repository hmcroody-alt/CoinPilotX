"""Healthy engagement loops for Pulse."""

from __future__ import annotations

from datetime import date


DAILY_MISSIONS = [
    {"key": "help_3_users", "title": "Help 3 users", "xp": 45},
    {"key": "educational_post", "title": "Post one educational Pulse", "xp": 25},
    {"key": "complete_lesson", "title": "Complete a lesson", "xp": 20},
    {"key": "verify_scam_report", "title": "Verify a scam report", "xp": 40},
    {"key": "attend_livestream", "title": "Attend a livestream", "xp": 15},
]


def today_missions(user_id=None):
    return {"date": date.today().isoformat(), "user_id": int(user_id or 0), "missions": DAILY_MISSIONS}


def milestone_for_counts(posts=0, comments=0, live_attendance=0):
    total = int(posts or 0) * 10 + int(comments or 0) * 3 + int(live_attendance or 0) * 8
    if total >= 1000:
        return "Pulse Legend"
    if total >= 450:
        return "Community Builder"
    if total >= 160:
        return "Trusted Contributor"
    return "Getting Started"
