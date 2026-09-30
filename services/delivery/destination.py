"""Where to quote to, on a page where nobody has typed an address.

The problem this exists for
--------------------------
Every other module in this package answers "how long to *there*". None of them
can say where *there* is. On a product page there is usually no address at all:
this platform collects one at checkout and freezes it onto the transaction, so
before a purchase the server genuinely does not know the buyer's country.

The wrong answers to that are all tempting. Defaulting to ``US`` invents a
promise for a buyer in Brazil. Asking the device for its location to show a
shipping estimate is a permission prompt out of all proportion to the question,
and a buyer who declines is then unservable. Quietly quoting to the warehouse's
own country produces a number that is never wrong in testing and always wrong in
production.

So the destination is *resolved*, from named sources, in a fixed order, and the
answer says which source produced it. Two callers need that label for different
reasons: the presentation layer, because "estimated delivery to Germany --
change" and "choose where you are" are different screens; and anything measuring
promise accuracy later, because an estimate to a guessed country is not evidence
about the estimator.

Not knowing is a first-class answer
-----------------------------------
``resolve`` returns a record whose ``destination`` may hold no country, and that
is not a failure. ``quote.quote_delivery`` already refuses an unresolved
destination by name (``destination_unresolved``), so this module does not get a
second opinion about it -- it reports what it found and lets the one place that
composes a delivery answer keep composing it.

What deliberately does not appear
---------------------------------
There is no latitude, longitude, IP address or device-location parameter, and no
argument through which one could be introduced without editing this docstring's
own test. Coarse country is the whole of what a pre-purchase estimate needs: the
international leg dominates every window this domain produces, and a postal code
only refines the domestic tail.

The buyer's user id enters this module and only a country leaves it. That is the
privacy property worth stating, because the value it produces goes on to be part
of a *cache key*: if identity could travel with it, one buyer's page load would
file an entry that another buyer's page load reads.

Why a past order's postal code is not re-used
---------------------------------------------
Country is inherited from a previous purchase; postal precision is not. A
country changes which routes are eligible at all, so carrying it forward buys a
materially better answer. A postal prefix only sharpens the domestic leg, and
inheriting it would put a fragment of an address the buyer gave for *one*
purchase into the cache key of a page they are merely browsing. Precision is
re-supplied at checkout, where the buyer is typing it anyway.

The edge tier is not configured in production
---------------------------------------------
``client_address.client_country`` reads a header named by
``PULSESOC_TRUSTED_GEO_HEADER``, and that variable is unset on this deployment --
so the tier resolves nothing today. It is wired anyway, because the alternative
is discovering at rollout that there is no coarse tier at all. What it must not
be is a silent hole: :func:`edge_configured` reports the tier's own availability
so an operator reading a health surface sees "not configured" instead of
concluding that every visitor is genuinely unlocatable.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from services import client_address, db

#: An address the buyer is completing, or completed, for *this* purchase. The
#: only tier that can be more precise than a country, and the only one whose
#: precision the buyer supplied for the thing being quoted.
TIER_CHECKOUT = "CHECKOUT"

#: The buyer named a country on this page -- picked it from a list, typed it into
#: a "deliver to" control. An assertion, not a verified fact, which is fine: the
#: only thing it buys them is a different estimate.
TIER_STATED = "STATED"

#: Read off the buyer's most recent paid order that shipped somewhere.
TIER_LAST_ORDER = "LAST_ORDER"

#: A trusted edge resolved it. Unconfigured on this deployment; see the module
#: docstring and :func:`edge_configured`.
TIER_EDGE = "EDGE"

#: Nobody said anything about *this* visitor, and the platform accepts delivery
#: addresses in exactly one country, so there is only one corridor it could be.
#: Weakest tier by design: it is a statement about PulseSoc's checkout
#: configuration and not an observation about the reader.
#:
#: It exists because of a specific and otherwise unsolvable problem. A page that
#: a proxy may hand to a second visitor must not carry a date resolved from the
#: first one's corridor, so the public product page has to render with no
#: destination — and then Googlebot, and the first paint, never see an arrival
#: date at all. When ``MARKETPLACE_SHIPPING_COUNTRIES`` names one country that
#: reasoning does not apply: the corridor is not visitor-dependent, because
#: checkout would refuse an address anywhere else. One entry in that variable is
#: therefore a fact about every buyer who can complete this purchase, which is
#: exactly what a shared cache is allowed to hold.
#:
#: ``known`` stays false for it (see :func:`_record`), so no surface says "to
#: United States" on the strength of it. The corridor is right; the claim to have
#: located the reader would not be.
TIER_POLICY = "POLICY"

#: Nothing resolved. An answer, not an error.
TIER_NONE = "NONE"

#: Precedence, most authoritative first. Declared once and iterated, so the
#: order is data rather than the order some ``if`` statements happen to appear
#: in -- a reordering is then a one-line diff a test can see.
#:
#: :data:`TIER_POLICY` is deliberately *not* in this tuple. It is not a source
#: :func:`resolve` consults — nothing about a request produces it — and putting it
#: last would make every unresolved visitor on every surface silently inherit the
#: single-country corridor, including at checkout, where the address is the thing
#: being collected. It is requested explicitly, by one caller, in
#: :func:`policy_destination`.
TIERS = (TIER_CHECKOUT, TIER_STATED, TIER_LAST_ORDER, TIER_EDGE)

#: Tiers permitted to carry a postal code. See "Why a past order's postal code is
#: not re-used". Enforced rather than assumed: a tier absent from this set has
#: its postal dropped even if the caller supplied one.
POSTAL_TIERS = frozenset({TIER_CHECKOUT})

PRECISION_POSTAL = "POSTAL"
PRECISION_COUNTRY = "COUNTRY"
PRECISION_NONE = "NONE"

#: A country code is exactly two letters. Anything else is discarded rather than
#: coerced: ``"USA"``, ``"united states"`` and ``"ZZ"`` reaching a cache key
#: would each produce their own entry for a destination no carrier recognises.
_CODE_LENGTH = 2

#: Only a *paid* order's address is inherited. An abandoned checkout holds an
#: address the buyer never completed a purchase with, and one plausible reason
#: for abandoning is that the address was wrong.
_PAID = "paid"

#: How far back to look for an order that shipped. A buyer's recent history is a
#: mix of lanes, and a digital purchase carries no address, so the newest paid
#: row is often not the newest shipped one. Bounded because this runs on a
#: product page: the cost of not finding one is falling through to the next
#: tier, which is harmless, and that makes an unbounded scan unjustifiable.
_HISTORY_DEPTH = 5


def edge_configured() -> bool:
    """Whether a trusted edge geo header is configured for this process.

    Separate from "the edge resolved nothing for this request" on purpose. Both
    produce :data:`TIER_NONE`, and they call for completely different actions:
    one is a visitor an edge could not place, the other is a tier that is not
    switched on. Conflating them is how a missing environment variable gets
    diagnosed as a population of unlocatable buyers.
    """
    return bool(client_address.geo_header_name())


def resolve(*, stated: Any = None, checkout: Any = None,
            buyer_user_id: Optional[int] = None, headers: Any = None,
            connect: Callable[[], Any] = db.connect) -> Dict[str, Any]:
    """The best available destination, and which source produced it.

    Every source is optional; passing none is legitimate and yields
    :data:`TIER_NONE`. Sources are consulted in :data:`TIERS` order and the first
    that yields a country wins -- there is no merging, because a country from one
    source and a postal code from another describe a place neither source
    claimed.

    Returns ``{"destination": {"country", "postal"}, "tier", "precision",
    "known"}``. ``destination`` is exactly the shape ``quote_delivery`` accepts
    and holds nothing else: the whole dict is handed to a supplier, so a tier
    label or a buyer id inside it would be sent to CJ.
    """
    for tier, found in (
        (TIER_CHECKOUT, lambda: _from_address(checkout)),
        (TIER_STATED, lambda: _from_address(stated)),
        (TIER_LAST_ORDER, lambda: _from_history(buyer_user_id, connect)),
        (TIER_EDGE, lambda: _from_edge(headers)),
    ):
        country, postal = found()
        if country:
            # Dropped, not trusted-and-ignored: a postal code from a tier that
            # may not carry one must not reach the record at all, because
            # everything downstream reads the record and not this function.
            if tier not in POSTAL_TIERS:
                postal = None
            return _record(tier, country, postal)
    return _record(TIER_NONE, None, None)


def policy_destination() -> Dict[str, Any]:
    """The corridor implied by a single-destination deployment, or nothing.

    Returns a :func:`resolve`-shaped record at :data:`TIER_POLICY` when
    ``MARKETPLACE_SHIPPING_COUNTRIES`` names exactly one country, and the
    :data:`TIER_NONE` record otherwise. Not a parameter of :func:`resolve`: this
    reads deployment configuration rather than anything about a request, and a
    caller has to opt in, because only a caller knows whether its response is
    about to be shared with a different reader.

    Two or more accepted countries yields nothing rather than the first one. The
    variable is an unordered set — it is shared verbatim with Stripe's
    ``shipping_address_collection`` — so "the first one" would be whichever the
    operator happened to type first, and a US-first deployment would quote US
    windows to a page a Canadian buyer is also served.

    ``shipping_countries`` and not a list of our own: it is the same function
    checkout enforces with, so this cannot offer an estimate for a corridor the
    purchase would then be refused in. Imported inside the function because
    ``marketplace_fulfillment`` is a large module in the order path and this
    package is imported by a product page render.
    """
    from services import marketplace_fulfillment

    try:
        accepted = marketplace_fulfillment.shipping_countries()
    except Exception:  # noqa: BLE001 — unreadable configuration resolves nothing
        return _record(TIER_NONE, None, None)
    if len(accepted) != 1:
        return _record(TIER_NONE, None, None)
    country = _code(accepted[0])
    if not country:
        return _record(TIER_NONE, None, None)
    return _record(TIER_POLICY, country, None)


def _record(tier: str, country: Optional[str], postal: Optional[str]) -> Dict[str, Any]:
    """The resolution, with precision derived rather than passed.

    ``precision`` is a reading of what was found, so it cannot disagree with it.
    A caller that could set it independently is a caller that can label a
    country-only answer ``POSTAL`` and get a postal-precise promise from it.
    """
    if country and postal:
        precision = PRECISION_POSTAL
    elif country:
        precision = PRECISION_COUNTRY
    else:
        precision = PRECISION_NONE
    return {
        "destination": {"country": country, "postal": postal},
        "tier": tier,
        "precision": precision,
        # A country *and* a source that observed this reader. `TIER_POLICY` has
        # the first and not the second: it is where the parcel would go if this
        # visitor buys, inferred from the platform accepting addresses nowhere
        # else, and `known` is what licenses a surface to write "to United
        # States". Saying that to somebody the deployment merely cannot ship to
        # anywhere else is a claim about them that nothing made.
        "known": bool(country) and tier != TIER_POLICY,
    }


def _from_address(value: Any) -> tuple:
    """A country and postal code out of a caller-supplied address.

    Accepts the checkout field names this platform already froze onto
    transactions (``address_country``, ``address_postal_code``) as well as the
    plain ones, because the two live surfaces that have an address spell it those
    two ways and a third spelling here would be a silent no-match rather than an
    error. A bare string is read as a country code, since that is what a
    "deliver to" control produces.
    """
    if isinstance(value, str):
        return _code(value), None
    if not isinstance(value, dict):
        return None, None
    country = _code(_first(value, "country", "address_country", "countryCode"))
    postal = _postal(_first(value, "postal", "address_postal_code", "postal_code", "zip"))
    # A postal code without a country is not a place. Returned as nothing rather
    # than as a postal-only answer, because `route_key` would accept it and file
    # an entry under a destination it cannot name.
    return (country, postal) if country else (None, None)


def _from_history(buyer_user_id: Optional[int], connect: Callable[[], Any]) -> tuple:
    """The country on the buyer's most recent paid order that shipped somewhere.

    A read failure is not an error here. This tier is a hint; if the database
    cannot answer, the right outcome is the next tier, not a product page that
    fails to render. The exception is swallowed narrowly -- around the read
    only -- so a defect in the parsing below still surfaces.
    """
    if not isinstance(buyer_user_id, int) or isinstance(buyer_user_id, bool):
        return None, None
    try:
        with connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT metadata_json FROM seller_transactions "
                "WHERE buyer_user_id = ? AND status = ? "
                "ORDER BY id DESC LIMIT ?",
                (buyer_user_id, _PAID, _HISTORY_DEPTH),
            )
            rows = cur.fetchall() or []
    except Exception:
        return None, None

    for row in rows:
        details = _frozen_details(db.row_values(row)[0])
        country = _code(details.get("address_country"))
        if country:
            # Country only, though `details` has the postal code right there. That
            # restraint is the point; see the module docstring.
            return country, None
    return None, None


def _frozen_details(blob: Any) -> Dict[str, Any]:
    """The frozen fulfillment details on a transaction, or ``{}``.

    ``marketplace_fulfillment.snapshot`` writes ``{"kind", "details"}`` under
    ``fulfillment``, and the address lives in ``details`` under the
    ``address_*`` names. Read defensively at every hop, and returning a mapping
    rather than raising: this column holds the metadata of every kind of
    transaction on the platform, most of which have no fulfillment block at all,
    so a missing key is the common case and not corruption.
    """
    if not isinstance(blob, str) or not blob.strip():
        return {}
    try:
        parsed = json.loads(blob)
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    fulfillment = parsed.get("fulfillment")
    if not isinstance(fulfillment, dict):
        return {}
    details = fulfillment.get("details")
    return details if isinstance(details, dict) else {}


def _from_edge(headers: Any) -> tuple:
    """What a trusted edge resolved, or nothing.

    Delegated entirely to ``client_address.client_country``, which already
    refuses to fall back to a client-supplied header and already rejects
    anything that is not two letters. Re-implementing either rule here would
    give this domain its own, looser notion of a trusted source -- and the
    looser one always wins in practice, because it is the one that returns an
    answer.
    """
    if headers is None:
        return None, None
    try:
        return _code(client_address.client_country(headers)), None
    except Exception:
        return None, None


def _first(value: Dict[str, Any], *names: str) -> Any:
    for name in names:
        found = value.get(name)
        if found not in (None, ""):
            return found
    return None


def _code(value: Any) -> Optional[str]:
    """An ISO-3166 alpha-2 code, or nothing. Never a coerced approximation."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip().upper()
    if len(cleaned) != _CODE_LENGTH or not cleaned.isalpha():
        return None
    return cleaned


def _postal(value: Any) -> Optional[str]:
    """A postal code as the buyer wrote it, or nothing.

    Not normalised or truncated here. ``cache._postal`` owns the prefix rule and
    owns it for the whole domain; a second truncation at this layer would mean
    the prefix depended on which path an address arrived by.
    """
    if not isinstance(value, str):
        return None
    return value.strip() or None
