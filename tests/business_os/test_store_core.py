"""Business OS — Section 2 (Store) exercised through the SERVICE layer.

Pins the flag-gated storefront + catalog logic directly (no controller, no Flask):

  * DARK when the flag is off — every entry point raises 503 ``disabled``;
  * access is resolved against S1 canonical RBAC (imported, never re-modeled): a caller
    with no role on the business gets 404 (existence not leaked), a viewer can read but
    not mutate (403), a manager can manage the catalog, and only admin+ can drive the
    storefront lifecycle;
  * server owns ids/status/timestamps — the client cannot smuggle them;
  * lifecycle state machines reject illegal transitions (409);
  * account hold beats every write (403 ``account_hold``);
  * the public projection only exposes a *published* storefront and its *active*
    products;
  * every mutation lands an append-only audit row.

    python tests/business_os/test_store_core.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_store_core_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_BUSINESS"] = "on"
os.environ["BUSINESS_OS_STORE"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.business import schema as biz_schema  # noqa: E402
from services.business_os.business import service as biz_svc  # noqa: E402
from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.store import schema as store_schema  # noqa: E402
from services.business_os.store import service as svc  # noqa: E402
from services.business_os.store.service import StoreError  # noqa: E402


OWNER = 900
MANAGER = 901
VIEWER = 902
STRANGER = 903
ADMIN = 904


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    biz_schema.ensure_schema()
    store_schema.ensure_schema()
    # The seller-approval table lives in the marketplace schema; Store now reads
    # it before letting anything go live, so it has to exist here.
    mkt_schema.ensure_schema()


def _approve_seller(user_id, status="approved"):
    """Set the owner's row in the one seller-approval table.

    Written with raw SQL rather than through ``marketplace.service`` because
    that module asserts its own feature flag, and this suite is about Store.
    Store reads the record without the flag for the same reason: two selling
    surfaces, one approval, independently switchable.
    """
    from services import db
    conn = db.connect()
    try:
        now = "2026-01-01T00:00:00.000000Z"
        conn.execute(
            "INSERT INTO business_os_mkt_sellers "
            "(seller_user_id, status, created_at, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(seller_user_id) DO UPDATE SET status = excluded.status",
            (str(user_id), status, now, now))
        conn.commit()
    finally:
        conn.close()


def _mk_business(owner=OWNER, name="Store Co", seller_status="approved"):
    biz = biz_svc.create_business(owner, {"display_name": name}, context=_ctx())
    if seller_status is not None:
        _approve_seller(owner, seller_status)
    return biz["business_id"]


def _seed_team(bid):
    """Owner grants a manager, a viewer, and an admin on the business."""
    biz_svc.add_member(bid, OWNER, MANAGER, "manager", context=_ctx())
    biz_svc.add_member(bid, OWNER, VIEWER, "viewer", context=_ctx())
    biz_svc.add_member(bid, OWNER, ADMIN, "admin", context=_ctx())


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_STORE"] = ""
    try:
        bid = _mk_business(name="Dark Co")
        for fn in (
            lambda: svc.get_storefront(bid, OWNER),
            lambda: svc.upsert_storefront(bid, OWNER, {"name": "X"}, context=_ctx()),
            lambda: svc.list_products(bid, OWNER),
            lambda: svc.public_storefront(bid),
        ):
            try:
                fn()
                raise AssertionError("expected StoreError disabled")
            except StoreError as e:
                assert e.http_status == 503 and e.code == "disabled", (e.http_status, e.code)
    finally:
        os.environ["BUSINESS_OS_STORE"] = "on"


def test_upsert_creates_draft_and_server_owns_fields():
    bid = _mk_business(name="Draft Co")
    sf = svc.upsert_storefront(bid, OWNER,
                               {"name": "Acme Shop", "slug": "acme-shop",
                                "status": "published", "storefront_id": "hacked"},
                               context=_ctx())
    assert sf["name"] == "Acme Shop"
    assert sf["slug"] == "acme-shop"
    assert sf["status"] == "draft"            # not the smuggled 'published'
    assert sf["storefront_id"].startswith("sf_")
    assert sf["storefront_id"] != "hacked"
    assert sf["currency"] == "USD"
    assert sf["created_at"] and sf["updated_at"]


def test_one_storefront_per_business_upsert_updates():
    bid = _mk_business(name="Single Co")
    svc.upsert_storefront(bid, OWNER, {"name": "First"}, context=_ctx())
    sf2 = svc.upsert_storefront(bid, OWNER, {"name": "Second", "headline": "Hi"},
                                context=_ctx())
    assert sf2["name"] == "Second"
    assert sf2["headline"] == "Hi"
    got = svc.get_storefront(bid, OWNER)
    assert got["name"] == "Second"


def test_slug_uniqueness_conflict():
    b1 = _mk_business(name="Slug One")
    b2 = _mk_business(name="Slug Two")
    svc.upsert_storefront(b1, OWNER, {"name": "One", "slug": "taken"}, context=_ctx())
    try:
        svc.upsert_storefront(b2, OWNER, {"name": "Two", "slug": "taken"}, context=_ctx())
        raise AssertionError("expected slug conflict")
    except StoreError as e:
        assert e.http_status == 409 and e.code == "conflict", (e.http_status, e.code)


def test_access_isolation_stranger_404():
    bid = _mk_business(name="Iso Co")
    svc.upsert_storefront(bid, OWNER, {"name": "Iso"}, context=_ctx())
    try:
        svc.get_storefront(bid, STRANGER)
        raise AssertionError("expected 404 for stranger")
    except StoreError as e:
        assert e.http_status == 404 and e.code == "not_found", (e.http_status, e.code)


def test_viewer_can_read_not_write():
    bid = _mk_business(name="RBAC Co")
    _seed_team(bid)
    svc.upsert_storefront(bid, OWNER, {"name": "RBAC Shop"}, context=_ctx())
    # viewer read ok
    assert svc.get_storefront(bid, VIEWER)["name"] == "RBAC Shop"
    # viewer manage denied
    try:
        svc.create_product(bid, VIEWER, {"title": "P"}, context=_ctx())
        raise AssertionError("expected 403 for viewer create")
    except StoreError as e:
        assert e.http_status == 403 and e.code == "forbidden", (e.http_status, e.code)


def test_manager_can_manage_catalog_but_not_publish():
    bid = _mk_business(name="Mgr Co")
    _seed_team(bid)
    svc.upsert_storefront(bid, MANAGER, {"name": "Mgr Shop"}, context=_ctx())
    p = svc.create_product(bid, MANAGER, {"title": "Widget", "price_cents": 500},
                           context=_ctx())
    assert p["status"] == "draft" and p["price_cents"] == 500
    # manager cannot drive lifecycle (needs admin+)
    try:
        svc.set_storefront_status(bid, MANAGER, "publish", context=_ctx())
        raise AssertionError("expected 403 for manager publish")
    except StoreError as e:
        assert e.http_status == 403 and e.code == "forbidden", (e.http_status, e.code)


def test_storefront_lifecycle_and_illegal_transition():
    bid = _mk_business(name="Life Co")
    _seed_team(bid)
    svc.upsert_storefront(bid, OWNER, {"name": "Life Shop"}, context=_ctx())
    sf = svc.set_storefront_status(bid, ADMIN, "publish", context=_ctx())
    assert sf["status"] == "published"
    # draft->publish again is illegal now (already published)
    try:
        svc.set_storefront_status(bid, ADMIN, "publish", context=_ctx())
        raise AssertionError("expected 409 double publish")
    except StoreError as e:
        assert e.http_status == 409 and e.code == "conflict", (e.http_status, e.code)
    # suspend then restore
    assert svc.set_storefront_status(bid, ADMIN, "suspend", context=_ctx())["status"] == "suspended"
    assert svc.set_storefront_status(bid, ADMIN, "restore", context=_ctx())["status"] == "published"


def test_product_price_and_inventory_validation():
    bid = _mk_business(name="Price Co")
    svc.upsert_storefront(bid, OWNER, {"name": "Price Shop"}, context=_ctx())
    for bad in ({"title": "T", "price_cents": -1},
                {"title": "T", "price_cents": 1.5},
                {"title": "T", "inventory_qty": -3}):
        try:
            svc.create_product(bid, OWNER, bad, context=_ctx())
            raise AssertionError(f"expected invalid for {bad}")
        except StoreError as e:
            assert e.http_status == 400 and e.code == "invalid", (bad, e.http_status)
    # null inventory = untracked
    p = svc.create_product(bid, OWNER, {"title": "Unlimited", "inventory_qty": None},
                           context=_ctx())
    assert p["inventory_qty"] is None


def test_product_status_machine():
    bid = _mk_business(name="PStatus Co")
    svc.upsert_storefront(bid, OWNER, {"name": "PS Shop"}, context=_ctx())
    p = svc.create_product(bid, OWNER, {"title": "W"}, context=_ctx())
    pid = p["product_id"]
    assert svc.set_product_status(bid, OWNER, pid, "activate", context=_ctx())["status"] == "active"
    # activate again -> 409
    try:
        svc.set_product_status(bid, OWNER, pid, "activate", context=_ctx())
        raise AssertionError("expected 409")
    except StoreError as e:
        assert e.http_status == 409, e.http_status
    # archive is terminal
    assert svc.set_product_status(bid, OWNER, pid, "archive", context=_ctx())["status"] == "archived"
    try:
        svc.set_product_status(bid, OWNER, pid, "activate", context=_ctx())
        raise AssertionError("expected 409 from archived")
    except StoreError as e:
        assert e.http_status == 409, e.http_status


def test_collections_membership():
    bid = _mk_business(name="Col Co")
    svc.upsert_storefront(bid, OWNER, {"name": "Col Shop"}, context=_ctx())
    c = svc.create_collection(bid, OWNER, {"title": "Featured"}, context=_ctx())
    cid = c["collection_id"]
    p = svc.create_product(bid, OWNER, {"title": "Star"}, context=_ctx())
    pid = p["product_id"]
    svc.add_product_to_collection(bid, OWNER, cid, pid, context=_ctx())
    # duplicate -> 409
    try:
        svc.add_product_to_collection(bid, OWNER, cid, pid, context=_ctx())
        raise AssertionError("expected 409 duplicate")
    except StoreError as e:
        assert e.http_status == 409, e.http_status
    prods = svc.list_collection_products(bid, OWNER, cid)
    assert len(prods) == 1 and prods[0]["product_id"] == pid
    svc.remove_product_from_collection(bid, OWNER, cid, pid, context=_ctx())
    assert svc.list_collection_products(bid, OWNER, cid) == []


def test_public_storefront_only_published_and_active():
    bid = _mk_business(name="Pub Co")
    _seed_team(bid)
    svc.upsert_storefront(bid, OWNER, {"name": "Pub Shop"}, context=_ctx())
    p_active = svc.create_product(bid, OWNER, {"title": "Live"}, context=_ctx())
    svc.set_product_status(bid, OWNER, p_active["product_id"], "activate", context=_ctx())
    svc.create_product(bid, OWNER, {"title": "Hidden Draft"}, context=_ctx())
    # not published yet -> None
    assert svc.public_storefront(bid) is None
    svc.set_storefront_status(bid, ADMIN, "publish", context=_ctx())
    pub = svc.public_storefront(bid)
    assert pub is not None
    titles = [p["title"] for p in pub["products"]]
    assert titles == ["Live"]   # only the active product


def test_account_hold_beats_write():
    bid = _mk_business(name="Hold Co")
    svc.upsert_storefront(bid, OWNER, {"name": "Hold Shop"}, context=_ctx())
    try:
        svc.create_product(bid, OWNER, {"title": "X"},
                           context=_ctx(status="suspended"))
        raise AssertionError("expected 403 account_hold")
    except StoreError as e:
        assert e.http_status == 403 and e.code == "account_hold", (e.http_status, e.code)


def test_timeline_records_mutations():
    bid = _mk_business(name="Audit Co")
    svc.upsert_storefront(bid, OWNER, {"name": "Audit Shop"}, context=_ctx())
    svc.create_product(bid, OWNER, {"title": "Logged"}, context=_ctx())
    tl = svc.get_timeline(bid, OWNER)
    actions = {e["action"] for e in tl}
    assert "storefront.create" in actions
    assert "product.create" in actions
    # audit is structured (before/after decoded)
    assert any("after" in e for e in tl)


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_upsert_creates_draft_and_server_owns_fields,
        test_one_storefront_per_business_upsert_updates,
        test_slug_uniqueness_conflict,
        test_access_isolation_stranger_404,
        test_viewer_can_read_not_write,
        test_manager_can_manage_catalog_but_not_publish,
        test_storefront_lifecycle_and_illegal_transition,
        test_product_price_and_inventory_validation,
        test_product_status_machine,
        test_collections_membership,
        test_public_storefront_only_published_and_active,
        test_account_hold_beats_write,
        test_timeline_records_mutations,
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
