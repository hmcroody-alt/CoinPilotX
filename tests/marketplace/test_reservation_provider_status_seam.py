"""The one function in the reconciler that touches the Stripe SDK.

Every other reconciler test injects ``fetch_status``, which is excellent for
driving the decision table deterministically and is exactly why this bug
survived: ``_fetch_payment_intent_status`` is the only code that actually
speaks to ``stripe``, and nothing exercised it. A 100%-green decision table sat
on top of a fetcher that raised on every single call.

So these tests build a real ``stripe.PaymentIntent`` — the genuine resource
class from the pinned SDK, constructed offline, no network and no API key — and
read it the way production does.

What was wrong: on stripe==15.1.0 a ``PaymentIntent`` is a resource object, not
a Mapping. It has no ``get`` method, and ``StripeObject.__getattr__`` raises
``AttributeError: get``. The old expression was::

    return (intent or {}).get("status")

``intent`` is always truthy here, so the ``or {}`` fallback never engaged and
the ``.get`` always landed on the resource. The resulting raise was caught one
frame up in ``decide_for_reservation`` and reported as ``provider_unreachable``
— so a perfectly healthy Stripe call was indistinguishable from a Stripe
outage, forever, for every reservation.
"""

from __future__ import annotations

import pytest

stripe = pytest.importorskip("stripe")

from services import marketplace_reservation_reconciler as reconciler  # noqa: E402


def _intent(**fields):
    """A real PaymentIntent resource, built without network or credentials."""
    payload = {"id": "pi_seam", "object": "payment_intent"}
    payload.update(fields)
    return stripe.PaymentIntent.construct_from(payload, "sk_test_not_a_real_key")


# --------------------------------------------------------------------------
# The SDK boundary itself
# --------------------------------------------------------------------------


def test_the_pinned_sdk_really_does_refuse_dot_get():
    """Pins the SDK behaviour this whole file exists for.

    If a future SDK bump makes ``PaymentIntent`` a Mapping again this test
    fails, which is the correct outcome: it means the hazard is gone and the
    reasoning in these docstrings is stale and should be revisited.
    """
    intent = _intent(status="requires_payment_method")

    assert not isinstance(intent, dict)
    assert not hasattr(intent, "get")
    with pytest.raises(AttributeError):
        intent.get("status")  # the exact pre-fix expression

    # Attribute and item access both work; only `.get` is absent.
    assert intent.status == "requires_payment_method"
    assert intent["status"] == "requires_payment_method"


def test_the_fetcher_reads_a_status_off_a_real_resource(monkeypatch):
    """The regression test proper. Red before the fix, green after."""
    monkeypatch.setattr(
        stripe.PaymentIntent, "retrieve",
        staticmethod(lambda pid, **kw: _intent(status="succeeded")))

    assert reconciler._fetch_payment_intent_status("pi_seam") == "succeeded"


@pytest.mark.parametrize("status", [
    "succeeded", "processing", "canceled", "requires_payment_method",
    "requires_action", "requires_confirmation", "requires_capture",
])
def test_every_real_status_survives_the_round_trip(monkeypatch, status):
    """Not just the happy one — the whole vocabulary the table branches on."""
    monkeypatch.setattr(
        stripe.PaymentIntent, "retrieve",
        staticmethod(lambda pid, **kw: _intent(status=status)))

    assert reconciler._fetch_payment_intent_status("pi_seam") == status


def test_a_plain_dict_still_works(monkeypatch):
    """A replayed or hand-built intent must keep working.

    ``stripe_response_value`` takes the Mapping branch here, so fixtures and
    any caller that passes recorded JSON are unaffected by the fix.
    """
    monkeypatch.setattr(
        stripe.PaymentIntent, "retrieve",
        staticmethod(lambda pid, **kw: {"status": "succeeded"}))

    assert reconciler._fetch_payment_intent_status("pi_seam") == "succeeded"


# --------------------------------------------------------------------------
# An unreadable status must not read as an absent one
# --------------------------------------------------------------------------


@pytest.mark.parametrize("payload", [
    {},                      # no status field at all
    {"status": None},        # present but null
    {"status": ""},          # present but empty
    {"status": "   "},       # present but whitespace
])
def test_an_unreadable_status_is_never_treated_as_no_intent(monkeypatch, payload):
    """The §14 case: unknown must not collapse into zero.

    ``decide_from_status("")`` releases, on the reasoning that no intent was
    ever created so nothing can settle. That reasoning holds only when the
    transaction carried no intent id — which ``decide_for_reservation`` already
    checks before it ever calls out. Reaching Stripe, getting an intent back,
    and failing to read its status is a different thing entirely, and releasing
    stock on it could resell a paid order.
    """
    monkeypatch.setattr(
        stripe.PaymentIntent, "retrieve",
        staticmethod(lambda pid, **kw: _intent(**payload)))

    status = reconciler._fetch_payment_intent_status("pi_seam")
    assert status == reconciler.STATUS_UNREADABLE

    decision = reconciler.decide_from_status(status, deferrals=0)
    assert decision["decision"] == reconciler.DECISION_DEFER
    assert decision["needs_attention"] is True


def test_an_unreadable_status_still_defers_past_the_deferral_bound():
    """Even exhausted, an unreadable answer must not release.

    The deferral bound exists to stop a hold living forever while the *buyer*
    is slow. It is not a licence to release on an answer we could not read —
    that needs an operator, not a timer.
    """
    exhausted = reconciler.max_deferrals() + 5
    decision = reconciler.decide_from_status(
        reconciler.STATUS_UNREADABLE, deferrals=exhausted)

    assert decision["decision"] == reconciler.DECISION_DEFER
    assert decision["needs_attention"] is True


def test_the_unreadable_sentinel_is_not_a_recognised_status():
    """Guards the sentinel against being accidentally adopted into a set."""
    assert reconciler.STATUS_UNREADABLE not in reconciler.AWAITING_BUYER_STATUSES
    assert reconciler.STATUS_UNREADABLE not in reconciler.CONCLUSIVE_FAILURE_STATUSES
    assert reconciler.STATUS_UNREADABLE != reconciler.STATUS_SUCCEEDED
    assert reconciler.STATUS_UNREADABLE != reconciler.STATUS_PROCESSING
    assert reconciler.STATUS_UNREADABLE != reconciler.STATUS_CANCELED
    assert reconciler.STATUS_UNREADABLE.strip() != ""


# --------------------------------------------------------------------------
# End to end through decide_for_reservation, the caller that swallowed it
# --------------------------------------------------------------------------


def test_a_healthy_stripe_call_no_longer_looks_like_an_outage(monkeypatch):
    """The production symptom, reproduced at the level the operator sees.

    Before the fix this returned ``defer / provider_unreachable`` for an intent
    Stripe had just answered ``succeeded`` for — meaning a paid order's hold
    would never be captured and would be re-examined every sweep forever.
    """
    monkeypatch.setattr(
        stripe.PaymentIntent, "retrieve",
        staticmethod(lambda pid, **kw: _intent(status="succeeded")))

    decision = reconciler.decide_for_reservation(
        {"stripe_payment_intent_id": "pi_seam",
         "transaction_status": "checkout_created"},
        deferrals=0)

    assert decision["decision"] == reconciler.DECISION_CAPTURE
    assert decision["detail"] == "payment_settled"
    assert decision.get("detail") != "provider_unreachable"


def test_a_genuine_provider_failure_still_defers(monkeypatch):
    """The outage path must keep working — it is the reason the catch exists."""
    def boom(pid, **kw):
        raise stripe.error.APIConnectionError("network down")

    monkeypatch.setattr(stripe.PaymentIntent, "retrieve", staticmethod(boom))

    decision = reconciler.decide_for_reservation(
        {"stripe_payment_intent_id": "pi_seam",
         "transaction_status": "checkout_created"},
        deferrals=0)

    assert decision["decision"] == reconciler.DECISION_DEFER
    assert decision["detail"] == "provider_unreachable"
    assert decision["needs_attention"] is True


def test_the_four_stranded_production_rows_resolve_to_defer(monkeypatch):
    """What the live rows actually do, now that the fetcher works.

    All four held reservations in production carry a ``checkout_created``
    transaction and a real ``pi_`` id, and a read-only lookup against live
    Stripe returned ``requires_payment_method`` for each: the buyer opened
    checkout and never paid.

    That is an awaiting-buyer status, so the first evaluation defers rather
    than releasing. Release only arrives after the deferral bound, which is
    the property that makes enabling this safe to do incrementally.
    """
    monkeypatch.setattr(
        stripe.PaymentIntent, "retrieve",
        staticmethod(lambda pid, **kw: _intent(status="requires_payment_method")))

    first = reconciler.decide_for_reservation(
        {"stripe_payment_intent_id": "pi_seam",
         "transaction_status": "checkout_created"},
        deferrals=0)
    assert first["decision"] == reconciler.DECISION_DEFER
    assert first["detail"] == "awaiting_buyer"

    bounded = reconciler.decide_for_reservation(
        {"stripe_payment_intent_id": "pi_seam",
         "transaction_status": "checkout_created"},
        deferrals=reconciler.max_deferrals())
    assert bounded["decision"] == reconciler.DECISION_RELEASE
    assert bounded["detail"] == "buyer_never_completed"
