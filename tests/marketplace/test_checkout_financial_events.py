"""The checkout path's financial events, and what they must never carry.

Two things are being proven here, and the second is the reason this file is
worth its length.

**That the events happen.** Every assertion reads a real ``logging`` record
captured from a real call. None of them greps source for an event name, and
none of them asserts that a name exists in :data:`EVENT_NAMES`. A registry of
names is exactly the wrong thing to test against: it makes ``grep`` report a
name as present whether or not any code path emits it, so a test written that
way stays green when the emit is deleted. The name constants are referenced
only where the *persisted* literal is also pinned, for the reason given in
:func:`test_event_names_are_pinned_to_their_literals`.

**That the events never carry a credential.** The specific hazard is not card
numbers -- this path never sees one, Stripe collects it -- it is that a Stripe
Checkout Session's ``url`` embeds that session's client secret. It is a
credential shaped like a link, it is already in the refusal payload's
neighbourhood, and ``_error``'s ``**extra`` is open-ended. So the tests below
feed the emitter a payload containing one and assert on the *formatted message*
that no part of it survives.
"""
from __future__ import annotations

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import marketplace_checkout_events as events  # noqa: E402


# A real Stripe Checkout Session URL shape. The `cs_test_...` segment is the
# session id and the fragment carries its client secret; the whole string is a
# bearer credential for that payment. Fabricated, obviously, but shaped so a
# substring assertion against it is meaningful.
SESSION_URL = (
    "https://checkout.stripe.com/c/pay/cs_test_a1B2c3D4e5F6g7H8i9"
    "#fidkdWxOYHwnPyd1blpxYHZxWjA0SjZp"
)
SECRET_FRAGMENT = "cs_test_a1B2c3D4e5F6g7H8i9"


def _records(caplog):
    """Every captured record's formatted message.

    ``record.getMessage()`` rather than ``caplog.text`` so the assertions are
    against what the handler would actually write, with the lazy ``%s`` args
    interpolated -- which is where a leaked value would appear. Reading
    ``record.msg`` instead would see only the format string and would pass no
    matter what was passed as an argument.
    """
    return [r.getMessage() for r in caplog.records]


def _one(caplog, name):
    matching = [m for m in _records(caplog) if m.startswith(name + " ")]
    assert len(matching) == 1, f"expected exactly one {name}, got {_records(caplog)}"
    return matching[0]


# ---------------------------------------------------------------------------
# the events happen at all
# ---------------------------------------------------------------------------

def test_an_attempt_is_recorded_before_any_decision(caplog):
    """The denominator.

    Without this line "nobody tried to check out" and "everyone who tried was
    refused" produce byte-identical logs. That is not a theoretical gap: the
    card rail sat at zero completed sales for its entire life and no log
    anywhere distinguished those two explanations.
    """
    with caplog.at_level(logging.INFO, logger=events.LOGGER.name):
        events.attempted(buyer_user_id=7, seller_user_id=9,
                         payment_mode="card", has_idempotency_key=True)

    message = _one(caplog, "CHECKOUT_ATTEMPTED")
    assert "buyer=7" in message
    assert "seller=9" in message
    assert "payment_mode=card" in message
    assert "idempotency_key_present=True" in message


def test_the_idempotency_key_itself_is_never_logged(caplog):
    """Only whether one was supplied.

    The key is chosen by the client, so it is an arbitrary buyer-controlled
    string. Whether one arrived explains the later ``CHECKOUT_IDEMPOTENCY_*``
    lines; its contents explain nothing and could be anything.

    Enforced by the signature -- there is no parameter to pass it through --
    which is what this test pins: a future change adding one has to delete this.
    """
    import inspect
    params = inspect.signature(events.attempted).parameters
    assert "idempotency_key" not in params
    assert "has_idempotency_key" in params

    with caplog.at_level(logging.INFO, logger=events.LOGGER.name):
        events.attempted(buyer_user_id=1, seller_user_id=2, payment_mode="card",
                         has_idempotency_key=True)
    assert "idempotency_key_present=True" in _one(caplog, "CHECKOUT_ATTEMPTED")


def test_a_refusal_records_the_code_it_handed_the_client(caplog):
    """The stable half of the contract, and the only half worth logging.

    ``message`` is prose -- it gets reworded -- so the code is what a later
    query can rely on. A buyer reporting "it won't let me check out" previously
    left no server-side trace at all.
    """
    with caplog.at_level(logging.INFO, logger=events.LOGGER.name):
        events.refused(code="OUT_OF_STOCK", status=409, op="cart_checkout",
                       extra={"blocking_line_ids": [4, 5]})

    message = _one(caplog, "MARKETPLACE_CART_REFUSED")
    assert "code=OUT_OF_STOCK" in message
    assert "status=409" in message
    assert "op=cart_checkout" in message
    assert "blocking_line_ids" in message


def test_an_unknown_refusal_code_is_recorded_verbatim(caplog):
    """A recorder, not a validator.

    ``_error``'s own docstring claims a fixed sixteen-code vocabulary and the
    shipped code disagrees with it -- five codes are emitted that the list never
    mentions, and ten refusal sites pass a code computed in another module. So
    no list written in the events module could be complete, and one that
    rejected or remapped an unrecognised code would silently drop exactly the
    refusals nobody predicted, which are the ones worth seeing.
    """
    with caplog.at_level(logging.INFO, logger=events.LOGGER.name):
        events.refused(code="SOME_CODE_INVENTED_NEXT_YEAR", status=400,
                       op="cart_checkout")

    assert "code=SOME_CODE_INVENTED_NEXT_YEAR" in _one(
        caplog, "MARKETPLACE_CART_REFUSED")


def test_a_card_payable_records_the_provider_object_and_its_mode(caplog):
    """The money moment, which used to be the only outcome *not* logged.

    ``livemode`` is the provider's own answer rather than a local reading of
    configuration, so a deployment whose keys disagree with its intent shows up
    in this line instead of only in the Stripe dashboard.
    """
    with caplog.at_level(logging.INFO, logger=events.LOGGER.name):
        events.payable_created(
            surface=events.SURFACE_CHECKOUT_SESSION,
            provider_object_id="cs_test_visible_id",
            amount_cents=625, currency="USD", transaction_ids=[11, 12],
            buyer_user_id=7, seller_user_id=9, livemode=False,
        )

    message = _one(caplog, "CHECKOUT_PAYABLE_CREATED")
    assert "surface=checkout_session" in message
    assert "provider_object=cs_test_visible_id" in message
    assert "amount_cents=625" in message
    assert "currency=usd" in message
    assert "livemode=False" in message
    assert "transaction_ids=11,12" in message


def test_a_cash_order_is_not_counted_as_a_card_payable(caplog):
    """The one surface that must stay separable.

    A cash checkout is a real obligation and belongs in the accounting, but no
    chargeable object exists at Stripe. Folding it in would corrupt the single
    number this taxonomy exists to make readable -- how often the card rail
    actually produced something payable -- and that number is the one the whole
    mission turns on.
    """
    with caplog.at_level(logging.INFO, logger=events.LOGGER.name):
        events.payable_created(
            surface=events.SURFACE_CASH, amount_cents=1200, currency="usd",
            transaction_ids=[3], buyer_user_id=7, seller_user_id=9,
        )

    message = _one(caplog, "CHECKOUT_PAYABLE_CREATED")
    assert "surface=cash_on_fulfillment" in message
    # Same shape as a card line, with the provider fields blank rather than
    # absent, so one query reads both.
    assert "provider_object= " in message
    assert "livemode=None" in message
    assert events.SURFACE_CASH not in {events.SURFACE_CHECKOUT_SESSION,
                                       events.SURFACE_PAYMENT_INTENT}


# ---------------------------------------------------------------------------
# what they must never carry
# ---------------------------------------------------------------------------

def test_a_stripe_session_url_in_a_refusal_payload_never_reaches_the_log(caplog):
    """The credential that looks like a link.

    A Checkout Session URL embeds that session's client secret, so logging one
    publishes a bearer token for a live payment into whatever aggregates the
    logs. ``_error``'s ``**extra`` is open-ended and this value is in its
    neighbourhood -- the success payload one branch away carries it -- so the
    emitter has to refuse it rather than trust that no caller ever passes it.
    """
    with caplog.at_level(logging.INFO, logger=events.LOGGER.name):
        events.refused(code="PAYMENT_FAILED", status=402, op="cart_checkout",
                       extra={"checkout_url": SESSION_URL, "trace_id": "abc123"})

    message = _one(caplog, "MARKETPLACE_CART_REFUSED")
    assert SECRET_FRAGMENT not in message
    assert SESSION_URL not in message
    assert "checkout.stripe.com" not in message
    # Dropped loudly: the key is named so the omission is visible, and an
    # operator can tell "someone tried to log a credential" from "someone added
    # a field". The key name is not itself sensitive.
    assert "denied=checkout_url" in message
    # The useful neighbour still arrives.
    assert "trace_id" in message and "abc123" in message


@pytest.mark.parametrize("key,value", [
    ("address", "14 Example Street, Apt 2, Springfield"),
    ("shipping_address", "14 Example Street"),
    ("email", "buyer@example.com"),
    ("phone", "+15551234567"),
    ("card", "4242424242424242"),
    ("payment_method", "pm_1AbCdEfGhIjKlMnO"),
    ("client_secret", "pi_3ABC_secret_XYZ"),
    ("token", "tok_visa_abcdef"),
    ("secret", "whsec_abcdefghijklmnop"),
    ("idempotency_key", "buyer-chosen-anything"),
])
def test_no_denied_field_value_ever_appears_in_a_refusal_line(caplog, key, value):
    """One case per thing the brief forbids logging.

    Parametrised rather than looped inside one test so a single leak names the
    field that leaked instead of failing an opaque aggregate. Each case asserts
    on the value, not the key: the key name is deliberately logged.
    """
    with caplog.at_level(logging.INFO, logger=events.LOGGER.name):
        events.refused(code="PAYMENT_FAILED", status=402, op="cart_checkout",
                       extra={key: value})

    message = _one(caplog, "MARKETPLACE_CART_REFUSED")
    assert value not in message, f"{key} leaked its value"
    assert f"denied={key}" in message


def test_a_field_nobody_allowlisted_is_dropped_and_named(caplog):
    """Default-deny, so a future refusal site cannot widen the log by accident.

    The failure mode this prevents is quiet: someone adds ``**extra`` context to
    a refusal, it happens to contain something private, and nothing complains
    because the emitter passed everything through. Here it is dropped, and the
    key name in ``unknown=`` is the notice that a decision is owed -- allowlist
    it in ``REFUSAL_SAFE_FIELDS`` or leave it out deliberately.
    """
    with caplog.at_level(logging.INFO, logger=events.LOGGER.name):
        events.refused(code="ITEM_UNAVAILABLE", status=409, op="cart_checkout",
                       extra={"buyer_note": "please leave at the back door",
                              "trace_id": "keepme"})

    message = _one(caplog, "MARKETPLACE_CART_REFUSED")
    assert "please leave at the back door" not in message
    assert "unknown=buyer_note" in message
    assert "keepme" in message


def test_the_denied_list_is_matched_case_insensitively():
    """Because a payload key's casing is not a security boundary.

    A refusal site writing ``Address`` or ``CHECKOUT_URL`` must be refused the
    same way. Tested on the pure splitter so the assertion is about the rule
    and not about a formatted string.
    """
    kept, denied, unknown = events.safe_refusal_extra(
        {"Address": "x", "CHECKOUT_URL": "y", "Token": "z"})
    assert kept == {}
    assert denied == ["Address", "CHECKOUT_URL", "Token"]
    assert unknown == []


def test_payable_created_has_no_parameter_for_a_session_url():
    """Enforced by the signature, not by a filter.

    The strongest available guarantee for this one field: there is no channel
    through which the URL could arrive, so no reviewer has to notice. A change
    that adds one has to delete this test, which is the conversation worth
    forcing.
    """
    import inspect
    params = set(inspect.signature(events.payable_created).parameters)
    for forbidden in ("checkout_url", "url", "client_secret", "secret"):
        assert forbidden not in params


# ---------------------------------------------------------------------------
# they cannot change an outcome
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("call", [
    lambda: events.attempted(buyer_user_id="not-an-int", seller_user_id=None,
                             payment_mode=None),
    lambda: events.refused(code=None, status="not-an-int", op=None,
                           extra={"trace_id": object()}),
    lambda: events.payable_created(surface=None, amount_cents="nope",
                                   currency=None, transaction_ids="xyz",
                                   buyer_user_id=None, seller_user_id=None),
])
def test_an_emitter_never_raises_on_hostile_input(call):
    """A log line must not be able to fail a checkout.

    Not defensive padding: two of these emits sit *inside* the handler's
    ``try``, whose ``except`` expires the Stripe session and returns a refusal.
    An exception raised while formatting an informational line would therefore
    convert a completed payment into a failed one and cancel the session behind
    it. The emitters swallow and self-report instead.
    """
    call()  # must not raise


def test_a_broken_logger_does_not_propagate(monkeypatch, caplog):
    """The same guarantee, proven against the logger rather than the arguments.

    ``caplog`` cannot see this one -- the handler is what is broken -- so the
    assertion is simply that the call returns.
    """
    def explode(*_a, **_k):
        raise RuntimeError("log backend down")

    monkeypatch.setattr(events.LOGGER, "info", explode)
    monkeypatch.setattr(events.LOGGER, "exception", lambda *_a, **_k: None)

    events.attempted(buyer_user_id=1, seller_user_id=2, payment_mode="card")
    events.refused(code="NOT_FOUND", status=404, op="cart_checkout")
    events.payable_created(surface=events.SURFACE_CASH, amount_cents=1,
                           currency="usd", transaction_ids=[1],
                           buyer_user_id=1, seller_user_id=2)


# ---------------------------------------------------------------------------
# the wiring, not just the module
# ---------------------------------------------------------------------------

def test_the_error_funnel_in_cart_routes_actually_emits(caplog):
    """The module working proves nothing if nothing calls it.

    ``_error`` is instrumented rather than its ~30 call sites, so this one
    assertion covers every refusal the surface can produce -- and, more to the
    point, a refusal site added later is covered without anyone remembering.
    Driven through the real function inside a real request context, because the
    thing being tested is the wiring.
    """
    from flask import Flask
    from services import marketplace_cart_routes as cart_routes

    app = Flask(__name__)
    with caplog.at_level(logging.INFO):
        with app.test_request_context("/api/pulse/marketplace/cart/checkout",
                                      method="POST"):
            cart_routes._error("Some items are no longer available.", 409,
                               code="OUT_OF_STOCK", blocking_line_ids=[7])

    message = _one(caplog, "MARKETPLACE_CART_REFUSED")
    assert "code=OUT_OF_STOCK" in message
    assert "status=409" in message
    assert "blocking_line_ids" in message


def test_the_error_funnel_does_not_leak_a_url_passed_as_extra(caplog):
    """End to end, through the real funnel, with the real hazard.

    The previous test proves the wiring exists; this proves the wiring is wired
    to the *filtered* path. An emit that bypassed ``safe_refusal_extra`` would
    pass that test and fail this one.
    """
    from flask import Flask
    from services import marketplace_cart_routes as cart_routes

    app = Flask(__name__)
    with caplog.at_level(logging.INFO):
        with app.test_request_context("/api/pulse/marketplace/cart/checkout",
                                      method="POST"):
            cart_routes._error("Payment failed.", 402, code="PAYMENT_FAILED",
                               checkout_url=SESSION_URL)

    message = _one(caplog, "MARKETPLACE_CART_REFUSED")
    assert SECRET_FRAGMENT not in message
    assert "denied=checkout_url" in message


def test_a_refusal_outside_a_request_context_still_records(caplog):
    """``_error`` is reachable from helpers that may run without a request.

    If the ``op`` lookup were a bare ``request.endpoint`` read, those calls
    would raise outside a request context -- turning a refusal into a 500 for
    the sake of a log field. The event is still emitted, with ``op`` unknown.
    """
    from services import marketplace_cart_routes as cart_routes
    from flask import Flask

    app = Flask(__name__)
    with caplog.at_level(logging.INFO):
        # An app context without a *request* context: `_json`/`jsonify` works,
        # `request.endpoint` does not.
        with app.app_context():
            try:
                cart_routes._error("Login required.", 401, code="LOGIN_REQUIRED")
            except Exception as exc:  # pragma: no cover - the point of the test
                pytest.fail(f"a refusal raised outside a request context: {exc!r}")

    assert "code=LOGIN_REQUIRED" in _one(caplog, "MARKETPLACE_CART_REFUSED")


# ---------------------------------------------------------------------------
# the emits exist at every exit
# ---------------------------------------------------------------------------
#
# A mutation run over this file found the gap these tests close. Every redaction
# claim was held, and all four *wiring* mutations survived: deleting the attempt
# emit, deleting either payable emit, or relabelling the cash order as a card
# payable all left the suite green. The module was proven and its callers were
# not, which is the same shape of hole that let an uncalled reconciliation sweep
# look finished earlier in this mission.
#
# These are source-structure assertions, and the honest limit is that they prove
# the call is written at that exit, not that it runs. The behavioural half is
# covered by the funnel tests above and by the handler's own suites. What they
# do buy is the thing a behavioural test of this handler cannot buy cheaply --
# the handler needs a database, a seller, listings and a stubbed provider to
# reach either payable branch -- and they buy it against deletion, which is the
# realistic regression.
#
# Parsed rather than grepped, deliberately. A grep counts a mention inside a
# comment or a docstring as a call; this repo has a protection suite that was
# blind for exactly that reason. `ast` sees only real calls.

def _cart_routes_tree():
    import ast
    import services.marketplace_cart_routes as cart_routes
    return ast.parse(open(cart_routes.__file__, encoding="utf-8").read())


def _event_calls(attr):
    """Every real call to ``checkout_events.<attr>`` in the cart routes."""
    import ast
    found = []
    for node in ast.walk(_cart_routes_tree()):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr == attr
                and isinstance(func.value, ast.Name)
                and func.value.id == "checkout_events"):
            found.append(node)
    return found


def test_the_handler_records_an_attempt():
    """The denominator has to be emitted by the handler, not merely available.

    This mutation survived the first run: removing the call left every test
    green, because the suite only proved the function worked when called.
    """
    assert len(_event_calls("attempted")) == 1


def test_every_success_exit_records_a_payable_with_its_own_surface():
    """Three exits, three surfaces, one call each.

    One assertion covering both failures the mutation run exposed: deleting a
    payable emit drops a surface from this mapping, and relabelling the cash
    order as a Checkout Session makes one surface appear twice while another
    disappears. Counting per surface catches both, where a bare "three calls
    exist" check would catch neither.
    """
    import ast
    surfaces = []
    for call in _event_calls("payable_created"):
        for kw in call.keywords:
            if kw.arg != "surface":
                continue
            # `surface=checkout_events.SURFACE_X` -> "SURFACE_X"
            assert isinstance(kw.value, ast.Attribute), (
                "surface should be one of the module's named constants, not a "
                f"literal or expression: {ast.dump(kw.value)}"
            )
            surfaces.append(kw.value.attr)

    assert sorted(surfaces) == [
        "SURFACE_CASH", "SURFACE_CHECKOUT_SESSION", "SURFACE_PAYMENT_INTENT"
    ], f"each success exit must report its own surface exactly once, got {surfaces}"


def test_the_refusal_funnel_is_the_only_refusal_emit():
    """One emit site, which is what makes the coverage total.

    If a second ``refused`` call appeared, some refusals would be double
    counted and the ``attempted = refused + payable + crash`` identity would
    stop holding -- and an identity an operator cannot trust is worse than no
    identity, because it gets believed once.
    """
    assert len(_event_calls("refused")) == 1


def test_no_success_exit_logs_the_checkout_url():
    """The credential-shaped link, checked at the call sites too.

    :func:`test_payable_created_has_no_parameter_for_a_session_url` proves the
    emitter has no channel for it. This proves no caller tries, so the day
    someone adds the parameter back there is still a test standing in the way.
    """
    import ast
    for call in _event_calls("payable_created"):
        for kw in call.keywords:
            assert kw.arg not in {"checkout_url", "url", "client_secret"}, (
                f"a payable emit passes {kw.arg}: {ast.dump(kw.value)[:120]}"
            )


def test_event_names_are_pinned_to_their_literals():
    """The constants and the strings operators will have saved queries against.

    Asserting ``events.ATTEMPTED == events.ATTEMPTED`` through a constant is
    how a rename satisfies both sides of a comparison while changing what lands
    in the log -- a mutation survived exactly that way earlier in this mission.
    So the literal is pinned here, once, and the tests above match on the
    literal prefix rather than on the constant.
    """
    assert events.ATTEMPTED == "CHECKOUT_ATTEMPTED"
    assert events.REFUSED == "MARKETPLACE_CART_REFUSED"
    assert events.PAYABLE_CREATED == "CHECKOUT_PAYABLE_CREATED"
    assert events.SURFACE_CHECKOUT_SESSION == "checkout_session"
    assert events.SURFACE_PAYMENT_INTENT == "payment_intent"
    assert events.SURFACE_CASH == "cash_on_fulfillment"
