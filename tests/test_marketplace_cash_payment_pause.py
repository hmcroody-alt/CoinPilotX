"""Marketplace card pause and fee-free cash checkout contracts."""

from __future__ import annotations

import pathlib
import re

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


def test_marketplace_card_modes_are_paused_but_cash_modes_are_open():
    assert marketplace_payment_pause.marketplace_card_payments_paused() is True
    for mode in ("", "card", "stripe", "payment_sheet", "checkout"):
        assert marketplace_payment_pause.normalize_marketplace_payment_mode(mode) == "card"
    for mode in ("cash", "cash-on-pickup", "local pickup cash", "in_person", "pay_in_person"):
        assert marketplace_payment_pause.normalize_marketplace_payment_mode(mode) == "cash"


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
    assert "Temporarily Unavailable" in NATIVE_CHECKOUT
    assert "disabled" in source
    assert 'const paymentMode = "cash"' in source
    assert "PaymentController.instruction(" in source
    assert source.index('if (paymentMethod === "cash")') < source.index("PaymentController.instruction(")
    assert source.index("Marketplace card payments are temporarily unavailable") < source.index("PaymentController.instruction(")
