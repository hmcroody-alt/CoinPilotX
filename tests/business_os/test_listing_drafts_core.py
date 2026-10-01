"""Business OS — Listing drafts engine (the add-listing backend).

Proves the composer's server side is honest and routes through the one
catalog engine:

  * DARK when BUSINESS_OS_MARKETPLACE is off;
  * eligibility fails FAST — an unapproved seller cannot even start a draft;
  * section writes: unknown section/field rejected, per-field validation at
    write time, completeness checklist tracks exactly the publish
    requirements;
  * publish: incomplete drafts 409 naming what is missing; a complete draft
    creates a REAL product via service.create_product and goes live via the
    publish verb; publish=False stops at a draft product; a draft publishes
    at most once; discarded drafts refuse everything;
  * scoping: a foreign seller's draft answers 404 (existence not leaked).

    python tests/business_os/test_listing_drafts_core.py   # no pytest needed
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_drafts_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_MARKETPLACE"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.marketplace import schema as mkt_schema  # noqa: E402
from services.business_os.marketplace import service as mkt  # noqa: E402
from services.business_os.marketplace import listing_drafts as ld  # noqa: E402
from services.business_os.marketplace.service import MarketplaceError  # noqa: E402


SELLER = 2370
STRANGER = 2372
ADMIN = "admin:23"


def _ctx(status="active", access=1):
    return {"account_status": status, "access_enabled": access}


def setup_module(module=None):
    mkt_schema.ensure_schema()
    ld.ensure_schema()


def _approve(uid):
    mkt.upsert_seller(uid, display_name="S")
    mkt.set_seller_status(uid, "approved", actor=ADMIN)


def _expect(code, fn):
    try:
        fn()
    except MarketplaceError as exc:
        assert exc.code == code, f"expected {code}, got {exc.code}"
        return exc
    raise AssertionError(f"expected MarketplaceError {code}")


def _complete_draft(uid, publish_media=True):
    d = ld.create_draft(uid, context=_ctx())
    did = d["draft_id"]
    ld.update_section(did, uid, "identity", {"title": "Lamp", "description": "warm"},
                      context=_ctx())
    if publish_media:
        ld.update_section(did, uid, "media", {"items": ["r2:img1", "r2:img2"]},
                          context=_ctx())
    ld.update_section(did, uid, "offer", {"price_cents": 2500}, context=_ctx())
    ld.update_section(did, uid, "fulfillment", {"fulfillment_type": "physical"},
                      context=_ctx())
    ld.update_section(did, uid, "inventory", {"inventory_qty": 4}, context=_ctx())
    ld.update_section(did, uid, "compliance", {"acknowledged": True}, context=_ctx())
    return did


# ---------------------------------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_MARKETPLACE"] = ""
    try:
        _expect("disabled", lambda: ld.create_draft(SELLER, context=_ctx()))
    finally:
        os.environ["BUSINESS_OS_MARKETPLACE"] = "on"


def test_eligibility_fails_fast():
    # No seller row at all -> cannot start the flow.
    _expect("seller_not_approved",
            lambda: ld.create_draft(9998, context=_ctx()))
    # Held account cannot start either.
    _approve(SELLER)
    _expect("account_hold",
            lambda: ld.create_draft(SELLER, context=_ctx(status="suspended")))


def test_section_writes_and_completeness():
    d = ld.create_draft(SELLER, context=_ctx())
    did = d["draft_id"]
    assert d["status"] == "in_progress"
    assert d["completeness"]["ready"] is False
    assert "identity.title" in d["completeness"]["missing"]

    _expect("invalid_section",
            lambda: ld.update_section(did, SELLER, "seo", {"x": 1}, context=_ctx()))
    _expect("unknown_field",
            lambda: ld.update_section(did, SELLER, "identity", {"slug": "x"},
                                      context=_ctx()))
    _expect("title_too_long",
            lambda: ld.update_section(did, SELLER, "identity",
                                      {"title": "x" * 200}, context=_ctx()))
    _expect("invalid_price",
            lambda: ld.update_section(did, SELLER, "offer",
                                      {"price_cents": -5}, context=_ctx()))
    _expect("invalid_media",
            lambda: ld.update_section(did, SELLER, "media",
                                      {"items": [1, 2]}, context=_ctx()))
    _expect("invalid_compliance",
            lambda: ld.update_section(did, SELLER, "compliance",
                                      {"acknowledged": "yes"}, context=_ctx()))

    d = ld.update_section(did, SELLER, "identity", {"title": "Lamp"},
                          context=_ctx())
    assert "identity.title" not in d["completeness"]["missing"]
    assert d["completeness"]["ready"] is False  # others still missing

    # Foreign seller: existence not leaked.
    _approve(STRANGER)
    _expect("not_found", lambda: ld.get_draft(did, STRANGER))
    _expect("not_found",
            lambda: ld.update_section(did, STRANGER, "identity",
                                      {"title": "hijack"}, context=_ctx()))


def test_publish_paths():
    # Incomplete -> 409 naming the gaps.
    d = ld.create_draft(SELLER, context=_ctx())
    exc = _expect("incomplete",
                  lambda: ld.publish_draft(d["draft_id"], SELLER, context=_ctx()))
    assert "offer.price_cents" in str(exc)

    # Complete -> live product through the real engine.
    did = _complete_draft(SELLER)
    out = ld.publish_draft(did, SELLER, context=_ctx())
    assert out["status"] == "published"
    pid = out["published_product_id"]
    assert out["product"]["product_id"] == pid
    assert out["product"]["status"] == "active"
    assert out["product"]["price_cents"] == 2500
    assert out["product"]["inventory_qty"] == 4
    # Publishes at most once.
    _expect("already_published",
            lambda: ld.publish_draft(did, SELLER, context=_ctx()))

    # publish=False stops at a draft product.
    did2 = _complete_draft(SELLER)
    out2 = ld.publish_draft(did2, SELLER, publish=False, context=_ctx())
    assert out2["product"]["status"] == "draft"

    # Audit trail exists for the draft lifecycle.
    conn = db.connect()
    actions = [r[0] for r in conn.execute(
        "SELECT action FROM business_os_mkt_audit WHERE subject_ref = ? "
        "ORDER BY id", (did,)).fetchall()]
    conn.close()
    assert actions == ["draft_create", "draft_publish"]


def test_discard_and_lists():
    did = ld.create_draft(SELLER, context=_ctx())["draft_id"]
    d = ld.discard_draft(did, SELLER, context=_ctx())
    assert d["status"] == "discarded"
    _expect("draft_not_editable",
            lambda: ld.update_section(did, SELLER, "identity", {"title": "x"},
                                      context=_ctx()))
    _expect("draft_not_editable",
            lambda: ld.publish_draft(did, SELLER, context=_ctx()))
    _expect("draft_not_editable",
            lambda: ld.discard_draft(did, SELLER, context=_ctx()))

    assert all(r["status"] == "in_progress"
               for r in ld.list_drafts(SELLER))
    assert any(r["draft_id"] == did
               for r in ld.list_drafts(SELLER, status="discarded"))
    _expect("invalid_status", lambda: ld.list_drafts(SELLER, status="weird"))


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_eligibility_fails_fast,
        test_section_writes_and_completeness,
        test_publish_paths,
        test_discard_and_lists,
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
