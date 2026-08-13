import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import marketplace_quote_service as quotes


def test_quote_examples_and_client_opaque_components():
    q = quotes.create_quote(listing_id=1, seller_id=2, quantity=1,
                            unit_price_minor=10_000, currency="USD",
                            live_fee_bps=500, shipping_minor=1_000, tax_minor=800)
    assert q["buyer_total_minor"] == 11_800
    assert q["platform_fee_minor"] == 500
    assert q["seller_earnings_minor"] == 10_500
    assert q["buyer_service_fee_minor"] == 0
    assert q["quote_id"].startswith("mktq_") and q["quote_expires_at"]


def test_offer_and_discount_are_snapshotted():
    q = quotes.create_quote(listing_id=1, seller_id=2, quantity=1,
                            unit_price_minor=10_000, seller_discount_minor=2_000,
                            currency="USD", live_fee_bps=500, offer_id=9,
                            offer_accepted_at="a", offer_expires_at="b")
    assert q["merchandise_net_minor"] == 8_000
    assert q["platform_fee_minor"] == 400
    assert q["offer_id"] == 9 and q["offer_price_minor"] == 10_000
