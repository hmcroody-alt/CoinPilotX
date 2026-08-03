"""A capture that spans three connections is not one operation.

## The defect

`marketplace/orders.py::pay_order` wrote to the orders database through three
different connections in a single call:

* the inventory decrement and the `created ─▶ paid` flip on `conn`, committed
  **only when it owned that connection**;
* the compensation for a failed capture on a freshly opened `c2`;
* the `capture_txn_ref` write on a freshly opened `c3`.

A caller passing its own connection therefore ended up with an *uncommitted*
inventory decrement sitting beside a *committed* ledger post. And the
compensation, running on `c2`, could not see that uncommitted decrement — so its
`inventory_qty + quantity` was applied to the committed value, which had never
been decremented. **A failed capture created stock.**

Nothing reconciled any of it afterwards: no repair job, no drift detector, no
invariant check between `business_os_mkt_orders` and the ledger. That absence is
why this was recorded as deliberately-open rather than fixed in the first pass.

## What the fix does, and what it deliberately does not do

`post_entry` opens, commits and closes its own connection by design, and on
SQLite takes a database-wide write lock while doing it. So the capture *cannot*
join the caller's transaction, and holding a write transaction open across it
deadlocks. Those two facts together mean the borrowed-connection path was
unsound by construction rather than merely buggy.

So `pay_order` now refuses a supplied `conn` outright — no caller passed one, so
this removes an invitation to a bug rather than a feature — and does all of its
own writing, including compensation and `capture_txn_ref`, on the one connection
it opened itself.

The residual window is real and is stated rather than papered over: the capture
can succeed and the `capture_txn_ref` write can then fail. `reconcile_captures`
is the detector for it, and it reports rather than repairs, because two orders
can look identical here and need opposite treatment.

Executable two ways:

    python -m pytest tests/business_os/test_capture_atomicity.py
    python tests/business_os/test_capture_atomicity.py
"""

import ast
import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_capture_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.ledger import ledger as _ledger  # noqa: E402
from services.business_os.marketplace import orders as orders_mod  # noqa: E402
from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as mkt_svc  # noqa: E402
from services.business_os.marketplace.service import MarketplaceError  # noqa: E402

SELLER = 8100
BUYER = 8101
ADMIN = 8102
_seq = [0]


def setup_module(module=None):
    mkt_schema.ensure_schema()
    _ledger.ensure_schema()


def _ctx():
    return {"account_status": "active", "access_enabled": 1}


def _seller():
    mkt_svc.upsert_seller(SELLER, display_name="S")
    mkt_svc.set_seller_status(SELLER, "approved", actor=ADMIN)


def _product(inventory=10, price=1000):
    """A published *physical* product with finite stock.

    Physical and finite on purpose: the defect this suite pins is an inventory
    number moving the wrong way, and a digital product (NULL inventory) has no
    number to move.
    """
    _seq[0] += 1
    _seller()
    product = mkt_svc.create_product(
        SELLER, title=f"Widget {_seq[0]}", price_cents=price,
        fulfillment_type="physical", inventory_qty=inventory, context=_ctx())
    mkt_svc.transition_product(SELLER, product["product_id"], "publish", context=_ctx())
    return product["product_id"]


def _inventory(product_id):
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT inventory_qty FROM business_os_mkt_products WHERE product_id = ?",
            (str(product_id),)).fetchone()
        return None if row is None else (row["inventory_qty"] if hasattr(row, "keys") else row[0])
    finally:
        conn.close()


def _order_status(order_id):
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT status FROM business_os_mkt_orders WHERE order_id = ?",
            (str(order_id),)).fetchone()
        return None if row is None else (row["status"] if hasattr(row, "keys") else row[0])
    finally:
        conn.close()


class _CaptureFails:
    """Make exactly the capture post fail, leaving every other ledger call alone."""

    def __init__(self, exc=None):
        self._exc = exc or RuntimeError("provider unavailable")
        self._real = None

    def __enter__(self):
        self._real = orders_mod._ledger.post_entry
        exc = self._exc

        def fake(*a, **kw):
            if str(kw.get("idempotency_key", "")).startswith("mkt_capture:"):
                raise exc
            return self._real(*a, **kw)

        orders_mod._ledger.post_entry = fake
        return self

    def __exit__(self, *a):
        orders_mod._ledger.post_entry = self._real
        return False


def _expect(code, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except MarketplaceError as exc:
        assert exc.code == code, f"expected {code}, got {exc.code}: {exc}"
        return exc
    raise AssertionError(f"expected MarketplaceError {code}, nothing was raised")


# --------------------------------------------------------------------------
# the borrowed connection
# --------------------------------------------------------------------------

def test_a_caller_supplied_connection_is_refused():
    """The path that could not be made correct is now closed rather than pretended.

    A ledger post that commits on its own connection cannot be rolled back with
    the caller's transaction. Accepting `conn` advertised an atomicity the code
    was structurally unable to provide.
    """
    pid = _product()
    oid = orders_mod.create_order(BUYER, pid, quantity=1, context=_ctx())["order_id"]
    conn = db.connect()
    try:
        exc = _expect("capture_needs_own_transaction",
                      orders_mod.pay_order, oid, BUYER, context=_ctx(), conn=conn)
        assert exc.http_status == 500
    finally:
        conn.close()
    # And it refused before touching anything.
    assert _order_status(oid) == "created"


def test_the_refusal_happens_before_any_write():
    """A guard that fires after the first UPDATE is not a guard.

    Inventory is the cheapest thing to check: if the decrement had run, this
    would be 9.
    """
    pid = _product(inventory=10)
    oid = orders_mod.create_order(BUYER, pid, quantity=1, context=_ctx())["order_id"]
    conn = db.connect()
    try:
        _expect("capture_needs_own_transaction",
                orders_mod.pay_order, oid, BUYER, context=_ctx(), conn=conn)
    finally:
        conn.close()
    assert _inventory(pid) == 10, "the refusal came after the inventory decrement"


def test_no_caller_in_the_codebase_passes_a_connection():
    """The refusal is only safe because nothing relied on the behaviour.

    Asserted rather than assumed, and asserted against the source: if someone
    later adds `pay_order(..., conn=c)` this test tells them why it will not
    work, instead of leaving them to discover it in production.
    """
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    offenders = []
    for folder in ("services", "bot.py"):
        target = os.path.join(root, folder)
        paths = [target] if target.endswith(".py") else [
            os.path.join(dp, f) for dp, _, fs in os.walk(target)
            for f in fs if f.endswith(".py")]
        for path in paths:
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
            if "pay_order" not in source:
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name != "pay_order":
                    continue
                if any(kw.arg == "conn" for kw in node.keywords):
                    offenders.append(os.path.relpath(path, root))
    assert not offenders, f"pay_order is called with conn= in: {offenders}"


# --------------------------------------------------------------------------
# compensation on one connection
# --------------------------------------------------------------------------

def test_a_failed_capture_does_not_create_inventory():
    """The money-shaped half of the defect, in the units it was measured in.

    Decrement 3, capture fails, restore 3. The number that must come back is the
    number we started with — not more.
    """
    pid = _product(inventory=10)
    oid = orders_mod.create_order(BUYER, pid, quantity=3, context=_ctx())["order_id"]
    with _CaptureFails():
        _expect("capture_failed", orders_mod.pay_order, oid, BUYER, context=_ctx())
    assert _inventory(pid) == 10, "compensation did not restore exactly what it took"
    assert _order_status(oid) == "created", "the state flip was not reverted"


def test_a_failed_capture_leaves_no_money_in_escrow():
    pid = _product(inventory=5, price=2500)
    oid = orders_mod.create_order(BUYER, pid, quantity=1, context=_ctx())["order_id"]
    with _CaptureFails():
        _expect("capture_failed", orders_mod.pay_order, oid, BUYER, context=_ctx())
    assert _ledger.get_balance(orders_mod.escrow_account(oid), "usd") == 0


def test_the_reversal_is_recorded_as_an_event():
    """A silent reversal is indistinguishable from an order that never advanced."""
    pid = _product(inventory=5)
    oid = orders_mod.create_order(BUYER, pid, quantity=1, context=_ctx())["order_id"]
    with _CaptureFails():
        _expect("capture_failed", orders_mod.pay_order, oid, BUYER, context=_ctx())
    events = orders_mod.get_order_events(oid)
    reasons = [str(e.get("reason") or "") for e in events]
    assert "capture_failed" in reasons, f"no reversal event recorded: {events}"


def test_a_retry_after_a_failed_capture_succeeds_cleanly():
    """The reversal has to leave the order genuinely payable again.

    A compensation that restores the numbers but leaves the order unpayable has
    converted a transient provider error into a dead order.
    """
    pid = _product(inventory=4, price=1500)
    oid = orders_mod.create_order(BUYER, pid, quantity=2, context=_ctx())["order_id"]
    with _CaptureFails():
        _expect("capture_failed", orders_mod.pay_order, oid, BUYER, context=_ctx())
    out = orders_mod.pay_order(oid, BUYER, context=_ctx())
    assert out["status"] == "paid"
    assert _inventory(pid) == 2, "the retry decremented from the restored value"
    assert _ledger.get_balance(orders_mod.escrow_account(oid), "usd") == 3000


def test_pay_order_opens_no_connection_of_its_own_beyond_the_first():
    """Source-level, because the behavioural tests above cannot see connection count.

    They pass whether the compensation runs on `conn` or on a second connection,
    since on the owned path the first one is committed by then and both see the
    same rows. The defect only bites on the borrowed path — which is now refused
    — so what keeps it fixed is that there is no second connection to reintroduce
    it with.
    """
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    with open(os.path.join(root, "services/business_os/marketplace/orders.py"),
              encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "pay_order")
    connects = [n for n in ast.walk(fn)
                if isinstance(n, ast.Call)
                and getattr(n.func, "attr", None) == "connect"]
    assert len(connects) == 1, \
        f"pay_order opens {len(connects)} connections; the fix leaves exactly one"


# --------------------------------------------------------------------------
# the drift detector that did not exist
# --------------------------------------------------------------------------

def test_a_clean_paid_order_reports_no_drift():
    pid = _product(inventory=6, price=800)
    oid = orders_mod.create_order(BUYER, pid, quantity=1, context=_ctx())["order_id"]
    orders_mod.pay_order(oid, BUYER, context=_ctx())
    report = orders_mod.reconcile_captures()
    assert not [f for f in report["findings"] if f["order_id"] == str(oid)], \
        f"a healthy order was reported as drift: {report['findings']}"


def test_money_captured_against_an_unpaid_order_is_detected():
    """The irreducible window, simulated by leaving the order behind the money.

    This is what a crash between the capture and the commit looks like from the
    outside, and before `reconcile_captures` existed there was nothing that would
    ever have noticed it.
    """
    pid = _product(inventory=6, price=1200)
    oid = orders_mod.create_order(BUYER, pid, quantity=1, context=_ctx())["order_id"]
    orders_mod.pay_order(oid, BUYER, context=_ctx())
    conn = db.connect()
    try:
        conn.execute("UPDATE business_os_mkt_orders SET status = 'created' "
                     "WHERE order_id = ?", (str(oid),))
        conn.commit()
    finally:
        conn.close()
    found = [f for f in orders_mod.reconcile_captures()["findings"]
             if f["order_id"] == str(oid)]
    assert found, "captured-but-not-paid was not detected"
    assert found[0]["kind"] == "captured_not_paid"
    assert found[0]["captured_cents"] == 1200
    assert found[0]["capture_transaction_id"], "the finding must name the transaction"


def test_a_paid_order_with_no_capture_is_detected():
    pid = _product(inventory=6, price=900)
    oid = orders_mod.create_order(BUYER, pid, quantity=1, context=_ctx())["order_id"]
    conn = db.connect()
    try:
        conn.execute("UPDATE business_os_mkt_orders SET status = 'paid' "
                     "WHERE order_id = ?", (str(oid),))
        conn.commit()
    finally:
        conn.close()
    found = [f for f in orders_mod.reconcile_captures()["findings"]
             if f["order_id"] == str(oid)]
    assert found and found[0]["kind"] == "paid_not_captured", f"got {found}"


def test_a_capture_the_order_cannot_name_is_detected():
    """Harmless to balances, awkward for anyone investigating one.

    This is the failure the residual window actually produces now that the
    status flip and the capture are on the same side of the commit: the money
    and the status agree, and only the reference is missing.
    """
    pid = _product(inventory=6, price=700)
    oid = orders_mod.create_order(BUYER, pid, quantity=1, context=_ctx())["order_id"]
    orders_mod.pay_order(oid, BUYER, context=_ctx())
    conn = db.connect()
    try:
        conn.execute("UPDATE business_os_mkt_orders SET capture_txn_ref = NULL "
                     "WHERE order_id = ?", (str(oid),))
        conn.commit()
    finally:
        conn.close()
    found = [f for f in orders_mod.reconcile_captures()["findings"]
             if f["order_id"] == str(oid)]
    assert found and found[0]["kind"] == "missing_capture_ref", f"got {found}"


def test_a_settled_order_is_not_reported_as_drift():
    """Escrow is *supposed* to be empty after completion.

    Comparing a completed order's escrow against its capture would flag every
    healthy order in the system, which is the fastest way to make a drift report
    worth ignoring.
    """
    pid = _product(inventory=6, price=1100)
    oid = orders_mod.create_order(BUYER, pid, quantity=1, context=_ctx())["order_id"]
    orders_mod.pay_order(oid, BUYER, context=_ctx())
    orders_mod.fulfill_order(oid, SELLER, context=_ctx())
    orders_mod.complete_order(oid, BUYER, context=_ctx())
    assert _ledger.get_balance(orders_mod.escrow_account(oid), "usd") == 0
    found = [f for f in orders_mod.reconcile_captures()["findings"]
             if f["order_id"] == str(oid)]
    assert not found, f"a settled order was reported as drift: {found}"


def test_the_reconciler_reports_and_does_not_repair():
    """Deliberate, and worth pinning so nobody helpfully adds a repair later.

    Two orders can present identically here — one is a crash mid-commit, the
    other is someone who moved money by hand — and a job that guesses will
    eventually guess wrong with real money.
    """
    pid = _product(inventory=6, price=1300)
    oid = orders_mod.create_order(BUYER, pid, quantity=1, context=_ctx())["order_id"]
    orders_mod.pay_order(oid, BUYER, context=_ctx())
    conn = db.connect()
    try:
        conn.execute("UPDATE business_os_mkt_orders SET status = 'created' "
                     "WHERE order_id = ?", (str(oid),))
        conn.commit()
    finally:
        conn.close()
    before = _ledger.get_balance(orders_mod.escrow_account(oid), "usd")
    orders_mod.reconcile_captures()
    assert _order_status(oid) == "created", "the reconciler changed order state"
    assert _ledger.get_balance(orders_mod.escrow_account(oid), "usd") == before, \
        "the reconciler moved money"


def test_the_report_counts_what_it_scanned():
    report = orders_mod.reconcile_captures(limit=3)
    assert report["scanned"] <= 3
    assert report["drift_count"] == len(report["findings"])


# --------------------------------------------------------------------------

def _main():
    setup_module()
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {fn.__name__}\n      {type(exc).__name__}: {exc}")
        else:
            print(f"PASS  {fn.__name__}")
    print(f"\n{len(tests) - failures}/{len(tests)} tests passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_main())
