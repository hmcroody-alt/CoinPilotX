"""What this viewer has already been shown, and what they already own.

The engine's original failure mode was not a scoring bug. It was that scoring
had no memory: every request re-ranked the same head of the catalogue against
the same signals and — deterministically — chose the same winners. A ranker with
no notion of "you saw this twenty seconds ago" cannot help but repeat itself.

This module supplies that memory. One read per request produces a frozen
:class:`ExposureState` that answers four questions the ranker and the pool both
need:

* **How often** — counts per product, per seller, per category, inside their
  windows. These drive the repetition penalties.
* **How recently** — seconds since last seen, per product and per seller. These
  drive the cooldowns, which are a *spacing* control and not a frequency one.
* **Where** — the set of surfaces each product has appeared on lately, which is
  what makes cross-surface de-duplication possible at all.
* **What is already theirs** — purchased, in the cart, saved. A recommendation
  for something already bought is the most obviously broken output a commerce
  engine can produce, and it is invisible to every signal in ``ranking``.

No new table
------------

There is deliberately no ``commerce_discovery_exposure`` table. The impression
event log already records every one of these facts, already carries the viewer's
``subject_ref`` rather than their user id, and already has the two composite
indexes this query needs (``idx_cd_impr_product_freq``,
``idx_cd_impr_seller_freq``). A second store would be a second thing to write, a
second thing to get out of step, and — since it would hold exactly the same
behavioural profile — a second thing to keep out of an analytics export.

The brief asks for bounded retention. Because the state is derived rather than
stored, the bound lives on the read (:func:`config.exposure_lookback_seconds`
and :func:`config.exposure_row_limit`) instead of on a sweeper job whose failure
would be silent.

Failure direction
-----------------

Every read here fails **soft** — to an empty state, with ``degraded=True``.
That is the opposite of ``preferences``, and the asymmetry is deliberate. A
preference read that fails is a *consent* question and must resolve to the
refusal. An exposure read that fails is a *quality* question: resolving it to
"assume everything has been seen" would empty all four surfaces on a transient
database hiccup, which is the discovery fault taking down the feed that the
fail-safe rule forbids. So a failed read costs anti-repetition, not the feature,
and says so in a field the metrics can count.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from . import config, subject

LOGGER = logging.getLogger(__name__)

#: Returned for a product that has never been shown. Larger than any cooldown,
#: so "never seen" and "seen long ago" take the same branch without a None check
#: at every call site.
NEVER = float(10**9)


@dataclass(frozen=True)
class ExposureState:
    """One viewer's recent exposure, frozen at the start of a request."""

    product_counts: Mapping[int, int] = field(default_factory=dict)
    seller_counts: Mapping[int, int] = field(default_factory=dict)
    category_counts: Mapping[str, int] = field(default_factory=dict)
    #: ``listing_id -> seconds since it was last shown``, on any surface.
    product_age: Mapping[int, float] = field(default_factory=dict)
    seller_age: Mapping[int, float] = field(default_factory=dict)
    #: ``listing_id -> {surface: seconds since shown there}``.
    surface_age: Mapping[int, Mapping[str, float]] = field(default_factory=dict)
    #: Listing ids most-recently-shown first. Bounded; used for the
    #: "never immediately after itself" check the brief calls out by name.
    recent_products: tuple = ()
    recent_sellers: tuple = ()
    recent_categories: tuple = ()
    purchased: frozenset = frozenset()
    in_cart: frozenset = frozenset()
    saved: frozenset = frozenset()
    #: True when any of the four reads failed. Anti-repetition is weaker than it
    #: should be for this request; nothing is unsafe. Counted, not raised.
    degraded: bool = False

    # -- product ------------------------------------------------------------
    def product_seen(self, listing_id: Any) -> int:
        return int(self.product_counts.get(_int(listing_id), 0))

    def seconds_since_product(self, listing_id: Any) -> float:
        return float(self.product_age.get(_int(listing_id), NEVER))

    def seconds_since_seller(self, seller_id: Any) -> float:
        return float(self.seller_age.get(_int(seller_id), NEVER))

    def seller_seen(self, seller_id: Any) -> int:
        return int(self.seller_counts.get(_int(seller_id), 0))

    def category_seen(self, category: Any) -> int:
        return int(self.category_counts.get(_text(category), 0))

    def seconds_since_other_surface(self, listing_id: Any, surface: str) -> float:
        """Age of the most recent sighting on a surface that is **not** this one.

        Asking "how long since this appeared somewhere else" rather than "has it
        appeared elsewhere" is what lets the same product legitimately return in
        a different context later, which the brief asks for explicitly, while
        still blocking the feed → reels → messenger run inside one minute.
        """
        ages = self.surface_age.get(_int(listing_id)) or {}
        best = NEVER
        for seen_surface, age in ages.items():
            if seen_surface == surface:
                continue
            best = min(best, float(age))
        return best

    @property
    def total_impressions(self) -> int:
        """Commerce impressions in the lookback window, across every surface.

        Summed from ``seller_counts`` rather than from ``product_counts`` so it
        is the same denominator that the seller share is a numerator of. The two
        differ only for rows carrying no seller, and letting those inflate the
        denominator would understate every real seller's share.
        """
        return sum(self.seller_counts.values())

    @property
    def distinct_sellers(self) -> int:
        return len(self.seller_counts)

    def owns(self, listing_id: Any) -> bool:
        key = _int(listing_id)
        return key in self.purchased or key in self.in_cart

    def is_saved(self, listing_id: Any) -> bool:
        return _int(listing_id) in self.saved


#: The state every caller gets when there is nothing to read, or nothing
#: readable. Shared so a degraded request and a brand-new viewer take the exact
#: same code path downstream.
EMPTY = ExposureState()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _rows(cur) -> list[dict]:
    try:
        fetched = cur.fetchall() or []
    except Exception:
        return []
    return [row if isinstance(row, dict) else dict(row) for row in fetched]


def _ids(cur) -> set[int]:
    return {_int(row.get("listing_id")) for row in _rows(cur)} - {0}


def load(cur, ref: str, user_id: Any) -> ExposureState:
    """Everything known about what this viewer has seen and owns.

    Two identities are needed and they are not interchangeable. ``ref`` is the
    salted ``subject_ref`` the discovery tables are keyed by; ``user_id`` is the
    real account id the *marketplace* tables are keyed by. Orders, carts and
    saves belong to the shop, not to discovery, and rewriting them to hold a
    subject ref would mean discovery reaching into commerce's schema to satisfy
    its own privacy model.
    """
    degraded = False

    impressions: list[dict] = []
    try:
        impressions = _load_impressions(cur, ref)
    except Exception:
        LOGGER.warning("COMMERCE_DISCOVERY_EXPOSURE_UNREADABLE", exc_info=True)
        degraded = True

    purchased, purchase_failed = _load_purchased(cur, user_id)
    in_cart, cart_failed = _load_cart(cur, user_id)
    saved, saved_failed = _load_saved(cur, user_id)
    degraded = degraded or purchase_failed or cart_failed or saved_failed

    state = _fold(impressions)
    return ExposureState(
        product_counts=state["product_counts"],
        seller_counts=state["seller_counts"],
        category_counts=state["category_counts"],
        product_age=state["product_age"],
        seller_age=state["seller_age"],
        surface_age=state["surface_age"],
        recent_products=state["recent_products"],
        recent_sellers=state["recent_sellers"],
        recent_categories=state["recent_categories"],
        purchased=frozenset(purchased),
        in_cart=frozenset(in_cart),
        saved=frozenset(saved),
        degraded=degraded,
    )


def _load_impressions(cur, ref: str) -> list[dict]:
    """Recent impression rows for one viewer, newest first.

    The join to ``marketplace_listings`` is what supplies ``category`` — the
    event row does not carry one, and denormalising it onto every impression
    would mean a listing recategorised tomorrow has its history recorded under
    yesterday's name. ``LEFT`` so a listing deleted since it was shown still
    contributes its product and seller counts instead of vanishing from the
    history that is meant to suppress it.
    """
    cur.execute(
        "SELECT e.listing_id AS listing_id, e.seller_user_id AS seller_user_id, "
        "e.surface AS surface, e.event_at AS event_at, "
        "COALESCE(l.category,'') AS category "
        "FROM commerce_discovery_impression_events e "
        "LEFT JOIN marketplace_listings l ON l.id = e.listing_id "
        "WHERE e.subject_ref=? AND e.event_at>? AND e.self_view=0 "
        "ORDER BY e.event_at DESC "
        "LIMIT ?",
        (
            ref,
            subject.window_start_iso(config.exposure_lookback_seconds()),
            config.exposure_row_limit(),
        ),
    )
    return _rows(cur)


def _fold(rows: list[dict]) -> dict:
    """Collapse the event rows into counts, ages and recency orders.

    One pass. Rows arrive newest-first, so the *first* sighting of any key is
    also its most recent one — which is why the age maps use ``setdefault``
    rather than ``min``.
    """
    now = subject.now_utc()
    product_counts: dict[int, int] = {}
    seller_counts: dict[int, int] = {}
    category_counts: dict[str, int] = {}
    product_age: dict[int, float] = {}
    seller_age: dict[int, float] = {}
    surface_age: dict[int, dict[str, float]] = {}
    recent_products: list[int] = []
    recent_sellers: list[int] = []
    recent_categories: list[str] = []

    product_window = config.product_window_seconds()
    seller_window = config.seller_window_seconds()
    category_window = config.category_window_seconds()

    for row in rows:
        listing_id = _int(row.get("listing_id"))
        if not listing_id:
            continue
        seller_id = _int(row.get("seller_user_id"))
        category = _text(row.get("category"))
        surface = _text(row.get("surface"))

        seen_at = subject.parse_iso(row.get("event_at"))
        # An unparseable timestamp is treated as *just now*, which is the
        # conservative direction here: it suppresses a product that might have
        # been fine rather than re-showing one that definitely was not.
        age = 0.0 if seen_at is None else max(0.0, (now - seen_at).total_seconds())

        if age <= product_window:
            product_counts[listing_id] = product_counts.get(listing_id, 0) + 1
        if seller_id and age <= seller_window:
            seller_counts[seller_id] = seller_counts.get(seller_id, 0) + 1
        if category and age <= category_window:
            category_counts[category] = category_counts.get(category, 0) + 1

        product_age.setdefault(listing_id, age)
        if seller_id:
            seller_age.setdefault(seller_id, age)
        if surface:
            surface_age.setdefault(listing_id, {}).setdefault(surface, age)

        if listing_id not in recent_products:
            recent_products.append(listing_id)
        if seller_id and seller_id not in recent_sellers:
            recent_sellers.append(seller_id)
        if category and category not in recent_categories:
            recent_categories.append(category)

    return {
        "product_counts": product_counts,
        "seller_counts": seller_counts,
        "category_counts": category_counts,
        "product_age": product_age,
        "seller_age": seller_age,
        "surface_age": surface_age,
        "recent_products": tuple(recent_products[:120]),
        "recent_sellers": tuple(recent_sellers[:60]),
        "recent_categories": tuple(recent_categories[:40]),
    }


def _load_purchased(cur, user_id: Any) -> tuple[set[int], bool]:
    """Listings this viewer has actually bought, inside the suppression window.

    Only settled states count. A ``pending_payment`` order is not a purchase —
    suppressing on one would hide a product from the person who is in the middle
    of trying to buy it, and if the payment then fails they have lost the card
    that was going to get them back to it.
    """
    window = config.purchase_suppression_seconds()
    if window <= 0:
        return set(), False
    try:
        cur.execute(
            "SELECT listing_id FROM marketplace_orders "
            "WHERE buyer_user_id=? AND status IN ('paid','fulfilled','completed','shipped','delivered') "
            "AND COALESCE(paid_at, created_at) > ? "
            "LIMIT 500",
            (_int(user_id), subject.window_start_iso(window)),
        )
        return _ids(cur), False
    except Exception:
        LOGGER.warning("COMMERCE_DISCOVERY_PURCHASES_UNREADABLE", exc_info=True)
        return set(), True


def _load_cart(cur, user_id: Any) -> tuple[set[int], bool]:
    """Listings already sitting in this viewer's cart.

    ``marketplace_cart_items`` is created lazily by the cart route pack, so on a
    deployment where nobody has ever opened a cart this table does not exist.
    That is not an error worth degrading over — it is simply an empty cart — but
    it is indistinguishable from a real failure at this level, so both land in
    the same soft branch.
    """
    try:
        cur.execute(
            "SELECT listing_id FROM marketplace_cart_items WHERE user_id=? LIMIT 200",
            (_int(user_id),),
        )
        return _ids(cur), False
    except Exception:
        LOGGER.debug("COMMERCE_DISCOVERY_CART_UNREADABLE", exc_info=True)
        return set(), False


def _load_saved(cur, user_id: Any) -> tuple[set[int], bool]:
    """Listings this viewer has saved.

    Saved is a *softer* signal than bought or carted, and it is read here for the
    opposite reason: a saved product does not need a discovery slot to be found
    again, because the user already has a list with it on. It is penalised, not
    suppressed, so a genuinely strong match can still surface it.
    """
    try:
        cur.execute(
            "SELECT listing_id FROM marketplace_saved_products WHERE user_id=? LIMIT 300",
            (_int(user_id),),
        )
        return _ids(cur), False
    except Exception:
        LOGGER.debug("COMMERCE_DISCOVERY_SAVED_UNREADABLE", exc_info=True)
        return set(), False


def rotation_offset(ref: str, *, batch: Optional[int] = None) -> int:
    """Where in the candidate ordering this viewer's pool starts, right now.

    Two inputs: the viewer, so two people browsing at the same moment do not
    walk the catalogue in lockstep; and a coarse clock epoch, so one person
    browsing at two different times does not either. Deterministic within an
    epoch, because a pool that reshuffled between the two requests of a single
    pull-to-refresh would serve the same products twice as often, not less.
    """
    period = config.rotation_period_seconds()
    slots = config.rotation_slots()
    if period <= 0 or slots <= 1:
        return 0
    epoch = int(subject.now_utc().timestamp()) // period
    seed = f"{ref}:{epoch}"
    # Cheap, stable, and not required to be unpredictable — this decides a page
    # offset, not a secret.
    digest = 0
    for char in seed:
        digest = (digest * 131 + ord(char)) & 0xFFFFFFFF
    return (digest % slots) * int(batch or config.candidate_batch_size())
