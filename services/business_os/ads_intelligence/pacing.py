"""Budget pacing — spreads delivery through the day, never authorises money.

A campaign with a daily budget and no pacing spends it in the first hour, in
whatever inventory happens to be available at 00:05 UTC. The advertiser reaches
the people who were awake, the platform sells its cheapest morning inventory at
the same rate as its best evening inventory, and the campaign report says the
budget was fully used. Everybody's numbers look fine and the outcome is bad.

Pacing fixes that by comparing spend so far against where it *should* be by now
and throttling delivery when it is ahead.

Pacing is not a money authority
--------------------------------
This module reads ``spend.get_campaign_spend``, which derives every figure live
from the immutable ledger. It never charges, reserves, releases, or writes a
financial record, and it does not decide whether a campaign can afford an
impression — the canonical billing path already owns that and remains the only
thing standing between a campaign and an overspend. Pacing can only ever
*reduce* delivery below what the budget would otherwise permit. A test greps
this module to keep it that way.

That separation is what makes the failure posture safe. Pacing fails **open**:
any error returns a full-delivery throttle and lets the canonical budget gates
do their job. Failing closed would let a bad read stop every campaign on the
platform, whereas failing open cannot overspend, because pacing was never what
was preventing overspend.

Throttling is deterministic and per-opportunity
------------------------------------------------
``admits`` hashes the opportunity, not the viewer. Hashing the viewer would
make throttling systematic: the same slice of people would be excluded from a
campaign every time it paced down, so a throttled campaign would reach a
biased audience rather than a smaller one. Hashing the opportunity spreads the
reduction evenly across everybody and still gives the same answer for the same
request, which is what makes it reproducible in a replay.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from . import taxonomy

_LOG = logging.getLogger(__name__)

#: Ratio of actual spend to target spend at this point in the day. Inside the
#: band a campaign is on target and nothing happens — a pacer that reacts to
#: every small deviation oscillates instead of pacing.
ON_TARGET_LOW = 0.85
ON_TARGET_HIGH = 1.15

#: Above this ratio the campaign is far enough ahead to warrant hard limiting.
LIMITED_RATIO = 1.50

#: Delivery is never throttled to nothing by pacing alone. A campaign that is
#: merely ahead of schedule should slow down, not disappear — going to zero
#: makes a campaign invisible for hours and is indistinguishable, from the
#: advertiser's side, from being broken. Only genuine exhaustion reaches zero,
#: and that is the canonical budget gate's call rather than this module's.
MIN_THROTTLE = 0.10

#: Early in the day the elapsed fraction is tiny, so the ratio of spend to
#: target explodes on the first impression and every campaign looks like it is
#: overpacing. Below this fraction of the day, pacing holds off.
MIN_ELAPSED_FRACTION = 0.05


def _now(now: Optional[datetime] = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def elapsed_fraction(now: Optional[datetime] = None) -> float:
    """How far through the UTC day we are, in 0..1."""
    moment = _now(now)
    seconds = moment.hour * 3600 + moment.minute * 60 + moment.second
    return max(0.0, min(1.0, seconds / 86400.0))


def target_spend_cents(daily_budget_cents: Any,
                       now: Optional[datetime] = None) -> int:
    """Where an evenly-paced campaign's spend should be by now."""
    budget = _int(daily_budget_cents)
    if budget <= 0:
        return 0
    return int(budget * elapsed_fraction(now))


def assess(daily_budget_cents: Any, observed_spend_cents: Any, *,
           now: Optional[datetime] = None, exhausted: bool = False) -> dict:
    """Classify pacing and produce a throttle factor, with the reason attached.

    Pure. Every input is passed in, so the pacing rules can be tested across a
    whole simulated day without a database or a clock.
    """
    budget = _int(daily_budget_cents)
    spent = _int(observed_spend_cents)

    if exhausted:
        return {"state": "EXHAUSTED", "throttle": 0.0,
                "reason": "the canonical budget gate reports this campaign "
                          "exhausted",
                "target_cents": budget, "observed_cents": spent, "ratio": None}

    if budget <= 0:
        # No daily budget means nothing to pace against. Not an error, and not
        # a reason to throttle: the campaign-level budget still applies.
        return {"state": "ON_TARGET", "throttle": 1.0,
                "reason": "no daily budget is configured, so there is nothing "
                          "to pace against",
                "target_cents": 0, "observed_cents": spent, "ratio": None}

    elapsed = elapsed_fraction(now)
    if elapsed < MIN_ELAPSED_FRACTION:
        return {"state": "ON_TARGET", "throttle": 1.0,
                "reason": "too early in the day for a spend ratio to mean "
                          "anything",
                "target_cents": target_spend_cents(budget, now),
                "observed_cents": spent, "ratio": None}

    target = target_spend_cents(budget, now)
    if target <= 0:
        return {"state": "ON_TARGET", "throttle": 1.0,
                "reason": "no spend target yet", "target_cents": 0,
                "observed_cents": spent, "ratio": None}

    ratio = spent / float(target)
    if ratio < ON_TARGET_LOW:
        return {"state": "UNDERPACING", "throttle": 1.0,
                "reason": f"spend is at {ratio:.0%} of where even pacing would "
                          f"put it, so delivery is unrestricted",
                "target_cents": target, "observed_cents": spent, "ratio": ratio}
    if ratio <= ON_TARGET_HIGH:
        return {"state": "ON_TARGET", "throttle": 1.0,
                "reason": f"spend is at {ratio:.0%} of target, inside the "
                          f"tolerance band",
                "target_cents": target, "observed_cents": spent, "ratio": ratio}

    # Ahead of schedule. Throttle inversely to how far ahead, floored so a
    # campaign slows rather than vanishes.
    throttle = max(MIN_THROTTLE, min(1.0, 1.0 / ratio))
    state = "LIMITED" if ratio >= LIMITED_RATIO else "OVERPACING"
    return {"state": state, "throttle": throttle,
            "reason": f"spend is at {ratio:.0%} of target, so delivery is "
                      f"throttled to {throttle:.0%}",
            "target_cents": target, "observed_cents": spent, "ratio": ratio}


def admits(campaign_id: Any, opportunity_key: Any, throttle: float) -> bool:
    """Whether this specific opportunity survives the throttle.

    Deterministic on the opportunity rather than the viewer: throttling by
    viewer would exclude the same people every time and hand the campaign a
    biased audience instead of a smaller one.
    """
    try:
        factor = float(throttle)
    except (TypeError, ValueError):
        return True
    if factor >= 1.0:
        return True
    if factor <= 0.0:
        return False
    raw = f"{campaign_id}:{opportunity_key}".encode("utf-8")
    bucket = int(hashlib.sha256(raw).hexdigest()[:8], 16) / 0xFFFFFFFF
    return bucket < factor


def state_for(campaign_id: Any, *, conn=None, currency: str = "usd",
              daily_budget_cents: Any = None,
              now: Optional[datetime] = None) -> dict:
    """Live pacing state for one campaign, read from the canonical spend view.

    Fails open. If the spend read is unavailable this returns a full-delivery
    throttle, because pacing was never the thing preventing an overspend — the
    canonical billing path is, and it is still there.
    """
    try:
        from services.business_os.advertising import spend as _spend
        view = _spend.get_campaign_spend(campaign_id, currency, conn=conn)
    except Exception:
        _LOG.warning("ADS_INTEL_PACING_SPEND_READ_FAILED campaign=%s",
                     campaign_id, exc_info=True)
        return {"state": "ON_TARGET", "throttle": 1.0, "degraded": True,
                "reason": "spend could not be read, so pacing defers to the "
                          "canonical budget gates",
                "target_cents": 0, "observed_cents": 0, "ratio": None}

    budget = daily_budget_cents
    if budget is None:
        budget = view.get("daily_budget_cents")
    result = assess(budget, view.get("spent_cents"), now=now,
                    exhausted=bool(view.get("budget_exhausted")))
    result["campaign_id"] = campaign_id
    result["degraded"] = False
    return result


def record(conn, campaign_id: Any, assessment: dict, *,
           now: Optional[datetime] = None) -> bool:
    """Persist a pacing observation for diagnostics. Never gates on success.

    This is a record of what pacing decided, not the authority for it: the live
    decision is always recomputed from the canonical spend view. Storing it
    means "why did my campaign slow down at 3pm" is answerable after the fact.
    """
    cid = str(campaign_id or "").strip()
    if not cid:
        return False
    moment = _now(now)
    day = moment.strftime("%Y-%m-%d")
    try:
        conn.execute("DELETE FROM ads_intel_campaign_pacing "
                     "WHERE campaign_id = ? AND day = ?", (cid, day))
        conn.execute(
            "INSERT INTO ads_intel_campaign_pacing "
            "(pacing_id, campaign_id, day, pacing_state, daily_budget_cents, "
            "observed_spend_cents, target_spend_cents, delivery_ratio, "
            "throttle_factor, computed_at, processing_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"{cid}:{day}", cid, day, assessment.get("state") or "ON_TARGET",
             _int(assessment.get("budget_cents")),
             _int(assessment.get("observed_cents")),
             _int(assessment.get("target_cents")),
             assessment.get("ratio"),
             float(assessment.get("throttle", 1.0)),
             _iso(moment), taxonomy.PROCESSING_VERSION))
        conn.commit()
        return True
    except Exception:
        _LOG.warning("ADS_INTEL_PACING_WRITE_FAILED campaign=%s", cid,
                     exc_info=True)
        return False


def explain(assessment: dict) -> str:
    """One sentence an advertiser can act on."""
    state = assessment.get("state")
    if state == "EXHAUSTED":
        return "This campaign has spent its budget and has stopped delivering."
    if state == "UNDERPACING":
        return ("This campaign is spending more slowly than its daily budget "
                "allows. Delivery is unrestricted; the limit is how many "
                "matching opportunities exist.")
    if state == "ON_TARGET":
        return "This campaign is spending evenly through the day."
    if state == "OVERPACING":
        return ("This campaign is ahead of an even spend for this time of day, "
                "so delivery has been slowed to make the budget last.")
    if state == "LIMITED":
        return ("This campaign is well ahead of an even spend, so delivery has "
                "been limited sharply to protect the rest of the day.")
    return "Pacing state is unknown."
