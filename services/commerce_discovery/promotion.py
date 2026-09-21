"""The three promotion classes, and the wall between them.

PulseSoc surfaces a product to a buyer for exactly three reasons, and they are
not variations of one another:

``organic``
    The ranking engine chose this listing on merit. **The seller paid nothing.**
    No campaign exists, no budget is reserved, no impression is billable, and
    the label the buyer sees is a recommendation label — never "Sponsored".

``house``
    PulseSoc itself is promoting the listing (a curated shelf, a launch push, a
    category we are seeding). Still unpaid by the seller, but it is *our*
    editorial thumb on the scale rather than the ranker's verdict, so it is
    tracked separately or we would read our own merchandising back as organic
    demand.

``paid``
    The existing Business OS advertising subsystem. Budgeted, billable,
    Sponsored-labelled, and **not served by this package at all** — it lives in
    ``services/business_os/advertising/``. The constant exists here only so the
    wall has a name on both sides of it.

Why a module rather than three string literals
----------------------------------------------

Because the failure mode is silent and expensive. If an organic impression ever
lands in an advertising table, or a paid impression is counted into organic
reach, nobody gets an error — they get a number that is wrong in a direction
that flatters us. Seller-facing "free reach" would include ads the seller paid
for; ad reporting would include impressions nobody was charged for. Both
numbers would look plausible forever.

So: organic and house events are written to ``commerce_discovery_*`` tables by
this package, paid events to ``business_os_ad_*`` tables by that one, and
:func:`assert_unpaid` is called at every write boundary here. It is a cheap
assertion guarding an error that is otherwise undetectable from the inside.

The one thing all three genuinely share is *placement safety* — a buyer being
shown too much commerce does not care which budget line paid for it. Frequency
accounting therefore reads across classes even though event storage never
merges (see ``engine.py``).
"""

from __future__ import annotations

from typing import Any

#: Ranked on merit, seller pays nothing, labelled as a recommendation.
ORGANIC = "organic"

#: PulseSoc's own editorial promotion. Unpaid by the seller, tracked apart.
HOUSE = "house"

#: Seller-funded advertising. Owned by ``business_os/advertising``, never here.
PAID = "paid"

#: Everything this package is allowed to serve and record.
UNPAID_CLASSES = frozenset({ORGANIC, HOUSE})

#: Every class that exists anywhere in the product.
ALL_CLASSES = frozenset({ORGANIC, HOUSE, PAID})

#: What the buyer is told, per class. Truthful by construction: an organic
#: placement is never given advertising language, and "Sponsored" appears in
#: this map only against the class that was in fact sponsored.
#:
#: Values are i18n **keys**, not copy. Hardcoded strings fail the mobile i18n
#: gate, and the label is the part of a commerce unit a regulator would read
#: first, so it must be translatable rather than English-shaped.
LABEL_KEYS = {
    ORGANIC: "marketplace:discovery.label.recommended",
    HOUSE: "marketplace:discovery.label.trending",
    PAID: "marketplace:discovery.label.sponsored",
}


class PromotionClassError(ValueError):
    """A promotion class crossed a boundary it is not allowed to cross."""


def normalize(value: Any) -> str:
    """Canonical class name, or ``""`` when the value names no known class.

    Unknown input returns empty rather than falling back to :data:`ORGANIC`.
    A default here would be the exact bug this module exists to prevent: a
    malformed or hostile class name would be silently accounted as free reach.
    Callers treat ``""`` as a rejection.
    """
    text = str(value or "").strip().lower()
    return text if text in ALL_CLASSES else ""


def is_unpaid(value: Any) -> bool:
    """True when this class is served and recorded by *this* package."""
    return normalize(value) in UNPAID_CLASSES


def assert_unpaid(value: Any) -> str:
    """Return the class, or raise if it is not one this package may record.

    Called at every write boundary in ``events.py``. Rejects both the unknown
    class and, deliberately, :data:`PAID` — a paid impression arriving here is
    not a rounding error to absorb, it is a billing event that would go
    unbilled and simultaneously inflate organic reach.
    """
    normalized = normalize(value)
    if normalized == PAID:
        raise PromotionClassError(
            "paid placements are recorded by services/business_os/advertising, "
            "never by commerce_discovery — recording one here would both skip "
            "billing and overstate unpaid reach"
        )
    if normalized not in UNPAID_CLASSES:
        raise PromotionClassError(f"unknown promotion class: {value!r}")
    return normalized


def label_key(value: Any) -> str:
    """i18n key for the buyer-facing label of this class.

    Falls back to the organic key for an unrecognised class, which is the safe
    direction: mislabelling an unknown placement as a recommendation understates
    a commercial relationship's absence, while defaulting to "Sponsored" would
    claim a payment that never happened.
    """
    return LABEL_KEYS.get(normalize(value) or ORGANIC, LABEL_KEYS[ORGANIC])
