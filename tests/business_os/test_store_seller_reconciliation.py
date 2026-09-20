"""Two seller tables, one verdict — and the stale one can never be the winner.

## The defect

Seller approval has been recorded in two places that nothing kept in step:

* ``marketplace_sellers.status`` — read by every selling surface in the app,
  and named by ``services/seller_access_state`` as the one authority on whether
  an account may sell.
* ``business_os_mkt_sellers.status`` — read by Business OS Store before it
  publishes a storefront or activates a product.

An admin suspending a seller through the marketplace tools wrote the first and
left the second saying ``approved``. The Business OS storefront published under
that stale row stayed live and kept taking orders from a seller the platform
had already stopped. Nothing reported it, because each subsystem was internally
consistent and each one's own tests passed.

## The fix these tests pin

Store now requires **both** to say approved. The direction is the whole point:
an AND can only refuse something that was previously allowed, so it cannot put a
shop online that either table would have kept down. The tempting shape — a union,
"approved anywhere is approved" — would have turned the stale row from a missed
refusal into a way *past* a live suspension.

Three properties, in order of how badly each one bit:

1. Canonical **suspension beats** a stale Business OS approval.
2. A canonical approval does **not rescue** a Business OS refusal.
3. A database with no canonical table at all falls back to Business OS's own
   gate rather than refusing everything — the subsystem is flag-gated and ships
   in environments where ``marketplace_sellers`` was never provisioned, and
   "the authority is not installed" is not the same fact as "this seller was
   refused".

Executable two ways:

    python -m pytest tests/business_os/test_store_seller_reconciliation.py
    python tests/business_os/test_store_seller_reconciliation.py
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_store_recon_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_BUSINESS"] = "on"
os.environ["BUSINESS_OS_STORE"] = "on"
# Off, as in the sibling eligibility suite: Store's gate must not depend on the
# marketplace feature flag. If this only passed with the marketplace switched
# on, the coupling would itself be the bug.
os.environ["BUSINESS_OS_MARKETPLACE"] = ""

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.business import schema as biz_schema  # noqa: E402
from services.business_os.business import service as biz_svc  # noqa: E402
from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.store import schema as store_schema  # noqa: E402
from services.business_os.store import service as svc  # noqa: E402
from services.business_os.store.service import StoreError  # noqa: E402

OWNER = 900
_seq = [0]


def setup_module(module=None):
    biz_schema.ensure_schema()
    store_schema.ensure_schema()
    mkt_schema.ensure_schema()
    _ensure_canonical_table()


def _ensure_canonical_table():
    """The canonical seller table, as the rest of the app declares it.

    Created here rather than imported from ``bot.init_db`` because that function
    builds ~550 tables and importing it to get one of them would make this suite
    depend on the whole monolith booting. Only the two columns the gate reads are
    needed; a mismatch with production's wider table would surface as a query
    error, not as a silently different answer.
    """
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS marketplace_sellers ("
            "  user_id INTEGER PRIMARY KEY,"
            "  status TEXT,"
            "  display_name TEXT,"
            "  business_name TEXT)")
        conn.commit()
    finally:
        conn.close()


def _drop_canonical_table():
    conn = db.connect()
    try:
        conn.execute("DROP TABLE IF EXISTS marketplace_sellers")
        conn.commit()
    finally:
        conn.close()


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def _set_bos_seller(user_id, status):
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


def _canonical_table_exists():
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'marketplace_sellers'"
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _set_canonical_seller(user_id, status):
    if not _canonical_table_exists():
        # The absent-table scenario builds its fixtures with the authority
        # uninstalled; "no row" is already true there and writing one is not
        # possible. Silently satisfying the request beats a harness crash that
        # escapes before the restoring ``finally`` and cascades into the next
        # two tests.
        assert status is None, "cannot seed a canonical status with no canonical table"
        return
    conn = db.connect()
    try:
        if status is None:
            conn.execute("DELETE FROM marketplace_sellers WHERE user_id = ?", (int(user_id),))
        else:
            conn.execute(
                "INSERT INTO marketplace_sellers (user_id, status) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET status = excluded.status",
                (int(user_id), status))
        conn.commit()
    finally:
        conn.close()


def _shop(bos_status="approved", canonical_status="approved"):
    """A business with a draft storefront and product, and both seller rows set."""
    _seq[0] += 1
    owner = OWNER + _seq[0]
    bid = biz_svc.create_business(owner, {"display_name": f"Recon {_seq[0]}"},
                                  context=_ctx())["business_id"]
    _set_bos_seller(owner, bos_status)
    _set_canonical_seller(owner, canonical_status)
    svc.upsert_storefront(bid, owner, {"name": f"Recon {_seq[0]}",
                                       "slug": f"recon-{_seq[0]}"}, context=_ctx())
    pid = svc.create_product(bid, owner, {"title": "Thing", "price_cents": 500},
                             context=_ctx())["product_id"]
    return bid, owner, pid


def _expect(code, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except StoreError as exc:
        assert exc.code == code, f"expected {code}, got {exc.code}: {exc}"
        return exc
    raise AssertionError(f"expected StoreError {code}, call succeeded")


# --- 1. the canonical table wins ---------------------------------------------

def test_a_canonical_suspension_beats_a_stale_business_os_approval():
    """The production shape: suspended by the marketplace, still 'approved' here."""
    bid, owner, _pid = _shop(bos_status="approved", canonical_status="suspended")
    exc = _expect("seller_not_approved",
                  svc.set_storefront_status, bid, owner, "publish", context=_ctx())
    # The message names the real state rather than a generic refusal. A merchant
    # told "not approved" while their Business OS record plainly reads approved
    # opens a support ticket; one told their status is 'suspended' knows who to
    # ask and what about.
    assert "suspended" in str(exc)


def test_a_canonical_denial_blocks_a_product_going_live_too():
    bid, owner, pid = _shop(bos_status="approved", canonical_status="rejected")
    _expect("seller_not_approved", svc.set_product_status, bid, owner, pid,
            "activate", context=_ctx())


def test_an_owner_with_no_canonical_row_is_refused():
    """An absent row is a real answer, and a denying one.

    Distinct from an absent *table*, which is the environment fact handled
    below. Conflating the two is how a missing authority becomes a free pass.
    """
    bid, owner, _pid = _shop(bos_status="approved", canonical_status=None)
    _expect("seller_not_approved", svc.set_storefront_status, bid, owner, "publish", context=_ctx())


def test_a_published_shop_goes_dark_when_the_canonical_row_is_suspended():
    """Revocation takes effect on read, not at the mercy of a sweep job."""
    bid, owner, _pid = _shop(bos_status="approved", canonical_status="approved")
    svc.set_storefront_status(bid, owner, "publish", context=_ctx())
    assert svc.public_storefront(bid) is not None
    _set_canonical_seller(owner, "suspended")
    assert svc.public_storefront(bid) is None


# --- 2. it is an AND, never a union ------------------------------------------

def test_a_canonical_approval_does_not_rescue_a_business_os_refusal():
    """The direction that matters.

    Had this been written as a union — "approved in either table is approved" —
    a stale row would have stopped being a missed refusal and started being a
    route *around* a live one.
    """
    bid, owner, _pid = _shop(bos_status="pending", canonical_status="approved")
    _expect("seller_not_approved", svc.set_storefront_status, bid, owner, "publish", context=_ctx())

    bid2, owner2, _p2 = _shop(bos_status=None, canonical_status="approved")
    _expect("seller_not_approved", svc.set_storefront_status, bid2, owner2, "publish", context=_ctx())


def test_both_approved_still_publishes():
    """The reconciliation must not have quietly closed the happy path."""
    bid, owner, pid = _shop(bos_status="approved", canonical_status="approved")
    svc.set_storefront_status(bid, owner, "publish", context=_ctx())
    svc.set_product_status(bid, owner, pid, "activate", context=_ctx())
    assert svc.public_storefront(bid) is not None


def test_canonical_status_is_read_case_and_space_insensitively():
    """Status text is written by several code paths; the gate reads intent."""
    bid, owner, _pid = _shop(bos_status="approved", canonical_status=" Approved ")
    svc.set_storefront_status(bid, owner, "publish", context=_ctx())
    assert svc.public_storefront(bid) is not None


# --- 3. taking a store down is still never gated -----------------------------

def test_a_suspended_seller_can_still_take_their_shop_down():
    """A gate that traps a live storefront online is worse than no gate.

    This held before the reconciliation and has to keep holding after it: the
    new check sits on the publish path only, so the newly-refused seller must
    not also find themselves unable to close.
    """
    bid, owner, _pid = _shop(bos_status="approved", canonical_status="approved")
    svc.set_storefront_status(bid, owner, "publish", context=_ctx())
    _set_canonical_seller(owner, "suspended")
    svc.set_storefront_status(bid, owner, "suspend", context=_ctx())
    assert svc.public_storefront(bid) is None


# --- 4. an uninstalled authority is not a verdict ----------------------------

def test_no_canonical_table_falls_back_to_the_business_os_gate():
    """Business OS is flag-gated and ships without the canonical table.

    Refusing every publish in that environment would be a different bug from the
    one being fixed, and a worse one: it would take down a subsystem that was
    working, on the grounds that an authority nobody installed had not spoken.
    Business OS's own gate is unchanged and still applies.
    """
    bid, owner, _pid = _shop(bos_status="approved", canonical_status="approved")
    _drop_canonical_table()
    try:
        svc.set_storefront_status(bid, owner, "publish", context=_ctx())
        assert svc.public_storefront(bid) is not None

        # And the fallback is a fallback, not an opening: Business OS's own
        # refusal still stands with the canonical table absent.
        bid2, owner2, _p2 = _shop(bos_status="pending", canonical_status=None)
        _expect("seller_not_approved", svc.set_storefront_status, bid2, owner2, "publish", context=_ctx())
    finally:
        _ensure_canonical_table()


def test_a_failed_canonical_read_refuses_loudly_instead_of_denying_quietly():
    """"We could not check" must not collapse into "we checked and no".

    A 503 is retryable and gets looked at. A 403 hides an outage inside a
    permission decision and bills the merchant for it — the same distinction the
    Business OS read already makes one layer up, held here too.
    """
    bid, owner, _pid = _shop(bos_status="approved", canonical_status="approved")

    real_connect = db.connect

    class _FailsOnCanonicalRead:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *a, **kw):
            if "marketplace_sellers" in str(sql):
                raise RuntimeError("connection reset by peer")
            return self._inner.execute(sql, *a, **kw)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    db.connect = lambda *a, **kw: _FailsOnCanonicalRead(real_connect(*a, **kw))
    try:
        _expect("seller_review_failed", svc.set_storefront_status, bid, owner, "publish", context=_ctx())
    finally:
        db.connect = real_connect


if __name__ == "__main__":
    setup_module()
    failures = 0
    for name, fn in sorted(list(globals().items())):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  FAIL  {name}: {exc}")
    print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
    sys.exit(1 if failures else 0)
