"""Business OS — Store policies HTTP controller, exercised DIRECTLY (no Flask).

Pins the (status, body) contract over the shipping/returns settings backend:

  * DARK when BUSINESS_OS_STORE is off — every handler returns 404;
  * unknown body fields rejected (400 unknown_field);
  * create 201; default/archive rules surface their 409 codes;
  * return policy reads null before setup (client renders "Not set up");
  * stranger gets 404 (existence not leaked); viewer write gets 403.

    python tests/business_os/test_store_policies_api.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_store_polapi_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_BUSINESS"] = "on"
os.environ["BUSINESS_OS_STORE"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.business import schema as biz_schema  # noqa: E402
from services.business_os.business import service as biz_svc  # noqa: E402
from services.business_os.store import schema as store_schema  # noqa: E402
from services.business_os.store import policies as pol  # noqa: E402
from services.business_os.store import policies_api as api  # noqa: E402


OWNER = 970
VIEWER = 971
STRANGER = 972


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    biz_schema.ensure_schema()
    store_schema.ensure_schema()
    pol.ensure_schema()


def _mk_business(name="Api Co"):
    biz = biz_svc.create_business(OWNER, {"display_name": name}, context=_ctx())
    bid = biz["business_id"]
    biz_svc.add_member(bid, OWNER, VIEWER, "viewer", context=_ctx())
    return bid


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_STORE"] = ""
    try:
        for status, body in (
            api.get_summary(OWNER, "b"),
            api.list_profiles(OWNER, "b"),
            api.create_profile(OWNER, "b", {"name": "X"}, context=_ctx()),
            api.get_return_policy(OWNER, "b"),
            api.put_return_policy(OWNER, "b", {"returns_accepted": False}, context=_ctx()),
        ):
            assert status == 404 and body["ok"] is False
    finally:
        os.environ["BUSINESS_OS_STORE"] = "on"


def test_flow_and_codes_through_controller():
    bid = _mk_business()
    # Honest null before setup.
    status, body = api.get_return_policy(OWNER, bid)
    assert status == 200 and body["policy"] is None
    status, body = api.get_summary(OWNER, bid)
    assert status == 200 and body["policies"]["shipping"]["configured"] is False

    # Unknown fields rejected loudly.
    status, body = api.create_profile(OWNER, bid, {"name": "X", "is_default": True},
                                      context=_ctx())
    assert status == 400 and body["code"] == "unknown_field"

    status, body = api.create_profile(OWNER, bid, {"name": "Standard",
                                                   "base_rate_cents": 599},
                                      context=_ctx())
    assert status == 201 and body["profile"]["is_default"] is True
    p1 = body["profile"]["profile_id"]
    status, body = api.create_profile(OWNER, bid, {"name": "Express"}, context=_ctx())
    assert status == 201
    p2 = body["profile"]["profile_id"]

    # Default cannot be archived; flip then archive works.
    status, body = api.archive_profile(OWNER, bid, p1, context=_ctx())
    assert status == 409 and body["code"] == "default_profile"
    status, body = api.make_default(OWNER, bid, p2, context=_ctx())
    assert status == 200 and body["profile"]["is_default"] is True
    status, body = api.archive_profile(OWNER, bid, p1, context=_ctx())
    assert status == 200 and body["profile"]["status"] == "archived"

    status, body = api.put_return_policy(
        OWNER, bid, {"returns_accepted": True, "window_days": 30}, context=_ctx())
    assert status == 200 and body["policy"]["window_days"] == 30

    status, body = api.get_summary(OWNER, bid)
    assert body["policies"]["shipping"]["default_profile_name"] == "Express"
    assert body["policies"]["returns"]["configured"] is True


def test_access_codes_surface():
    bid = _mk_business("Access Co")
    status, body = api.get_summary(STRANGER, bid)
    assert status == 404 and body["code"] == "not_found"
    status, body = api.create_profile(VIEWER, bid, {"name": "X"}, context=_ctx())
    assert status == 403 and body["code"] == "forbidden"
    status, body = api.create_profile(OWNER, bid, {"name": "X"},
                                      context=_ctx(status="suspended"))
    assert status == 403 and body["code"] == "account_hold"


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_flow_and_codes_through_controller,
        test_access_codes_surface,
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
