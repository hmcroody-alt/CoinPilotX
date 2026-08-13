"""Classify a payment-provider exception into a buyer-safe checkout error.

Both marketplace checkout paths (`marketplace_cart_routes` and
`marketplace_offers_routes`) create a Stripe PaymentIntent / Checkout Session
inside a single ``try``. Until now every failure in that block collapsed into one
opaque line — "Checkout could not be created." — with the real reason visible
only in a server log the app owner cannot easily read. When the live key is
misconfigured, or a transfer is routed to an account Stripe will not accept, the
buyer and the owner saw the same dead end and no next move.

This module turns the caught exception into a stable, machine-readable triple:

    - ``code``    canonical error code the native client already maps to copy
                  (PAYMENT_CONFIGURATION_ERROR, PAYMENT_FAILED, NETWORK_ERROR,
                  PAYMENT_UNAVAILABLE)
    - ``status``  the HTTP status that matches that class of failure
    - ``provider_error``  a *non-sensitive* {type, code, param} fingerprint of
                  the Stripe error, safe to return to the client so the failing
                  stage is visible on the next tap without a log dive

It never returns the provider's raw message or any secret. Stripe is detected by
duck-typing (class name + attributes) so this module has no import dependency on
the ``stripe`` package and stays trivially unit-testable.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Canonical, buyer-facing copy. Every message ends by reassuring the buyer that
# nothing was charged, because a failed checkout must never read like a charge.
_CONFIG_MESSAGE = "Payments are temporarily unavailable. No card was charged."
_DECLINE_MESSAGE = "Your card could not be charged. No card was charged."
_NETWORK_MESSAGE = "We couldn't reach the payment network. No card was charged."
_GENERIC_MESSAGE = "Checkout could not be created. No card was charged."

# Stripe error class name -> (code, http_status, message). Matched on
# ``type(exc).__name__`` so no import of ``stripe`` is required here.
_STRIPE_CLASS_MAP: dict[str, tuple[str, int, str]] = {
    # Bad / missing / wrong-mode API key — the classic "live key not wired" case.
    "AuthenticationError": ("PAYMENT_CONFIGURATION_ERROR", 503, _CONFIG_MESSAGE),
    # Key lacks permission for the account (e.g. Connect on_behalf_of).
    "PermissionError": ("PAYMENT_CONFIGURATION_ERROR", 503, _CONFIG_MESSAGE),
    # Malformed request: e.g. transfer_data.destination to a non-chargeable
    # account, or an amount/currency the account cannot accept.
    "InvalidRequestError": ("PAYMENT_CONFIGURATION_ERROR", 400, _CONFIG_MESSAGE),
    "IdempotencyError": ("PAYMENT_CONFIGURATION_ERROR", 400, _CONFIG_MESSAGE),
    # An actual card decline surfaced at intent/session creation.
    "CardError": ("PAYMENT_FAILED", 402, _DECLINE_MESSAGE),
    # Transient reachability / throttling — a retry is reasonable.
    "APIConnectionError": ("NETWORK_ERROR", 503, _NETWORK_MESSAGE),
    "RateLimitError": ("NETWORK_ERROR", 503, _NETWORK_MESSAGE),
    # Base class / anything else Stripe-shaped we didn't name explicitly.
    "StripeError": ("PAYMENT_UNAVAILABLE", 502, _GENERIC_MESSAGE),
    "APIError": ("PAYMENT_UNAVAILABLE", 502, _GENERIC_MESSAGE),
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


def _mro_names(exc: Exception) -> list[str]:
    """Class names of the exception and every base, nearest first."""
    return [cls.__name__ for cls in type(exc).__mro__]


def _match_stripe_class(exc: Exception) -> tuple[str, int, str] | None:
    """First named Stripe class in the MRO, so a subclass of ``CardError`` is
    still treated as a card error rather than falling through to generic."""
    for name in _mro_names(exc):
        mapped = _STRIPE_CLASS_MAP.get(name)
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

    Returns a dict with ``code``, ``status``, ``message`` and ``provider_error``.
    ``provider_error`` is ``{type, code, param}`` and never includes the raw
    message or any credential.
    """
    name = type(exc).__name__
    mapped = _match_stripe_class(exc)
    code, status, message = mapped if mapped else ("", 0, "")

    if not code:
        if _looks_like_stripe_error(exc):
            # Stripe-shaped but an unnamed subclass — treat as a provider outage.
            code, status, message = "PAYMENT_UNAVAILABLE", 502, _GENERIC_MESSAGE
        else:
            # Not a provider error at all (a bug in our own code path). Preserve
            # the prior contract: opaque 500, PAYMENT_UNAVAILABLE.
            code, status, message = "PAYMENT_UNAVAILABLE", 500, _GENERIC_MESSAGE

    provider_error = {
        "type": name,
        "code": _safe_attr(exc, "code"),
        "param": _safe_attr(exc, "param"),
    }
    return {
        "code": code,
        "status": status,
        "message": message,
        "provider_error": provider_error,
    }
