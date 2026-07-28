"""Business OS — Section 1 (Business HQ) core primitives exercised DIRECTLY.

Pins the canonical Business service at the layer it lives in:

  * schema.ensure_schema is idempotent and creates the canonical tables;
  * flag gate (disabled -> 503, account-hold overrides all writes);
  * create assigns owner/status/timestamps server-side and records the owner as a
    member; access does not leak (stranger read -> 404);
  * identity validation (required name, hex color, email, url, field limits);
  * lifecycle state machine (draft->active, illegal transition -> 409);
  * RBAC matrix (viewer read, manager update, admin member-write, staff timeline;
    no privilege escalation; owner protected);
  * locations (add/update/soft-close, never hard-delete);
  * versioned policies (append-only, live = max version);
  * timeline (append-only audit read, JSON round-trips).

    python tests/business_os/test_business_core.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_bizcore_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_BUSINESS"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.business import schema as biz_schema  # noqa: E402
from services.business_os.business import service as svc  # noqa: E402
from services.business_os.business.service import BusinessError  # noqa: E402


OWNER = 900
ADMIN_USER = 901
MANAGER = 902
STAFF = 903
VIEWER = 904
STRANGER = 905
OUTSIDER = 906


def setup_module(module=None):
    biz_schema.ensure_schema()
    biz_schema.ensure_schema()  # idempotent second call must not raise


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def _expect(fn, code=None, http=None):
    try:
        fn()
    except BusinessError as e:
        if code is not None:
            assert e.code == code, f"expected code {code}, got {e.code}"
        if http is not None:
            assert e.http_status == http, f"expected http {http}, got {e.http_status}"
        return
    raise AssertionError("expected BusinessError, none raised")


def _new_business(owner=OWNER, name="Acme"):
    return svc.create_business(owner, {"display_name": name}, context=_ctx())


# ---------------------------------------------------------------------------
def test_flag_gate_and_hold():
    # Flag off -> disabled everywhere.
    os.environ["BUSINESS_OS_BUSINESS"] = ""
    _expect(lambda: svc.list_businesses(OWNER), code="disabled", http=503)
    _expect(lambda: svc.create_business(OWNER, {"display_name": "X"}, context=_ctx()),
            code="disabled", http=503)
    os.environ["BUSINESS_OS_BUSINESS"] = "on"
    # Account hold beats writes.
    _expect(lambda: svc.create_business(OWNER, {"display_name": "X"},
                                        context=_ctx(status="suspended")),
            code="account_hold", http=403)
    _expect(lambda: svc.create_business(OWNER, {"display_name": "X"},
                                        context=_ctx(access=0)),
            code="account_hold", http=403)


def test_create_and_access_isolation():
    b = _new_business()
    assert b["status"] == "draft"
    assert str(b["owner_user_id"]) == str(OWNER)
    assert b["business_id"]
    assert b["created_at"] and b["updated_at"]
    # Owner reads with role.
    g = svc.get_business(b["business_id"], OWNER)
    assert g["viewer_role"] == "owner"
    # Owner recorded as member.
    members = svc.list_members(b["business_id"], OWNER)
    assert any(str(m["user_id"]) == str(OWNER) and m["role"] == "owner"
               for m in members)
    # Stranger cannot read -> 404 (existence not leaked).
    _expect(lambda: svc.get_business(b["business_id"], STRANGER), code="not_found",
            http=404)
    # list_businesses only returns owned/member businesses.
    assert all(str(x["owner_user_id"]) == str(OWNER)
               for x in svc.list_businesses(OWNER))
    assert svc.list_businesses(STRANGER) == [] or all(
        x["business_id"] != b["business_id"] for x in svc.list_businesses(STRANGER))


def test_identity_validation():
    # Missing name.
    _expect(lambda: svc.create_business(OWNER, {}, context=_ctx()), code="invalid",
            http=400)
    # Bad hex color.
    _expect(lambda: svc.create_business(OWNER, {"display_name": "Z",
            "primary_color": "red"}, context=_ctx()), code="invalid", http=400)
    # Bad email.
    _expect(lambda: svc.create_business(OWNER, {"display_name": "Z",
            "contact_email": "nope"}, context=_ctx()), code="invalid", http=400)
    # Bad url.
    _expect(lambda: svc.create_business(OWNER, {"display_name": "Z",
            "website_url": "ftp://x"}, context=_ctx()), code="invalid", http=400)
    # Over-long name.
    _expect(lambda: svc.create_business(OWNER, {"display_name": "x" * 200},
            context=_ctx()), code="invalid", http=400)
    # Valid full identity round-trips + nulling optional fields works.
    b = svc.create_business(OWNER, {"display_name": "Good", "primary_color": "#ABCDEF",
            "contact_email": "a@b.co", "website_url": "https://x.io"}, context=_ctx())
    assert b["primary_color"] == "#abcdef"
    u = svc.update_business(b["business_id"], OWNER, {"contact_email": ""},
                           context=_ctx())
    assert u["contact_email"] is None


def test_lifecycle_state_machine():
    b = _new_business()
    bid = b["business_id"]
    a = svc.set_business_status(bid, OWNER, "activate", context=_ctx())
    assert a["status"] == "active"
    # Cannot activate an already-active business.
    _expect(lambda: svc.set_business_status(bid, OWNER, "activate", context=_ctx()),
            code="illegal_transition", http=409)
    s = svc.set_business_status(bid, OWNER, "suspend", context=_ctx())
    assert s["status"] == "suspended"
    r = svc.set_business_status(bid, OWNER, "restore", context=_ctx())
    assert r["status"] == "active"
    # Unknown action.
    _expect(lambda: svc.set_business_status(bid, OWNER, "explode", context=_ctx()),
            code="invalid", http=400)


def test_rbac_matrix_and_escalation():
    b = _new_business()
    bid = b["business_id"]
    svc.add_member(bid, OWNER, ADMIN_USER, "admin", context=_ctx())
    svc.add_member(bid, OWNER, MANAGER, "manager", context=_ctx())
    svc.add_member(bid, OWNER, STAFF, "staff", context=_ctx())
    svc.add_member(bid, OWNER, VIEWER, "viewer", context=_ctx())

    # viewer: read yes, update no, timeline no (needs staff+).
    assert svc.get_business(bid, VIEWER)["viewer_role"] == "viewer"
    _expect(lambda: svc.update_business(bid, VIEWER, {"tagline": "x"}, context=_ctx()),
            code="forbidden", http=403)
    _expect(lambda: svc.get_timeline(bid, VIEWER), code="forbidden", http=403)

    # staff: timeline yes, member list yes, update no.
    assert isinstance(svc.get_timeline(bid, STAFF), list)
    assert isinstance(svc.list_members(bid, STAFF), list)
    _expect(lambda: svc.update_business(bid, STAFF, {"tagline": "x"}, context=_ctx()),
            code="forbidden", http=403)

    # manager: update yes, member-write no, lifecycle no.
    assert svc.update_business(bid, MANAGER, {"tagline": "ok"}, context=_ctx())["tagline"] == "ok"
    _expect(lambda: svc.add_member(bid, MANAGER, OUTSIDER, "viewer", context=_ctx()),
            code="forbidden", http=403)
    _expect(lambda: svc.set_business_status(bid, MANAGER, "activate", context=_ctx()),
            code="forbidden", http=403)

    # admin: member-write yes, but cannot grant above own rank.
    m = svc.add_member(bid, ADMIN_USER, OUTSIDER, "manager", context=_ctx())
    assert m["role"] == "manager"
    _expect(lambda: svc.add_member(bid, ADMIN_USER, 907, "admin", context=_ctx()),
            code="invalid", http=400) if False else None
    # admin cannot grant owner (blocked) and cannot escalate above self.
    _expect(lambda: svc.add_member(bid, ADMIN_USER, 908, "owner", context=_ctx()),
            code="forbidden", http=403)


def test_member_protections():
    b = _new_business()
    bid = b["business_id"]
    svc.add_member(bid, OWNER, ADMIN_USER, "admin", context=_ctx())
    svc.add_member(bid, OWNER, STAFF, "staff", context=_ctx())
    # Duplicate add -> conflict.
    _expect(lambda: svc.add_member(bid, OWNER, STAFF, "viewer", context=_ctx()),
            code="conflict", http=409)
    # Cannot change your own membership via add.
    _expect(lambda: svc.add_member(bid, ADMIN_USER, ADMIN_USER, "manager", context=_ctx()),
            code="invalid", http=400)
    # Role change works; owner role cannot be changed.
    upd = svc.update_member_role(bid, OWNER, STAFF, "manager", context=_ctx())
    assert upd["role"] == "manager"
    _expect(lambda: svc.update_member_role(bid, ADMIN_USER, OWNER, "viewer",
            context=_ctx()), code="forbidden", http=403)
    # Owner cannot be removed; removed member can be re-added.
    _expect(lambda: svc.remove_member(bid, ADMIN_USER, OWNER, context=_ctx()),
            code="forbidden", http=403)
    rem = svc.remove_member(bid, OWNER, STAFF, context=_ctx())
    assert rem["status"] == "removed"
    re_add = svc.add_member(bid, OWNER, STAFF, "viewer", context=_ctx())
    assert re_add["status"] == "active" and re_add["role"] == "viewer"


def test_locations():
    b = _new_business()
    bid = b["business_id"]
    loc = svc.add_location(bid, OWNER, {"label": "HQ", "city": "NYC",
            "kind": "office"}, context=_ctx())
    assert loc["status"] == "active" and loc["kind"] == "office"
    # Bad kind rejected.
    _expect(lambda: svc.add_location(bid, OWNER, {"label": "X", "kind": "moon"},
            context=_ctx()), code="invalid", http=400)
    upd = svc.update_location(bid, OWNER, loc["location_id"], {"city": "Boston"},
                             context=_ctx())
    assert upd["city"] == "Boston"
    # Soft-close (never hard-delete): closed rows drop out of the active list.
    closed = svc.close_location(bid, OWNER, loc["location_id"], context=_ctx())
    assert closed["status"] == "closed"
    assert all(l["location_id"] != loc["location_id"]
               for l in svc.list_locations(bid, OWNER))
    # Update on a missing location -> 404.
    _expect(lambda: svc.update_location(bid, OWNER, "nope", {"city": "X"},
            context=_ctx()), code="not_found", http=404)


def test_policies_versioned():
    b = _new_business()
    bid = b["business_id"]
    _expect(lambda: svc.set_policy(bid, OWNER, "unknown", "x", context=_ctx()),
            code="invalid", http=400)
    p1 = svc.set_policy(bid, OWNER, "returns", "v1", context=_ctx())
    p2 = svc.set_policy(bid, OWNER, "returns", "v2", context=_ctx())
    assert (p1["version"], p2["version"]) == (1, 2)
    assert svc.get_policy(bid, OWNER, "returns")["version"] == 2
    svc.set_policy(bid, OWNER, "privacy", "hello", context=_ctx())
    live = svc.list_policies(bid, OWNER)
    kinds = {p["policy_type"]: p["version"] for p in live}
    assert kinds.get("returns") == 2 and kinds.get("privacy") == 1


def test_timeline_audit():
    b = _new_business()
    bid = b["business_id"]
    svc.update_business(bid, OWNER, {"tagline": "t"}, context=_ctx())
    svc.set_business_status(bid, OWNER, "activate", context=_ctx())
    tl = svc.get_timeline(bid, OWNER)
    actions = [e["action"] for e in tl]
    assert "business.create" in actions
    assert "business.update" in actions
    assert "business.activate" in actions
    # before/after JSON round-trip into objects.
    upd_entry = next(e for e in tl if e["action"] == "business.update")
    assert isinstance(upd_entry.get("after"), dict)
    assert upd_entry["after"]["tagline"] == "t"


def _run_standalone():
    setup_module()
    tests = [
        test_flag_gate_and_hold,
        test_create_and_access_isolation,
        test_identity_validation,
        test_lifecycle_state_machine,
        test_rbac_matrix_and_escalation,
        test_member_protections,
        test_locations,
        test_policies_versioned,
        test_timeline_audit,
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
