"""CoinPlotXAI user privilege ladder and unlock helpers."""

from __future__ import annotations


#: Mirror of ``campaign.LIVE_MIN_VERIFIED_MEMBERS``, used only when Progress OS
#: cannot be imported at all — so it cannot be read from there. The two are
#: pinned together by test, because an outage must not move the gate: this
#: module previously fell back to the historical 30 and would have re-locked
#: Live for every creator who qualified under the real threshold.
LIVE_THRESHOLD_FALLBACK = 2


def live_creator_threshold() -> int:
    """Certified invites required for Live, read from the Founding Path ladder.

    Derived rather than hardcoded so the gate and the rung a member sees can
    never drift apart.
    """
    try:
        from services.business_os.progress import campaign as campaign_mod
        return campaign_mod.get().live_threshold()
    except Exception:
        return LIVE_THRESHOLD_FALLBACK


def live_unlock_sentence(threshold=None) -> str:
    """The one sentence every Live surface uses to state the requirement.

    Centralised because the number used to be typed into the copy by hand on
    four pages and a denial response, which is how the product came to promise
    thirty invites for a gate the server opened at two.
    """
    count = int(live_creator_threshold() if threshold is None else threshold)
    noun = "real member" if count == 1 else "real members"
    return f"Invite {count} {noun} and build verified trust to unlock Live."


def live_progress_label(completed, threshold=None) -> str:
    """``1 of 2 verified members`` while locked, ``Live unlocked`` once met."""
    count = int(live_creator_threshold() if threshold is None else threshold)
    done = max(0, int(completed or 0))
    if done >= count:
        return "Live unlocked"
    return f"{done} of {count} verified members"


def live_unlock_status_line(completed, threshold=None) -> str:
    """The whole line a Live surface shows: the ask, then where they stand.

    Composed here rather than at each call site so an unlocked creator is never
    told to go and invite people they have already invited.
    """
    count = int(live_creator_threshold() if threshold is None else threshold)
    if max(0, int(completed or 0)) >= count:
        return "Live unlocked."
    return f"{live_unlock_sentence(count)} {live_progress_label(completed, count)}."


PRIVILEGE_LEVELS = [
    "Visitor",
    "Member",
    "Trusted Member",
    "Verified User",
    "Creator",
    "Teacher",
    "Marketplace Seller",
    "Livestream Eligible",
    "Partner Creator",
    "Platform Ambassador",
    "Founder / Owner",
]


LEVEL_THRESHOLDS = [
    (96, "Platform Ambassador"),
    (82, "Partner Creator"),
    (74, "Livestream Eligible"),
    (66, "Marketplace Seller"),
    (58, "Teacher"),
    (50, "Creator"),
    (40, "Verified User"),
    (28, "Trusted Member"),
    (15, "Member"),
    (0, "Visitor"),
]


def level_for_trust(score, verification_types=None, referral_count=0):
    verification_types = set(verification_types or [])
    score = int(score or 0)
    level = "Visitor"
    for threshold, name in LEVEL_THRESHOLDS:
        if score >= threshold:
            level = name
            break
    if "teacher" in verification_types and PRIVILEGE_LEVELS.index(level) < PRIVILEGE_LEVELS.index("Teacher"):
        level = "Teacher"
    if "seller" in verification_types and PRIVILEGE_LEVELS.index(level) < PRIVILEGE_LEVELS.index("Marketplace Seller"):
        level = "Marketplace Seller"
    if int(referral_count or 0) >= live_creator_threshold() and PRIVILEGE_LEVELS.index(level) < PRIVILEGE_LEVELS.index("Livestream Eligible"):
        level = "Livestream Eligible"
    return level


def get_user_privileges(user_id=None, trust_score=0, current_level="", referral_count=0, verification_types=None, live_status="locked"):
    verification_types = set(verification_types or [])
    if current_level in {"Founder / Owner", "Owner", "Founder"}:
        level = "Founder / Owner"
        level_index = PRIVILEGE_LEVELS.index(level)
        return {
            "user_id": user_id,
            "current_level": level,
            "can_post": True,
            "can_comment": True,
            "can_react": True,
            "can_message": True,
            "can_follow": True,
            "can_create_groups": True,
            "can_create_group": True,
            "can_create_spaces": True,
            "can_create_space": True,
            "can_sell_marketplace": True,
            "can_sell": True,
            "can_teach": True,
            "can_upload_images": True,
            "can_upload_videos": True,
            "can_upload_video": True,
            "can_go_live": True,
            "can_use_creator_filters": True,
            "can_create_reels": True,
            "can_pin_posts": True,
            "can_feature_posts": True,
            "can_host_live_rooms": True,
            "can_host_room": True,
            "can_receive_fan_messages": True,
            "can_access_creator_analytics": True,
            "can_access_teacher_tools": True,
            "can_access_marketplace_tools": True,
            "max_video_duration": 600,
            "max_upload_mb": 250,
            "profile_badges": ["Owner", "Founder", "Platform Ambassador"],
            "required_next_steps": [],
        }
    level = "Visitor" if current_level == "New User" else current_level or level_for_trust(trust_score, verification_types, referral_count)
    level_index = PRIVILEGE_LEVELS.index(level) if level in PRIVILEGE_LEVELS else 0
    referral_count = int(referral_count or 0)
    trust_score = int(trust_score or 0)
    live_threshold = live_creator_threshold()
    live_unlocked = live_status in {"eligible", "approved"} or referral_count >= live_threshold or level_index >= PRIVILEGE_LEVELS.index("Livestream Eligible")

    next_steps = []
    if referral_count < live_threshold:
        short = live_threshold - referral_count
        next_steps.append(f"Invite {short} more real member{'' if short == 1 else 's'} to unlock Live.")
    if trust_score < 50:
        next_steps.append("Complete your profile and keep posting helpful PulseSoc content.")
    if "identity" not in verification_types:
        next_steps.append("Earn verified trust with identity, creator, teacher, seller, or safety verification.")

    return {
        "user_id": user_id,
        "current_level": level,
        "can_post": level_index >= PRIVILEGE_LEVELS.index("Member"),
        "can_comment": level_index >= PRIVILEGE_LEVELS.index("Member"),
        "can_react": level_index >= PRIVILEGE_LEVELS.index("Member"),
        "can_message": level_index >= PRIVILEGE_LEVELS.index("Member"),
        "can_follow": level_index >= PRIVILEGE_LEVELS.index("Member"),
        "can_create_groups": level_index >= PRIVILEGE_LEVELS.index("Member"),
        "can_create_spaces": level_index >= PRIVILEGE_LEVELS.index("Creator"),
        "can_sell_marketplace": level_index >= PRIVILEGE_LEVELS.index("Marketplace Seller") or "seller" in verification_types,
        "can_go_live": bool(live_unlocked),
        "can_sell": level_index >= PRIVILEGE_LEVELS.index("Marketplace Seller") or "seller" in verification_types,
        "can_teach": level_index >= PRIVILEGE_LEVELS.index("Teacher") or "teacher" in verification_types,
        "can_create_group": level_index >= PRIVILEGE_LEVELS.index("Member"),
        "can_upload_images": level_index >= PRIVILEGE_LEVELS.index("Member"),
        "can_upload_videos": level_index >= PRIVILEGE_LEVELS.index("Member"),
        "can_upload_video": level_index >= PRIVILEGE_LEVELS.index("Member"),
        "can_create_space": level_index >= PRIVILEGE_LEVELS.index("Creator"),
        "can_host_room": level_index >= PRIVILEGE_LEVELS.index("Creator"),
        "can_use_creator_filters": level_index >= PRIVILEGE_LEVELS.index("Creator"),
        "can_create_reels": level_index >= PRIVILEGE_LEVELS.index("Creator"),
        "can_pin_posts": level_index >= PRIVILEGE_LEVELS.index("Partner Creator"),
        "can_feature_posts": level_index >= PRIVILEGE_LEVELS.index("Platform Ambassador"),
        "can_host_live_rooms": bool(live_unlocked),
        "can_receive_fan_messages": level_index >= PRIVILEGE_LEVELS.index("Creator"),
        "can_access_creator_analytics": level_index >= PRIVILEGE_LEVELS.index("Creator"),
        "can_access_teacher_tools": level_index >= PRIVILEGE_LEVELS.index("Teacher") or "teacher" in verification_types,
        "can_access_marketplace_tools": level_index >= PRIVILEGE_LEVELS.index("Marketplace Seller") or "seller" in verification_types,
        "max_video_duration": 45 if level_index < PRIVILEGE_LEVELS.index("Creator") else 180,
        "max_upload_mb": 25 if level_index < PRIVILEGE_LEVELS.index("Creator") else 100,
        "profile_badges": sorted(verification_types) + ([level] if level else []),
        "required_next_steps": next_steps,
    }
