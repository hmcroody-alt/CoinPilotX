"""Business OS — Seller reputation: verified-transaction ratings only.

Proves reputation cannot be fabricated:

  * DARK when BUSINESS_OS_MARKETPLACE is off;
  * only THE buyer of an order that reached fulfilled/completed/refunded can
    rate — stranger 404 (existence not leaked), unpaid/just-paid order 409
    not_ratable, held account blocked;
  * one rating per order — duplicate 409; validation (range, comment cap);
  * honest empty state: no ratings -> average None (never fake 0);
  * aggregate math: count/average/distribution over real ratings, incl. a
    refunded-order (negative experience) rating;
  * public listing exposes rating/comment/date but never buyer identity.

    python tests/business_os/test_reputation_core.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_rep_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as mkt  # noqa: E402
from services.business_os.marketplace import orders as ordm  # noqa: E402
from services.business_os.marketplace import returns as retm  # noqa: E402
from services.business_os.marketplace import reputation as rep  # noqa: E402
from services.business_os.marketplace.service import MarketplaceError  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402


SELLER = 2570
ADMIN = "admin:25"

_uid = [2580]


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    mkt_schema.ensure_schema()
    retm.ensure_schema()
    rep.ensure_schema()
    ledger.ensure_schema()


def _buyer():
    _uid[0] += 1
    return _uid[0]


def _published_product(price=1000):
    mkt.upsert_seller(SELLER, display_name="S")
    mkt.set_seller_status(SELLER, "approved", actor=ADMIN)
    p = mkt.create_product(SELLER, title="Lamp", price_cents=price,
                           inventory_qty=30, context=_ctx())
    mkt.transition_product(SELLER, p["product_id"], "publish", context=_ctx())
    return p["product_id"]


def _expect(code, fn):
    try:
        fn()
    except MarketplaceError as exc:
        assert exc.code == code, f"expected {code}, got {exc.code}"
        return exc
    raise AssertionError(f"expected MarketplaceError {code}")


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_MARKETPLACE"] = ""
    try:
        _expect("disabled", lambda: rep.seller_reputation(SELLER))
    finally:
        os.environ["BUSINESS_OS_MARKETPLACE"] = "on"


def test_only_verified_transactions_rate():
    pid = _published_product()
    b = _buyer()
    o = ordm.create_order(b, pid, context=_ctx())
    oid = o["order_id"]

    # Created (unpaid) order: real row, but no verified transaction yet.
    _expect("not_ratable", lambda: rep.rate_order(oid, b, rating=5,
                                                  context=_ctx()))
    o = ordm.pay_order(oid, b, context=_ctx())
    # Paid but not yet fulfilled: still premature.
    _expect("not_ratable", lambda: rep.rate_order(oid, b, rating=5,
                                                  context=_ctx()))
    ordm.fulfill_order(oid, SELLER, context=_ctx())

    # Stranger (incl. the seller) gets 404, not a role hint.
    _expect("not_found", lambda: rep.rate_order(oid, _buyer(), rating=5,
                                                context=_ctx()))
    _expect("not_found", lambda: rep.rate_order(oid, SELLER, rating=5,
                                                context=_ctx()))
    # Held buyer blocked.
    _expect("account_hold",
            lambda: rep.rate_order(oid, b, rating=5,
                                   context=_ctx(status="suspended")))
    # Validation.
    _expect("invalid_rating", lambda: rep.rate_order(oid, b, rating=0,
                                                     context=_ctx()))
    _expect("invalid_rating", lambda: rep.rate_order(oid, b, rating=True,
                                                     context=_ctx()))
    _expect("comment_too_long",
            lambda: rep.rate_order(oid, b, rating=5, comment="x" * 3000,
                                   context=_ctx()))

    r = rep.rate_order(oid, b, rating=5, comment="great", context=_ctx())
    assert r["seller_user_id"] == str(SELLER)
    _expect("already_rated", lambda: rep.rate_order(oid, b, rating=1,
                                                    context=_ctx()))


def test_aggregate_and_honest_empty_state():
    # A different seller with zero ratings: None, not 0-stars.
    mkt.upsert_seller(9911, display_name="Z")
    fresh = rep.seller_reputation(9911)
    assert fresh["count"] == 0 and fresh["average"] is None

    pid = _published_product()
    # Second verified rating: completed order, 4 stars.
    b2 = _buyer()
    o2 = ordm.create_order(b2, pid, context=_ctx())
    ordm.pay_order(o2["order_id"], b2, context=_ctx())
    ordm.fulfill_order(o2["order_id"], SELLER, context=_ctx())
    ordm.complete_order(o2["order_id"], b2, context=_ctx())
    rep.rate_order(o2["order_id"], b2, rating=4, context=_ctx())

    # Third: refunded order — negative experience still counts.
    b3 = _buyer()
    o3 = ordm.create_order(b3, pid, context=_ctx())
    ordm.pay_order(o3["order_id"], b3, context=_ctx())
    r3 = retm.request_return(b3, o3["order_id"], reason="broken", context=_ctx())
    retm.approve_return(r3["return_id"], SELLER, context=_ctx())
    retm.mark_received(r3["return_id"], SELLER, context=_ctx())
    retm.refund_return(r3["return_id"], SELLER, context=_ctx())
    rep.rate_order(o3["order_id"], b3, rating=1, comment="arrived broken",
                   context=_ctx())

    agg = rep.seller_reputation(SELLER)
    assert agg["count"] == 3
    assert agg["average"] == round((5 + 4 + 1) / 3, 2)
    assert agg["distribution"][5] == 1 and agg["distribution"][4] == 1 \
        and agg["distribution"][1] == 1 and agg["distribution"][3] == 0

    rows = rep.list_ratings(SELLER)
    assert len(rows) == 3
    for row in rows:
        assert "buyer_user_id" not in row  # identity never exposed
        assert set(row) == {"rating_id", "rating", "comment", "created_at"}


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_only_verified_transactions_rate,
        test_aggregate_and_honest_empty_state,
    ]
    passed = 0
    for t in tests:
        t()
        print(f"PASS  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")
    return passed == len(tests)


if __name__ == "__main__":
    ok = _run_standalone()
    raise SystemExit(0 if ok else 1)
