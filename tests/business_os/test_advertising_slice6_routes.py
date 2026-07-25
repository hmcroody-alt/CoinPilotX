"""Advertising slice 6 — bot.py ad-set + creative route-wiring structural checks.

bot.py cannot be imported in the hermetic sandbox (stripe/flask/telegram + no
PyPI), so the request-time guards on the new ad-set/creative adapters —
authentication, CSRF on writes, the owner RBAC guard, the flag-off dark
behaviour, and the administrative audit trail on review decisions — are verified
here by parsing the bot.py source and asserting each new canonical route wires
the required guard. The decision logic itself is covered in-process by
test_advertising_slice6_api.py.

    python tests/business_os/test_advertising_slice6_routes.py   # no pytest needed
"""

import ast
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

_BOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "bot.py"))
SRC = open(_BOT, encoding="utf-8").read()

# Advertiser reads: flag gate + auth (owner from session), no write CSRF needed.
ADVERTISER_READ_FUNCS = {
    "api_business_os_advertising_list_ad_sets_for_campaign": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required"],
    "api_business_os_advertising_list_ad_sets": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required"],
    "api_business_os_advertising_get_ad_set": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required"],
    "api_business_os_advertising_list_creatives_for_ad_set": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required"],
    "api_business_os_advertising_list_creatives": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required"],
    "api_business_os_advertising_get_creative": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required"],
    "api_business_os_advertising_creative_readiness": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required"],
}
# Advertiser writes: flag gate + auth + write CSRF.
ADVERTISER_WRITE_FUNCS = {
    "api_business_os_advertising_create_ad_set": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required",
        "pulse_ads_verify_write"],
    "api_business_os_advertising_update_ad_set": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required",
        "pulse_ads_verify_write"],
    "api_business_os_advertising_ad_set_lifecycle": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required",
        "pulse_ads_verify_write"],
    "api_business_os_advertising_create_creative": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required",
        "pulse_ads_verify_write"],
    "api_business_os_advertising_update_creative": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required",
        "pulse_ads_verify_write"],
    "api_business_os_advertising_revise_creative": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required",
        "pulse_ads_verify_write"],
    "api_business_os_advertising_creative_lifecycle": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required",
        "pulse_ads_verify_write"],
}
# Admin reads: owner guard + flag gate, read-only (no audit write).
ADMIN_READ_FUNCS = {
    "admin_business_os_advertising_list_ad_sets": [
        "require_owner_api", "_business_os_advertising_enabled"],
    "admin_business_os_advertising_get_ad_set": [
        "require_owner_api", "_business_os_advertising_enabled"],
    "admin_business_os_advertising_list_creatives": [
        "require_owner_api", "_business_os_advertising_enabled"],
    "admin_business_os_advertising_get_creative": [
        "require_owner_api", "_business_os_advertising_enabled"],
    "admin_business_os_advertising_creative_readiness": [
        "require_owner_api", "_business_os_advertising_enabled"],
}
# Admin review writes: owner guard + flag gate + CSRF + AUDIT.
ADMIN_WRITE_FUNCS = {
    "admin_business_os_advertising_review_ad_set": [
        "require_owner_api", "_business_os_advertising_enabled",
        "_business_os_ent_csrf_ok", "log_admin_audit"],
    "admin_business_os_advertising_review_creative": [
        "require_owner_api", "_business_os_advertising_enabled",
        "_business_os_ent_csrf_ok", "log_admin_audit"],
}


TREE = ast.parse(SRC)

# Index FunctionDef NODES by name ONCE (cheap). bot.py is ~104k lines, so walking
# the whole tree per lookup is prohibitively slow; and computing a source segment
# for every function is slower still. We map name -> node up front, then slice the
# source lazily (and cache it) only for the handful of functions we assert on.
_FUNC_NODE = {}
for _node in ast.walk(TREE):
    if isinstance(_node, ast.FunctionDef) and _node.name not in _FUNC_NODE:
        _FUNC_NODE[_node.name] = _node

_SRC_LINES = SRC.splitlines(keepends=True)
_FUNC_SRC_CACHE = {}


def _func_source(tree, name):
    if name in _FUNC_SRC_CACHE:
        return _FUNC_SRC_CACHE[name]
    node = _FUNC_NODE.get(name)
    if node is None:
        _FUNC_SRC_CACHE[name] = None
        return None
    # Slice directly from the line buffer (fast) rather than ast.get_source_segment.
    src = "".join(_SRC_LINES[node.lineno - 1:node.end_lineno])
    _FUNC_SRC_CACHE[name] = src
    return src


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


# 1 -- bot.py still parses after the slice-6 edits --------------------------
def test_bot_parses():
    _assert(TREE is not None, "bot.py failed to parse")


# 2 -- advertiser read routes: auth + flag gate, owner from session ----------
def test_advertiser_read_guards():
    for fn, required in ADVERTISER_READ_FUNCS.items():
        src = _func_source(TREE, fn)
        _assert(src is not None, f"missing advertiser route {fn}")
        for token in required:
            _assert(token in src, f"{fn} missing guard {token}")
        _assert('user.get("user_id")' in src,
                f"{fn} must derive owner from session user")
        _assert("pulse_ads_service" not in src,
                f"{fn} must not touch legacy pulse_ads_service")


# 3 -- advertiser write routes: auth + write CSRF + flag gate ---------------
def test_advertiser_write_guards():
    for fn, required in ADVERTISER_WRITE_FUNCS.items():
        src = _func_source(TREE, fn)
        _assert(src is not None, f"missing advertiser route {fn}")
        for token in required:
            _assert(token in src, f"{fn} missing guard {token}")
        _assert('user.get("user_id")' in src,
                f"{fn} must derive owner from session user")
        _assert("pulse_ads_service" not in src,
                f"{fn} must not touch legacy pulse_ads_service")


# 4 -- flag-off dark behaviour: advertiser routes 404 when the flag is off --
def test_advertiser_routes_dark_when_off():
    for fn in list(ADVERTISER_READ_FUNCS) + list(ADVERTISER_WRITE_FUNCS):
        src = _func_source(TREE, fn)
        _assert("404" in src, f"{fn} must return 404 when flag off (dark)")


# 5 -- admin read routes: owner guard + flag gate, read-only ----------------
def test_admin_read_guards():
    for fn, required in ADMIN_READ_FUNCS.items():
        src = _func_source(TREE, fn)
        _assert(src is not None, f"missing admin route {fn}")
        for token in required:
            _assert(token in src, f"{fn} missing guard {token}")
        # admin routes gate the flag AFTER the owner guard -> 409, not dark 404
        _assert("409" in src, f"{fn} must return 409 when flag off (post owner-guard)")
        _assert("log_admin_audit" not in src,
                f"{fn} must be read-only (no audit/state write)")


# 6 -- admin review routes: owner guard + flag + CSRF + AUDIT ----------------
def test_admin_write_guards():
    for fn, required in ADMIN_WRITE_FUNCS.items():
        src = _func_source(TREE, fn)
        _assert(src is not None, f"missing admin route {fn}")
        for token in required:
            _assert(token in src, f"{fn} missing guard {token}")
        _assert("409" in src, f"{fn} must return 409 when flag off (post owner-guard)")
        # the administrative audit records the acting admin + a request reference
        _assert('admin["id"]' in src, f"{fn} must audit the acting admin id")
        _assert("request_ref" in src, f"{fn} must record a request reference")
        # reject decisions must carry a reason through to the audit trail
        _assert("reason" in src, f"{fn} must forward a review reason")


# 7 -- new routes live under the canonical namespace; legacy intact ---------
def test_separate_from_legacy():
    canonical = [
        '/api/business-os/advertising/campaigns/<campaign_id>/ad-sets"',
        '/api/business-os/advertising/ad-sets"',
        '/api/business-os/advertising/ad-sets/<ad_set_id>"',
        '/api/business-os/advertising/ad-sets/<ad_set_id>/update"',
        '/api/business-os/advertising/ad-sets/<ad_set_id>/<action>"',
        '/api/business-os/advertising/ad-sets/<ad_set_id>/creatives"',
        '/api/business-os/advertising/creatives"',
        '/api/business-os/advertising/creatives/<creative_id>"',
        '/api/business-os/advertising/creatives/<creative_id>/readiness"',
        '/api/business-os/advertising/creatives/<creative_id>/update"',
        '/api/business-os/advertising/creatives/<creative_id>/revise"',
        '/api/business-os/advertising/creatives/<creative_id>/<action>"',
        '/admin/business-os/advertising/ad-sets"',
        '/admin/business-os/advertising/ad-sets/<ad_set_id>"',
        '/admin/business-os/advertising/ad-sets/<ad_set_id>/review"',
        '/admin/business-os/advertising/creatives"',
        '/admin/business-os/advertising/creatives/<creative_id>"',
        '/admin/business-os/advertising/creatives/<creative_id>/readiness"',
        '/admin/business-os/advertising/creatives/<creative_id>/review"',
    ]
    for path in canonical:
        _assert(path in SRC, f"missing canonical route path {path}")
    # legacy pulse ads routes still present and untouched (separate namespace)
    _assert('"/api/pulse/ads/placements"' in SRC, "legacy pulse ads route must remain")


# 8 -- every new route delegates to the tested controller -------------------
def test_routes_delegate_to_controller():
    funcs = (list(ADVERTISER_READ_FUNCS) + list(ADVERTISER_WRITE_FUNCS)
             + list(ADMIN_READ_FUNCS) + list(ADMIN_WRITE_FUNCS))
    for fn in funcs:
        src = _func_source(TREE, fn)
        _assert("advertising import api" in src,
                f"{fn} must delegate to advertising.api")


# 9 -- no delivery / spend surface leaked into any slice-6 route ------------
def test_no_delivery_or_spend_in_routes():
    funcs = (list(ADVERTISER_READ_FUNCS) + list(ADVERTISER_WRITE_FUNCS)
             + list(ADMIN_READ_FUNCS) + list(ADMIN_WRITE_FUNCS))
    banned = ("post_entry", "impression", "auction", "reserve_funds",
              "release_funds", "deduct")
    for fn in funcs:
        src = _func_source(TREE, fn)
        for token in banned:
            _assert(token not in src,
                    f"{fn} must not deliver, spend, or move money ({token})")


# 10 -- advertiser has NO approve/review verb on the route surface ----------
def test_advertiser_cannot_review_via_routes():
    # the owner-only review endpoints are the ONLY places 'review' appears as a
    # path segment; there is no advertiser-facing approve/reject route.
    _assert(SRC.count('/ad-sets/<ad_set_id>/review"') == 1, "one ad-set review route")
    _assert(SRC.count('/creatives/<creative_id>/review"') == 1, "one creative review route")
    for fn in ("admin_business_os_advertising_review_ad_set",
               "admin_business_os_advertising_review_creative"):
        src = _func_source(TREE, fn)
        _assert("require_owner_api" in src, f"{fn} must be owner-guarded")


# --- standalone runner -----------------------------------------------------
def _run_standalone():
    tests = [
        test_bot_parses,
        test_advertiser_read_guards,
        test_advertiser_write_guards,
        test_advertiser_routes_dark_when_off,
        test_admin_read_guards,
        test_admin_write_guards,
        test_separate_from_legacy,
        test_routes_delegate_to_controller,
        test_no_delivery_or_spend_in_routes,
        test_advertiser_cannot_review_via_routes,
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
