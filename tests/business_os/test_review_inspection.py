"""§7 — the dossier a reviewer reads before deciding.

The queue answers "which listings need a decision". It cannot answer "should
this one be approved", because everything that question depends on lives in four
other tables: the gallery, the variants (and their supplier cost), the seller
record, and the safety scoring. A reviewer who has to open four admin pages to
judge one product will judge it from the title.

What this file is actually guarding
-----------------------------------
*The dossier must not acquire a verdict of its own.* It reports; ``block_reason``
decides. Two surfaces in this codebase have now shipped a second copy of the
"can this be approved" rule and had it disagree with the endpoint, so the
assertion here is that ``verdicts`` is ``block_reason`` evaluated per action —
not that it looks plausible.

*Unknown is not zero.* A missing ``cost_cents`` read as ``0`` reports a 100%
margin. It reports it on precisely the listings whose supplier data is thinnest,
so the rows the reviewer knows least about are the ones that look best.

*Gaps are advisory.* Almost every gap describes a listing that is approvable and
merely bad. Promoting them to blockers would fill the queue with rows no
reviewer is permitted to clear.

These are pure functions over dicts — no database, no app::

    ./.venv/bin/python3 -m pytest tests/business_os/test_review_inspection.py
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import marketplace_listing_lifecycle as lifecycle  # noqa: E402
from services.business_os.marketplace import listing_review as rv  # noqa: E402

NOW = datetime(2026, 9, 14, tzinfo=timezone.utc)
SELLER = 4101
REVIEWER = 4102


def listing(**overrides):
    row = {
        "id": 7001,
        "seller_user_id": SELLER,
        "title": "Brass desk lamp",
        "description": "A weighted brass lamp with a linen shade and a dimmer.",
        "category": "Home",
        "status": lifecycle.PENDING_REVIEW,
        "approval_status": lifecycle.PENDING_REVIEW,
        "product_type": "physical",
        "delivery_type": "standard_shipping",
        "estimated_delivery": "5-9 days",
        "quantity": 12,
        "price_label": "$24.00",
        "currency": "USD",
    }
    row.update(overrides)
    return row


def seller(**overrides):
    row = {
        "user_id": SELLER,
        "display_name": "Lamp Co",
        "status": "approved",
        "verification_status": "verified",
        "risk_score": 4,
    }
    row.update(overrides)
    return row


def variant(**overrides):
    row = {
        "variant_key": "default",
        "sku": "LAMP-1",
        "currency": "USD",
        "price_cents": 2400,
        "cost_cents": 900,
        "stock_quantity": 12,
        "stock_state": "in_stock",
        "stock_synced_at": (NOW - timedelta(hours=6)).isoformat(),
        "status": "active",
    }
    row.update(overrides)
    return row


def media(**overrides):
    row = {"media_type": "image", "media_url": "https://cdn.example/1.jpg",
           "is_cover": True, "moderation_status": "approved", "position": 0}
    row.update(overrides)
    return row


def dossier(**kwargs):
    kwargs.setdefault("reviewer_id", REVIEWER)
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("seller", seller())
    kwargs.setdefault("variants", [variant()])
    kwargs.setdefault("media", [media()])
    row = kwargs.pop("listing", None) or listing()
    return rv.inspection(row, **kwargs)


# -- the dossier does not decide anything --------------------------------------


def test_the_verdicts_are_block_reason_and_nothing_else():
    """Not "there is an approve key with a plausible value" — the same function.

    A detail page that computed its own eligibility would be the third surface in
    this codebase to hold a private copy of the approval rule, and the previous
    two both drifted before anyone noticed.
    """
    row = listing(seller_user_id=REVIEWER)
    result = rv.inspection(row, reviewer_id=REVIEWER, now=NOW)
    assert set(result["verdicts"]) == set(rv.ACTIONS)
    for action in rv.ACTIONS:
        assert result["verdicts"][action] == rv.block_reason(
            row, action, reviewer_id=REVIEWER)
    assert result["verdicts"][rv.APPROVE] == rv.SELF_REVIEW


def test_a_prohibited_product_is_still_rejectable_in_the_dossier():
    """§34. The same asymmetry the queue row has. If the dossier reported one
    flat "blocked", the detail page would grow buttons the endpoint disagrees
    with the moment somebody wires them off a single boolean."""
    result = dossier(listing=listing(title="Case of whisky", category="Alcohol"))
    assert result["verdicts"][rv.APPROVE] == rv.PROHIBITED
    assert result["verdicts"][rv.REJECT] is None


def test_a_bad_listing_is_full_of_gaps_and_still_approvable():
    """Gaps are advisory on purpose. A thin description is a reason to request
    changes, not a reason the server refuses to let a human decide."""
    result = rv.inspection(
        listing(description="ok", quantity=0), reviewer_id=REVIEWER, now=NOW,
        seller=seller(verification_status="", risk_score=90), variants=[], media=[])
    assert result["verdicts"][rv.APPROVE] is None
    assert {"NO_MEDIA", "THIN_DESCRIPTION", "NO_STOCK", "SELLER_UNVERIFIED",
            "SELLER_HIGH_RISK"} <= set(result["gaps"])


def test_a_missing_listing_is_reported_not_faked():
    assert rv.inspection(None, reviewer_id=REVIEWER) == {"found": False, "listing_id": 0}


# -- money: unknown is not zero -------------------------------------------------


def test_margin_is_computed_from_price_and_cost():
    economics = rv.variant_economics(variant(price_cents=2400, cost_cents=900))
    assert economics["margin_cents"] == 1500
    assert economics["margin_pct"] == 62.5


def test_an_unrecorded_cost_produces_an_unknown_margin_not_a_perfect_one():
    """The whole reason `_int_or_none` exists. `int(None or 0)` gives a 100%
    margin on every listing with no supplier data — which is every listing the
    reviewer has the least information about."""
    economics = rv.variant_economics(variant(cost_cents=None))
    assert economics["cost_cents"] is None
    assert economics["margin_cents"] is None
    assert economics["margin_pct"] is None

    result = dossier(variants=[variant(cost_cents=None)])
    assert "COST_UNKNOWN" in result["gaps"]
    assert result[rv.INTERNAL_SECTION]["cost_known"] is False
    assert result[rv.INTERNAL_SECTION]["min_margin_pct"] is None


def test_a_zero_cost_is_a_real_zero_and_not_confused_with_missing():
    economics = rv.variant_economics(variant(cost_cents=0))
    assert economics["cost_cents"] == 0
    assert economics["margin_pct"] == 100.0
    assert "COST_UNKNOWN" not in dossier(variants=[variant(cost_cents=0)])["gaps"]


@pytest.mark.parametrize("price,cost", [(900, 900), (800, 900)])
def test_selling_at_or_under_cost_is_flagged(price, cost):
    """At cost is as much of a signal as under it: a dropship import priced to the
    cent of its supplier price is usually an import that never got priced."""
    result = dossier(variants=[variant(price_cents=price, cost_cents=cost)])
    assert "SELLING_BELOW_COST" in result["gaps"]


def test_a_free_product_does_not_divide_by_zero():
    economics = rv.variant_economics(variant(price_cents=0, cost_cents=0))
    assert economics["margin_cents"] == 0
    assert economics["margin_pct"] is None


def test_price_range_spans_the_variants():
    result = dossier(variants=[variant(price_cents=2400), variant(price_cents=9900)])
    internal = result[rv.INTERNAL_SECTION]
    assert internal["min_price_cents"] == 2400
    assert internal["max_price_cents"] == 9900
    assert internal["variant_count"] == 2


def test_the_worst_margin_is_the_one_reported():
    """A dossier that averaged would hide one loss-making variant behind four
    healthy ones, and the loss-making variant is the entire reason to look."""
    result = dossier(variants=[variant(price_cents=2400, cost_cents=200),
                               variant(price_cents=2400, cost_cents=2300)])
    assert result[rv.INTERNAL_SECTION]["min_margin_cents"] == 100


# -- §43: the cost data is namespaced so it cannot be leaked by accident --------


def test_everything_priced_from_supplier_data_sits_under_one_key():
    """§43. Supplier cost is legitimate here and forbidden on any merchant- or
    buyer-facing payload. Spread across sibling fields it has to be remembered
    field by field; under one key a leak is a grep."""
    result = dossier()
    internal = result.pop(rv.INTERNAL_SECTION)
    assert internal["variants"][0]["cost_cents"] == 900

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert "cost" not in str(key).lower(), f"cost outside the internal section: {key}"
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(result)


# -- supplier sync --------------------------------------------------------------


def test_a_variant_that_never_synced_is_not_reported_as_fresh():
    result = dossier(variants=[variant(stock_synced_at="")])
    assert "SUPPLIER_NEVER_SYNCED" in result["gaps"]
    assert "SUPPLIER_STALE" not in result["gaps"]


def test_a_stale_sync_is_flagged_and_a_recent_one_is_not():
    stale = (NOW - timedelta(days=rv.SUPPLIER_STALE_DAYS + 1)).isoformat()
    assert "SUPPLIER_STALE" in dossier(variants=[variant(stock_synced_at=stale)])["gaps"]
    fresh = (NOW - timedelta(days=1)).isoformat()
    assert "SUPPLIER_STALE" not in dossier(variants=[variant(stock_synced_at=fresh)])["gaps"]


def test_the_freshest_variant_decides_staleness():
    """One variant syncing keeps the supplier link alive. Reporting stale because
    a retired variant has not been touched in a year is a false alarm the
    reviewer learns to ignore, which costs the real ones."""
    old = (NOW - timedelta(days=400)).isoformat()
    new = (NOW - timedelta(hours=2)).isoformat()
    result = dossier(variants=[variant(stock_synced_at=old), variant(stock_synced_at=new)])
    assert "SUPPLIER_STALE" not in result["gaps"]


def test_an_unparseable_timestamp_reads_as_never_synced_not_as_now():
    result = dossier(variants=[variant(stock_synced_at="last tuesday")])
    assert "SUPPLIER_NEVER_SYNCED" in result["gaps"]


# -- gallery --------------------------------------------------------------------


def test_media_is_counted_by_what_a_buyer_will_actually_see():
    """"Has 8 images" and "has 8 images, 3 of which are rejected" are different
    products, and only one of them is worth approving."""
    gallery = rv.media_summary([
        media(is_cover=True),
        media(is_cover=False, position=1, moderation_status="pending"),
        media(is_cover=False, position=2, moderation_status="rejected"),
    ])
    assert gallery["total"] == 3
    assert (gallery["approved"], gallery["pending"], gallery["rejected"]) == (1, 1, 1)
    assert gallery["has_cover"] is True


def test_the_cover_sorts_first_regardless_of_position():
    gallery = rv.media_summary([media(is_cover=False, position=0),
                                media(is_cover=True, position=9)])
    assert gallery["items"][0]["is_cover"] is True


def test_a_listing_with_no_media_at_all_is_flagged_once():
    result = dossier(media=[])
    assert "NO_MEDIA" in result["gaps"]
    # Not also NO_COVER — one listing, one problem, one line for the reviewer.
    assert "NO_COVER" not in result["gaps"]


def test_a_legacy_cover_url_counts_as_a_cover():
    """Older listings carry the cover on the listing row rather than in the media
    table. Flagging those as coverless would put a gap on most of the catalogue,
    and a gap that is on everything is read by nobody."""
    result = dossier(listing=listing(cover_image_url="https://cdn.example/c.jpg"),
                     media=[media(is_cover=False)])
    assert "NO_COVER" not in result["gaps"]


# -- seller standing and safety -------------------------------------------------


def test_seller_standing_comes_from_the_seller_record():
    result = dossier(seller=seller(status="suspended", risk_score=71))
    assert result["seller"]["status"] == "suspended"
    assert {"SELLER_NOT_APPROVED", "SELLER_HIGH_RISK"} <= set(result["gaps"])


def test_a_missing_seller_record_does_not_crash_the_dossier():
    result = dossier(seller=None)
    assert result["seller"]["user_id"] is None
    assert "SELLER_UNVERIFIED" in result["gaps"]


def test_safety_flags_survive_both_json_shapes():
    as_list = rv.safety_signals(listing(safety_flags_json='["weapon_terms"]'))
    assert as_list["flags"] == ["weapon_terms"]
    as_map = rv.safety_signals(listing(safety_flags_json='{"weapon_terms": true, "ok": false}'))
    assert as_map["flags"] == ["weapon_terms"]
    assert rv.safety_signals(listing(safety_flags_json="not json"))["flags"] == []


def test_the_dossier_names_the_policy_that_refused_the_approval():
    """Being told "no" twice is not an explanation. If Approve is blocked by the
    goods policy the reviewer needs to see which rule, not just that a rule."""
    result = dossier(listing=listing(title="Case of whisky", category="Alcohol"))
    assert result["verdicts"][rv.APPROVE] == rv.PROHIBITED
    assert result["safety"]["policy_decision"] == "PROHIBITED"
    # Which rule, and which revision of it — a policy verdict with no version is
    # unarguable after the fact when the seller appeals.
    assert result["safety"]["policy_reason"] == "alcohol"
    assert result["safety"]["policy_version"]


def test_the_policy_section_is_read_as_a_mapping_not_as_an_object():
    """`marketplace_goods_policy.evaluate` returns a dict. `getattr(v, "decision",
    "")` on it compiles, never raises, and returns "" for every listing — a
    permanently blank policy panel that no test asserting "the key exists" would
    ever catch."""
    signals = rv.safety_signals(listing(title="Case of whisky", category="Alcohol"))
    assert signals["policy_decision"] == "PROHIBITED"
    assert rv.safety_signals(listing())["policy_decision"] == "ALLOWED"


# -- what approving would actually achieve --------------------------------------


def test_the_dossier_says_in_advance_whether_approving_makes_it_visible():
    """§37 asks the question after the write. A reviewer about to approve a
    listing from a suspended seller should not have to make the decision to find
    out it achieves nothing."""
    healthy = dossier()["publication_if_approved"]
    assert healthy["live"] is True

    stuck = rv.inspection(listing(quantity=0), reviewer_id=REVIEWER, now=NOW,
                          seller=seller(), variants=[], media=[media()])
    assert stuck["publication_if_approved"]["live"] is False
    assert stuck["publication_if_approved"]["note"]


def test_the_preview_does_not_mutate_the_listing_it_was_given():
    row = listing()
    rv.inspection(row, reviewer_id=REVIEWER, now=NOW)
    assert row["status"] == lifecycle.PENDING_REVIEW
    assert row["approval_status"] == lifecycle.PENDING_REVIEW


# -- every gap the code can emit has a sentence ---------------------------------


def test_no_gap_can_reach_a_reviewer_without_an_explanation():
    """A bare code in the UI is an unexplained red mark, which is worse than
    silence: the reviewer cannot act on it and cannot dismiss it either."""
    import re

    source = open(rv.__file__, encoding="utf-8").read()
    emitted = set(re.findall(r'flag\("([A-Z_]+)"\)', source))
    assert emitted, "the gap detector was refactored — this test now checks nothing"
    assert emitted <= set(rv.GAP_NOTES), sorted(emitted - set(rv.GAP_NOTES))
    for code in emitted:
        assert rv.GAP_NOTES[code].strip().endswith("."), code
