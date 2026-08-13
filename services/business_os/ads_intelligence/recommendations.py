"""Recommendations — rules that explain themselves and never spend money.

Every recommendation here is derived from a finding in ``diagnostics.py`` by a
rule you can read. There is no model, no score, and nothing that could be
described as the system "deciding" anything. That is a deliberate ceiling: a
recommendation an advertiser cannot interrogate is one they either follow
blindly or ignore entirely, and both are worse than a plain rule.

The money rule is structural, not procedural
---------------------------------------------
No autonomy level, no configuration, and no argument to any function in this
module can cause money to move. This is enforced three ways rather than one,
because the failure would be expensive and silent:

* ``MONEY_ACTIONS`` names every action that changes spend. Membership makes an
  action permanently manual — ``max_autonomy_for`` returns ``LEVEL_RECOMMEND``
  for them regardless of the account's configured level.
* ``apply`` does not exist. This module returns proposals; something else with
  the authority carries them out. There is no code path from a rule firing to a
  setting changing.
* A test greps this module for the vocabulary of spending.

The reason for all three is the same. "Increase your budget" is a reasonable
thing to *suggest* and never a reasonable thing for an analytics layer to *do*,
because the layer proposing the spend is the layer that benefits from it, and a
system that can quietly act on that conflict will eventually be caught doing so.

Autonomy is about reversibility, not confidence
------------------------------------------------
The levels below are graded by how easy the change is to undo, not by how sure
the rule is. A confident recommendation to pause a campaign is still a change
somebody must approve, because confidence is not the same as consent.

Recommendations expire
-----------------------
Each carries the evidence it was computed from. Advice built on last week's
numbers is not advice, and a stale recommendation that says "your creative is
worn out" about a creative that has since been replaced destroys trust in every
other recommendation on the page.
"""

from __future__ import annotations

from typing import Any

from . import diagnostics, taxonomy

#: Autonomy levels. Higher may do more, and no level may do money.
LEVEL_OBSERVE = 0      # gather and report only
LEVEL_RECOMMEND = 1    # propose, human applies
LEVEL_ASSIST = 2       # apply reversible non-money changes, with approval
LEVEL_AUTO = 3         # apply reversible non-money changes automatically

AUTONOMY_LEVELS = (LEVEL_OBSERVE, LEVEL_RECOMMEND, LEVEL_ASSIST, LEVEL_AUTO)

#: Actions that change what an advertiser is charged. Permanently manual.
#: Pausing is here despite *saving* money: a campaign paused automatically is a
#: campaign that stopped earning the advertiser business without them agreeing,
#: and "we saved you money" is not a defence anybody accepts afterwards.
MONEY_ACTIONS = frozenset({
    "increase_daily_budget", "decrease_daily_budget", "increase_total_budget",
    "pause_campaign", "resume_campaign", "add_funds", "change_bid",
    "change_rate", "enable_auto_top_up",
})

#: Reversible, non-financial changes. These are the only things automation may
#: ever touch, and even then only at LEVEL_AUTO.
REVERSIBLE_ACTIONS = frozenset({
    "rotate_creative", "widen_placements", "extend_schedule",
    "broaden_audience", "refresh_creative",
})


def max_autonomy_for(action: str) -> int:
    """The highest level at which this action may be taken without a person.

    A money action tops out at ``LEVEL_RECOMMEND`` whatever the account is
    configured for. This function is the single place that decision is made, so
    a caller cannot arrive at a different answer by reasoning about it locally.
    """
    name = str(action or "").strip()
    if name in MONEY_ACTIONS:
        return LEVEL_RECOMMEND
    if name in REVERSIBLE_ACTIONS:
        return LEVEL_AUTO
    return LEVEL_RECOMMEND


def may_apply_automatically(action: str, *, autonomy_level: int) -> bool:
    """Whether automation at this level may carry out this action unattended.

    ``LEVEL_ASSIST`` deliberately answers False: assisting means the change is
    prepared and a person approves it, which is not the same as acting alone.
    Only ``LEVEL_AUTO`` acts alone, and only for actions whose ceiling is
    ``LEVEL_AUTO`` — which by construction excludes everything that touches
    money.
    """
    try:
        level = int(autonomy_level)
    except (TypeError, ValueError):
        return False
    return level >= LEVEL_AUTO and max_autonomy_for(action) >= LEVEL_AUTO


def recommendation(code: str, *, action: str, headline: str, rationale: str,
                   finding_code: str, evidence: dict,
                   confidence: str = "measured") -> dict:
    """One proposal. ``requires_human`` is computed, never passed in."""
    return {
        "code": code,
        "action": action,
        "headline": headline,
        "rationale": rationale,
        "from_finding": finding_code,
        "evidence": evidence,
        "confidence": confidence,
        "affects_spend": action in MONEY_ACTIONS,
        "reversible": action in REVERSIBLE_ACTIONS,
        "max_autonomy": max_autonomy_for(action),
        "requires_human": max_autonomy_for(action) < LEVEL_AUTO,
        "version": taxonomy.RECOMMENDATION_VERSION,
    }


#: finding code (or prefix) → the proposal it produces. Kept as data so the
#: whole rule set is readable in one screen; a rule set spread across branching
#: code is one nobody audits.
_RULES = {
    "NOT_DELIVERING_ACCOUNT_UNVERIFIED": (
        "VERIFY_ACCOUNT", "verify_account",
        "Finish verifying your advertiser account",
        "Campaigns cannot serve until the account behind them is verified. "
        "Nothing about this campaign's setup needs to change."),
    "NOT_DELIVERING_CAMPAIGN_IN_REVIEW": (
        "WAIT_FOR_REVIEW", "wait",
        "Wait for review to finish",
        "This is with us, not you. Changing the campaign now restarts the "
        "review rather than speeding it up."),
    "NOT_DELIVERING_AUDIENCE_MISMATCH": (
        "BROADEN_AUDIENCE", "broaden_audience",
        "Widen your audience",
        "The audience you selected did not match anybody in the placements "
        "you chose, so there was never an opportunity to win."),
    "NOT_DELIVERING_CREATIVE_UNAVAILABLE": (
        "FIX_CREATIVE", "refresh_creative",
        "Re-upload the creative",
        "The creative could not be loaded at delivery time, so the campaign "
        "had nothing to show even where it was eligible."),
    "NOT_DELIVERING_WALLET_EMPTY": (
        "ADD_FUNDS", "add_funds",
        "Add funds to your advertising balance",
        "Delivery stops when the balance is empty. This one is about the "
        "balance rather than the campaign."),
    "NOT_DELIVERING_BUDGET_EXHAUSTED": (
        "RAISE_BUDGET", "increase_total_budget",
        "Raise the budget if you want this to keep running",
        "The campaign spent what you allocated. It is doing what you asked."),
    "BUDGET_EXHAUSTED": (
        "RAISE_DAILY_BUDGET", "increase_daily_budget",
        "Consider a higher daily budget",
        "This campaign reaches its daily budget and stops. If the results are "
        "worth it to you, a higher daily budget buys more of the same day."),
    "PACING_THROTTLED": (
        "PACING_IS_WORKING", "none",
        "No action needed",
        "Delivery is being slowed so the budget lasts the day. Raising the "
        "budget would raise the ceiling being paced against; it is not a fault "
        "to fix."),
    "UNDERSPENDING": (
        "WIDEN_REACH", "widen_placements",
        "Add placements or widen the audience",
        "Your budget is not the limit — the number of matching opportunities "
        "is. More budget would not be spent."),
    "CREATIVE_REJECTED": (
        "REPLACE_REJECTED_CREATIVE", "rotate_creative",
        "Replace this creative",
        "Enough people have hidden or reported it that we have stopped "
        "showing it. No amount of budget changes that, and a different "
        "creative starts clean."),
    "CREATIVE_FATIGUED": (
        "REPLACE_CREATIVE", "rotate_creative",
        "Rotate in a new creative",
        "This creative's click rate has fallen against its own earlier "
        "performance with this audience. A new creative usually recovers it; "
        "more budget on the same one usually does not."),
    "CREATIVE_WEARING": (
        "PREPARE_NEW_CREATIVE", "rotate_creative",
        "Prepare a replacement creative",
        "Performance is drifting down but has not fallen far. Having the next "
        "creative ready avoids the gap when it does."),
    "FUNNEL_DROP_SERVED": (
        "REVIEW_TARGETING", "widen_placements",
        "Look at targeting, pacing and frequency",
        "The opportunities existed and this campaign rarely won them. That is "
        "upstream of the creative — nothing about the ad itself is being "
        "judged at that point."),
    "FUNNEL_DROP_RENDERED": (
        "FIX_MEDIA", "refresh_creative",
        "Check that the creative loads",
        "The ad was chosen and the app did not draw it. That is almost always "
        "a media file that is too large or fails to load."),
    "FUNNEL_DROP_VIEWABLE": (
        "REVIEW_PLACEMENTS", "widen_placements",
        "Look at where your ads are appearing",
        "Your ads are being drawn but scrolled past before they count as "
        "seen. That is usually the placement rather than the creative."),
    "FUNNEL_DROP_CLICK": (
        "REVIEW_CREATIVE", "refresh_creative",
        "Look at the creative and the match to your audience",
        "People are seeing the ad and not acting. Neither budget nor "
        "placement changes that."),
    "FUNNEL_DROP_CONVERSION": (
        "REVIEW_DESTINATION", "none",
        "Look at where the ad sends people",
        "People are clicking and not completing. The drop is after they leave "
        "us, so it is the destination rather than the ad."),
    "NOT_ENOUGH_DELIVERY": (
        "WAIT_FOR_DATA", "wait",
        "Let it run before changing anything",
        "There is not enough delivery yet to tell a real effect from noise. "
        "Changes made now cannot be evaluated."),
    "INVALID_TRAFFIC_EXCLUDED": (
        "NO_ACTION_INVALID_TRAFFIC", "none",
        "No action needed",
        "We excluded this activity and did not charge you for it. It is "
        "reported so your numbers and ours agree."),
}


def for_finding(item: dict) -> Any:
    """The proposal for one finding, or ``None`` when there is nothing to say.

    A finding with no rule produces nothing rather than generic advice.
    "Consider optimising your campaign" is what a system says when it has
    nothing, and saying it costs the credibility of the recommendations that do.
    """
    code = str((item or {}).get("code") or "")
    rule = _RULES.get(code)
    if rule is None:
        return None
    rec_code, action, headline, rationale = rule
    return recommendation(
        rec_code, action=action, headline=headline, rationale=rationale,
        finding_code=code, evidence=(item or {}).get("evidence") or {},
        confidence=(item or {}).get("confidence") or "measured")


def for_campaign(conn, campaign_id: Any, *, creative_id: Any = None,
                 daily_budget_cents: Any = None,
                 autonomy_level: int = LEVEL_RECOMMEND, now=None) -> dict:
    """Diagnose, then propose. Returns proposals ordered by the finding severity.

    ``autonomy_level`` annotates what could be automated; it never changes what
    is proposed. A system that recommends different things depending on whether
    it is allowed to act on them is choosing its advice to widen its own remit.
    """
    diagnosis = diagnostics.diagnose(
        conn, campaign_id, creative_id=creative_id,
        daily_budget_cents=daily_budget_cents, now=now)

    proposals = []
    seen = set()
    for item in diagnosis.get("findings") or []:
        proposal = for_finding(item)
        if proposal is None or proposal["code"] in seen:
            continue
        seen.add(proposal["code"])
        proposal["can_be_applied_automatically"] = may_apply_automatically(
            proposal["action"], autonomy_level=autonomy_level)
        proposals.append(proposal)

    return {
        "campaign_id": diagnosis.get("campaign_id"),
        "recommendations": proposals,
        "primary": proposals[0] if proposals else None,
        "autonomy_level": autonomy_level,
        "any_affects_spend": any(p["affects_spend"] for p in proposals),
        "diagnosis": diagnosis,
        "version": taxonomy.RECOMMENDATION_VERSION,
    }


def explain(proposal: dict) -> str:
    """One paragraph an advertiser can decide from."""
    if not proposal:
        return "There is nothing we would change about this campaign."
    text = f"{proposal['headline']}. {proposal['rationale']}"
    if proposal.get("affects_spend"):
        text += (" This one changes what you spend, so we will not do it for "
                 "you — it is yours to decide.")
    return text
