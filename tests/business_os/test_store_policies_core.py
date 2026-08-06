"""Business OS — Store shipping profiles + return policy, exercised DIRECTLY.

The settings backend behind the dashboard's Shipping and Returns tiles
(mission finding: those tiles were inert — no routes, no tables). Pins:

  * DARK when BUSINESS_OS_STORE is off — every entry point raises 503;
  * S1 RBAC inherited: stranger -> 404 (existence not leaked), viewer can read
    but not write (403), manager can manage;
  * first active shipping profile becomes the default automatically; the
    default cannot be archived while it is the default (409);
  * "free" rate profiles are normalised to zero charges;
  * return policy: absent means absent (None — the tile says "Not set up"),
    upsert validates window/fee/payer, accepted-without-window refused;
  * policies_summary reports honest configured=false before setup and real
    values after;
  * account hold beats every write; every mutation audits.

    python tests/business_os/test_store_policies_core.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_store_pol_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_BUSINESS"] = "on"
os.environ["BUSINESS_OS_STORE"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.business import schema as biz_schema  # noqa: E402
from services.business_os.business import service as biz_svc  # noqa: E402
from services.business_os.store import schema as store_schema  # noqa: E402
from services.business_os.store import policies as pol  # noqa: E402
from services.business_os.store.service import StoreError  # noqa: E402


OWNER = 950
MANAGER = 951
VIEWER = 952
STRANGER = 953


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    biz_schema.ensure_schema()
    store_schema.ensure_schema()
    pol.ensure_schema()


def _mk_business(name="Policy Co"):
    biz = biz_svc.create_business(OWNER, {"display_name": name}, context=_ctx())
    bid = biz["business_id"]
    biz_svc.add_member(bid, OWNER, MANAGER, "manager", context=_ctx())
    biz_svc.add_member(bid, OWNER, VIEWER, "viewer", context=_ctx())
    return bid


def _expect(code, http, fn):
    try:
        fn()
        raise AssertionError(f"expected {code}")
    except StoreError as e:
        assert e.http_status == http and e.code == code, (e.http_status, e.code)


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_STORE"] = ""
    try:
        for fn in (
            lambda: pol.list_shipping_profiles("b", OWNER),
            lambda: pol.create_shipping_profile("b", OWNER, {"name": "X"}, context=_ctx()),
            lambda: pol.get_return_policy("b", OWNER),
            lambda: pol.upsert_return_policy("b", OWNER, {"returns_accepted": False}, context=_ctx()),
            lambda: pol.policies_summary("b", OWNER),
        ):
            _expect("disabled", 503, fn)
    finally:
        os.environ["BUSINESS_OS_STORE"] = "on"


def test_summary_honest_before_setup():
    bid = _mk_business("Empty Co")
    s = pol.policies_summary(bid, OWNER)
    assert s["shipping"]["configured"] is False
    assert s["shipping"]["profile_count"] == 0
    assert s["shipping"]["default_profile_name"] is None
    assert s["returns"]["configured"] is False
    assert s["returns"]["returns_accepted"] is None
    assert pol.get_return_policy(bid, OWNER) is None


def test_first_profile_becomes_default_and_summary_updates():
    bid = _mk_business("Ship Co")
    p1 = pol.create_shipping_profile(bid, MANAGER, {
        "name": "Standard", "rate_type": "flat", "base_rate_cents": 599,
        "regions": ["US", "CA"], "min_delivery_days": 3, "max_delivery_days": 7,
    }, context=_ctx())
    assert p1["is_default"] is True and p1["base_rate_cents"] == 599
    p2 = pol.create_shipping_profile(bid, MANAGER, {"name": "Express",
                                                    "base_rate_cents": 1499},
                                     context=_ctx())
    assert p2["is_default"] is False
    s = pol.policies_summary(bid, OWNER)
    assert s["shipping"] == {"configured": True, "profile_count": 2,
                             "default_profile_name": "Standard"}


def test_default_flip_and_archive_rules():
    bid = _mk_business("Flip Co")
    p1 = pol.create_shipping_profile(bid, OWNER, {"name": "A"}, context=_ctx())
    p2 = pol.create_shipping_profile(bid, OWNER, {"name": "B"}, context=_ctx())
    # Default cannot be archived while default.
    _expect("default_profile", 409,
            lambda: pol.archive_shipping_profile(bid, OWNER, p1["profile_id"],
                                                 context=_ctx()))
    p2 = pol.set_default_shipping_profile(bid, OWNER, p2["profile_id"], context=_ctx())
    assert p2["is_default"] is True
    # Exactly one default.
    profs = pol.list_shipping_profiles(bid, OWNER)
    assert sum(1 for p in profs if p["is_default"]) == 1
    # Old default now archivable; archive is idempotent.
    a = pol.archive_shipping_profile(bid, OWNER, p1["profile_id"], context=_ctx())
    assert a["status"] == "archived"
    a = pol.archive_shipping_profile(bid, OWNER, p1["profile_id"], context=_ctx())
    assert a["status"] == "archived"
    # Archived profile cannot be edited or made default.
    _expect("archived", 409,
            lambda: pol.update_shipping_profile(bid, OWNER, p1["profile_id"],
                                                {"name": "Z"}, context=_ctx()))
    _expect("archived", 409,
            lambda: pol.set_default_shipping_profile(bid, OWNER, p1["profile_id"],
                                                     context=_ctx()))
    # Archived hidden by default, visible on request.
    assert len(pol.list_shipping_profiles(bid, OWNER)) == 1
    assert len(pol.list_shipping_profiles(bid, OWNER, include_archived=True)) == 2


def test_free_rate_normalised_and_validation():
    bid = _mk_business("Free Co")
    p = pol.create_shipping_profile(bid, OWNER, {
        "name": "Free ship", "rate_type": "free", "base_rate_cents": 999,
    }, context=_ctx())
    assert p["base_rate_cents"] == 0 and p["per_item_rate_cents"] == 0
    _expect("invalid", 400,
            lambda: pol.create_shipping_profile(bid, OWNER, {"name": ""}, context=_ctx()))
    _expect("invalid", 400,
            lambda: pol.create_shipping_profile(bid, OWNER,
                                                {"name": "X", "base_rate_cents": -1},
                                                context=_ctx()))
    _expect("invalid", 400,
            lambda: pol.create_shipping_profile(bid, OWNER,
                                                {"name": "X", "min_delivery_days": 9,
                                                 "max_delivery_days": 2},
                                                context=_ctx()))
    _expect("invalid", 400,
            lambda: pol.create_shipping_profile(bid, OWNER,
                                                {"name": "X", "rate_type": "carrier"},
                                                context=_ctx()))


def test_return_policy_upsert_and_validation():
    bid = _mk_business("Return Co")
    _expect("invalid", 400,
            lambda: pol.upsert_return_policy(bid, OWNER, {"returns_accepted": "yes"},
                                             context=_ctx()))
    _expect("invalid", 400,  # accepted without a window is refused
            lambda: pol.upsert_return_policy(bid, OWNER, {"returns_accepted": True},
                                             context=_ctx()))
    _expect("invalid", 400,
            lambda: pol.upsert_return_policy(
                bid, OWNER, {"returns_accepted": True, "window_days": 30,
                             "restocking_fee_bps": 20000}, context=_ctx()))
    _expect("invalid", 400,
            lambda: pol.upsert_return_policy(
                bid, OWNER, {"returns_accepted": True, "window_days": 30,
                             "return_shipping_paid_by": "platform"}, context=_ctx()))
    p = pol.upsert_return_policy(bid, OWNER, {
        "returns_accepted": True, "window_days": 30, "restocking_fee_bps": 500,
        "return_shipping_paid_by": "buyer", "policy_text": "30 days, unopened.",
    }, context=_ctx())
    assert p["returns_accepted"] is True and p["window_days"] == 30
    # Update in place (still one row).
    p = pol.upsert_return_policy(bid, OWNER, {"returns_accepted": False},
                                 context=_ctx())
    assert p["returns_accepted"] is False
    s = pol.policies_summary(bid, OWNER)
    assert s["returns"] == {"configured": True, "returns_accepted": False,
                            "window_days": None}


def test_rbac_and_account_hold():
    bid = _mk_business("Rbac Co")
    # Stranger: existence not leaked.
    _expect("not_found", 404, lambda: pol.list_shipping_profiles(bid, STRANGER))
    _expect("not_found", 404, lambda: pol.policies_summary(bid, STRANGER))
    # Viewer reads but cannot write.
    assert pol.list_shipping_profiles(bid, VIEWER) == []
    _expect("forbidden", 403,
            lambda: pol.create_shipping_profile(bid, VIEWER, {"name": "X"},
                                                context=_ctx()))
    _expect("forbidden", 403,
            lambda: pol.upsert_return_policy(bid, VIEWER,
                                             {"returns_accepted": False},
                                             context=_ctx()))
    # Hold beats every write.
    _expect("account_hold", 403,
            lambda: pol.create_shipping_profile(bid, OWNER, {"name": "X"},
                                                context=_ctx(status="suspended")))
    _expect("account_hold", 403,
            lambda: pol.upsert_return_policy(bid, OWNER,
                                             {"returns_accepted": False},
                                             context=_ctx(access=0)))


def test_audit_trail_lands():
    bid = _mk_business("Audit Co")
    p = pol.create_shipping_profile(bid, OWNER, {"name": "Std"}, context=_ctx())
    pol.update_shipping_profile(bid, OWNER, p["profile_id"], {"name": "Std2"},
                                context=_ctx())
    pol.upsert_return_policy(bid, OWNER,
                             {"returns_accepted": True, "window_days": 14},
                             context=_ctx())
    conn = db.connect()
    try:
        actions = [r["action"] if hasattr(r, "keys") else r[0] for r in conn.execute(
            "SELECT action FROM business_os_store_audit WHERE business_id = ? "
            "AND subject_type IN ('shipping_profile','return_policy') ORDER BY id",
            (bid,)).fetchall()]
    finally:
        conn.close()
    assert actions == ["shipping_profile.create", "shipping_profile.update",
                       "return_policy.create"]


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_summary_honest_before_setup,
        test_first_profile_becomes_default_and_summary_updates,
        test_default_flip_and_archive_rules,
        test_free_rate_normalised_and_validation,
        test_return_policy_upsert_and_validation,
        test_rbac_and_account_hold,
        test_audit_trail_lands,
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
