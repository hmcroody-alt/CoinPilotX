"""Versioned campaign configuration for Progress OS.

A campaign is the *rules of the game* for one progression program. It is
versioned because the rules are a promise made to users: someone who started
the Founding Member Challenge under a 30-referral target must not silently
wake up needing 40. Changing the rules means publishing a new version, not
editing the old one — every qualification, milestone and reward row records
the ``campaign_version`` it was decided under, so a historical decision stays
explainable after the rules move on.

Why a config object rather than constants scattered through the engine
---------------------------------------------------------------------
The mission this module was written for asked for a program that can evolve.
Constants inline in the qualification code would mean the only way to run a
second campaign is to fork the engine. Here the engine is generic and the
campaign is data, so a future "Creator Challenge" is a new row, not a new
codebase.

What is deliberately NOT configurable
-------------------------------------
Integrity rules are not campaign knobs. A campaign may not configure away
the requirement that posting days are distinct, that a referred user has
exactly one referrer, or that a reward cycle pays once. Those live in the
engine because they are what makes the numbers mean anything.
"""

from __future__ import annotations

from typing import Optional

#: Milestones are awarded at most once per user per campaign, forever.
ONE_TIME = "one_time"

#: Milestone unlock kinds. These name what the milestone *unlocks*; the
#: engine maps them onto existing canonical grant paths and never invents a
#: new benefit surface of its own.
RECOGNITION = "recognition"          # a badge / status only
CREATOR_PERK = "creator_perk"        # a profile or creator capability
LIVE_ELIGIBILITY = "live_access"     # the actual Live Creator unlock
FOUNDING_STATUS = "founding_status"  # permanent historical standing


class Milestone:
    """One rung of the ladder.

    ``threshold`` is a count of QUALIFIED referrals — never signups. The
    distinction is the whole point of the program: an unqualified signup is
    worth zero here no matter how many of them there are.
    """

    __slots__ = ("key", "label", "threshold", "kind", "badge_key",
                 "entitlement_key", "description")

    def __init__(self, key: str, label: str, threshold: int, kind: str,
                 badge_key: str = "", entitlement_key: str = "",
                 description: str = ""):
        self.key = key
        self.label = label
        self.threshold = int(threshold)
        self.kind = kind
        self.badge_key = badge_key
        self.entitlement_key = entitlement_key
        self.description = description

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "threshold": self.threshold,
            "kind": self.kind,
            "badge_key": self.badge_key,
            "entitlement_key": self.entitlement_key,
            "description": self.description,
        }


class Campaign:
    """An immutable, versioned set of program rules."""

    __slots__ = ("campaign_id", "campaign_version", "name", "active_from",
                 "active_until", "qualification_target", "reward_interval",
                 "reward_amount_cents", "reward_currency",
                 "required_posting_days", "attribution_window_days",
                 "milestones", "status")

    def __init__(self, campaign_id: str, campaign_version: int, name: str, *,
                 qualification_target: int, reward_interval: int,
                 reward_amount_cents: int, reward_currency: str = "usd",
                 required_posting_days: int = 2,
                 attribution_window_days: int = 0,
                 active_from: str = "", active_until: str = "",
                 status: str = "active",
                 milestones: tuple = ()):
        self.campaign_id = campaign_id
        self.campaign_version = int(campaign_version)
        self.name = name
        self.qualification_target = int(qualification_target)
        self.reward_interval = int(reward_interval)
        self.reward_amount_cents = int(reward_amount_cents)
        self.reward_currency = reward_currency
        self.required_posting_days = int(required_posting_days)
        self.attribution_window_days = int(attribution_window_days)
        self.active_from = active_from
        self.active_until = active_until
        self.status = status
        self.milestones = tuple(sorted(milestones, key=lambda m: m.threshold))

    # -- legacy reward cycles ----------------------------------------------
    # Retained only so campaigns recorded under an earlier, monetary version
    # stay readable. A campaign with ``reward_interval=0`` — which is every
    # campaign shipping today — earns no cycles at any count.
    def cycles_earned(self, qualified_count: int) -> int:
        n = int(qualified_count or 0)
        if n <= 0 or self.reward_interval <= 0:
            return 0
        return n // self.reward_interval

    # -- milestones --------------------------------------------------------
    def live_threshold(self) -> int:
        """The certified-invite count that unlocks Live Creator.

        Derived from the ladder rather than hardcoded anywhere else, so the
        server-side gate and the displayed rung can never disagree.
        """
        for m in self.milestones:
            if m.kind == LIVE_ELIGIBILITY:
                return m.threshold
        return self.qualification_target

    def milestones_reached(self, qualified_count: int) -> list:
        n = int(qualified_count or 0)
        return [m for m in self.milestones if n >= m.threshold]

    def next_milestone(self, qualified_count: int) -> Optional[Milestone]:
        n = int(qualified_count or 0)
        for m in self.milestones:
            if n < m.threshold:
                return m
        return None

    def as_dict(self) -> dict:
        return {
            "campaign_id": self.campaign_id,
            "campaign_version": self.campaign_version,
            "name": self.name,
            "status": self.status,
            "active_from": self.active_from,
            "active_until": self.active_until,
            "qualification_target": self.qualification_target,
            "reward_interval": self.reward_interval,
            "reward_amount_cents": self.reward_amount_cents,
            "reward_currency": self.reward_currency,
            "required_posting_days": self.required_posting_days,
            "milestones": [m.as_dict() for m in self.milestones],
        }


# --- the shipped campaign ---------------------------------------------------
# THE FOUNDING PATH IS NOT MONETARY.
# ``reward_interval=0`` is the kill switch: ``cycles_earned`` returns 0 for
# every count, so no new reward cycle is ever created and no reward hand-off
# ever fires. The fields stay on the object because historical cycle rows
# recorded under v1 must remain explainable, not because anything still earns.
#
# WHY THE ID DOES NOT CHANGE
# ``campaign_id`` is the partition key on every Progress OS table. Minting a
# new id would orphan every qualification, posting day and milestone award and
# reset each member to zero certified invites. The rules moved, so the version
# moves; the identity of the program does not.
#
# EVERY MILESTONE UNLOCKS SOMETHING THAT ALREADY EXISTS. Live Creator maps to
# the existing livestream gate, the profile rung to an existing entitlement,
# and the rest to badges the badge store already understands. Nothing here
# promises reach, views, followers or ranking, because nothing here can.
FOUNDING_PATH = Campaign(
    "FOUNDING_MEMBER_CHALLENGE_V1", 2, "PulseSoc Founding Path",
    qualification_target=30,
    reward_interval=0,
    reward_amount_cents=0,
    reward_currency="",
    required_posting_days=2,
    milestones=(
        Milestone(
            "live_creator", "Live Creator", 2, LIVE_ELIGIBILITY,
            badge_key="live_creator",
            description="You can broadcast LIVE on PulseSoc.",
        ),
        Milestone(
            "early_supporter", "Early Supporter", 5, RECOGNITION,
            badge_key="early_supporter",
            description="Recognition for the first five people you brought in.",
        ),
        # Key preserved from v1 so members who already earned this rung keep
        # their award row and their entitlement; only the label moved.
        Milestone(
            "creator_perk", "Rising Creator", 10, CREATOR_PERK,
            badge_key="creator_perk",
            entitlement_key="premium.profile.customization",
            description="Unlocks profile customization on your account.",
        ),
        Milestone(
            "network_builder", "Network Builder", 15, RECOGNITION,
            badge_key="network_builder",
            description="Recognition for building real reach into the network.",
        ),
        Milestone(
            "priority_creator", "Priority Creator", 20, RECOGNITION,
            badge_key="priority_creator",
            description="Priority Creator standing on your profile.",
        ),
        Milestone(
            "founding_creator", "Founding Creator", 25, RECOGNITION,
            badge_key="founding_creator",
            description="Founding Creator standing on your profile.",
        ),
        Milestone(
            "founding_member", "Founding Member", 30, FOUNDING_STATUS,
            badge_key="founding_member",
            description=(
                "The highest rung of the Founding Path. Permanent Founding "
                "Generation status, kept on your profile for good."
            ),
        ),
    ),
)

_CAMPAIGNS = {FOUNDING_PATH.campaign_id: FOUNDING_PATH}

DEFAULT_CAMPAIGN_ID = FOUNDING_PATH.campaign_id


def get(campaign_id: str = "") -> Campaign:
    """Look up a campaign, defaulting to the shipped one.

    Unknown ids fall back to the default rather than raising: a stale client
    asking about a retired campaign should see the current program, not a 500.
    """
    return _CAMPAIGNS.get(campaign_id or DEFAULT_CAMPAIGN_ID, FOUNDING_PATH)


def all_campaigns() -> list:
    return [c.as_dict() for c in _CAMPAIGNS.values()]
