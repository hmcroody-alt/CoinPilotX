"""The words a delivery estimate is allowed to use, on the server.

§91-93 asks for web and app to present *one* canonical estimate. The estimate
has been canonical since :mod:`services.delivery.quote` — one function composes
it and one endpoint serves it. The sentences were not. They lived only in
``mobile-native/src/components/commerce/deliveryCopy.ts``, which is exactly the
wrong place for the web to reach, and so the web had two options: import nothing
and write its own lines, or render nothing. It rendered nothing, and the gap is
where ``fulfilment_html``'s blanket copy sat.

This module is the server's half of that vocabulary. It is a deliberate second
implementation, not a shared one, and the reason is worth stating because "don't
duplicate" is the obvious objection:

* The app cannot call this. It is a React Native binary that speaks HTTP to this
  deployment, and the sentence has to exist in the bundle for the screen to
  render offline-first the way every other line in it does.
* This cannot call the app. A Jinja template rendering a product page for
  Googlebot has no JavaScript runtime and must not acquire one.
* Serving the sentence *in the API response* was the third option and is worse
  than either. A shipped binary would render whatever a future deployment sent
  it, including a sentence a reviewer never saw, and the app would lose the
  ability to say anything at all when the request fails — which is the one state
  the copy exists for.

So there are two implementations and a **contract test that compares them**:
``tests/delivery/test_delivery_copy.py`` reads the TypeScript source and asserts
that every sentence, every reason grouping and every tone name in it is the same
as the ones here. Drift is a red test rather than two product pages that disagree
about the same parcel. That is a weaker guarantee than one shared function and a
stronger one than a convention.

What is enforced here, identically to the TS module
--------------------------------------------------
* **Never "Guaranteed".** §58, §125. There is no branch that reaches the word.
  Every window is prefixed "Estimated". The ``guaranteed`` flag on the estimate
  is not consulted at all, because no value of it would license the word.
* **Never a single fabricated date.** §124. A window is a range; a range whose
  ends coincide prints as the one day the *server* chose, not as a rounding.
* **Never silence.** Every state yields a line. The old copy grew in the gap a
  failed estimate left behind.
* **Say whose problem it is, not how it broke.** Reasons are grouped by what the
  buyer should *do*; an unrecognised reason lands in the retryable group, which
  says less than it knows rather than more.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional

from . import estimate, quote

# --- tones -----------------------------------------------------------------
#
# Four, and they are about the buyer's next move rather than about severity. A
# five-level severity scale would be a design that looks tidier and tells the
# reader nothing they can act on.

#: A real window. The only tone that carries dates.
TONE_ESTIMATE = "ESTIMATE"
#: No window now, but asking again later may produce one.
TONE_RETRYABLE = "RETRYABLE"
#: No window until the buyer tells us something — today, only the country.
TONE_ACTIONABLE = "ACTIONABLE"
#: Settled. Waiting does not help and a retry control beside it would lie.
TONE_TERMINAL = "TERMINAL"

TONES = (TONE_ESTIMATE, TONE_RETRYABLE, TONE_ACTIONABLE, TONE_TERMINAL)

#: The server's own free-shipping token, compared rather than assumed. This
#: module renders the phrase only when the quote said it — §3/§32/§59 are a
#: policy, and a renderer that asserted the policy itself would keep asserting it
#: through a change to it.
SHIPPING_FREE = quote.SHIPPING_FREE
FREE_SHIPPING_LINE = "FREE Shipping"

#: Reasons the buyer fixes by saying where the parcel goes. Not an error: the
#: server had nothing to resolve from, and a retry resolves nothing.
ACTIONABLE_REASONS = frozenset({quote.REASON_NO_DESTINATION})

#: Reasons that are settled. ``not_supplier_fulfilled`` is here because it is not
#: a failure — the seller ships it — and ``fulfillment_undeclared`` because a
#: listing nobody has declared cannot be quoted by anyone, so inviting a retry is
#: inviting the buyer to keep pulling a lever with nothing attached.
TERMINAL_REASONS = frozenset({
    quote.REASON_NOT_SUPPLIER_FULFILLED,
    quote.REASON_FULFILLMENT_UNDECLARED,
})

#: Per-reason lines, for the reasons where the tone's default would be unhelpful.
#: Anything absent falls through to its tone's default, which is what makes an
#: unrecognised reason safe rather than blank.
REASON_LINES: Dict[str, str] = {
    # The seller-arranged sentence is correct here and *only* here. That is the
    # distinction the copy this slice replaces threw away by saying it on every
    # listing, including the ones a warehouse ships within a known window.
    quote.REASON_NOT_SUPPLIER_FULFILLED:
        "This seller ships this item themselves — delivery is arranged with them "
        "after your order is confirmed.",
    quote.REASON_FULFILLMENT_UNDECLARED:
        "Delivery for this item has not been set up yet.",
    quote.REASON_NO_DESTINATION:
        "Choose a delivery country to see an estimated arrival date.",
    # Our data, not the supplier's: a variant with no weight or dimensions. Says
    # nothing about which, because "the seller has not entered a parcel weight"
    # is the platform's plumbing showing through a product page.
    quote.REASON_VARIANT_INCOMPLETE:
        "An estimated arrival date is not available for this item yet.",
}

TONE_DEFAULTS: Dict[str, str] = {
    TONE_ESTIMATE: "",
    # Deliberately not "something went wrong". The buyer's question is whether to
    # keep going and the answer is yes: the estimate is missing, the item is not.
    TONE_RETRYABLE: "An estimated arrival date is not available right now.",
    TONE_ACTIONABLE: "Choose a delivery country to see an estimated arrival date.",
    TONE_TERMINAL: "This item cannot be delivered to your location.",
}

#: The line shown while the estimate is in flight. On the web this is what a
#: server-rendered page carries before the fetch resolves, so it is read by more
#: people than any other string here and must not imply a date is certain to
#: follow.
LOADING_TEXT = "Checking delivery options…"

#: Month abbreviations, indexed from one by the formatter below.
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

_ISO_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

#: The separator in a range: space, EN DASH (U+2013), space -- matching the app
#: byte for byte. A hyphen here and an en dash there is exactly the kind of
#: difference nobody notices until a screenshot from each is put side by side in
#: a store listing, and the contract test compares these bytes.
RANGE_SEPARATOR = " – "


def format_day(iso: Optional[str]) -> Optional[str]:
    """``"2026-03-16"`` → ``"16 Mar"``. ``None`` for anything else.

    Splits the string rather than parsing it into a date, and that is not a
    micro-optimisation. The server's value is already a calendar day *at the
    destination*: it has no time and no zone, because "arrives Tuesday" is not a
    moment. Reconstituting it through a datetime introduces a zone that was never
    part of the claim, and the only way for that to be visible is as an
    off-by-one on the day a buyer reads — the exact failure this whole package
    was built to avoid. Python's ``date.fromisoformat`` would not shift anything
    by itself, but ``strftime("%-d %b")`` is not portable and ``%d`` pads to
    "06 Mar", so there is nothing gained by the trip either.
    """
    match = _ISO_DAY.match(iso.strip()) if isinstance(iso, str) else None
    if not match:
        return None
    _year, month, day = match.groups()
    index = int(month)
    if not 1 <= index <= 12:
        return None
    return f"{int(day)} {MONTHS[index - 1]}"


def format_window(delivery: Mapping[str, Any]) -> Optional[str]:
    """The window as one phrase, or ``None`` when there is not one.

    ``"16 – 21 Mar"`` inside a month, ``"28 Mar – 2 Apr"` across one. A single
    day comes back as a single day rather than a range repeated: ``"16 – 16 Mar"``
    reads as a formatting bug and invites a reader to distrust the rest of the
    page.

    ``None`` rather than a partial phrase, so a caller that renders the return
    value cannot print half a window.
    """
    if not isinstance(delivery, Mapping):
        return None
    if delivery.get("state") != estimate.STATE_ESTIMATED:
        return None
    earliest, latest = delivery.get("earliest"), delivery.get("latest")
    start, end = format_day(earliest), format_day(latest)
    if not start or not end:
        return None
    if start == end:
        return start
    if str(earliest)[:7] == str(latest)[:7]:
        # Same month: say it once.
        return f"{start.split(' ')[0]}{RANGE_SEPARATOR}{end}"
    return f"{start}{RANGE_SEPARATOR}{end}"


def tone_for(delivery: Mapping[str, Any]) -> str:
    """Which of the four tones one estimate has."""
    state = (delivery or {}).get("state")
    if state == estimate.STATE_ESTIMATED:
        return TONE_ESTIMATE
    # §55: an unserviceable corridor is settled, and it is the one state that has
    # to be able to stop a checkout rather than merely disappoint a product page.
    if state == estimate.STATE_UNSUPPORTED_ROUTE:
        return TONE_TERMINAL
    reason = (delivery or {}).get("reason") or ""
    if reason in ACTIONABLE_REASONS:
        return TONE_ACTIONABLE
    if reason in TERMINAL_REASONS:
        return TONE_TERMINAL
    return TONE_RETRYABLE


def delivery_copy(*, delivery: Optional[Mapping[str, Any]],
                  destination: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """One estimate → one line. Total: no input returns nothing.

    ``delivery`` is the **buyer half** of a quote and nothing else. Passing the
    whole ``{"buyer", "internal"}`` result would work by accident — the keys this
    reads are all in the buyer half — and would put the freight the platform pays
    one attribute access away from a template. The type is the boundary.

    ``destination`` is what the *server* resolved, not what the buyer guessed.
    The country is appended to a real window only when the resolution is
    ``known``: "Estimated 16 – 21 Mar to United States" is a stronger claim than
    "Estimated 16 – 21 Mar", and it must not be made on a corridor inferred from
    an edge header nobody has confirmed.
    """
    delivery = dict(delivery or {})
    where = dict(destination or {})
    tone = tone_for(delivery)
    shipping = (FREE_SHIPPING_LINE
                if delivery.get("shipping_price") == SHIPPING_FREE else None)

    if tone == TONE_ESTIMATE:
        window = format_window(delivery)
        if window:
            suffix = (f" to {where['country']}"
                      if where.get("known") and where.get("country") else "")
            return {
                "tone": tone,
                # "Estimated", always, including the one-day case: a window that
                # happens to be a single day is still an estimate.
                "text": f"Estimated delivery {window}{suffix}",
                "shipping": shipping,
                "retryable": False,
                "stale": delivery.get("confidence") == estimate.CONFIDENCE_PROVIDER_CACHED,
            }
        # ESTIMATED with no formattable window should be impossible: `_compose`
        # only reaches that state through `arrival_window`, which sets both ends.
        # Reaching here means something upstream changed shape, and the only safe
        # thing to say about dates is nothing.
        return {
            "tone": TONE_RETRYABLE,
            "text": TONE_DEFAULTS[TONE_RETRYABLE],
            "shipping": shipping,
            "retryable": True,
            "stale": False,
        }

    return {
        "tone": tone,
        "text": REASON_LINES.get(delivery.get("reason") or "", TONE_DEFAULTS[tone]),
        "shipping": shipping,
        "retryable": tone == TONE_RETRYABLE,
        # Nothing for a freshness qualifier to qualify when there is no date.
        "stale": False,
    }


def loading_copy() -> Dict[str, Any]:
    """The line a server-rendered page carries before the estimate resolves.

    A function returning the same shape as :func:`delivery_copy` rather than a
    fifth tone, because a pending request is not an estimate with unknown fields
    — it is the absence of an answer, and a caller always knows which of the two
    it is holding without inspecting anything.

    ``shipping`` is ``None`` here even though free shipping is a platform policy
    and would be true. A page that printed "FREE Shipping" beside "Checking
    delivery options…" would be making the one claim it is sure of at the moment
    it is least able to support the claim beside it; and if the estimate then
    comes back ``not_supplier_fulfilled``, the free-shipping line was attached to
    a parcel PulseSoc is not shipping.
    """
    return {"tone": TONE_RETRYABLE, "text": LOADING_TEXT, "shipping": None,
            "retryable": False, "stale": False}


def blocks_checkout(delivery: Optional[Mapping[str, Any]]) -> bool:
    """Whether this estimate is allowed to hold up a purchase. §55.

    Only a settled, unserviceable corridor. Not a supplier outage, not a missing
    handling policy, not an unresolved destination: blocking on any of those
    would mean an operator who has not finished configuring delivery had silently
    closed the store, which is the opposite of the shadow-mode property the
    server side was built to have.
    """
    return (delivery or {}).get("state") == estimate.STATE_UNSUPPORTED_ROUTE
