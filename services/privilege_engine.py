"""CoinPilotXAI user privilege ladder and unlock helpers."""

from __future__ import annotations


PRIVILEGE_LEVELS = [
    "New User",
    "Active Member",
    "Trusted Member",
    "Verified User",
    "Creator",
    "Teacher",
    "Marketplace Seller",
    "Livestream Eligible",
    "Partner Creator",
    "Platform Ambassador",
]


LEVEL_THRESHOLDS = [
    (90, "Platform Ambassador"),
    (82, "Partner Creator"),
    (74, "Livestream Eligible"),
    (66, "Marketplace Seller"),
    (58, "Teacher"),
    (50, "Creator"),
    (40, "Verified User"),
    (28, "Trusted Member"),
    (15, "Active Member"),
    (0, "New User"),
]


def level_for_trust(score, verification_types=None, referral_count=0):
    verification_types = set(verification_types or [])
    score = int(score or 0)
    level = "New User"
    for threshold, name in LEVEL_THRESHOLDS:
        if score >= threshold:
            level = name
            break
    if "teacher" in verification_types and PRIVILEGE_LEVELS.index(level) < PRIVILEGE_LEVELS.index("Teacher"):
        level = "Teacher"
    if "seller" in verification_types and PRIVILEGE_LEVELS.index(level) < PRIVILEGE_LEVELS.index("Marketplace Seller"):
        level = "Marketplace Seller"
    if int(referral_count or 0) >= 30 and PRIVILEGE_LEVELS.index(level) < PRIVILEGE_LEVELS.index("Livestream Eligible"):
        level = "Livestream Eligible"
    return level


def get_user_privileges(user_id=None, trust_score=0, current_level="", referral_count=0, verification_types=None, live_status="locked"):
    verification_types = set(verification_types or [])
    level = current_level or level_for_trust(trust_score, verification_types, referral_count)
    level_index = PRIVILEGE_LEVELS.index(level) if level in PRIVILEGE_LEVELS else 0
    referral_count = int(referral_count or 0)
    trust_score = int(trust_score or 0)
    live_unlocked = live_status in {"eligible", "approved"} or referral_count >= 30 or level_index >= PRIVILEGE_LEVELS.index("Livestream Eligible")

    next_steps = []
    if referral_count < 30:
        next_steps.append(f"Invite {30 - referral_count} more real members to unlock Live.")
    if trust_score < 50:
        next_steps.append("Complete your profile and keep posting helpful Pulse content.")
    if "identity" not in verification_types:
        next_steps.append("Earn verified trust with identity, creator, teacher, seller, or safety verification.")

    return {
        "user_id": user_id,
        "current_level": level,
        "can_go_live": bool(live_unlocked),
        "can_sell": level_index >= PRIVILEGE_LEVELS.index("Marketplace Seller") or "seller" in verification_types,
        "can_teach": level_index >= PRIVILEGE_LEVELS.index("Teacher") or "teacher" in verification_types,
        "can_create_group": level_index >= PRIVILEGE_LEVELS.index("Active Member"),
        "can_upload_video": level_index >= PRIVILEGE_LEVELS.index("Active Member"),
        "can_create_space": level_index >= PRIVILEGE_LEVELS.index("Creator"),
        "can_host_room": level_index >= PRIVILEGE_LEVELS.index("Creator"),
        "can_use_creator_filters": level_index >= PRIVILEGE_LEVELS.index("Creator"),
        "max_video_duration": 45 if level_index < PRIVILEGE_LEVELS.index("Creator") else 180,
        "max_upload_mb": 25 if level_index < PRIVILEGE_LEVELS.index("Creator") else 100,
        "profile_badges": sorted(verification_types) + ([level] if level else []),
        "required_next_steps": next_steps,
    }
