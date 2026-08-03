"""A refund that is retried must move money once.

## The defect

`refund_order` minted its own identifier and handed it to the ledger as the
idempotency key:

    rid = "mktr_" + uuid.uuid4().hex
    txn = _ledger.post_entry(idempotency_key=f"mkt_refund:{rid}", ...)

A value generated inside the call is not an idempotency key. It is unique per
*invocation*, which is the opposite of what the ledger needs: the ledger
deduplicates faithfully, but it was asked a different question every time, so a
retried refund posted again. The function signature accepted no key either, so a
caller who understood the problem had no way to fix it from outside.

The blast radius was bounded but real. Escrow is not an allow-negative account,
so the ledger's overdraft guard stopped the drain once escrow hit zero — a
double-submitted refund button paid the buyer twice and the seller nothing, and
stopped there. "The guard caught it eventually" is not the same as "it did not
happen".

The correct pattern already existed one directory over, at
`advertising/funding.py:489`, which derives its ledger key from a caller-supplied
reservation key.

## The fix these tests pin

`refund_order` now takes `idempotency_key`. The `refund_id` — which is the
PRIMARY KEY of `business_os_mkt_refunds` — is derived from it by SHA-256, so the
ledger key derived from the refund id is stable across retries and the database
enforces single-insertion rather than trusting the caller. No new column, no
migration.

Executable two ways:

    python -m pytest tests/business_os/test_refund_idempotency.py
    python tests/business_os/test_refund_idempotency.py
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_refundidem_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as svc  # noqa: E402
from services.business_os.marketplace import orders as orders_mod  # noqa: E402
from services.business_os.marketplace import refunds as refunds_mod  # noqa: E402
from services.business_os.marketplace import admin as mkadmin  # noqa: E402
from services.business_os.marketplace import api as mkapi  # noqa: E402
from services.business_os.marketplace.service import MarketplaceError  # noqa: E402
from services.business_os.ledger import ledger  # noqa: E402

SELLER = 800
BUYER = 801
ADMIN = "admin:8"


def setup_module(module=None):
    mkt_schema.ensure_schema()
    ledger.ensure_schema()


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


_seq = [0]


def _paid_order(price_cents=2000, qty=2):
    """An order sitting in escrow, ready to be refunded."""
    _seq[0] += 1
    svc.upsert_seller(SELLER, display_name="S")
    svc.set_seller_status(SELLER, "approved", actor=ADMIN)
    p = svc.create_product(SELLER, title=f"Widget {_seq[0]}", price_cents=price_cents,
                           fulfillment_type="physical", inventory_qty=50,
                           context=_ctx())
    pid = p["product_id"]
    svc.transition_product(SELLER, pid, "publish", context=_ctx())
    oid = orders_mod.create_order(BUYER, pid, quantity=qty, context=_ctx())["order_id"]
    orders_mod.pay_order(oid, BUYER, context=_ctx())
    return oid


def _escrow(oid):
    return ledger.get_balance(orders_mod.escrow_account(oid), "usd")


def _refund_rows(oid):
    conn = db.connect()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM business_os_mkt_refunds WHERE order_id = ?",
            (str(oid),)).fetchone()[0]
    finally:
        conn.close()


# --------------------------------------------------------------------------

def test_retry_with_the_same_key_moves_money_once():
    """The headline regression. Fails against the pre-fix code.

    Before the fix the second call posted a second ledger entry and escrow fell
    by 1000 instead of 500.
    """
    oid = _paid_order()
    opening = _escrow(oid)
    assert opening == 4000, opening

    first = refunds_mod.refund_order(
        oid, amount_cents=500, reason="damaged", actor=ADMIN,
        idempotency_key="retry-me")
    after_first = _escrow(oid)

    second = refunds_mod.refund_order(
        oid, amount_cents=500, reason="damaged", actor=ADMIN,
        idempotency_key="retry-me")
    after_second = _escrow(oid)

    assert first["duplicate"] is False
    assert second["duplicate"] is True, "the retry was treated as a new refund"
    assert first["refund_id"] == second["refund_id"]
    assert first["ledger_txn_ref"] == second["ledger_txn_ref"]
    assert after_first == opening - 500, after_first
    assert after_second == after_first, (
        f"escrow moved on the retry: {after_first} -> {after_second}")
    assert _refund_rows(oid) == 1, "the retry wrote a second refund row"


def test_order_refunded_total_is_not_double_counted():
    """A replay must not inflate `refunded_cents` either.

    Money is only half the state. The order's running refunded total drives the
    transition to `refunded`, so double-counting it would flip an order to fully
    refunded while escrow still held funds.
    """
    oid = _paid_order()
    for _ in range(3):
        refunds_mod.refund_order(oid, amount_cents=1000, reason="partial",
                                 actor=ADMIN, idempotency_key="same-key")
    order = orders_mod.get_order(oid)
    assert int(order["refunded_cents"]) == 1000, order["refunded_cents"]
    assert order["status"] != "refunded", "one partial refund flipped the order to refunded"
    assert _escrow(oid) == 3000, _escrow(oid)


def test_distinct_keys_are_distinct_refunds():
    """Idempotency must not collapse two deliberate partial refunds."""
    oid = _paid_order()
    a = refunds_mod.refund_order(oid, amount_cents=500, reason="one", actor=ADMIN,
                                 idempotency_key="first")
    b = refunds_mod.refund_order(oid, amount_cents=500, reason="two", actor=ADMIN,
                                 idempotency_key="second")
    assert a["refund_id"] != b["refund_id"]
    assert a["duplicate"] is False and b["duplicate"] is False
    assert _escrow(oid) == 3000, _escrow(oid)
    assert _refund_rows(oid) == 2


def test_the_same_key_on_a_different_order_does_not_alias():
    """Keys are scoped per order, so "retry-1" is safe to reuse across orders."""
    oid1 = _paid_order()
    oid2 = _paid_order()
    r1 = refunds_mod.refund_order(oid1, amount_cents=500, reason="x", actor=ADMIN,
                                  idempotency_key="retry-1")
    r2 = refunds_mod.refund_order(oid2, amount_cents=500, reason="x", actor=ADMIN,
                                  idempotency_key="retry-1")
    assert r1["refund_id"] != r2["refund_id"]
    assert r2["duplicate"] is False, "a different order's refund was mistaken for a replay"
    assert _escrow(oid1) == 3500 and _escrow(oid2) == 3500


def test_unkeyed_calls_still_work():
    """Backwards compatibility: existing callers keep their behaviour."""
    oid = _paid_order()
    out = refunds_mod.refund_order(oid, amount_cents=500, reason="x", actor=ADMIN)
    assert out["duplicate"] is False
    assert out["refund_id"].startswith("mktr_")
    assert _escrow(oid) == 3500


def test_admin_layer_threads_the_key_and_does_not_double_audit():
    """The admin form is the caller that most needed this.

    A replay must not add a second "admin refunded this order" row to the audit
    log; one refund is one governed action however many times the button was hit.
    """
    oid = _paid_order()
    mkadmin.admin_refund_order(oid, actor=ADMIN, reason="goodwill",
                               amount_cents=500, idempotency_key="admin-retry")
    mkadmin.admin_refund_order(oid, actor=ADMIN, reason="goodwill",
                               amount_cents=500, idempotency_key="admin-retry")
    assert _escrow(oid) == 3500, _escrow(oid)
    assert _refund_rows(oid) == 1

    conn = db.connect()
    try:
        audits = conn.execute(
            "SELECT COUNT(*) FROM business_os_mkt_audit WHERE action = 'admin_refund' "
            "AND subject_ref = ?", (str(oid),)).fetchone()[0]
    finally:
        conn.close()
    assert int(audits) == 1, f"replay wrote {audits} admin_refund audit rows"


def test_api_layer_accepts_the_key():
    """The field has to survive the controller's allowlist to be reachable."""
    oid = _paid_order()
    st1, body1 = mkapi.admin_refund_order(
        ADMIN, oid, {"amount_cents": 500, "reason": "goodwill",
                     "idempotency_key": "http-retry"})
    st2, body2 = mkapi.admin_refund_order(
        ADMIN, oid, {"amount_cents": 500, "reason": "goodwill",
                     "idempotency_key": "http-retry"})
    assert st1 == 200 and st2 == 200, (st1, st2)
    # The controller wraps the admin envelope, which itself wraps the refund.
    inner1 = body1["refund"]["refund"]
    inner2 = body2["refund"]["refund"]
    assert inner1["refund_id"] == inner2["refund_id"], (
        "the allowlist dropped idempotency_key, so the retry became a new refund")
    assert inner1["duplicate"] is False and inner2["duplicate"] is True
    # A retry must get back the shape it got the first time.
    assert set(body1["refund"].keys()) == set(body2["refund"].keys()), (
        "the replay path returns a different envelope than the first call")
    assert _escrow(oid) == 3500, _escrow(oid)


def test_the_http_form_route_lets_the_key_through_its_allowlist():
    """The caller the fix was written for, checked where it actually lives.

    `test_admin_layer_threads_the_key_and_does_not_double_audit` exercises
    `admin.admin_refund_order`, and `test_api_layer_accepts_the_key` exercises
    `api.admin_refund_order`. Neither is the refund *form*. The form posts to the
    Flask route, which builds its payload with
    `_bo_mkt_form_payload({"amount_cents", "reason"})` — a strict allowlist that
    silently dropped `idempotency_key`, so `refund_order` fell through to
    `"mktr_" + uuid4().hex` and a double-submitted button issued two refunds.

    Every layer had the key and the one that faced the button did not, which is
    the failure a value-based test cannot see: each layer passes its own
    assertions while the chain is broken between them. So this reads the route.

    Parsed, not grepped. The string `idempotency_key` appears in this route's own
    comments explaining the defect, and a substring search would match the
    explanation as readily as it would a relapse.
    """
    import ast
    import re

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    source = open(os.path.join(root, "bot.py"), encoding="utf-8").read()
    # Slice the one function out; parsing 80k lines to reach it is wasteful and
    # the slice is what pins the assertion to *this* route rather than any route.
    start = source.index("def admin_business_os_marketplace_refund_order(")
    end = source.index("\n@webhook_app.route", start)
    fn = ast.parse(re.sub(r"^", "", source[start:end]))

    allowlists = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name) and target.id == "_bo_mkt_form_payload":
            allowlists.append(node.args[0])
    assert len(allowlists) == 1, (
        f"expected one _bo_mkt_form_payload call in the refund route, "
        f"found {len(allowlists)}")
    keys = {e.value for e in allowlists[0].elts if isinstance(e, ast.Constant)}
    assert "idempotency_key" in keys, (
        f"the refund route's allowlist is {sorted(keys)}: it strips "
        f"idempotency_key, so a double-submitted refund form issues two refunds")
    assert {"amount_cents", "reason"} <= keys, (
        f"the allowlist lost a field it used to carry: {sorted(keys)}")


def test_the_form_allowlist_and_the_api_allowlist_agree():
    """Two allowlists for one operation must not drift apart.

    They already had: `api.ADMIN_REFUND_FIELDS` carried `idempotency_key` and the
    route's inline set did not. Nothing connected them, so nothing noticed. This
    is that connection.
    """
    import ast
    import re

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    source = open(os.path.join(root, "bot.py"), encoding="utf-8").read()
    start = source.index("def admin_business_os_marketplace_refund_order(")
    end = source.index("\n@webhook_app.route", start)
    fn = ast.parse(re.sub(r"^", "", source[start:end]))
    route_keys = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_bo_mkt_form_payload"):
            route_keys = {e.value for e in node.args[0].elts
                          if isinstance(e, ast.Constant)}
    assert route_keys == set(mkapi.ADMIN_REFUND_FIELDS), (
        f"form route accepts {sorted(route_keys)} but the JSON API accepts "
        f"{sorted(mkapi.ADMIN_REFUND_FIELDS)} — the same operation, two contracts")


def test_a_dispute_refund_is_keyed_on_the_dispute():
    """`resolve_dispute` issued its refund with no key at all.

    The `status != 'open'` guard is a read followed by a write, so two admins
    resolving at once can both pass it. The guard cannot close that window
    because the guard is the thing racing; a key derived from the dispute can,
    because it makes the second refund a replay rather than a second refund.

    Simulated by reopening the dispute rather than by threads: the point under
    test is whether the *refund* deduplicates, and forcing the status back to
    'open' isolates that from the status check that would otherwise mask it.
    """
    oid = _paid_order()
    dispute = refunds_mod.open_dispute(oid, BUYER, reason="never arrived",
                                       context=_ctx())
    did = dispute["dispute_id"]

    refunds_mod.resolve_dispute(did, resolution="refund", actor=ADMIN,
                                reason="buyer is right", refund_amount_cents=500)
    after_first = _escrow(oid)
    assert after_first == 3500, after_first

    conn = db.connect()
    try:
        conn.execute("UPDATE business_os_mkt_disputes SET status = 'open' "
                     "WHERE dispute_id = ?", (did,))
        conn.commit()
    finally:
        conn.close()

    out = refunds_mod.resolve_dispute(did, resolution="refund", actor=ADMIN,
                                      reason="buyer is right",
                                      refund_amount_cents=500)
    assert out["refund"]["duplicate"] is True, (
        "the second resolution issued a second refund — the dispute refund "
        "carries no idempotency key")
    assert _escrow(oid) == 3500, (
        f"escrow fell to {_escrow(oid)}: the buyer was paid twice for one dispute")
    assert _refund_rows(oid) == 1


def test_two_disputes_on_different_orders_do_not_alias():
    """The dispute key is scoped by its own id, and refund ids by order too."""
    oid1 = _paid_order()
    oid2 = _paid_order()
    d1 = refunds_mod.open_dispute(oid1, BUYER, reason="a", context=_ctx())["dispute_id"]
    d2 = refunds_mod.open_dispute(oid2, BUYER, reason="b", context=_ctx())["dispute_id"]
    r1 = refunds_mod.resolve_dispute(d1, resolution="refund", actor=ADMIN,
                                     reason="x", refund_amount_cents=500)
    r2 = refunds_mod.resolve_dispute(d2, resolution="refund", actor=ADMIN,
                                     reason="x", refund_amount_cents=500)
    assert r1["refund"]["refund_id"] != r2["refund"]["refund_id"]
    assert r2["refund"]["duplicate"] is False, (
        "a second dispute's refund was mistaken for the first's replay")
    assert _escrow(oid1) == 3500 and _escrow(oid2) == 3500


def test_a_denied_dispute_moves_no_money():
    """The key must not have turned 'deny' into a refund path."""
    oid = _paid_order()
    did = refunds_mod.open_dispute(oid, BUYER, reason="c", context=_ctx())["dispute_id"]
    out = refunds_mod.resolve_dispute(did, resolution="deny", actor=ADMIN,
                                      reason="tracking shows delivered")
    assert "refund" not in out, out
    assert _escrow(oid) == 4000
    assert _refund_rows(oid) == 0


def test_derived_id_is_stable_and_scoped():
    """Pin the derivation directly; everything above depends on it."""
    a = refunds_mod._derive_refund_id("ord_1", "k")
    b = refunds_mod._derive_refund_id("ord_1", "k")
    c = refunds_mod._derive_refund_id("ord_2", "k")
    d = refunds_mod._derive_refund_id("ord_1", "k2")
    assert a == b, "derivation is not deterministic"
    assert a != c, "derivation is not scoped by order"
    assert a != d, "derivation ignores the key"
    assert a.startswith("mktr_") and len(a) == len("mktr_") + 32


def test_overdraft_guard_still_refuses_an_over_refund():
    """The ledger guard remains the backstop, keyed or not."""
    oid = _paid_order()
    try:
        refunds_mod.refund_order(oid, amount_cents=99_999, reason="oops",
                                 actor=ADMIN, idempotency_key="too-big")
        raise AssertionError("expected the over-refund to be refused")
    except MarketplaceError as exc:
        assert exc.code in ("refund_exceeds_escrow", "refund_rejected"), exc.code
    assert _escrow(oid) == 4000


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
