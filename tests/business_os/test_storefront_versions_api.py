"""Business OS — Storefront versions HTTP controller, exercised DIRECTLY (no Flask).

  * DARK when BUSINESS_OS_STORE is off — all six handlers 404;
  * allowlist (unknown_field), bad_body;
  * publish 201 / unchanged 200; draft-status; restore 201; public read serves
    the live snapshot and 404s an unversioned shop; engine codes surface.

    python tests/business_os/test_storefront_versions_api.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_sfver_api_"), "test.db")
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
from services.business_os.store import versions_api as api  # noqa: E402


OWNER = 3070
STRANGER = 3072


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


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_STORE"] = ""
    try:
        for status, body in (api.post_publish("b", OWNER, {}),
                             api.post_restore("b", OWNER, "v", {}),
                             api.get_list("b", OWNER),
                             api.get_one("b", OWNER, "v"),
                             api.get_draft_status("b", OWNER),
                             api.get_published("b")):
            assert status == 404 and body["ok"] is False
    finally:
        os.environ["BUSINESS_OS_STORE"] = "on"


def test_versioning_through_controller():
    bid = biz_svc.create_business(OWNER, {"display_name": "Api Co"},
                                  context=_ctx())["business_id"]
    _approve_seller(OWNER)
    svc.upsert_storefront(bid, OWNER, {"name": "Shop", "headline": "One"},
                          context=_ctx())
    svc.set_storefront_status(bid, OWNER, "publish", context=_ctx())

    status, body = api.post_publish(bid, OWNER, {"nope": 1})
    assert status == 400 and body["code"] == "unknown_field"
    status, body = api.post_publish(bid, OWNER, "junk")
    assert status == 400 and body["code"] == "bad_body"

    # Unversioned but lifecycle-published: public surface is an honest 404.
    status, body = api.get_published(bid)
    assert status == 404 and body["code"] == "not_found"

    status, body = api.post_publish(bid, OWNER, {"note": "v1"}, context=_ctx())
    assert status == 201 and body["version"]["version_no"] == 1
    v1 = body["version"]["version_id"]
    status, body = api.post_publish(bid, OWNER, None, context=_ctx())
    assert status == 200 and body["version"]["unchanged"] is True

    svc.upsert_storefront(bid, OWNER, {"name": "Shop", "headline": "Two"},
                          context=_ctx())
    status, body = api.get_draft_status(bid, OWNER)
    assert status == 200 and body["draft"]["changed_fields"] == ["headline"]
    status, body = api.post_publish(bid, OWNER, None, context=_ctx())
    assert status == 201 and body["version"]["version_no"] == 2

    status, body = api.get_published(bid)
    assert status == 200 and body["storefront"]["headline"] == "Two"

    status, body = api.post_restore(bid, OWNER, v1, {"note": "undo"},
                                    context=_ctx())
    assert status == 201 and body["version"]["restored_from"] == 1
    status, body = api.get_published(bid)
    assert status == 200 and body["storefront"]["headline"] == "One"

    status, body = api.get_list(bid, OWNER)
    assert status == 200 and [v["version_no"] for v in body["versions"]] == [3, 2, 1]
    status, body = api.get_one(bid, OWNER, v1)
    assert status == 200 and body["version"]["snapshot"]["headline"] == "One"

    # Stranger: existence not leaked through any handler.
    for status, body in (api.get_list(bid, STRANGER),
                         api.get_one(bid, STRANGER, v1),
                         api.get_draft_status(bid, STRANGER),
                         api.post_publish(bid, STRANGER, None, context=_ctx())):
        assert status == 404 and body["code"] == "not_found"


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_versioning_through_controller,
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
