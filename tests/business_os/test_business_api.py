"""Business OS — Section 1 (Business HQ) exercised through the HTTP CONTROLLER.

Where test_business_core.py pins the service layer, this pins the framework-agnostic
``api.py`` controller — the exact ``(status_code, body)`` contract bot.py depends on:

  * DARK when the flag is off: every handler returns 404 (no canonical path leaks);
  * every body carries an ``ok`` bool;
  * client-writable field allowlists drop server-authoritative fields
    (business_id / owner_user_id / status / timestamps are never client-settable);
  * create -> 201, get/list/update -> 200, RBAC denial -> 403, missing -> 404;
  * account-hold precedence surfaces as 403 through the controller;
  * required-field validation (add_member without role/user_id -> 400).

    python tests/business_os/test_business_api.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_bizapi_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_BUSINESS"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.business import schema as biz_schema  # noqa: E402
from services.business_os.business import api as biz_api  # noqa: E402


OWNER = 800
MANAGER = 801
VIEWER = 802
STRANGER = 803


def setup_module(module=None):
    biz_schema.ensure_schema()


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def _mk(owner=OWNER, name="Acme"):
    st, body = biz_api.create_business(owner, {"display_name": name}, context=_ctx())
    assert st == 201, (st, body)
    assert body["ok"] is True
    return body["business"]


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_BUSINESS"] = ""
    try:
        for st, body in (
            biz_api.list_businesses(OWNER),
            biz_api.create_business(OWNER, {"display_name": "X"}, context=_ctx()),
            biz_api.get_business(OWNER, "biz_missing"),
            biz_api.get_timeline(OWNER, "biz_missing"),
        ):
            assert st == 404, (st, body)
            assert body["ok"] is False
            assert body["code"] == "not_found"
    finally:
        os.environ["BUSINESS_OS_BUSINESS"] = "on"


def test_create_projection_and_ok_envelope():
    biz = _mk(name="Envelope Co")
    # Server-authoritative fields present and correct.
    assert biz["display_name"] == "Envelope Co"
    assert biz["status"] == "draft"
    assert biz["owner_user_id"] in (OWNER, str(OWNER))
    assert biz["business_id"]
    assert biz["created_at"] and biz["updated_at"]


def test_client_cannot_set_server_fields():
    # Attempt to smuggle server-authoritative fields via the create payload.
    st, body = biz_api.create_business(
        OWNER,
        {
            "display_name": "Smuggle Inc",
            "business_id": "attacker_chosen",
            "owner_user_id": 999999,
            "status": "active",
            "created_at": "1970-01-01T00:00:00Z",
        },
        context=_ctx(),
    )
    assert st == 201, (st, body)
    biz = body["business"]
    assert biz["business_id"] != "attacker_chosen"
    assert biz["owner_user_id"] in (OWNER, str(OWNER))
    assert biz["status"] == "draft"           # not the smuggled 'active'
    assert biz["created_at"] != "1970-01-01T00:00:00Z"


def test_unknown_fields_ignored():
    st, body = biz_api.create_business(
        OWNER, {"display_name": "Clean Co", "bogus_field": "x", "nope": 1},
        context=_ctx(),
    )
    assert st == 201, (st, body)
    assert "bogus_field" not in body["business"]


def test_get_and_access_isolation():
    biz = _mk(name="Isolated Co")
    bid = biz["business_id"]
    st, body = biz_api.get_business(OWNER, bid)
    assert st == 200 and body["ok"] is True
    # Stranger must get a 404 (existence not leaked), not 403.
    st, body = biz_api.get_business(STRANGER, bid)
    assert st == 404, (st, body)
    assert body["ok"] is False


def test_rbac_denial_through_controller():
    biz = _mk(name="RBAC Co")
    bid = biz["business_id"]
    # Add a viewer, then have the viewer attempt an update -> 403.
    st, body = biz_api.add_member(OWNER, bid, {"user_id": VIEWER, "role": "viewer"},
                                  context=_ctx())
    assert st == 201, (st, body)
    st, body = biz_api.update_business(VIEWER, bid, {"tagline": "hi"}, context=_ctx())
    assert st == 403, (st, body)
    assert body["ok"] is False


def test_add_member_requires_fields():
    biz = _mk(name="Fields Co")
    bid = biz["business_id"]
    st, body = biz_api.add_member(OWNER, bid, {"user_id": MANAGER}, context=_ctx())
    assert st == 400, (st, body)
    st, body = biz_api.add_member(OWNER, bid, {"role": "manager"}, context=_ctx())
    assert st == 400, (st, body)


def test_account_hold_surfaces_403():
    biz = _mk(name="Hold Co")
    bid = biz["business_id"]
    st, body = biz_api.update_business(OWNER, bid, {"tagline": "t"},
                                       context=_ctx(status="suspended"))
    assert st == 403, (st, body)
    assert body["ok"] is False


def test_lifecycle_through_controller():
    biz = _mk(name="Lifecycle Co")
    bid = biz["business_id"]
    st, body = biz_api.set_business_status(OWNER, bid, {"action": "activate"},
                                           context=_ctx())
    assert st == 200 and body["business"]["status"] == "active"
    # Illegal double-activate -> 409 conflict.
    st, body = biz_api.set_business_status(OWNER, bid, {"action": "activate"},
                                           context=_ctx())
    assert st == 409, (st, body)


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_create_projection_and_ok_envelope,
        test_client_cannot_set_server_fields,
        test_unknown_fields_ignored,
        test_get_and_access_isolation,
        test_rbac_denial_through_controller,
        test_add_member_requires_fields,
        test_account_hold_surfaces_403,
        test_lifecycle_through_controller,
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
