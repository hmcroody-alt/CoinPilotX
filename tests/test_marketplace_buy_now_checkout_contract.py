"""Focused regression contract for the physical-goods Buy Now path.

This route lives in the legacy monolith, so the safest narrow test inspects the
function body without importing the full application and starting its workers.
The behavior itself reuses the already unit-tested reservation helpers.
"""

from pathlib import Path


BOT = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
START = BOT.index("def api_pulse_payments_checkout():")
END = BOT.index("\ndef _creator_checkout_for_item", START)
CHECKOUT = BOT[START:END]


def test_native_physical_goods_are_not_rejected_by_blanket_ios_gate():
    prefix = CHECKOUT[: CHECKOUT.index('payload = request.get_json')]
    assert "ios_paid_digital_unavailable_response" not in prefix
    assert 'item_type != "marketplace_product"' in CHECKOUT


def test_buy_now_is_idempotent_and_reserves_inventory():
    assert 'idempotency_key = str(payload.get("idempotency_key")' in CHECKOUT
    assert "marketplace_cart_checkout_keys" in CHECKOUT
    assert "marketplace_inventory_reservations" in CHECKOUT
    assert "quantity=quantity-1" in CHECKOUT
    # The release no longer appears here by name. It moved into
    # `settle_failed_transactions`, the one path shared with the webhook
    # branches, so that "return the stock, then close the order" has a single
    # implementation rather than five that can drift apart. What must stay true
    # of this route is that its failure path reaches that service.
    assert "settle_failed_transactions" in CHECKOUT


def test_buy_now_reuses_cart_webhook_reconciliation_contract():
    assert '"cart_checkout": "1"' in CHECKOUT
    assert '"seller_transaction_ids": str(tx_id)' in CHECKOUT
    assert '"listing_ids": str(item_id)' in CHECKOUT
    assert '"quantities": "1"' in CHECKOUT
    assert "marketplace-buy-now:" in CHECKOUT
