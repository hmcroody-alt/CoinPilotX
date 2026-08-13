"""Why am I seeing this ad — read from the decision, never reconstructed.

The failure mode this module exists to avoid is subtle and extremely common:
an explanation generated *after the fact* by asking "what would plausibly have
caused this ad to be shown?". Such an explanation is always coherent, usually
flattering, and unrelated to what actually happened. It is a rationalisation
with a UI.

So every sentence here comes from ``ads_intel_delivery_decisions`` — the row
the ranker wrote at the moment it decided, including the score breakdown it
scored on. If the decision was made by the legacy rotation and carries no
breakdown, this module says the ad was shown in rotation rather than inventing
targeting that did not happen. "We do not have a detailed reason for this one"
is a true sentence and an acceptable one; a false detailed reason is neither.

Three rules about what may be said
-----------------------------------
**Never name another person.** Not the advertiser's staff, not a friend who
engaged with the ad, not "people like you who bought X". Social proof in an ad
explanation is a disclosure about somebody who did not consent to it.

**Never surface an inferred sensitive attribute.** The interest graph is built
from a closed taxonomy that excludes sensitive categories by construction, so
there is normally nothing to leak — but this module also refuses to echo a
category it does not recognise, so a future widening of the taxonomy cannot
quietly start telling people what the system thinks they are.

**Say what can be changed.** An explanation that ends without an action is a
notice, not a control. Every response carries the controls that actually exist
and actually work, mapped to the explicit negative events the interest graph is
required to honour.

Reading somebody else's explanation is not possible
----------------------------------------------------
The decision is looked up by id *and* subject. A decision that belongs to
another viewer answers exactly as a decision that never existed, because a
distinguishable "not yours" reveals that the id is real and therefore that
somebody was shown that ad.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from . import taxonomy

_LOG = logging.getLogger(__name__)

#: The controls offered alongside every explanation. Each maps to an event the
#: interest graph treats as an explicit negative, which is the only class of
#: signal permitted to suppress delivery. A control that logged nothing would be
#: worse than no control at all.
CONTROLS = (
    {"action": "not_interested", "event": "ad_not_interested",
     "label": "Show me fewer ads like this"},
    {"action": "hide", "event": "ad_hide",
     "label": "Hide this ad"},
    {"action": "report", "event": "ad_report",
     "label": "Report this ad"},
)

#: Component name in the score breakdown → the sentence a viewer reads. The
#: phrasing describes the *slot or the signal*, never the person: "this fits
#: what you were looking at" rather than "you are interested in fitness".
_COMPONENT_SENTENCES = {
    "context": "It matches the kind of content you were looking at.",
    "affinity": "You have engaged with similar things on PulseSoc before.",
    "quality": "This ad performs well with people who see it.",
    "exploration": "It is new, and we are showing it to a small number of "
                   "people to see how it does.",
}

#: Reasons the ad was shown that have nothing to do with the viewer at all.
_NON_PERSONAL_SENTENCES = {
    "legacy": "This ad was shown in a rotation of ads available for this "
              "space. We do not have a detailed reason for this one.",
    "no_breakdown": "This ad was eligible for this space. We do not have a "
                    "detailed reason for this one.",
}

#: Below this share of the total score a component did not meaningfully cause
#: the placement, and listing it pads the explanation with things that were not
#: the reason.
MIN_COMPONENT_SHARE = 0.15


def _loads(blob: Any) -> dict:
    if isinstance(blob, dict):
        return blob
    try:
        parsed = json.loads(blob or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _row_to_dict(row, cursor=None) -> dict:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    keys = ("decision_id", "subject_ref", "campaign_id", "creative_id",
            "occurred_at", "ranking_mode", "score", "score_breakdown_json",
            "exploration", "placement_key")
    return {k: row[i] for i, k in enumerate(keys) if i < len(row)}


def known_category(value: Any) -> Optional[str]:
    """Echo a category only if it is in the closed taxonomy.

    A category the taxonomy does not know is not shown to the viewer even if it
    is stored on the campaign. Advertisers supply free text in more places than
    anybody expects, and an explanation is the wrong surface to discover that.
    """
    text = str(value or "").strip().lower()
    return text if text in taxonomy.INTEREST_CATEGORIES else None


def reasons_from_breakdown(breakdown: Any, *, exploration: bool = False) -> list:
    """Turn a stored score breakdown into the sentences that caused it.

    Only components that actually carried the decision are listed, largest
    first. An explanation naming four reasons when one of them supplied 92% of
    the score is technically complete and practically misleading.
    """
    parsed = _loads(breakdown)
    components = {k: _float(v) for k, v in parsed.items()
                  if k in _COMPONENT_SENTENCES}
    total = sum(v for v in components.values() if v > 0)
    if total <= 0:
        return ([_COMPONENT_SENTENCES["exploration"]] if exploration
                else [_NON_PERSONAL_SENTENCES["no_breakdown"]])

    ordered = sorted(components.items(), key=lambda kv: kv[1], reverse=True)
    sentences = [_COMPONENT_SENTENCES[name] for name, value in ordered
                 if value > 0 and (value / total) >= MIN_COMPONENT_SHARE]
    if exploration and _COMPONENT_SENTENCES["exploration"] not in sentences:
        sentences.append(_COMPONENT_SENTENCES["exploration"])
    return sentences or [_NON_PERSONAL_SENTENCES["no_breakdown"]]


def explain_delivery(conn, decision_id: Any, *, subject_ref: Any) -> dict:
    """Why one viewer saw one ad. Returns ``{found, reasons, controls, ...}``.

    ``found`` is False both when the decision does not exist and when it belongs
    to somebody else — deliberately the same answer, because distinguishing them
    confirms that a given ad was shown to a given person.
    """
    decision = str(decision_id or "").strip()
    subject = str(subject_ref or "").strip()
    if not decision or not subject:
        return _not_found()

    try:
        row = conn.execute(
            "SELECT decision_id, subject_ref, campaign_id, creative_id, "
            "occurred_at, ranking_mode, score, score_breakdown_json, "
            "exploration, placement_key FROM ads_intel_delivery_decisions "
            "WHERE decision_id = ? AND subject_ref = ?",
            (decision, subject)).fetchone()
    except Exception:
        _LOG.warning("ADS_INTEL_TRANSPARENCY_READ_FAILED", exc_info=True)
        return _not_found(degraded=True)

    record = _row_to_dict(row)
    if not record:
        return _not_found()

    mode = str(record.get("ranking_mode") or "legacy")
    exploring = bool(record.get("exploration"))
    if mode == "legacy":
        # An honest "we do not know in detail" rather than a plausible story.
        reasons = [_NON_PERSONAL_SENTENCES["legacy"]]
    else:
        reasons = reasons_from_breakdown(record.get("score_breakdown_json"),
                                         exploration=exploring)

    return {
        "found": True,
        "decision_id": decision,
        "shown_at": record.get("occurred_at"),
        "placement": record.get("placement_key"),
        "reasons": reasons,
        "advertiser_targeted_you_personally": False,
        "uses_sensitive_categories": False,
        "sold_your_data": False,
        "controls": [dict(c) for c in CONTROLS],
        "ranking_mode": mode,
        "degraded": False,
    }


def _not_found(degraded: bool = False) -> dict:
    return {"found": False, "decision_id": None, "shown_at": None,
            "placement": None, "reasons": [], "controls": [dict(c)
                                                           for c in CONTROLS],
            "advertiser_targeted_you_personally": False,
            "uses_sensitive_categories": False, "sold_your_data": False,
            "ranking_mode": None, "degraded": degraded}


def render(result: dict) -> str:
    """The explanation as one block of text, for a surface without a list UI."""
    if not result.get("found"):
        return ("We do not have a record of this ad being shown to you.")
    lines = ["You are seeing this ad because:"]
    lines += [f"• {r}" for r in result.get("reasons") or []]
    lines.append("No advertiser was given your identity, and we do not use "
                 "sensitive categories to choose ads.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# What the system holds about a viewer
# --------------------------------------------------------------------------- #

def interest_disclosure(conn, subject_ref: Any, *, limit: int = 20) -> dict:
    """The categories the ad system associates with one person, for that person.

    Shown to the subject only. Categories outside the closed taxonomy are
    filtered rather than displayed, so a widening of the taxonomy cannot start
    telling somebody what the system decided they are without that being a
    deliberate change here.

    The raw scores are not exposed. A number invites the question "why 0.62"
    which has no answer a person can act on, whereas the category itself is
    exactly what the controls operate on.
    """
    subject = str(subject_ref or "").strip()
    if not subject:
        return {"subject_known": False, "categories": [], "degraded": False}
    try:
        rows = conn.execute(
            "SELECT category, score FROM ads_intel_interest_affinity "
            "WHERE subject_ref = ? AND score > 0 "
            "ORDER BY score DESC LIMIT ?",
            (subject, max(int(limit or 20), 1))).fetchall() or []
    except Exception:
        _LOG.warning("ADS_INTEL_DISCLOSURE_READ_FAILED", exc_info=True)
        return {"subject_known": False, "categories": [], "degraded": True}

    categories = []
    for row in rows:
        name = known_category(row[0])
        if name and name not in categories:
            categories.append(name)
    return {"subject_known": bool(categories), "categories": categories,
            "how_this_was_built": (
                "These come from what you have engaged with on PulseSoc. We do "
                "not buy information about you, and we do not use sensitive "
                "categories."),
            "controls": [dict(c) for c in CONTROLS],
            "degraded": False}
