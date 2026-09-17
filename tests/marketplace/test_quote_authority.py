import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import marketplace_quote_service as quotes
from services.business_os.marketplace import policy


@pytest.fixture
def gates_open(monkeypatch):
    # The 5% math below is what the policy charges *after* the owner opens the
    # gates. It is unreachable until then, which is the point of the gates, so
    # the arithmetic has to be tested with them held open.
    monkeypatch.setenv("MARKETPLACE_STANDARD_V1_OWNER_APPROVED", "1")
    monkeypatch.setenv("MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY", "1")
    monkeypatch.setenv("MARKETPLACE_STANDARD_V1_EFFECTIVE_AT", "2000-01-01T00:00:00Z")
    assert policy.platform_fee_bps() == 500


@pytest.fixture
def gates_shut(monkeypatch):
    for key in ("MARKETPLACE_STANDARD_V1_OWNER_APPROVED",
                "MARKETPLACE_STANDARD_V1_SELLER_DISCLOSURE_READY",
                "MARKETPLACE_STANDARD_V1_EFFECTIVE_AT"):
        monkeypatch.delenv(key, raising=False)
    assert policy.platform_fee_bps() == 0


def test_quote_examples_and_client_opaque_components(gates_open):
    q = quotes.create_quote(listing_id=1, seller_id=2, quantity=1,
                            unit_price_minor=10_000, currency="USD",
                            live_fee_bps=500, shipping_minor=1_000, tax_minor=800)
    assert q["buyer_total_minor"] == 11_800
    assert q["platform_fee_minor"] == 500
    assert q["seller_earnings_minor"] == 10_500
    assert q["buyer_service_fee_minor"] == 0
    assert q["quote_id"].startswith("mktq_") and q["quote_expires_at"]


def test_offer_and_discount_are_snapshotted(gates_open):
    q = quotes.create_quote(listing_id=1, seller_id=2, quantity=1,
                            unit_price_minor=10_000, seller_discount_minor=2_000,
                            currency="USD", live_fee_bps=500, offer_id=9,
                            offer_accepted_at="a", offer_expires_at="b")
    assert q["merchandise_net_minor"] == 8_000
    assert q["platform_fee_minor"] == 400
    assert q["offer_id"] == 9 and q["offer_price_minor"] == 10_000


def test_no_commission_is_taken_until_the_owner_opens_the_gates(gates_shut):
    # The seller keeps everything while the policy is undisclosed. A quote that
    # charged 5% here would be taking a commission off terms nobody agreed to.
    q = quotes.create_quote(listing_id=1, seller_id=2, quantity=1,
                            unit_price_minor=10_000, currency="USD",
                            live_fee_bps=0, shipping_minor=1_000)
    assert q["platform_fee_minor"] == 0
    assert q["seller_earnings_minor"] == 11_000
    assert q["fee_policy_version"] == "MARKETPLACE_STANDARD_V1"
    assert q["fee_policy_active"] is False


def test_a_rate_the_policy_did_not_set_is_refused(gates_shut):
    # The legacy `platform_fee_rules` merchant row said 10%. Nothing may price a
    # quote off a mutable admin row again, and the failure has to be loud —
    # silently substituting the policy rate would hide the miswired caller.
    with pytest.raises(ValueError, match="1000"):
        quotes.create_quote(listing_id=1, seller_id=2, quantity=1,
                            unit_price_minor=10_000, currency="USD",
                            live_fee_bps=1000)


def test_the_zero_fee_lane_survives_an_active_policy(gates_open):
    # Cash and pickup settle in person and carry no commission, so zero stays
    # legal even when the policy rate is not zero.
    q = quotes.create_quote(listing_id=1, seller_id=2, quantity=1,
                            unit_price_minor=10_000, currency="USD",
                            live_fee_bps=0)
    assert q["platform_fee_minor"] == 0
    assert q["fee_policy_active"] is True
