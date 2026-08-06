"""Business OS — Listing drafts HTTP controller, exercised DIRECTLY (no Flask).

Pins the (status, body) contract over the drafts engine:

  * DARK when BUSINESS_OS_MARKETPLACE is off — every handler 404;
  * bad bodies / unknown publish fields rejected; engine codes surface
    (invalid_section, incomplete 409, already_published 409);
  * full composer flow: create 201 → section PATCHes → publish 201 with the
    live product surfaced; discard path; foreign seller 404.

    python tests/business_os/test_listing_drafts_api.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_drafts_api_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as mkt  # noqa: E402
from services.business_os.marketplace import listing_drafts as ld  # noqa: E402
from services.business_os.marketplace import listing_drafts_api as api  # noqa: E402


SELLER = 2470
STRANGER = 2472
ADMIN = "admin:24"


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    mkt_schema.ensure_schema()
    ld.ensure_schema()
    for uid in (SELLER, STRANGER):
        mkt.upsert_seller(uid, display_name="S")
        mkt.set_seller_status(uid, "approved", actor=ADMIN)


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_MARKETPLACE"] = ""
    try:
        for status, body in (
            api.create(SELLER, context=_ctx()),
            api.get(SELLER, "x"),
            api.list_own(SELLER),
            api.update_section(SELLER, "x", "identity", {"title": "t"},
                               context=_ctx()),
            api.publish(SELLER, "x", context=_ctx()),
            api.discard(SELLER, "x", context=_ctx()),
        ):
            assert status == 404 and body["ok"] is False
    finally:
        os.environ["BUSINESS_OS_MARKETPLACE"] = "on"


def test_flow_and_codes_through_controller():
    status, body = api.create(SELLER, context=_ctx())
    assert status == 201 and body["draft"]["completeness"]["ready"] is False
    did = body["draft"]["draft_id"]

    status, body = api.update_section(SELLER, did, "identity", "nope",
                                      context=_ctx())
    assert status == 400 and body["code"] == "bad_body"
    status, body = api.update_section(SELLER, did, "seo", {"x": 1},
                                      context=_ctx())
    assert status == 400 and body["code"] == "invalid_section"
    status, body = api.publish(SELLER, did, {"go": True}, context=_ctx())
    assert status == 400 and body["code"] == "unknown_field"
    status, body = api.publish(SELLER, did, context=_ctx())
    assert status == 409 and body["code"] == "incomplete"

    for section, fields in (
        ("identity", {"title": "Chair"}),
        ("media", {"items": ["r2:a"]}),
        ("offer", {"price_cents": 4200}),
        ("fulfillment", {"fulfillment_type": "physical"}),
        ("inventory", {"inventory_qty": 3}),
        ("compliance", {"acknowledged": True}),
    ):
        status, body = api.update_section(SELLER, did, section, fields,
                                          context=_ctx())
        assert status == 200, (section, body)
    assert body["draft"]["completeness"]["ready"] is True

    status, body = api.publish(SELLER, did, context=_ctx())
    assert status == 201 and body["product"]["status"] == "active"
    assert body["product"]["price_cents"] == 4200
    status, body = api.publish(SELLER, did, context=_ctx())
    assert status == 409 and body["code"] == "already_published"

    # Foreign seller: existence not leaked.
    status, body = api.get(STRANGER, did)
    assert status == 404 and body["code"] == "not_found"

    # Discard path + role-scoped list.
    status, body = api.create(SELLER, context=_ctx())
    did2 = body["draft"]["draft_id"]
    status, body = api.discard(SELLER, did2, context=_ctx())
    assert status == 200 and body["draft"]["status"] == "discarded"
    status, body = api.list_own(SELLER, status="discarded")
    assert status == 200 and any(d["draft_id"] == did2 for d in body["drafts"])
    status, body = api.list_own(STRANGER)
    assert status == 200 and body["drafts"] == []


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_flow_and_codes_through_controller,
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
