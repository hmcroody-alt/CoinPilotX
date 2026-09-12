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


def _draft_with(uid, *, fulfillment, inventory_qty):
    """A draft satisfying every field requirement, parameterised on the two
    fields the catalog engine has an opinion about beyond the field list."""
    did = ld.create_draft(uid, context=_ctx())["draft_id"]
    ld.update_section(did, uid, "identity", {"title": "Lamp"}, context=_ctx())
    ld.update_section(did, uid, "media", {"items": ["r2:img1"]}, context=_ctx())
    ld.update_section(did, uid, "offer", {"price_cents": 2500}, context=_ctx())
    ld.update_section(did, uid, "fulfillment", {"fulfillment_type": fulfillment},
                      context=_ctx())
    if inventory_qty is not None:
        ld.update_section(did, uid, "inventory", {"inventory_qty": inventory_qty},
                          context=_ctx())
    ld.update_section(did, uid, "compliance", {"acknowledged": True}, context=_ctx())
    return did


def test_the_checklist_and_publish_agree_about_every_draft():
    """The checklist promises honesty. Honesty is not a property of its output,
    it is agreement with the verb it is describing -- so this asks both.

    Deliberately NOT a restatement of the inventory rule. It never says what
    the answer should be for a given quantity; it says the two answers have to
    match. A test that repeated the rule would pass against a checklist that
    repeated the rule too, which is exactly how the zero case shipped: the
    checklist tested `is None`, the verb tested `(x or 0) <= 0`, and a truthful
    zero was called complete and then refused.
    """
    _approve(SELLER)
    cases = [
        ("physical", None, "a seller who never answered the stock question"),
        ("physical", 0, "a seller who answered it truthfully with none left"),
        ("physical", 4, "a seller with stock on hand"),
        ("digital", None, "a download, which needs no stock at all"),
    ]
    for fulfillment, qty, described_as in cases:
        did = _draft_with(SELLER, fulfillment=fulfillment, inventory_qty=qty)
        promised = ld.get_draft(did, SELLER)["completeness"]
        try:
            ld.publish_draft(did, SELLER, context=_ctx())
            refused = None
        except MarketplaceError as exc:
            refused = exc.code
        assert promised["ready"] is (refused is None), (
            f"{described_as}: the checklist said ready={promised['ready']} and "
            f"publish said {refused or 'OK'}. A checklist that disagrees with "
            f"the verb is worse than no checklist, because the seller acts on it")

    # And the specific case that was broken, pinned by name so a regression
    # reads as itself rather than as an arithmetic surprise.
    did = _draft_with(SELLER, fulfillment="physical", inventory_qty=0)
    assert ld.get_draft(did, SELLER)["completeness"] == {
        "ready": False, "missing": ["inventory.inventory_qty"]}


def test_the_checklist_asks_the_engine_rather_than_restating_it():
    """The guard against the same defect coming back in a different rule.

    The two tests above would still pass if someone re-inlined the inventory
    rule into `_completeness`, because the rule would agree with itself. What
    must hold is stronger: the checklist has no opinion of its own, it reports
    whatever the publish verb refuses. So invent a refusal the checklist has
    never heard of and require it to appear.
    """
    _approve(SELLER)
    did = _draft_with(SELLER, fulfillment="physical", inventory_qty=4)
    assert ld.get_draft(did, SELLER)["completeness"]["ready"] is True

    real = mkt.publish_blockers
    try:
        mkt.publish_blockers = lambda **_: ["a_rule_invented_by_this_test"]
        verdict = ld.get_draft(did, SELLER)["completeness"]
    finally:
        mkt.publish_blockers = real

    assert verdict == {"ready": False, "missing": ["a_rule_invented_by_this_test"]}, (
        "the checklist has to follow the engine even for a refusal it has no "
        "label for -- an unmapped code travels as itself rather than being "
        "dropped, because dropping it would report a draft ready that publish "
        f"will refuse. Got {verdict}")

    # Restoring the engine restores the verdict: the checklist cached nothing.
    assert ld.get_draft(did, SELLER)["completeness"]["ready"] is True


def test_the_engine_itself_refuses_a_physical_product_with_no_stock():
    """The cost of unifying the two authorities, paid deliberately.

    Once the checklist asks the engine, the two can no longer disagree -- which
    also means a wrong answer in the engine is echoed by the checklist instead
    of being contradicted by it. The agreement test above would stay green if
    `publish_blockers` started allowing zero stock, because both sides would
    move together and a product with nothing behind it would go live.

    So the rule gets pinned directly here. Restating a rule is only worthless
    when the thing under test is a *forecast* of it; the engine IS the rule, and
    an oracle for the rule is the honest way to hold it.
    """
    assert mkt.publish_blockers(fulfillment_type="physical", inventory_qty=0) == \
        ["no_inventory"], "nothing on the shelf cannot be offered for sale"
    assert mkt.publish_blockers(fulfillment_type="physical", inventory_qty=None) == \
        ["no_inventory"], "an unanswered stock question is not a yes"
    assert mkt.publish_blockers(fulfillment_type="physical", inventory_qty=1) == []
    assert mkt.publish_blockers(fulfillment_type="digital", inventory_qty=None) == [], \
        "a download has no shelf to be empty"

    # And the verb enforces it, not just the predicate: a product that reached
    # draft with zero stock cannot be transitioned live.
    _approve(SELLER)
    product = mkt.create_product(SELLER, title="Lamp", price_cents=2500,
                                 fulfillment_type="physical", inventory_qty=0,
                                 context=_ctx())
    exc = _expect("no_inventory",
                  lambda: mkt.transition_product(SELLER, product["product_id"],
                                                 "publish", context=_ctx()))
    assert str(exc) == mkt.PUBLISH_BLOCKER_MESSAGES["no_inventory"], (
        "the verb has to raise the sentence the code maps to, or a caller "
        "holding only the code cannot render what the verb would have said")


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_eligibility_fails_fast,
        test_section_writes_and_completeness,
        test_publish_paths,
        test_discard_and_lists,
        test_the_checklist_and_publish_agree_about_every_draft,
        test_the_checklist_asks_the_engine_rather_than_restating_it,
        test_the_engine_itself_refuses_a_physical_product_with_no_stock,
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
