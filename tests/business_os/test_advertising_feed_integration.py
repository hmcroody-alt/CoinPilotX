"""Advertising Stage 2 — Part 7: production Feed/Reels → canonical delivery.

bot.py's live ``/api/pulse/feed`` and ``/api/pulse/reels/feed`` responses attach ONE
canonical sponsored placement through the module-level helper
``_bo_ad_attach_sponsored``. bot.py is not importable in the hermetic sandbox, so
this suite extracts that helper's REAL source out of bot.py via AST and execs it
against the canonical delivery service and a fully delivery-ready hierarchy (reusing
the slice-7 builders). It proves the strangler contract the live feed depends on:

  * flag OFF ⇒ the organic response is left byte-for-byte unchanged (no ``sponsored``
    key) and nothing is raised;
  * an eligible campaign ⇒ the helper injects the delivery pipeline's already
    CLIENT-SAFE payload under ``sponsored`` + ``sponsored_placement``, and never
    mutates the organic keys (posts / reels / next_offset survive intact);
  * the reels placement serves a reels creative on the reels response;
  * a raising delivery layer can NEVER break the feed — the helper swallows and
    returns the organic response untouched;
  * a non-dict response is returned unchanged.

    python tests/business_os/test_advertising_feed_integration.py   # no pytest needed
"""

import os
import sys
import ast
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.dirname(__file__))

# Importing the slice-7 delivery suite establishes the temp DATABASE_URL + flag and
# gives us its vetted builders for a delivery-ready canonical hierarchy.
import test_advertising_slice7_delivery as s7  # noqa: E402
from services.business_os.advertising import delivery as deliv  # noqa: E402


_BOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "bot.py"))


def _extract_helper():
    """Pull the REAL _bo_ad_attach_sponsored source out of bot.py and exec it in a
    namespace with just the globals it references (request, logging, the flag
    checker). We test bot.py's own code path, not a hand-copied twin."""
    src = open(_BOT).read()
    tree = ast.parse(src)
    node = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "_bo_ad_attach_sponsored")
    fn_src = ast.get_source_segment(src, node)

    class _Headers:
        @staticmethod
        def get(_k):
            return None

    class _Req:
        headers = _Headers()

    def _flag_on():
        return (os.getenv("BUSINESS_OS_ADVERTISING", "") or "").strip().lower() in (
            "1", "true", "on", "yes", "enabled", "canonical")

    ns = {"logging": logging, "request": _Req, "_business_os_advertising_enabled": _flag_on}
    exec(fn_src, ns)
    return ns["_bo_ad_attach_sponsored"]


_attach = _extract_helper()


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


# 1 -- flag OFF: organic response untouched, nothing raised -------------------
def test_flag_off_leaves_feed_unchanged():
    os.environ["BUSINESS_OS_ADVERTISING"] = "off"
    try:
        result = {"ok": True, "posts": [{"id": 1}], "next_offset": 0}
        out = _attach(999001, "feed", result)
        _assert(out is result, "helper returns the same dict")
        _assert("sponsored" not in result, ("flag-off must not inject", result))
        _assert(result["posts"] == [{"id": 1}], "organic posts untouched")
    finally:
        os.environ["BUSINESS_OS_ADVERTISING"] = "on"


# 2 -- eligible campaign: client-safe sponsored injected, organic intact ------
def test_eligible_injects_client_safe_sponsored():
    s7._ready_feed()  # one approved Feed (image) creative in the canonical hierarchy
    result = {"ok": True, "posts": [{"id": 7}], "next_offset": 3, "has_more": True}
    _attach(730001, "feed", result)
    # organic keys survive exactly
    _assert(result["posts"] == [{"id": 7}], ("organic posts mutated", result))
    _assert(result["next_offset"] == 3 and result["has_more"] is True, result)
    sp = result.get("sponsored")
    _assert(sp is not None, ("expected a sponsored placement injected", result))
    _assert(sp.get("sponsored") is True and sp.get("sponsored_label") == "Sponsored", sp)
    _assert(result.get("sponsored_placement") == "feed", result)
    _assert(str(sp.get("delivery_id", "")).startswith("adlv_"), sp)
    # client-safe: no internal ledger/targeting/identity fields leak into the feed
    for banned in ("advertiser_user_id", "campaign_id", "ad_set_id", "subject_ref",
                   "eligibility_snapshot_json", "budget_cents", "escrow", "price"):
        _assert(banned not in sp, f"sponsored leaked internal field {banned!r}: {list(sp)}")


# 3 -- reels placement serves a reels creative on the reels response ----------
def test_reels_placement_injected():
    owner = s7._new_owner()
    cid = s7._active_campaign(owner)
    asid = s7._approved_ad_set(owner, cid, placements=("reels",))
    s7._approved_creative(owner, asid, creative_type="reels_video")
    result = {"ok": True, "reels": [{"id": 9}], "next_offset": 0}
    _attach(730002, "reels", result)
    _assert(result["reels"] == [{"id": 9}], ("organic reels mutated", result))
    sp = result.get("sponsored")
    _assert(sp is not None and sp.get("placement") == "reels", ("reels ad expected", result))
    _assert(result.get("sponsored_placement") == "reels", result)


# 4 -- a raising delivery layer can never break the feed ----------------------
def test_delivery_failure_never_breaks_feed():
    orig = deliv.request_placement

    def _boom(*_a, **_k):
        raise RuntimeError("delivery exploded")

    deliv.request_placement = _boom
    try:
        result = {"ok": True, "posts": ["x", "y"], "next_offset": 2}
        out = _attach(730003, "feed", result)
        _assert(out is result, "helper returns the organic dict")
        _assert("sponsored" not in result, ("failure must not inject", result))
        _assert(result["posts"] == ["x", "y"], "organic feed survived a delivery crash")
    finally:
        deliv.request_placement = orig


# 5 -- non-dict responses are returned unchanged ------------------------------
def test_non_dict_result_unchanged():
    _assert(_attach(1, "feed", None) is None, "None passthrough")
    payload = ["not", "a", "dict"]
    _assert(_attach(1, "feed", payload) is payload, "list passthrough")


def _run_standalone():
    s7.setup_module()
    tests = [
        test_flag_off_leaves_feed_unchanged,
        test_eligible_injects_client_safe_sponsored,
        test_reels_placement_injected,
        test_delivery_failure_never_breaks_feed,
        test_non_dict_result_unchanged,
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
