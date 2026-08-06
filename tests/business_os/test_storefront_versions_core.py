"""Business OS — Storefront versioning (Phase 5) through the SERVICE layer.

Proves draft-vs-published is real and honest:

  * DARK when BUSINESS_OS_STORE is off;
  * publish gates: stranger 404 (existence not leaked), no storefront 404,
    unapproved seller 403, account hold 403; viewer/manager cannot publish;
  * publish freezes a snapshot; an unchanged re-publish returns the live
    version flagged unchanged (no junk rows); draft edits do NOT leak to the
    public read until the next publish;
  * draft_status names the exact changed fields;
  * restore creates a NEW version from an old snapshot (append-only history)
    and rewrites the draft to match; restoring the live version is a 409;
  * the public read still honors lifecycle (suspended = dark) and live seller
    approval (revoked = dark), and an unversioned shop is dark on this surface.

    python tests/business_os/test_storefront_versions_core.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_sfver_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_BUSINESS"] = "on"
os.environ["BUSINESS_OS_STORE"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.business import schema as biz_schema  # noqa: E402
from services.business_os.business import service as biz_svc  # noqa: E402
from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.store import schema as store_schema  # noqa: E402
from services.business_os.store import service as svc  # noqa: E402
from services.business_os.store import versions as ver  # noqa: E402
from services.business_os.store.service import StoreError  # noqa: E402


OWNER = 2970
VIEWER = 2971
STRANGER = 2972


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    biz_schema.ensure_schema()
    store_schema.ensure_schema()
    mkt_schema.ensure_schema()
    ver.ensure_schema()


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


def _expect(code, fn):
    try:
        fn()
    except StoreError as exc:
        assert exc.code == code, f"expected {code}, got {exc.code}"
        return exc
    raise AssertionError(f"expected StoreError {code}")


_state = {}


def _setup_world():
    if _state:
        return _state
    bid = biz_svc.create_business(OWNER, {"display_name": "Vers Co"},
                                  context=_ctx())["business_id"]
    biz_svc.add_member(bid, OWNER, VIEWER, "viewer", context=_ctx())
    _approve_seller(OWNER)
    svc.upsert_storefront(bid, OWNER, {"name": "Lamp Land", "slug": "lamp-land",
                                       "headline": "Lamps v1"}, context=_ctx())
    svc.set_storefront_status(bid, OWNER, "publish", context=_ctx())
    p = svc.create_product(bid, OWNER, {"title": "Lamp", "price_cents": 900},
                           context=_ctx())
    svc.set_product_status(bid, OWNER, p["product_id"], "activate",
                           context=_ctx())
    _state.update(bid=bid, pid=p["product_id"])
    return _state


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_STORE"] = ""
    try:
        _expect("disabled", lambda: ver.publish_version("x", OWNER, context=_ctx()))
        _expect("disabled", lambda: ver.published_storefront("x"))
    finally:
        os.environ["BUSINESS_OS_STORE"] = "on"


def test_publish_gates():
    s = _setup_world()
    _expect("not_found",
            lambda: ver.publish_version(s["bid"], STRANGER, context=_ctx()))
    _expect("forbidden",
            lambda: ver.publish_version(s["bid"], VIEWER, context=_ctx()))
    _expect("account_hold",
            lambda: ver.publish_version(s["bid"], OWNER,
                                        context=_ctx(status="suspended")))
    # A business with no storefront: 404.
    bid2 = biz_svc.create_business(2980, {"display_name": "Empty Co"},
                                   context=_ctx())["business_id"]
    _expect("not_found",
            lambda: ver.publish_version(bid2, 2980, context=_ctx()))
    # Unapproved seller cannot make a version live.
    bid3 = biz_svc.create_business(2981, {"display_name": "Unvetted Co"},
                                   context=_ctx())["business_id"]
    svc.upsert_storefront(bid3, 2981, {"name": "Nope"}, context=_ctx())
    _expect("seller_not_approved",
            lambda: ver.publish_version(bid3, 2981, context=_ctx()))


def test_publish_snapshot_and_draft_isolation():
    s = _setup_world()
    # Lifecycle-published but never versioned: this surface is dark.
    assert ver.published_storefront(s["bid"]) is None
    st = ver.draft_status(s["bid"], OWNER)
    assert st["live_version_no"] is None and st["dirty"] is True

    v1 = ver.publish_version(s["bid"], OWNER, note="first", context=_ctx())
    assert v1["version_no"] == 1 and v1["unchanged"] is False
    again = ver.publish_version(s["bid"], OWNER, context=_ctx())
    assert again["unchanged"] is True and again["version_id"] == v1["version_id"]

    pub = ver.published_storefront(s["bid"])
    assert pub["headline"] == "Lamps v1" and pub["version_no"] == 1
    assert pub["products"][0]["title"] == "Lamp"

    # Edit the draft: the public read must NOT move.
    svc.upsert_storefront(s["bid"], OWNER,
                          {"name": "Lamp Land", "headline": "Lamps v2"},
                          context=_ctx())
    assert ver.published_storefront(s["bid"])["headline"] == "Lamps v1"
    st = ver.draft_status(s["bid"], OWNER)
    assert st["dirty"] is True and st["changed_fields"] == ["headline"]

    v2 = ver.publish_version(s["bid"], OWNER, context=_ctx())
    assert v2["version_no"] == 2
    assert ver.published_storefront(s["bid"])["headline"] == "Lamps v2"
    assert ver.draft_status(s["bid"], OWNER)["dirty"] is False
    _state["v1"] = v1["version_id"]
    _state["v2"] = v2["version_id"]


def test_restore_is_append_only():
    s = _setup_world()
    _expect("conflict",  # live version cannot be "restored"
            lambda: ver.restore_version(s["bid"], OWNER, s["v2"], context=_ctx()))
    _expect("not_found",
            lambda: ver.restore_version(s["bid"], OWNER, "sfv_none",
                                        context=_ctx()))
    r = ver.restore_version(s["bid"], OWNER, s["v1"], note="undo v2",
                            context=_ctx())
    assert r["version_no"] == 3 and r["restored_from"] == 1
    assert ver.published_storefront(s["bid"])["headline"] == "Lamps v1"
    # The draft was rewritten to match: not dirty, and a publish is a no-op.
    assert ver.draft_status(s["bid"], OWNER)["dirty"] is False
    assert ver.publish_version(s["bid"], OWNER, context=_ctx())["unchanged"] is True
    # History is intact and ordered; exactly one live row.
    vs = ver.list_versions(s["bid"], OWNER)
    assert [v["version_no"] for v in vs] == [3, 2, 1]
    assert [v["status"] for v in vs] == ["published", "superseded", "superseded"]
    got = ver.get_version(s["bid"], OWNER, s["v1"])
    assert got["snapshot"]["headline"] == "Lamps v1"
    _expect("not_found", lambda: ver.get_version(s["bid"], STRANGER, s["v1"]))


def test_public_read_honors_lifecycle_and_approval():
    s = _setup_world()
    svc.set_storefront_status(s["bid"], OWNER, "suspend", context=_ctx())
    assert ver.published_storefront(s["bid"]) is None
    svc.set_storefront_status(s["bid"], OWNER, "restore", context=_ctx())
    assert ver.published_storefront(s["bid"]) is not None
    _approve_seller(OWNER, "suspended")
    assert ver.published_storefront(s["bid"]) is None
    _approve_seller(OWNER, "approved")
    assert ver.published_storefront(s["bid"]) is not None


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_publish_gates,
        test_publish_snapshot_and_draft_isolation,
        test_restore_is_append_only,
        test_public_read_honors_lifecycle_and_approval,
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
