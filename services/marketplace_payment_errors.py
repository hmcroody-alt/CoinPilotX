"""Buyer-safe checkout errors: one pre-flight rule, one exception classifier.

Both marketplace checkout paths (`marketplace_cart_routes` and
`marketplace_offers_routes`) create a Stripe PaymentIntent / Checkout Session
inside a single ``try``. Until now every failure in that block collapsed into one
opaque line — "Checkout could not be created." — with the real reason visible
only in a server log the app owner cannot easily read. When the live key is
misconfigured, or a transfer is routed to an account Stripe will not accept, the
buyer and the owner saw the same dead end and no next move.

This module turns the caught exception into a stable, machine-readable
descriptor:

    - ``code``    canonical error code the native client already maps to copy
                  (PAYMENT_CONFIGURATION_ERROR, PAYMENT_FAILED, NETWORK_ERROR,
                  PAYMENT_UNAVAILABLE)
    - ``status``  the HTTP status that matches that class of failure
    - ``message`` buyer-facing copy, honest about whether retrying can work
    - ``retryable`` whether the buyer's next identical tap could succeed
    - ``cta``     what a buyer-facing surface may do with the refusal:
                  ``CTA_RETRY`` (keep offering payment) or ``CTA_BLOCKED``
                  (stop offering it)
    - ``provider_error``  a *non-sensitive* {type, code, param} fingerprint of
                  the Stripe error, safe to return to the client so the failing
                  stage is visible on the next tap without a log dive

``retryable``/``cta`` were added after the October 2026 incident, in which the
first three fields were not enough. A buyer saw a refusal and an active
"Continue to secure payment" button on the same screen, because the server never
said which of the two it meant and the client defaulted to "offer payment
again". A refusal that does not carry its own verdict gets one invented at the
far end.

It never returns the provider's raw message or any secret. Stripe is detected by
duck-typing (class name + attributes) so this module has no import dependency on
the ``stripe`` package and stays trivially unit-testable.

One failure is worth refusing *before* the provider rather than classifying
after it: an order total under Stripe's per-currency minimum. It is not a
provider outage and it is not transient — the same tap will fail forever — so it
is answered here as a validation error naming the actual floor, before a
transaction row exists and before stock is reserved.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Canonical, buyer-facing copy. Every message ends by reassuring the buyer that
# nothing was charged, because a failed checkout must never read like a charge.
#
# That reassurance is only sayable here because every failure this module
# classifies is raised *while opening* a payment surface — a Checkout Session or
# a PaymentIntent — and neither is a charge. A failure after a payment has
# succeeded is not this module's to describe, and must never borrow this copy:
# the webhook is the authority on a completed payment, not an exception handler.
_DECLINE_MESSAGE = "Your card could not be charged. No card was charged."
_NETWORK_MESSAGE = "We couldn't reach the payment network. No card was charged."
_GENERIC_MESSAGE = "Checkout could not be created. No card was charged."

# "Payments are temporarily unavailable. No card was charged." used to answer
# every one of the four configuration-shaped failures below. It is the string a
# real buyer read nine times across five hours in October 2026 while payments
# were, in fact, entirely available — Stripe was healthy, the seller was
# chargeable, the card rail was live, and the only thing wrong was that our own
# idempotency key had been burned against different parameters.
#
# Two separate dishonesties, which is why it is now two strings:
#
# "temporarily" invited a retry that could never succeed, for the failures where
# retrying genuinely cannot help. "payments are unavailable" blamed the rail for
# a fault in one order's setup. A buyer cannot act on either, and the first one
# actively cost this buyer five hours.
_SETUP_RETRY_MESSAGE = (
    "We couldn't open secure payment. No card was charged. Please try again."
)
_SETUP_BLOCKED_MESSAGE = (
    "We couldn't open secure payment for this order, and trying again will not "
    "help. No card was charged. We've been notified and are looking into it."
)

#: The two things a buyer-facing surface may do with a refusal.
#:
#: Deliberately not the longer vocabulary §18 suggests. The states that belong
#: on a *button* are only the ones that change what the button does, and nothing
#: here distinguishes OUT_OF_STOCK from DESTINATION_UNAVAILABLE in that respect
#: — both are "this will not work, stop offering it". The richer domain states
#: already exist upstream, as the pre-flight verdicts the cart renders per line
#: and per group; duplicating them in this module would be a second, lesser
#: implementation of a decision already made somewhere better.
CTA_RETRY = "retry"
CTA_BLOCKED = "blocked"

# Stripe error class name -> (code, http_status, message, retryable, cta).
# Matched on ``type(exc).__name__`` so no import of ``stripe`` is required here.
#
# ``retryable`` hangs off the *cause*, not off ``code``, and it has to.
# ``IdempotencyError`` and ``InvalidRequestError`` are both
# PAYMENT_CONFIGURATION_ERROR and they are opposites: the first is our own key
# management and clears on the next attempt, the second is a request Stripe will
# refuse identically forever. A retryability lookup keyed on the code could not
# tell them apart, and would have to be wrong about one of them. This is §26's
# chain read in the order it actually flows — internal cause, then domain error,
# then retryable? — rather than treating the code as the primary key.
#
# The codes themselves are deliberately unchanged. They are a closed union in
# `mobile-native/src/api/marketplaceErrors.ts` with a copy map beside it, so a
# new code would fall outside the union and lose its copy on a client that
# cannot be updated in step with the server. PAYMENT_CONFIGURATION_ERROR is
# therefore a slightly wrong *name* for the idempotency case, kept because it is
# the right *wire value*; `retryable` carries the meaning the name does not.
_STRIPE_CLASS_MAP: dict[str, tuple[str, int, str, bool, str]] = {
    # Bad / missing / wrong-mode API key — the classic "live key not wired" case.
    # Nobody's thumb fixes a secret key.
    "AuthenticationError": (
        "PAYMENT_CONFIGURATION_ERROR", 503, _SETUP_BLOCKED_MESSAGE, False, CTA_BLOCKED),
    # Key lacks permission for the account (e.g. Connect on_behalf_of).
    "PermissionError": (
        "PAYMENT_CONFIGURATION_ERROR", 503, _SETUP_BLOCKED_MESSAGE, False, CTA_BLOCKED),
    # Malformed request: e.g. transfer_data.destination to a non-chargeable
    # account, or an amount/currency the account cannot accept. The same request
    # will be refused the same way every time.
    "InvalidRequestError": (
        "PAYMENT_CONFIGURATION_ERROR", 400, _SETUP_BLOCKED_MESSAGE, False, CTA_BLOCKED),
    # Our own key reused against changed parameters — the October 2026 incident.
    # Entirely retryable, and the one failure in this table that was *caused* by
    # being described as un-retryable: the buyer was told to wait for payments to
    # come back while the only broken thing was a key they could have stepped
    # past immediately.
    "IdempotencyError": (
        "PAYMENT_CONFIGURATION_ERROR", 400, _SETUP_RETRY_MESSAGE, True, CTA_RETRY),
    # An actual card decline surfaced at intent/session creation. Another card,
    # or the same card once the issuer is satisfied, is a real next move.
    "CardError": ("PAYMENT_FAILED", 402, _DECLINE_MESSAGE, True, CTA_RETRY),
    # Transient reachability / throttling — a retry is reasonable.
    "APIConnectionError": ("NETWORK_ERROR", 503, _NETWORK_MESSAGE, True, CTA_RETRY),
    "RateLimitError": ("NETWORK_ERROR", 503, _NETWORK_MESSAGE, True, CTA_RETRY),
    # Base class / anything else Stripe-shaped we didn't name explicitly.
    "StripeError": ("PAYMENT_UNAVAILABLE", 502, _GENERIC_MESSAGE, True, CTA_RETRY),
    "APIError": ("PAYMENT_UNAVAILABLE", 502, _GENERIC_MESSAGE, True, CTA_RETRY),
}


# Stripe refuses a charge below a floor that is *per currency*, not a fixed
# dollar figure: 0.50 in USD and EUR, 0.30 in GBP, 50 in JPY (zero-decimal, so
# fifty yen rather than half a yen), 175 in HUF, 10 in MXN. Sending less raises
# an InvalidRequestError, which reached the buyer as "Checkout could not be
# created." on a listing whose price they could see was fine — so a total the
# card networks will not carry read as PulseSoc being broken.
#
# Values are minor units, the units every checkout amount is already carried in,
# so no conversion happens at a call site.
# Source: https://docs.stripe.com/currencies, "Minimum charge amounts".
STRIPE_MINIMUM_CHARGE_MINOR: dict[str, int] = {
    "AED": 200, "ARS": 50, "AUD": 50, "BRL": 50, "CAD": 50, "CHF": 50,
    "COP": 50, "CZK": 1500, "DKK": 250, "EUR": 50, "GBP": 30, "HKD": 400,
    "HUF": 17500, "IDR": 50, "ILS": 50, "INR": 50, "JPY": 50, "KRW": 50,
    "MXN": 1000, "MYR": 200, "NOK": 300, "NZD": 50, "PHP": 50, "PLN": 200,
    "RON": 200, "RUB": 50, "SEK": 300, "SGD": 50, "THB": 1000, "USD": 50,
    "ZAR": 50,
}

# An unlisted currency falls back deliberately low rather than high. Too low
# simply defers to Stripe's own rejection, which the classifier below still
# maps; too high would refuse a checkout Stripe would have accepted, which is a
# worse failure because nothing in the app could explain it.
DEFAULT_MINIMUM_CHARGE_MINOR = 50

# Charges in these currencies are quoted without a decimal point, so a minor
# unit *is* the unit. Used for display only — the table above is already minor.
_ZERO_DECIMAL_CURRENCIES = frozenset({
    "BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA", "PYG", "RWF",
    "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
})

BELOW_MINIMUM_CODE = "ORDER_TOTAL_BELOW_MINIMUM"


def minimum_charge_minor(currency: str) -> int:
    """Smallest chargeable amount for ``currency``, in minor units."""
    code = str(currency or "USD").upper()
    return STRIPE_MINIMUM_CHARGE_MINOR.get(code, DEFAULT_MINIMUM_CHARGE_MINOR)


def format_minor(amount_minor: int, currency: str) -> str:
    """``1000, "MXN"`` -> ``"MXN 10.00"``; ``50, "JPY"`` -> ``"JPY 50"``."""
    code = str(currency or "USD").upper()
    if code in _ZERO_DECIMAL_CURRENCIES:
        return f"{code} {int(amount_minor)}"
    return f"{code} {int(amount_minor) / 100:.2f}"


def below_minimum_charge_error(amount_minor: Any, currency: str) -> dict[str, Any] | None:
    """``None`` when the total is chargeable, else a buyer-safe descriptor.

    Shaped like :func:`classify_provider_exception` — ``code``/``status``/
    ``message``/``retryable``/``cta`` — so the three checkout lanes answer both
    failures the same way.
    """
    floor = minimum_charge_minor(currency)
    amount = int(amount_minor or 0)
    if amount >= floor:
        return None
    return {
        "code": BELOW_MINIMUM_CODE,
        "status": 400,
        "message": (
            f"This order total is below {format_minor(floor, currency)}, the smallest "
            "amount card payments accept. No card was charged."
        ),
        # The clearest non-retryable case in the module, and the one the
        # docstring above already described in prose before anything acted on
        # it: the same tap fails forever, because the floor is the card
        # networks' and not ours. A buyer whose only route forward is to add
        # another item must not be handed a button that re-asks the question.
        "retryable": False,
        "cta": CTA_BLOCKED,
        "minimum_minor": floor,
        "amount_minor": amount,
        "currency": str(currency or "USD").upper(),
    }


def stripe_response_value(response: Any, name: str, default: Any = "") -> Any:
    """Read one field from either Stripe's mapping or resource response.

    Stripe 15 may return generated resource objects whose fields are exposed as
    attributes but which deliberately do not implement ``dict.get``.  Checkout
    used to call ``intent.get(...)`` after a successful live PaymentIntent
    creation, turning that successful provider call into an application 500
    whose exception text was only ``"get"``.  Keep that SDK-version boundary
    here so Buy Now, cart, and accepted-offer checkout cannot drift again.
    """
    if isinstance(response, Mapping):
        return response.get(name, default)
    value = getattr(response, name, default)
    return default if value is None else value


def _plain(value: Any) -> Any:
    """Recursively strip Stripe resource objects down to ordinary Python data."""
    if value is None or isinstance(value, (str, bytes, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return {str(key): _plain(item) for key, item in to_dict().items()}
        except Exception:
            return {}
    return value


def stripe_response_dict(response: Any) -> dict[str, Any]:
    """Whole-object companion to :func:`stripe_response_value`.

    ``dict(account)`` on a Stripe 15 resource raises ``KeyError: 0`` — the
    object is not a ``dict`` subclass, so ``dict()`` falls back to iterating it
    as a sequence of pairs and asks for index 0. Nested fields such as
    ``requirements`` stay resource objects even after ``to_dict()``, which then
    fail to serialise. Both are flattened here so callers can store the result
    and hand it to ``jsonify`` without knowing which SDK produced it.
    """
    plain = _plain(response)
    return plain if isinstance(plain, dict) else {}


def _safe_attr(exc: Any, name: str) -> str | None:
    """Read an attribute and coerce to a short string, or ``None``.

    Provider error ``code`` and ``param`` are non-sensitive identifiers
    (e.g. ``"resource_missing"``, ``"transfer_data[destination]"``). They are the
    part worth surfacing; the free-text message is deliberately not.
    """
    value = getattr(exc, name, None)
    if value in (None, ""):
        return None
    text = str(value)
    return text[:120] if text else None


def _match_stripe_class(exc: Exception) -> tuple[str, int, str, bool, str] | None:
    """First named Stripe class in the MRO, so a subclass of ``CardError`` is
    still treated as a card error rather than falling through to generic.

    A match whose *defining* class is a builtin is not a Stripe error and is
    skipped. ``PermissionError`` is both a key in the table above and a builtin
    subclass of ``OSError``, so an ordinary denial raised by our own code inside
    the checkout ``try`` was answered as a payment misconfiguration: blocked,
    non-retryable, and therefore rendered with the payment button disabled — a
    dead end for a tap that would have succeeded. It belongs in the
    non-provider branch, which is where ``AttributeError`` already goes.

    Keyed on the class rather than on ``type(exc).__module__`` so our own
    subclasses of the builtin are covered too: those carry their own module
    name, but the class that matches the table is still the builtin.
    """
    for cls in type(exc).__mro__:
        if (cls.__module__ or "") == "builtins":
            continue
        mapped = _STRIPE_CLASS_MAP.get(cls.__name__)
        if mapped:
            return mapped
    return None


def _looks_like_stripe_error(exc: Exception) -> bool:
    """Duck-type a Stripe error without importing ``stripe``.

    True when any class in the MRO is a named Stripe error, or when the defining
    module is ``stripe*`` (covers unnamed real subclasses).
    """
    if _match_stripe_class(exc) is not None:
        return True
    for cls in type(exc).__mro__:
        if (cls.__module__ or "").startswith("stripe"):
            return True
    return False


def classify_provider_exception(exc: Exception) -> dict[str, Any]:
    """Map a caught checkout exception to a buyer-safe error descriptor.

    Returns ``code``, ``status``, ``message``, ``retryable``, ``cta`` and
    ``provider_error``. ``provider_error`` is ``{type, code, param}`` and never
    includes the raw message or any credential.

    ``retryable``/``cta`` exist because without them the client had to infer
    retryability from the copy, and inferred wrongly: a buyer-facing surface read
    "Payments are temporarily unavailable" and kept an active payment CTA beside
    it for a failure that could not succeed. The verdict is the server's to make
    — it is the only party that knows which stage failed — so it now travels on
    the wire instead of being guessed at the far end.
    """
    name = type(exc).__name__
    mapped = _match_stripe_class(exc)
    code, status, message, retryable, cta = (
        mapped if mapped else ("", 0, "", True, CTA_RETRY))

    if not code:
        if _looks_like_stripe_error(exc):
            # Stripe-shaped but an unnamed subclass — treat as a provider outage.
            code, status, message = "PAYMENT_UNAVAILABLE", 502, _GENERIC_MESSAGE
            retryable, cta = True, CTA_RETRY
        else:
            # Not a provider error at all (a bug in our own code path). Preserve
            # the prior contract: opaque 500, PAYMENT_UNAVAILABLE.
            #
            # Retryable, which is not the obvious answer for "we crashed" but is
            # the correct one, and the incident is the proof. Its first cause was
            # exactly this branch — an `AttributeError` reading `.get` off a
            # Stripe resource — and the buyer's next tap was capable of
            # succeeding every single time. What stopped it was a second defect,
            # not this one. Calling our own crash non-retryable would take the
            # one move that worked away from the next buyer it happens to.
            code, status, message = "PAYMENT_UNAVAILABLE", 500, _GENERIC_MESSAGE
            retryable, cta = True, CTA_RETRY

    provider_error = {
        "type": name,
        "code": _safe_attr(exc, "code"),
        "param": _safe_attr(exc, "param"),
    }
    return {
        "code": code,
        "status": status,
        "message": message,
        "retryable": retryable,
        "cta": cta,
        "provider_error": provider_error,
    }
