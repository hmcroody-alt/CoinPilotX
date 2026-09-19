"""`GET /api/pulse/marketplace/cart/checkout-options` — the wire the app reads.

The native checkout no longer decides for itself whether Marketplace card
payments are open; it asks this route. That makes the response body a contract
across a language boundary, and the failure mode is silent in the worst
direction: the client resolves a missing or renamed key to ``false``, so a
broken contract produces a checkout that is permanently, quietly paused with no
error anywhere.

The route is unauthenticated on purpose — it is deployment configuration, not
anyone's data — so the platform verdict is all it may carry. Whether a
*particular* seller may take a card is a fact about that seller's Connect
account and is asked later, on the checkout lanes, where the buyer is known.
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


def _options(client) -> dict:
    response = client.get(PATH)
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
    body = _options(client)
    serialized = repr(body)
    for reason in capability.SELLER_PRIVATE_REASONS:
        assert reason not in serialized
