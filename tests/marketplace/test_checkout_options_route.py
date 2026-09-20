"""`GET /api/pulse/marketplace/cart/checkout-options` — the wire the app reads.

The native checkout no longer decides for itself whether Marketplace card
payments are open; it asks this route. That makes the response body a contract
across a language boundary, and the failure mode is silent in the worst
direction: the client resolves a missing or renamed key to ``false``, so a
broken contract produces a checkout that is permanently, quietly paused with no
error anywhere.

The route is unauthenticated on purpose — it is deployment configuration, not
anyone's data. Asked bare, the platform verdict is all it carries.

It also answers ``?seller_id=``, and that is a widening worth stating plainly.
While the rail was off it would have been pointless: everything was unavailable
and the platform answer was the whole truth. With the rail on the two answers
disagree for every seller who has not finished Connect onboarding, and a form
built from the platform answer alone shows a card row the checkout lane then
refuses — after the buyer has chosen to pay. So the route has to be askable
about a seller.

What it may disclose about one is unchanged. The answer goes through
``buyer_view``, which collapses all five seller-private reasons to a single
code and a single sentence, so an anonymous caller learns one bit: whether this
seller can take a card. That bit is already visible to anyone who opens the
seller's checkout, which is why it is publishable and why *why* is not.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB = tempfile.mkstemp(suffix=".db", prefix="checkout_options_route_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"

import bot  # noqa: E402
from services import marketplace_payment_pause  # noqa: E402

PATH = "/api/pulse/marketplace/cart/checkout-options"


@pytest.fixture
def client():
    bot.app.config["TESTING"] = True
    with bot.app.test_client() as c:
        yield c


def _options(client, seller=None) -> dict:
    response = client.get(PATH if seller is None else f"{PATH}?seller_id={seller}")
    assert response.status_code == 200
    return response.get_json()


def test_an_unset_flag_serves_a_closed_rail_with_something_to_show_for_it(client, monkeypatch):
    monkeypatch.delenv(marketplace_payment_pause.CARD_PAYMENTS_ENABLED_ENV_VAR, raising=False)
    body = _options(client)

    assert body["card_payments_available"] is False
    # Both strings are non-empty because the client renders them. A closed rail
    # that answered with blanks would disable the card row and label it nothing.
    assert body["payment_badge"] == marketplace_payment_pause.MARKETPLACE_CARD_UNAVAILABLE_BADGE
    assert body["payment_unavailable_message"] == marketplace_payment_pause.MARKETPLACE_CARD_UNAVAILABLE_MESSAGE


def test_the_flag_is_what_the_route_reports(client, monkeypatch):
    monkeypatch.setenv(marketplace_payment_pause.CARD_PAYMENTS_ENABLED_ENV_VAR, "true")
    body = _options(client)
    assert body["card_payments_available"] is True
    assert body["payment_badge"] == ""
    assert body["payment_unavailable_message"] == ""

    monkeypatch.setenv(marketplace_payment_pause.CARD_PAYMENTS_ENABLED_ENV_VAR, "false")
    assert _options(client)["card_payments_available"] is False


def test_the_verdict_is_a_real_boolean(client, monkeypatch):
    """The client tests ``=== true``.

    A truthy non-boolean — ``1``, ``"true"`` — would read as *closed* through
    that check, which is safe and permanently wrong, and nothing would report it.
    """
    monkeypatch.setenv(marketplace_payment_pause.CARD_PAYMENTS_ENABLED_ENV_VAR, "1")
    assert _options(client)["card_payments_available"] is True


def test_the_country_list_is_still_served_beside_it(client, monkeypatch):
    """The card verdict was added to an endpoint that already had a job.

    A checkout that could not build its country picker would be blocked for
    every buyer, on the one payment lane that is currently open.
    """
    monkeypatch.delenv(marketplace_payment_pause.CARD_PAYMENTS_ENABLED_ENV_VAR, raising=False)
    body = _options(client)
    assert body["ok"] is True
    assert body["shipping_countries"], "the picker would have nothing to offer"


def test_the_route_tells_an_anonymous_caller_nothing_about_any_seller(client, monkeypatch):
    """It is unauthenticated, so it must stay free of anyone's account state.

    Asserted over every reason code the capability module can produce rather
    than the ones this route happens to emit, so a later change that widens the
    payload into per-seller territory fails here.
    """
    from services import marketplace_card_capability as capability

    monkeypatch.setenv(marketplace_payment_pause.CARD_PAYMENTS_ENABLED_ENV_VAR, "true")
    for body in (_options(client), _options(client, 4242)):
        serialized = repr(body)
        for reason in capability.SELLER_PRIVATE_REASONS:
            assert reason not in serialized


# --------------------------------------------------------------------------- #
# ?seller_id= — the half of the answer the platform flag cannot give
# --------------------------------------------------------------------------- #

def _patch_verdict(monkeypatch, *, available, message="nope"):
    """Stand in for the capability engine at the seam the route uses.

    The engine's own behaviour is pinned in ``test_card_capability.py`` against
    a real cursor. What is under test *here* is narrower and not covered there:
    that this route asks at all, and that it treats the answer as a veto.
    """
    from services import marketplace_card_capability as capability

    monkeypatch.setattr(capability, "evaluate", lambda *a, **k: {"stub": True})
    monkeypatch.setattr(capability, "buyer_view", lambda decision: {
        "card_payments_available": available,
        "reason_code": marketplace_payment_pause.MARKETPLACE_CARD_UNAVAILABLE_CODE,
        "message": message,
        "badge": marketplace_payment_pause.MARKETPLACE_CARD_UNAVAILABLE_BADGE,
    })


def test_an_ineligible_seller_closes_the_card_row_on_an_open_rail(client, monkeypatch):
    """The whole point. Without this the buyer is refused at charge time.

    The platform answer and the seller answer only ever disagree while the rail
    is on, so this case did not exist until the flag was flipped — which is
    exactly why it is easy to ship the flip without it.
    """
    monkeypatch.setenv(marketplace_payment_pause.CARD_PAYMENTS_ENABLED_ENV_VAR, "true")
    assert _options(client)["card_payments_available"] is True, "platform rail should be open"

    _patch_verdict(monkeypatch, available=False, message="Seller has not enabled card payments yet.")
    body = _options(client, 4242)
    assert body["card_payments_available"] is False
    assert body["payment_badge"] == marketplace_payment_pause.MARKETPLACE_CARD_UNAVAILABLE_BADGE
    assert body["payment_unavailable_message"] == "Seller has not enabled card payments yet."


def test_an_eligible_seller_leaves_the_open_rail_open(client, monkeypatch):
    """Otherwise the veto would be indistinguishable from an outage."""
    monkeypatch.setenv(marketplace_payment_pause.CARD_PAYMENTS_ENABLED_ENV_VAR, "true")
    _patch_verdict(monkeypatch, available=True)
    body = _options(client, 4242)
    assert body["card_payments_available"] is True
    assert body["payment_badge"] == ""
    assert body["payment_unavailable_message"] == ""


def test_a_seller_cannot_reopen_a_closed_rail(client, monkeypatch):
    """The two gates are an AND, and the platform one is outermost.

    A stale or wrong row in a seller table must not be able to outvote the
    switch the operator actually controls — that is the rollback lever.
    """
    monkeypatch.delenv(marketplace_payment_pause.CARD_PAYMENTS_ENABLED_ENV_VAR, raising=False)
    _patch_verdict(monkeypatch, available=True)
    body = _options(client, 4242)
    assert body["card_payments_available"] is False
    assert body["payment_unavailable_message"] == marketplace_payment_pause.MARKETPLACE_CARD_UNAVAILABLE_MESSAGE


def test_a_lookup_that_blows_up_fails_closed(client, monkeypatch):
    """An unreachable database is not evidence that a seller may take money."""
    from services import marketplace_card_capability as capability

    monkeypatch.setenv(marketplace_payment_pause.CARD_PAYMENTS_ENABLED_ENV_VAR, "true")

    def _boom(*_a, **_k):
        raise RuntimeError("no connection")

    monkeypatch.setattr(capability, "evaluate", _boom)
    body = _options(client, 4242)
    assert body["card_payments_available"] is False
    assert body["payment_unavailable_message"].strip(), "a closed row still has to say something"


@pytest.mark.parametrize("raw", ["", "0", "-1", "abc", "1;DROP TABLE", "9e99999", " "])
def test_a_seller_id_that_is_not_one_is_ignored_not_obeyed(client, monkeypatch, raw):
    """Unparseable input must fall back to the platform answer, not crash.

    This route is the checkout's first call; a 500 here blanks the country
    picker too and blocks the cash lane, which is the lane that works.
    """
    monkeypatch.setenv(marketplace_payment_pause.CARD_PAYMENTS_ENABLED_ENV_VAR, "true")
    body = _options(client, raw)
    assert body["ok"] is True
    assert body["shipping_countries"]
    assert body["card_payments_available"] is True


def test_the_real_engine_refuses_a_seller_that_does_not_exist(client, monkeypatch):
    """No stub. An empty database is the production state today.

    Proves the wiring end to end — route to engine to response — and pins the
    property that matters most right now: with zero onboarded sellers, an open
    rail still offers cards to nobody.
    """
    monkeypatch.setenv(marketplace_payment_pause.CARD_PAYMENTS_ENABLED_ENV_VAR, "true")
    body = _options(client, 987654321)
    assert body["card_payments_available"] is False
    assert body["payment_badge"] == marketplace_payment_pause.MARKETPLACE_CARD_UNAVAILABLE_BADGE
