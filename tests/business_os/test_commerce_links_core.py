"""Business OS — Commerce thread links: the Commerce Inbox's server-side join.

Proves the join is party-gated and honest:

  * DARK when BUSINESS_OS_MESSAGES is off;
  * linking needs write access to the thread AND party status on the object:
    a stranger to the thread gets 404; a thread writer linking someone
    ELSE'S order gets 404 (existence not leaked); nonexistent object 404;
  * invalid related_type rejected; idempotent per (thread, type, ref) —
    re-link returns duplicate: True, one row in the table;
  * thread_context returns curated projections (order money/status; no buyer
    identity fields) to anyone who can read the thread; stranger 404;
  * a link whose subsystem is missing reports context: None (unavailable),
    proven against a type whose table was never created in this run.

    python tests/business_os/test_commerce_links_core.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_clinks_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_BUSINESS"] = "on"
os.environ["BUSINESS_OS_MESSAGES"] = "on"
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.business import schema as biz_schema  # noqa: E402
from services.business_os.business import service as biz_svc  # noqa: E402
from services.business_os.messages import schema as msg_schema  # noqa: E402
from services.business_os.messages import service as msg  # noqa: E402
from services.business_os.messages.service import MessageError  # noqa: E402
from services.business_os.messages import commerce_links as cl  # noqa: E402
from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as mkt  # noqa: E402
from services.business_os.marketplace import orders as ordm  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402


OWNER = 2870      # business owner (also the marketplace seller here)
CUSTOMER = 2872   # customer on the thread AND buyer on the order
STRANGER = 2873
OTHER_BUYER = 2874
ADMIN = "admin:28"


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    biz_schema.ensure_schema()
    msg_schema.ensure_schema()
    mkt_schema.ensure_schema()
    ledger.ensure_schema()
    cl.ensure_schema()
    # NOTE: returns/offers schemas deliberately NOT ensured — used to prove
    # the unavailable path.


def _expect(code, fn):
    try:
        fn()
    except MessageError as exc:
        assert exc.code == code, f"expected {code}, got {exc.code}"
        return exc
    raise AssertionError(f"expected MessageError {code}")


_state = {}


def _setup_world():
    if _state:
        return _state
    biz = biz_svc.create_business(OWNER, {"display_name": "Acme"}, context=_ctx())
    bid = biz["business_id"]
    cid = msg.start_business_thread(bid, CUSTOMER, CUSTOMER,
                                    context=_ctx())["conversation_id"]
    mkt.upsert_seller(OWNER, display_name="S")
    mkt.set_seller_status(OWNER, "approved", actor=ADMIN)
    p = mkt.create_product(OWNER, title="Lamp", price_cents=1000,
                           inventory_qty=10, context=_ctx())
    mkt.transition_product(OWNER, p["product_id"], "publish", context=_ctx())
    o = ordm.create_order(CUSTOMER, p["product_id"], context=_ctx())
    o = ordm.pay_order(o["order_id"], CUSTOMER, context=_ctx())
    o2 = ordm.create_order(OTHER_BUYER, p["product_id"], context=_ctx())
    _state.update(bid=bid, cid=cid, pid=p["product_id"],
                  oid=o["order_id"], other_oid=o2["order_id"])
    return _state


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_MESSAGES"] = ""
    try:
        _expect("disabled",
                lambda: cl.link_thread(1, CUSTOMER, related_type="order",
                                       related_ref="x", context=_ctx()))
        _expect("disabled", lambda: cl.thread_context(1, CUSTOMER))
    finally:
        os.environ["BUSINESS_OS_MESSAGES"] = "on"


def test_link_gates():
    s = _setup_world()
    # Stranger to the thread: 404.
    _expect("not_found",
            lambda: cl.link_thread(s["cid"], STRANGER, related_type="order",
                                   related_ref=s["oid"], context=_ctx()))
    # Invalid type / missing ref.
    _expect("invalid_related_type",
            lambda: cl.link_thread(s["cid"], CUSTOMER, related_type="invoice",
                                   related_ref="x", context=_ctx()))
    _expect("invalid",
            lambda: cl.link_thread(s["cid"], CUSTOMER, related_type="order",
                                   related_ref="", context=_ctx()))
    # Thread writer linking someone ELSE'S order: 404, existence not leaked.
    _expect("not_found",
            lambda: cl.link_thread(s["cid"], CUSTOMER, related_type="order",
                                   related_ref=s["other_oid"], context=_ctx()))
    # Nonexistent object: identical 404.
    _expect("not_found",
            lambda: cl.link_thread(s["cid"], CUSTOMER, related_type="order",
                                   related_ref="mko_none", context=_ctx()))
    # Held account blocked.
    _expect("account_hold",
            lambda: cl.link_thread(s["cid"], CUSTOMER, related_type="order",
                                   related_ref=s["oid"],
                                   context=_ctx(status="suspended")))


def test_link_and_context():
    s = _setup_world()
    lk = cl.link_thread(s["cid"], CUSTOMER, related_type="order",
                        related_ref=s["oid"], context=_ctx())
    assert lk["duplicate"] is False
    again = cl.link_thread(s["cid"], CUSTOMER, related_type="order",
                           related_ref=s["oid"], context=_ctx())
    assert again["duplicate"] is True and again["id"] == lk["id"]
    conn = db.connect()
    n = conn.execute("SELECT COUNT(*) FROM business_os_commerce_thread_links "
                     "WHERE conversation_id = ?", (s["cid"],)).fetchone()[0]
    conn.close()
    assert n == 1

    # Seller is a party too: can link their product.
    cl.link_thread(s["cid"], OWNER, related_type="product",
                   related_ref=s["pid"], context=_ctx())

    ctx = cl.thread_context(s["cid"], CUSTOMER)
    assert len(ctx["links"]) == 2
    order_link = next(l for l in ctx["links"] if l["related_type"] == "order")
    assert order_link["context"]["status"] == "paid"
    assert order_link["context"]["total_cents"] == 1000
    assert "buyer_user_id" not in order_link["context"]  # curated fields only
    prod_link = next(l for l in ctx["links"] if l["related_type"] == "product")
    assert prod_link["context"]["title"] == "Lamp"

    # Business owner can read; stranger cannot.
    assert len(cl.thread_context(s["cid"], OWNER)["links"]) == 2
    _expect("not_found", lambda: cl.thread_context(s["cid"], STRANGER))


def test_unavailable_subsystem_is_none():
    s = _setup_world()
    # The returns table was never created in this run: linking is an honest 409.
    _expect("unavailable",
            lambda: cl.link_thread(s["cid"], CUSTOMER, related_type="return",
                                   related_ref="mktret_x", context=_ctx()))
    # A pre-existing link whose subsystem vanished reports context: None.
    conn = db.connect()
    conn.execute(
        "INSERT INTO business_os_commerce_thread_links "
        "(conversation_id, related_type, related_ref, created_by, created_at) "
        "VALUES (?, 'offer', 'mkoff_ghost', ?, '2026-01-01T00:00:00.000000Z')",
        (s["cid"], str(CUSTOMER)))
    conn.commit()
    conn.close()
    ctx = cl.thread_context(s["cid"], CUSTOMER)
    ghost = next(l for l in ctx["links"] if l["related_ref"] == "mkoff_ghost")
    assert ghost["context"] is None  # unavailable, not fabricated


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_link_gates,
        test_link_and_context,
        test_unavailable_subsystem_is_none,
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
