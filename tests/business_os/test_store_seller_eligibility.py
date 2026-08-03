"""A business cannot sell in the Store without being approved to sell.

## The defect

Store had exactly one access check — `_require_biz_permission`, which resolves
the caller's S1 role on the business. That answers "may this person act for this
business", which is a completely different question from "may this business take
money from the public". Publishing a storefront and activating products are the
two acts that put goods in front of shoppers, and both were reachable by anyone
holding an admin role on a business nobody had ever reviewed.

The marketplace, which is the *other* surface over the same catalogue idea,
gets this right: `marketplace/service.py::require_active_seller` refuses every
seller write unless `business_os_mkt_sellers.status == 'approved'`. So the
approval record existed, was already described in its own schema comment as the
authority on who may sell, and Store simply never read it. One selling surface
gated, one open, one approval table sitting between them.

## The fix these tests pin

Store reads that same record before it makes anything live, keyed on the
business **owner** rather than the caller. Drafting stays open — the review is
about selling, not about typing — and taking a store *down* is never gated,
because a gate that traps a live storefront online is worse than no gate.

`public_storefront` re-checks on read, so a revoked approval goes dark
immediately rather than at the mercy of a sweep job.

Executable two ways:

    python -m pytest tests/business_os/test_store_seller_eligibility.py
    python tests/business_os/test_store_seller_eligibility.py
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_store_elig_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_BUSINESS"] = "on"
os.environ["BUSINESS_OS_STORE"] = "on"
# Deliberately left OFF. Store's gate reads the seller record directly, so it
# must work whether or not the marketplace feature is switched on; if this suite
# only passed with the marketplace enabled, the coupling would be the bug.
os.environ["BUSINESS_OS_MARKETPLACE"] = ""

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.business import schema as biz_schema  # noqa: E402
from services.business_os.business import service as biz_svc  # noqa: E402
from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.store import schema as store_schema  # noqa: E402
from services.business_os.store import service as svc  # noqa: E402
from services.business_os.store.service import StoreError  # noqa: E402

OWNER = 700
ADMIN = 701
_seq = [0]


def setup_module(module=None):
    biz_schema.ensure_schema()
    store_schema.ensure_schema()
    mkt_schema.ensure_schema()


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def _set_seller(user_id, status):
    """Write the owner's approval row, or delete it when status is None."""
    conn = db.connect()
    try:
        now = "2026-01-01T00:00:00.000000Z"
        if status is None:
            conn.execute("DELETE FROM business_os_mkt_sellers WHERE seller_user_id = ?",
                         (str(user_id),))
        else:
            conn.execute(
                "INSERT INTO business_os_mkt_sellers "
                "(seller_user_id, status, created_at, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(seller_user_id) DO UPDATE SET status = excluded.status",
                (str(user_id), status, now, now))
        conn.commit()
    finally:
        conn.close()


def _shop(seller_status="approved", owner=None):
    """A business with a draft storefront and one draft product."""
    _seq[0] += 1
    owner = OWNER + _seq[0] if owner is None else owner
    bid = biz_svc.create_business(owner, {"display_name": f"Shop {_seq[0]}"},
                                  context=_ctx())["business_id"]
    _set_seller(owner, seller_status)
    svc.upsert_storefront(bid, owner, {"name": f"Shop {_seq[0]}",
                                       "slug": f"shop-{_seq[0]}"}, context=_ctx())
    pid = svc.create_product(bid, owner, {"title": "Thing", "price_cents": 500},
                             context=_ctx())["product_id"]
    return bid, owner, pid


def _expect(code, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except StoreError as exc:
        assert exc.code == code, f"expected {code}, got {exc.code}: {exc}"
        return exc
    raise AssertionError(f"expected StoreError {code}, nothing was raised")


# --------------------------------------------------------------------------

def test_an_unapproved_business_cannot_publish_its_storefront():
    """The headline regression. Passes trivially against the pre-fix code."""
    bid, owner, _ = _shop(seller_status=None)
    exc = _expect("seller_not_approved", svc.set_storefront_status,
                  bid, owner, "publish", context=_ctx())
    assert exc.http_status == 403
    assert svc.get_storefront(bid, owner)["status"] == "draft"


def test_an_unapproved_business_cannot_activate_a_product():
    """Publishing is not the only way to put goods in front of people."""
    bid, owner, pid = _shop(seller_status=None)
    _expect("seller_not_approved", svc.set_product_status,
            bid, owner, pid, "activate", context=_ctx())
    assert svc.get_product(bid, owner, pid)["status"] == "draft"


def test_a_pending_application_is_not_an_approval():
    """"Under review" is the state most likely to be mistaken for permission."""
    bid, owner, _ = _shop(seller_status="pending")
    exc = _expect("seller_not_approved", svc.set_storefront_status,
                  bid, owner, "publish", context=_ctx())
    assert "pending" in str(exc), str(exc)


def test_rejected_and_suspended_sellers_cannot_publish():
    for status in ("rejected", "suspended"):
        bid, owner, _ = _shop(seller_status=status)
        _expect("seller_not_approved", svc.set_storefront_status,
                bid, owner, "publish", context=_ctx())


def test_drafting_is_still_open_while_the_application_is_in_review():
    """The review is about selling, not about typing.

    A business locked out of its own catalogue editor until approval would have
    nothing to launch on the day it is approved.
    """
    bid, owner, pid = _shop(seller_status="pending")
    svc.upsert_storefront(bid, owner, {"name": "Renamed", "headline": "Soon"},
                          context=_ctx())
    svc.update_product(bid, owner, pid, {"title": "Better Thing",
                                         "price_cents": 900}, context=_ctx())
    svc.create_product(bid, owner, {"title": "Second", "price_cents": 100},
                       context=_ctx())
    svc.create_collection(bid, owner, {"title": "Coming Soon"}, context=_ctx())
    assert svc.get_product(bid, owner, pid)["title"] == "Better Thing"
    assert len(svc.list_products(bid, owner)) == 2


def test_an_approved_business_publishes_normally():
    bid, owner, pid = _shop()
    assert svc.set_storefront_status(bid, owner, "publish",
                                     context=_ctx())["status"] == "published"
    assert svc.set_product_status(bid, owner, pid, "activate",
                                  context=_ctx())["status"] == "active"
    pub = svc.public_storefront(bid)
    assert pub is not None and len(pub["products"]) == 1


def test_taking_a_store_down_is_never_gated():
    """A gate that traps a live storefront online is worse than no gate.

    Approval is revoked *after* the store went live — exactly the situation in
    which someone urgently needs to pull it down — and suspend and archive both
    have to keep working.
    """
    bid, owner, _ = _shop()
    svc.set_storefront_status(bid, owner, "publish", context=_ctx())
    _set_seller(owner, "suspended")
    assert svc.set_storefront_status(bid, owner, "suspend",
                                     context=_ctx())["status"] == "suspended"
    assert svc.set_storefront_status(bid, owner, "archive",
                                     context=_ctx())["status"] == "archived"


def test_restoring_a_suspended_store_is_gated_like_publishing():
    """`restore` also targets 'published', so it is also a way to go live."""
    bid, owner, _ = _shop()
    svc.set_storefront_status(bid, owner, "publish", context=_ctx())
    svc.set_storefront_status(bid, owner, "suspend", context=_ctx())
    _set_seller(owner, "suspended")
    _expect("seller_not_approved", svc.set_storefront_status,
            bid, owner, "restore", context=_ctx())


def test_revoking_approval_takes_the_public_storefront_dark_immediately():
    """Checked on read, so revocation does not wait for a sweep job.

    The stored status stays 'published' on purpose: restoring approval must
    restore the store without anyone having to remember to re-publish it.
    """
    bid, owner, pid = _shop()
    svc.set_storefront_status(bid, owner, "publish", context=_ctx())
    svc.set_product_status(bid, owner, pid, "activate", context=_ctx())
    assert svc.public_storefront(bid) is not None

    _set_seller(owner, "suspended")
    assert svc.public_storefront(bid) is None, "a suspended seller's store is still live"
    assert svc.get_storefront(bid, owner)["status"] == "published", (
        "the stored status was mutated; re-approval would not restore the store")

    _set_seller(owner, "approved")
    assert svc.public_storefront(bid) is not None, "re-approval did not restore the store"


def test_the_gate_keys_on_the_owner_not_the_caller():
    """An approved individual must not be able to front for an unvetted business.

    This is the substitution the approval exists to prevent, and the reason the
    lookup resolves `owner_user_id` rather than trusting `actor_user_id`.
    """
    bid, owner, _ = _shop(seller_status=None)
    biz_svc.add_member(bid, owner, ADMIN, "admin", context=_ctx())
    _set_seller(ADMIN, "approved")          # the caller personally is approved
    _expect("seller_not_approved", svc.set_storefront_status,
            bid, ADMIN, "publish", context=_ctx())


def test_a_missing_seller_table_is_reported_as_unavailable_not_as_rejection():
    """"We cannot check" and "we checked and no" are different facts.

    Both refuse — the gate fails closed either way, which is the whole point —
    but an operator staring at a 403 would go looking for an application that
    was never the problem.
    """
    bid, owner, _ = _shop()
    conn = db.connect()
    try:
        conn.execute("DROP TABLE business_os_mkt_sellers")
        conn.commit()
    finally:
        conn.close()
    try:
        exc = _expect("seller_review_unavailable", svc.set_storefront_status,
                      bid, owner, "publish", context=_ctx())
        assert exc.http_status == 503
        # The public path must not leak provisioning state to a stranger.
        assert svc.public_storefront(bid) is None
    finally:
        mkt_schema.ensure_schema()
        _set_seller(owner, "approved")


class _FailingSellerRead:
    """A connection that answers everything except the seller lookup.

    Narrow on purpose. Breaking the whole connection would prove nothing —
    `public_storefront` reads the storefront row first and would fail there
    instead, never reaching the gate. The scenario worth reproducing is the one
    that actually happens: a healthy connection where this one statement times
    out, deadlocks, or is cancelled.
    """

    def __init__(self, inner, exc):
        self._inner = inner
        self._exc = exc

    def execute(self, sql, params=None):
        if "business_os_mkt_sellers" in str(sql):
            raise self._exc
        return self._inner.execute(sql, params or ())

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _with_failing_seller_read(exc, fn):
    real_connect = db.connect

    def fake_connect(*a, **kw):
        return _FailingSellerRead(real_connect(*a, **kw), exc)

    db.connect = fake_connect
    try:
        return fn()
    finally:
        db.connect = real_connect


def test_a_transient_read_failure_is_not_reported_as_a_missing_table():
    """The third case the sentinel was hiding.

    A lock timeout is not a provisioning fact. Reporting it as
    `seller_review_unavailable` sends an operator to check whether the
    marketplace was ever installed, when the answer is that the database was
    busy for two seconds.
    """
    bid, owner, _ = _shop()
    exc = _expect(
        "seller_review_failed",
        lambda: _with_failing_seller_read(
            RuntimeError("canceling statement due to lock timeout"),
            lambda: svc.set_storefront_status(bid, owner, "publish", context=_ctx())))
    assert exc.http_status == 503, "a transient failure is retryable, so 503"


def test_a_transient_read_failure_does_not_take_a_live_shop_dark():
    """The expensive half, and the reason this was worth fixing.

    Against the old code the bare `except Exception` turned any read failure
    into `"__unavailable__"`, `public_storefront` compared that to `"approved"`,
    and an approved, published shop returned None — a 404 to every shopper and
    every crawler, for as long as the blip lasted, with nothing anywhere saying
    why. A 503 is retryable, it is visible in the error rate, and it does not
    tell a search engine the page is gone.
    """
    bid, owner, pid = _shop()
    svc.set_storefront_status(bid, owner, "publish", context=_ctx())
    svc.set_product_status(bid, owner, pid, "activate", context=_ctx())
    assert svc.public_storefront(bid) is not None, "the shop should be live to begin with"
    exc = _expect(
        "seller_review_failed",
        lambda: _with_failing_seller_read(
            RuntimeError("server closed the connection unexpectedly"),
            lambda: svc.public_storefront(bid)))
    assert exc.http_status == 503
    # And the outage is not sticky: the next read is normal.
    assert svc.public_storefront(bid) is not None


def test_a_missing_table_is_still_recognised_by_message():
    """SQLite says "no such table"; Postgres says "does not exist"."""
    bid, owner, _ = _shop()
    for message in ("no such table: business_os_mkt_sellers",
                    'relation "business_os_mkt_sellers" does not exist'):
        exc = _expect(
            "seller_review_unavailable",
            lambda m=message: _with_failing_seller_read(
                RuntimeError(m),
                lambda: svc.set_storefront_status(bid, owner, "publish", context=_ctx())))
        assert exc.http_status == 503


def test_sqlstate_42P01_is_recognised_without_reading_the_message():
    """A code beats a substring, and localised servers do not translate codes.

    PostgreSQL emits messages in the server's configured language. A deployment
    running with a non-English locale would fail every message match, so the
    code path has to be the one that decides when a code is present.
    """
    class _Undefined(RuntimeError):
        pgcode = "42P01"

    bid, owner, _ = _shop()
    exc = _expect(
        "seller_review_unavailable",
        lambda: _with_failing_seller_read(
            _Undefined("relação não existe"),
            lambda: svc.set_storefront_status(bid, owner, "publish", context=_ctx())))
    assert exc.http_status == 503


def test_a_sqlstate_that_is_not_undefined_table_is_treated_as_transient():
    """Having a code is not the same as having *that* code.

    40P01 is deadlock_detected. Matching on "a pgcode exists" rather than on its
    value would have swept every coded error into the provisioning bucket — the
    original defect with an extra step.
    """
    class _Deadlock(RuntimeError):
        pgcode = "40P01"

    bid, owner, _ = _shop()
    exc = _expect(
        "seller_review_failed",
        lambda: _with_failing_seller_read(
            _Deadlock("deadlock detected"),
            lambda: svc.set_storefront_status(bid, owner, "publish", context=_ctx())))
    assert exc.http_status == 503


def test_the_gate_still_refuses_when_it_genuinely_cannot_check():
    """The narrowing must not have removed the fail-closed behaviour.

    Every branch here refuses. What changed is which refusal, and whether the
    refusal claims to be an answer.
    """
    import inspect
    src = inspect.getsource(svc._seller_status)
    assert "except Exception" in src, "the read is still guarded"
    assert "raise StoreError" in src, "a non-provisioning failure is raised, not swallowed"
    assert src.index("_is_missing_table_error") < src.index("raise StoreError"), \
        "the provisioning case must be recognised before the generic raise"


def test_the_public_path_refuses_quietly_rather_than_raising():
    """An unauthenticated shopper gets the same nothing an unpublished store gives."""
    bid, owner, pid = _shop()
    svc.set_storefront_status(bid, owner, "publish", context=_ctx())
    svc.set_product_status(bid, owner, pid, "activate", context=_ctx())
    _set_seller(owner, None)
    assert svc.public_storefront(bid) is None


def test_the_gate_does_not_depend_on_the_marketplace_flag():
    """Two selling surfaces over one approval record, independently switchable.

    The module-level env in this file leaves `BUSINESS_OS_MARKETPLACE` off, so
    every test above already proves this; asserting it explicitly stops a future
    reader from "simplifying" the gate into `require_active_seller`, which
    asserts that flag and would take Store dark with it.
    """
    from services.business_os.marketplace import service as mkt_svc
    assert not mkt_svc.is_enabled(), "this suite is not testing what it claims"
    bid, owner, _ = _shop()
    assert svc.set_storefront_status(bid, owner, "publish",
                                     context=_ctx())["status"] == "published"


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
