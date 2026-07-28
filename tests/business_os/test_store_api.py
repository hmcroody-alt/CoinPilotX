"""Business OS — Section 2 (Store) exercised through the HTTP CONTROLLER.

Where test_store_core.py pins the service layer, this pins the framework-agnostic
``api.py`` controller — the exact ``(status_code, body)`` contract bot.py depends on:

  * DARK when the flag is off: every authenticated handler returns 404;
  * every body carries an ``ok`` bool;
  * client-writable field allowlists drop server-authoritative fields
    (status / storefront_id / timestamps are never client-settable);
  * create -> 201, get/list/update -> 200, RBAC denial -> 403, missing -> 404,
    conflict -> 409;
  * account-hold precedence surfaces as 403 through the controller;
  * required-field validation (add_product_to_collection without product_id -> 400);
  * public_storefront is 404 until published, 200 after.

    python tests/business_os/test_store_api.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_store_api_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_BUSINESS"] = "on"
os.environ["BUSINESS_OS_STORE"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.business import schema as biz_schema  # noqa: E402
from services.business_os.business import service as biz_svc  # noqa: E402
from services.business_os.store import schema as store_schema  # noqa: E402
from services.business_os.store import api as store_api  # noqa: E402


OWNER = 950
MANAGER = 951
VIEWER = 952
STRANGER = 953
ADMIN = 954


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    biz_schema.ensure_schema()
    store_schema.ensure_schema()


def _mk_business(owner=OWNER, name="API Store Co"):
    biz = biz_svc.create_business(owner, {"display_name": name}, context=_ctx())
    return biz["business_id"]


def _seed_team(bid):
    biz_svc.add_member(bid, OWNER, MANAGER, "manager", context=_ctx())
    biz_svc.add_member(bid, OWNER, VIEWER, "viewer", context=_ctx())
    biz_svc.add_member(bid, OWNER, ADMIN, "admin", context=_ctx())


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_STORE"] = ""
    try:
        bid = _mk_business(name="Dark API Co")
        for st, body in (
            store_api.get_storefront(OWNER, bid),
            store_api.upsert_storefront(OWNER, bid, {"name": "X"}, context=_ctx()),
            store_api.list_products(OWNER, bid),
            store_api.public_storefront(bid),
        ):
            assert st == 404, (st, body)
            assert body["ok"] is False and body["code"] == "not_found"
    finally:
        os.environ["BUSINESS_OS_STORE"] = "on"


def test_upsert_envelope_and_server_fields():
    bid = _mk_business(name="Env Co")
    st, body = store_api.upsert_storefront(
        OWNER, bid,
        {"name": "Env Shop", "slug": "env-shop", "status": "published",
         "storefront_id": "hacked", "bogus": 1},
        context=_ctx())
    assert st == 200 and body["ok"] is True
    sf = body["storefront"]
    assert sf["name"] == "Env Shop"
    assert sf["status"] == "draft"                 # smuggled status dropped
    assert sf["storefront_id"] != "hacked"


def test_create_product_201_and_get_200():
    bid = _mk_business(name="Prod Co")
    store_api.upsert_storefront(OWNER, bid, {"name": "Prod Shop"}, context=_ctx())
    st, body = store_api.create_product(OWNER, bid,
                                        {"title": "Thing", "price_cents": 1234},
                                        context=_ctx())
    assert st == 201 and body["ok"] is True
    pid = body["product"]["product_id"]
    st, body = store_api.get_product(OWNER, bid, pid)
    assert st == 200 and body["product"]["price_cents"] == 1234


def test_stranger_get_404_not_leaked():
    bid = _mk_business(name="Leak Co")
    store_api.upsert_storefront(OWNER, bid, {"name": "Leak Shop"}, context=_ctx())
    st, body = store_api.get_storefront(STRANGER, bid)
    assert st == 404 and body["ok"] is False


def test_rbac_denial_through_controller():
    bid = _mk_business(name="RBAC API Co")
    _seed_team(bid)
    store_api.upsert_storefront(OWNER, bid, {"name": "RBAC Shop"}, context=_ctx())
    st, body = store_api.create_product(VIEWER, bid, {"title": "P"}, context=_ctx())
    assert st == 403 and body["ok"] is False and body["code"] == "forbidden"


def test_account_hold_surfaces_403():
    bid = _mk_business(name="Hold API Co")
    store_api.upsert_storefront(OWNER, bid, {"name": "Hold Shop"}, context=_ctx())
    st, body = store_api.create_product(OWNER, bid, {"title": "X"},
                                        context=_ctx(status="suspended"))
    assert st == 403 and body["code"] == "account_hold"


def test_lifecycle_through_controller():
    bid = _mk_business(name="Life API Co")
    _seed_team(bid)
    store_api.upsert_storefront(OWNER, bid, {"name": "Life Shop"}, context=_ctx())
    st, body = store_api.set_storefront_status(ADMIN, bid, {"action": "publish"},
                                               context=_ctx())
    assert st == 200 and body["storefront"]["status"] == "published"
    st, body = store_api.set_storefront_status(ADMIN, bid, {"action": "publish"},
                                               context=_ctx())
    assert st == 409 and body["ok"] is False


def test_add_to_collection_requires_product_id():
    bid = _mk_business(name="Col API Co")
    store_api.upsert_storefront(OWNER, bid, {"name": "Col Shop"}, context=_ctx())
    st, body = store_api.create_collection(OWNER, bid, {"title": "Feat"}, context=_ctx())
    cid = body["collection"]["collection_id"]
    st, body = store_api.add_product_to_collection(OWNER, bid, cid, {}, context=_ctx())
    assert st == 400 and body["code"] == "invalid"


def test_collection_membership_201_and_list():
    bid = _mk_business(name="Mem API Co")
    store_api.upsert_storefront(OWNER, bid, {"name": "Mem Shop"}, context=_ctx())
    _, cbody = store_api.create_collection(OWNER, bid, {"title": "Feat"}, context=_ctx())
    cid = cbody["collection"]["collection_id"]
    _, pbody = store_api.create_product(OWNER, bid, {"title": "Item"}, context=_ctx())
    pid = pbody["product"]["product_id"]
    st, body = store_api.add_product_to_collection(OWNER, bid, cid,
                                                   {"product_id": pid}, context=_ctx())
    assert st == 201 and body["ok"] is True
    st, body = store_api.list_collection_products(OWNER, bid, cid)
    assert st == 200 and len(body["products"]) == 1


def test_public_storefront_404_until_published():
    bid = _mk_business(name="Pub API Co")
    _seed_team(bid)
    store_api.upsert_storefront(OWNER, bid, {"name": "Pub Shop"}, context=_ctx())
    st, body = store_api.public_storefront(bid)
    assert st == 404
    store_api.set_storefront_status(ADMIN, bid, {"action": "publish"}, context=_ctx())
    st, body = store_api.public_storefront(bid)
    assert st == 200 and body["storefront"]["name"] == "Pub Shop"


def test_list_products_status_filter():
    bid = _mk_business(name="Filter Co")
    store_api.upsert_storefront(OWNER, bid, {"name": "Filter Shop"}, context=_ctx())
    _, b1 = store_api.create_product(OWNER, bid, {"title": "A"}, context=_ctx())
    store_api.set_product_status(OWNER, bid, b1["product"]["product_id"],
                                 {"action": "activate"}, context=_ctx())
    store_api.create_product(OWNER, bid, {"title": "B"}, context=_ctx())  # stays draft
    st, body = store_api.list_products(OWNER, bid, status="active")
    assert st == 200 and len(body["products"]) == 1 and body["products"][0]["title"] == "A"


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_upsert_envelope_and_server_fields,
        test_create_product_201_and_get_200,
        test_stranger_get_404_not_leaked,
        test_rbac_denial_through_controller,
        test_account_hold_surfaces_403,
        test_lifecycle_through_controller,
        test_add_to_collection_requires_product_id,
        test_collection_membership_201_and_list,
        test_public_storefront_404_until_published,
        test_list_products_status_filter,
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
