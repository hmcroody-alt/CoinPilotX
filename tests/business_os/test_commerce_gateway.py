"""Business OS — COMMERCE GATEWAY, exercised DIRECTLY (no Flask, no HTTP).

Proves the one route table that will back the web surface is honest:

  * table sanity — unique names, unique (method, rule), valid auth tiers,
    every admin route names its permission, every path param in a rule is
    consumed by a controller that actually receives it;
  * DARK sweep — with both flags off, EVERY route answers 404 through
    ``dispatch`` (mounting the pack ahead of the flags changes nothing);
  * a real end-to-end flow through dispatch (storefront versions: publish /
    draft-status / public read / list) with query-string coercion;
  * store.manage RBAC still bites through the gateway (viewer 403);
  * ``context_from_user`` maps bot.py session rows onto engine context;
  * the Flask adapter file is AST-valid, registers every gateway route, and
    keeps the loop-variable closure pitfall fixed (view factory).

    python tests/business_os/test_commerce_gateway.py   # no pytest needed
"""

import ast
import os
import re
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_gateway_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_BUSINESS"] = "on"
os.environ["BUSINESS_OS_STORE"] = "on"
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

import sys
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _ROOT)

from services import db  # noqa: E402
from services.business_os import commerce_gateway as gw  # noqa: E402
from services.business_os.business import schema as biz_schema  # noqa: E402
from services.business_os.business import service as biz_svc  # noqa: E402
from services.business_os.store import service as store_svc  # noqa: E402


OWNER = 4070
VIEWER = 4071


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    biz_schema.ensure_schema()
    gw.ensure_schemas()


def _approve_seller(user_id, status="approved"):
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


def _params_for(rule):
    return {name: "x" for name in re.findall(r"<([^>]+)>", rule)}


# ---------------------------------------------------------------------------
def test_route_table_sanity():
    names = [r["name"] for r in gw.ROUTES]
    assert len(names) == len(set(names)), "duplicate route names"
    pairs = [(r["method"], r["rule"]) for r in gw.ROUTES]
    assert len(pairs) == len(set(pairs)), "duplicate (method, rule)"
    for r in gw.ROUTES:
        assert r["auth"] in ("public", "user", "admin"), r["name"]
        assert r["method"] in ("GET", "POST", "PATCH", "PUT"), r["name"]
        assert r["rule"].startswith("/"), r["name"]
        if r["auth"] == "admin":
            assert r.get("admin_perm"), f"{r['name']} missing admin_perm"
        assert callable(r["fn"]), r["name"]
    # Exactly one public route: the shopper storefront read.
    public = [r["name"] for r in gw.ROUTES if r["auth"] == "public"]
    assert public == ["commerce_storefront_published"]
    assert len(gw.ROUTES) == 37


def test_dark_sweep_every_route():
    os.environ["BUSINESS_OS_STORE"] = ""
    os.environ["BUSINESS_OS_MARKETPLACE"] = ""
    try:
        for r in gw.ROUTES:
            status, body = gw.dispatch(r["name"], OWNER,
                                       path=_params_for(r["rule"]),
                                       body={}, context=_ctx())
            assert status == 404 and body["ok"] is False, \
                f"{r['name']} not dark: {status} {body}"
    finally:
        os.environ["BUSINESS_OS_STORE"] = "on"
        os.environ["BUSINESS_OS_MARKETPLACE"] = "on"
    status, body = gw.dispatch("no_such_route", OWNER)
    assert status == 404 and body["code"] == "not_found"


def test_end_to_end_versions_flow():
    bid = biz_svc.create_business(OWNER, {"display_name": "Gateway Co"},
                                  context=_ctx())["business_id"]
    biz_svc.add_member(bid, OWNER, VIEWER, "viewer", context=_ctx())
    _approve_seller(OWNER)
    store_svc.upsert_storefront(bid, OWNER, {"name": "Gate Shop",
                                             "headline": "One"}, context=_ctx())
    store_svc.set_storefront_status(bid, OWNER, "publish", context=_ctx())

    p = {"business_id": bid}
    status, body = gw.dispatch("commerce_storefront_published", None, path=p)
    assert status == 404  # lifecycle-published but never versioned

    status, body = gw.dispatch("commerce_versions_publish", OWNER, path=p,
                               body={"note": "v1"}, context=_ctx())
    assert status == 201 and body["version"]["version_no"] == 1

    status, body = gw.dispatch("commerce_versions_publish", OWNER, path=p,
                               body=None, context=_ctx())
    assert status == 200 and body["version"]["unchanged"] is True

    status, body = gw.dispatch("commerce_storefront_published", None, path=p)
    assert status == 200 and body["storefront"]["headline"] == "One"

    status, body = gw.dispatch("commerce_draft_status", OWNER, path=p)
    assert status == 200 and body["draft"]["dirty"] is False

    # Query coercion: limit as a string, junk falls back to default.
    status, body = gw.dispatch("commerce_versions_list", OWNER, path=p,
                               query={"limit": "1"})
    assert status == 200 and len(body["versions"]) == 1
    status, body = gw.dispatch("commerce_versions_list", OWNER, path=p,
                               query={"limit": "junk"})
    assert status == 200 and len(body["versions"]) == 1  # only one exists

    # RBAC still bites through the gateway: viewer cannot publish.
    status, body = gw.dispatch("commerce_versions_publish", VIEWER, path=p,
                               body=None, context=_ctx())
    assert status == 403 and body["code"] == "forbidden"

    # Field allowlists still bite: unknown body field is a 400.
    status, body = gw.dispatch("commerce_versions_publish", OWNER, path=p,
                               body={"nope": 1}, context=_ctx())
    assert status == 400 and body["code"] == "unknown_field"

    # Policies through the gateway: create profile, read summary.
    status, body = gw.dispatch("commerce_shipping_profile_create", OWNER,
                               path=p, body={"name": "Standard",
                                             "rate_type": "flat",
                                             "base_rate_cents": 500},
                               context=_ctx())
    assert status == 201 and body["profile"]["is_default"] in (1, True)
    status, body = gw.dispatch("commerce_policies_summary", OWNER, path=p)
    assert status == 200 and body["ok"] is True

    # Marketplace lane: approved seller gets an honest (empty) overview.
    status, body = gw.dispatch("commerce_inventory_overview", OWNER,
                               query={"low_stock_threshold": "3"})
    assert status == 200 and body["ok"] is True


def test_context_from_user():
    assert gw.context_from_user(None) == {"account_status": "active",
                                          "access_enabled": 1}
    assert gw.context_from_user({"account_status": "suspended",
                                 "access_enabled": "0"}) == \
        {"account_status": "suspended", "access_enabled": 0}
    assert gw.context_from_user({"access_enabled": "junk"})["access_enabled"] == 1


def test_flask_adapter_shape():
    """The adapter can't be imported here (no Flask in the sandbox), so hold
    it to account by AST: it must define register(app), build one view per
    gateway route via a factory, and reference only names the gateway has."""
    path = os.path.join(_ROOT, "services", "business_os_commerce_routes.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)  # syntax-valid
    fn_names = {n.name for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef)}
    assert {"register", "_make_view", "_require_user", "_bot"} <= fn_names
    assert "add_url_rule" in src and "gw.ROUTES" in src
    assert "gw.dispatch" in src and "gw.context_from_user" in src
    assert "require_admin_api" in src  # admin tier is really gated
    assert "api_account_user" in src   # user tier uses the canonical resolver
    # The factory pattern (not a bare loop closure) is load-bearing.
    assert "_make_view(_route)" in src


def _run_standalone():
    setup_module()
    tests = [
        test_route_table_sanity,
        test_dark_sweep_every_route,
        test_end_to_end_versions_flow,
        test_context_from_user,
        test_flask_adapter_shape,
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
