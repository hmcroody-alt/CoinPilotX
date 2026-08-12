"""The checkout catch-all must stop lying by omission.

Every Stripe failure used to return one opaque line — "Checkout could not be
created." — with the real reason buried in a server log. These tests pin the
replacement: each provider failure class maps to a stable canonical code, a
matching HTTP status, and a *non-sensitive* {type, code, param} fingerprint, and
a non-provider bug still degrades to the old opaque 500 contract.

Runs with nothing but the interpreter (no pytest, no Flask, no network):

    python tests/marketplace/test_payment_error_classification.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.marketplace_payment_errors import classify_provider_exception


# --- Fakes that duck-type stripe.error.* without importing stripe ------------

class _StripeLike(Exception):
    """Base that mimics stripe.error.StripeError's attribute surface."""

    def __init__(self, message: str = "", *, code=None, param=None):
        super().__init__(message)
        self.code = code
        self.param = param


class AuthenticationError(_StripeLike):
    pass


class PermissionError(_StripeLike):  # noqa: A001 - name mirrors stripe.error
    pass


class InvalidRequestError(_StripeLike):
    pass


class CardError(_StripeLike):
    pass


class APIConnectionError(_StripeLike):
    pass


class StripeError(_StripeLike):
    pass


def _check(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def test_authentication_error_is_configuration() -> None:
    result = classify_provider_exception(AuthenticationError("Invalid API Key provided"))
    _check(result["code"], "PAYMENT_CONFIGURATION_ERROR", "auth code")
    _check(result["status"], 503, "auth status")
    _check(result["provider_error"]["type"], "AuthenticationError", "auth type")
    assert "No card was charged." in result["message"], result["message"]


def test_invalid_request_surfaces_param_without_message() -> None:
    exc = InvalidRequestError(
        "No such destination", code="resource_missing", param="transfer_data[destination]"
    )
    result = classify_provider_exception(exc)
    _check(result["code"], "PAYMENT_CONFIGURATION_ERROR", "invalid code")
    _check(result["status"], 400, "invalid status")
    _check(result["provider_error"]["code"], "resource_missing", "invalid provider code")
    _check(result["provider_error"]["param"], "transfer_data[destination]", "invalid provider param")
    # The raw provider message must never ride along on the client payload.
    assert "No such destination" not in str(result["provider_error"]), result


def test_permission_error_is_configuration() -> None:
    result = classify_provider_exception(PermissionError("The provided key does not have access"))
    _check(result["code"], "PAYMENT_CONFIGURATION_ERROR", "perm code")
    _check(result["status"], 503, "perm status")


def test_card_error_is_payment_failed() -> None:
    result = classify_provider_exception(CardError("Your card was declined", code="card_declined"))
    _check(result["code"], "PAYMENT_FAILED", "card code")
    _check(result["status"], 402, "card status")


def test_api_connection_error_is_network() -> None:
    result = classify_provider_exception(APIConnectionError("Network communication failed"))
    _check(result["code"], "NETWORK_ERROR", "network code")
    _check(result["status"], 503, "network status")


def test_unnamed_stripe_subclass_degrades_to_unavailable() -> None:
    class WeirdStripeError(StripeError):
        pass

    result = classify_provider_exception(WeirdStripeError("something odd"))
    _check(result["code"], "PAYMENT_UNAVAILABLE", "weird code")
    _check(result["status"], 502, "weird status")


def test_non_provider_bug_keeps_opaque_500_contract() -> None:
    # A KeyError in our own code is not a provider failure and must not be
    # mislabelled as one; it preserves the pre-existing opaque 500.
    result = classify_provider_exception(KeyError("seller_user_id"))
    _check(result["code"], "PAYMENT_UNAVAILABLE", "bug code")
    _check(result["status"], 500, "bug status")
    _check(result["provider_error"]["type"], "KeyError", "bug type")


def _main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"ok   - {test.__name__}")
        except Exception as exc:  # noqa: BLE001 - test harness
            failures += 1
            print(f"FAIL - {test.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
