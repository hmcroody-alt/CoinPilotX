"""Marketplace card pause and fee-free cash checkout contracts."""

from __future__ import annotations

import pathlib
import re

import pytest

from services import marketplace_payment_pause
from services import marketplace_quote_service

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

BOT = (REPO_ROOT / "bot.py").read_text(encoding="utf-8", errors="ignore")
CART = (REPO_ROOT / "services" / "marketplace_cart_routes.py").read_text(encoding="utf-8")
OFFERS = (REPO_ROOT / "services" / "marketplace_offers_routes.py").read_text(encoding="utf-8")
NATIVE_CHECKOUT = (REPO_ROOT / "mobile-native" / "src" / "screens" / "MarketplaceCheckoutScreen.tsx").read_text(encoding="utf-8")


def _code(source: str) -> str:
    return "\n".join(line for line in source.splitlines() if not line.lstrip().startswith(("#", "//", "*")))


def _view(source: str, name: str) -> str:
    """One checkout view, sliced out before any ordering assertion runs.

    ``bot.py`` carries unrelated Stripe call sites — Premium and ads — roughly
    69k lines above Marketplace checkout. Comparing ``str.index`` across the
    whole file therefore measures the distance to the wrong call site and can
    never pass, however correct the gate is. Slice to the view first so the
    ordering claim is about the lane under test.
    """
    body = source[source.index(f"def {name}("):]
    nxt = re.search(r"\n(?:@|def )", body[1:])
    return body[: nxt.start() + 1] if nxt else body


# Each Marketplace checkout lane, paired with the view that owns its Stripe call.
LANES = (
    ("bot.api_pulse_payments_checkout", _view(_code(BOT), "api_pulse_payments_checkout")),
    ("cart.cart_checkout", _view(_code(CART), "cart_checkout")),
    ("offers.offer_checkout", _view(_code(OFFERS), "offer_checkout")),
)

# Every way a Marketplace lane can begin collecting money from a card.
CHARGE_CALLS = ("PaymentIntent.create(", "checkout.Session.create(")


def test_marketplace_card_modes_are_paused_but_cash_modes_are_open(monkeypatch):
    monkeypatch.delenv(marketplace_payment_pause.CARD_PAYMENTS_ENABLED_ENV_VAR, raising=False)
    assert marketplace_payment_pause.marketplace_card_payments_paused() is True
    for mode in ("", "card", "stripe", "payment_sheet", "checkout"):
        assert marketplace_payment_pause.normalize_marketplace_payment_mode(mode) == "card"
    for mode in ("cash", "cash-on-pickup", "local pickup cash", "in_person", "pay_in_person"):
        assert marketplace_payment_pause.normalize_marketplace_payment_mode(mode) == "cash"


@pytest.mark.parametrize("value", ["", "   ", "false", "False", "0", "no", "off", "maybe", "TRUE!"])
def test_every_value_that_is_not_an_explicit_yes_leaves_the_rail_paused(monkeypatch, value):
    """Fail closed.

    The realistic way this flag gets the wrong value is a mistyped Railway
    variable, not a deliberate one. Every unrecognised spelling must therefore
    mean *off*, because the failure in the other direction is a live card rail
    nobody decided to open.
    """
    monkeypatch.setenv(marketplace_payment_pause.CARD_PAYMENTS_ENABLED_ENV_VAR, value)
    assert marketplace_payment_pause.marketplace_card_payments_enabled() is False
    assert marketplace_payment_pause.marketplace_card_payments_paused() is True


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " Yes ", "on", "enabled"])
def test_an_explicit_yes_lifts_the_global_pause(monkeypatch, value):
    monkeypatch.setenv(marketplace_payment_pause.CARD_PAYMENTS_ENABLED_ENV_VAR, value)
    assert marketplace_payment_pause.marketplace_card_payments_enabled() is True
    assert marketplace_payment_pause.marketplace_card_payments_paused() is False


def test_the_flag_is_read_per_call_not_captured_at_import(monkeypatch):
    """A module-level read would freeze the answer at the first import.

    In a worker that imports early and serves for hours, that is the difference
    between a deploy that changes behaviour and one that appears to do nothing.
    """
    monkeypatch.setenv(marketplace_payment_pause.CARD_PAYMENTS_ENABLED_ENV_VAR, "true")
    assert marketplace_payment_pause.marketplace_card_payments_paused() is False
    monkeypatch.setenv(marketplace_payment_pause.CARD_PAYMENTS_ENABLED_ENV_VAR, "false")
    assert marketplace_payment_pause.marketplace_card_payments_paused() is True


def test_the_shipped_default_is_off():
    """`.env.example` is the deployed contract, not documentation.

    It is what an operator copies, and `tests/protection/test_environment_contract.py`
    requires every `os.getenv` to appear in it. Shipping it with a value would
    hand somebody a live card rail on their first deploy.
    """
    declared = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert f"\n{marketplace_payment_pause.CARD_PAYMENTS_ENABLED_ENV_VAR}=\n" in declared


def test_cash_marketplace_quotes_have_zero_platform_fee():
    fee_bps = marketplace_payment_pause.platform_fee_bps_for_marketplace_payment(1000, "cash")
    quote = marketplace_quote_service.create_quote(
        listing_id=10,
        seller_id=20,
        quantity=2,
        unit_price_minor=2500,
        currency="USD",
        live_fee_bps=fee_bps,
    )

    assert quote["platform_fee_bps"] == 0
    assert quote["platform_fee_minor"] == 0
    assert quote["seller_earnings_minor"] == quote["merchandise_net_minor"]


def test_every_marketplace_checkout_lane_blocks_card_before_stripe():
    assert marketplace_payment_pause.MARKETPLACE_CARD_UNAVAILABLE_CODE == "PAYMENT_UNAVAILABLE"
    for lane, view in LANES:
        assert "marketplace_card_payments_paused()" in view, lane
        assert "MARKETPLACE_CARD_UNAVAILABLE_MESSAGE" in view, lane
        assert "MARKETPLACE_CARD_UNAVAILABLE_CODE" in view, lane
        gate = view.index("marketplace_card_payments_paused()")
        for call in CHARGE_CALLS:
            assert call in view, f"{lane} lost its preserved card path"
            assert gate < view.index(call), f"{lane} can reach {call} before the pause gate"


def test_every_marketplace_checkout_lane_also_asks_whether_this_seller_may_charge():
    """The global flag is the outer gate; it is not the whole gate.

    While the pause was a hardcoded ``True`` these lanes could get away with
    asking only the global question, because no card checkout could start
    whatever the answer was. The moment it became a flag that stopped being
    true: turning it on would otherwise have opened the rail for every seller at
    once, including sellers with no connected account and no way to receive the
    money they had just been allowed to collect.

    Ordering is asserted against the Stripe call, not against the flag check,
    because the two gates are deliberately in different places — the flag runs
    before any connection is open, and this one runs where the seller is known.
    """
    for lane, view in LANES:
        assert "marketplace_card_capability.evaluate(" in view, (
            f"{lane} does not ask whether this seller may take a card")
        assert "buyer_view(" in view, (
            f"{lane} would hand a buyer the seller's own account state")
        seller_gate = view.index("marketplace_card_capability.evaluate(")
        for call in CHARGE_CALLS:
            assert seller_gate < view.index(call), (
                f"{lane} can reach {call} before the seller capability gate")


def test_every_marketplace_checkout_lane_submits_cash_without_stripe():
    for lane, view in LANES:
        assert '"cash_pending"' in view, lane
        assert '"cash_collect_in_person"' in view, lane
        assert "cash_checkout_payload(" in view, lane
        returns_cash = view.index("cash_checkout_payload(")
        for call in CHARGE_CALLS:
            assert returns_cash < view.index(call), f"{lane} reaches {call} on a cash checkout"


def test_the_pause_is_scoped_to_marketplace_and_leaves_other_rails_alone():
    """Premium, courses and ads settle through the same view as Marketplace goods.

    ``api_pulse_payments_checkout`` also sells courses, lessons and live classes,
    so an unscoped gate here would have taken non-Marketplace payments down with
    it — which the mission explicitly forbids.
    """
    view = _view(_code(BOT), "api_pulse_payments_checkout")
    gate = view.index("marketplace_card_payments_paused()")
    scope = view.rindex('item_type == "marketplace_product"', 0, gate)
    assert gate - scope < 400, "the card pause is not scoped to marketplace_product"


def test_native_checkout_keeps_card_visible_disabled_and_defaults_to_cash():
    source = _code(NATIVE_CHECKOUT)

    assert 'useState<MarketplaceCheckoutPaymentMethod>("cash")' in source
    assert "Card / Stripe" in NATIVE_CHECKOUT
    assert "disabled={!options.cardPaymentsAvailable}" in source
    assert 'const paymentMode = "cash"' in source
    assert "PaymentController.instruction(" in source
    assert source.index('if (paymentMethod === "cash")') < source.index("PaymentController.instruction(")
    assert source.index("!options.cardPaymentsAvailable") < source.index("PaymentController.instruction(")


def test_the_native_checkout_holds_no_opinion_of_its_own_about_the_pause():
    """A shipped binary cannot be corrected by an environment variable.

    While the server's pause was a hard-coded ``True`` the screen was free to
    carry its own copy — the two could not disagree. Now that it reads
    ``MARKETPLACE_CARD_PAYMENTS_ENABLED``, a constant in the app is a second
    authority that an operator turning the rail on cannot reach, and the only
    way to correct it would be an App Store release.

    The default the client falls back to when it cannot ask is a separate
    question, and it lives in ``api/checkoutCountries`` where its own tests pin
    it closed.
    """
    options = (REPO_ROOT / "mobile-native" / "src" / "api" / "checkoutCountries.ts").read_text(encoding="utf-8")

    source = _code(NATIVE_CHECKOUT)

    assert "MARKETPLACE_CARD_PAYMENTS_PAUSED" not in source
    assert "fetchCheckoutOptions(" in source
    assert "card_payments_available" in options
    assert "cardPaymentsAvailable: false" in options


def test_the_options_endpoint_serves_the_verdict_the_client_reads():
    """The client asks one endpoint; this is the one that answers.

    Named here rather than only in the cart's own tests because the two halves
    are a contract across a language boundary, and the failure mode is silent:
    a renamed key leaves the client reading ``undefined``, which its
    ``=== true`` check resolves to *closed*. Safe, and permanently wrong.
    """
    assert "card_payments_available" in CART
    assert "payment_unavailable_message" in CART
    view = _view(_code(CART), "cart_checkout_options")
    assert "marketplace_card_payments_paused()" in view
    assert "MARKETPLACE_CARD_UNAVAILABLE_BADGE" in view
