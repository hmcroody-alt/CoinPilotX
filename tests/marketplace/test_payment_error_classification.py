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


# --- The retryability verdict ------------------------------------------------
#
# §26 asks for a taxonomy whose chain runs: internal cause -> domain error ->
# retryable? -> user message -> CTA state -> observability code. The three links
# the module used to return stopped at the message, so the client had to infer
# retryability from prose and inferred it wrongly — it kept an active payment CTA
# beside a refusal that could never succeed. These pin the two new links.


class IdempotencyError(_StripeLike):
    pass


class RateLimitError(_StripeLike):
    pass


def test_every_classification_carries_a_verdict_and_a_cta() -> None:
    """No failure may reach a buyer without saying what to do about it.

    A missing verdict is the defect this whole section exists to fix: absent a
    field, the client defaults, and the default that shipped was "offer payment
    again" for every failure including the ones that could not work.
    """
    cases = [
        AuthenticationError("bad key"), PermissionError("no access"),
        InvalidRequestError("malformed"), IdempotencyError("key reused"),
        CardError("declined"), APIConnectionError("unreachable"),
        RateLimitError("slow down"), StripeError("odd"),
        KeyError("seller_user_id"),
    ]
    for exc in cases:
        result = classify_provider_exception(exc)
        label = type(exc).__name__
        assert isinstance(result["retryable"], bool), f"{label}: no retryable verdict"
        _check(result["cta"], "retry" if result["retryable"] else "blocked",
               f"{label}: cta disagrees with retryable")


def test_two_failures_sharing_one_code_can_still_disagree_about_retrying() -> None:
    """The case that forces the verdict off the code and onto the cause.

    ``IdempotencyError`` and ``InvalidRequestError`` are both
    PAYMENT_CONFIGURATION_ERROR — the wire value cannot change, the native union
    is closed — and they are opposites. The first is our own key management and
    clears on the next tap; the second is a request Stripe refuses identically
    forever. Any retryability lookup keyed on the code has to be wrong about one
    of them, which is why this pair is asserted together rather than apart.
    """
    burned = classify_provider_exception(IdempotencyError("keys for idempotent requests"))
    malformed = classify_provider_exception(InvalidRequestError("no such destination"))

    _check(burned["code"], malformed["code"], "shared code")
    _check(burned["retryable"], True, "idempotency retryable")
    _check(malformed["retryable"], False, "invalid request retryable")
    assert burned["message"] != malformed["message"], (
        "same code, opposite verdicts, identical copy — the buyer is told to "
        "retry something that cannot work, or to give up on something that can"
    )


def test_a_blocked_failure_never_invites_a_retry_it_cannot_honour() -> None:
    """§19/§20: copy and CTA must agree, in both directions."""
    for exc in (AuthenticationError("bad key"), PermissionError("no access"),
                InvalidRequestError("malformed")):
        result = classify_provider_exception(exc)
        label = type(exc).__name__
        _check(result["retryable"], False, f"{label}: retryable")
        _check(result["cta"], "blocked", f"{label}: cta")
        # "temporarily" was the specific word that cost the October 2026 buyer
        # five hours of retrying: it promised a wait that would fix things.
        assert "temporarily" not in result["message"].lower(), f"{label}: {result['message']}"
        assert "try again" not in result["message"].lower(), f"{label}: {result['message']}"


def test_our_own_crash_is_retryable_because_the_incident_proved_it() -> None:
    """Not the obvious answer for "we threw"; the evidenced one.

    The incident's *first* cause was an ``AttributeError`` reading ``.get`` off a
    Stripe resource — this branch. The buyer's next tap was capable of
    succeeding every time; what actually stopped it was a second, separate
    defect. Marking our own crash non-retryable would take the one move that
    worked away from the next buyer it happens to.
    """
    result = classify_provider_exception(AttributeError("get"))
    _check(result["status"], 500, "bug status")
    _check(result["retryable"], True, "bug retryable")
    _check(result["cta"], "retry", "bug cta")


def test_no_buyer_message_leaks_provider_or_internal_detail() -> None:
    """§27, asserted over the copy rather than only over ``provider_error``."""
    banned = ("stripe", "idempotenc", "api key", "account", "transfer_data",
              "sql", "traceback", "seller_transaction")
    cases = [
        AuthenticationError("Invalid API Key sk_live_abc123 provided"),
        InvalidRequestError("No such destination: acct_1234",
                            code="resource_missing", param="transfer_data[destination]"),
        IdempotencyError("Keys for idempotent requests can only be used with the "
                         "same parameters"),
        CardError("Your card was declined", code="card_declined"),
        APIConnectionError("Could not reach api.stripe.com"),
        KeyError("seller_user_id"),
    ]
    for exc in cases:
        message = classify_provider_exception(exc)["message"].lower()
        for word in banned:
            assert word not in message, f"{type(exc).__name__} leaked {word!r}: {message}"


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
