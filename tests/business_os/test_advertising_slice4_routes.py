"""Advertising slice 4 — bot.py funding route-wiring structural checks.

bot.py cannot be imported in the hermetic sandbox (stripe/flask/telegram + no
PyPI), so the request-time guards on the new funding adapters — authentication,
CSRF on writes, the owner RBAC guard, and the flag-off dark behaviour — are
verified here by parsing the bot.py source and asserting each new canonical
funding route wires the required guard. The decision logic itself is covered
in-process by test_advertising_slice4_api.py.

    python tests/business_os/test_advertising_slice4_routes.py   # no pytest needed
"""

import ast
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

_BOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "bot.py"))
SRC = open(_BOT, encoding="utf-8").read()

# Advertiser funding routes -> substrings that MUST appear in the body.
# get_funding is a read: flag gate + auth only.
# set_budget/reserve/release are owned writes: flag gate + auth + write CSRF.
ADVERTISER_READ_FUNCS = {
    "api_business_os_advertising_get_funding": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required"],
}
ADVERTISER_WRITE_FUNCS = {
    "api_business_os_advertising_set_budget": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required",
        "pulse_ads_verify_write"],
    "api_business_os_advertising_reserve_funds": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required",
        "pulse_ads_verify_write"],
    "api_business_os_advertising_release_funds": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required",
        "pulse_ads_verify_write"],
}

# Admin funding routes -> substrings that MUST appear in the body.
# Both are read-only: owner guard + flag gate. No new reconcile route, no audit
# write (admins can never fabricate balances here).
ADMIN_FUNCS = {
    "admin_business_os_advertising_list_funding": [
        "require_owner_api", "_business_os_advertising_enabled"],
    "admin_business_os_advertising_get_funding": [
        "require_owner_api", "_business_os_advertising_enabled"],
}


def _func_source(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SRC, node)
    return None


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


TREE = ast.parse(SRC)


# 1 -- bot.py still parses after the slice-4 edits --------------------------
def test_bot_parses():
    _assert(TREE is not None, "bot.py failed to parse")


# 2 -- advertiser read route: auth + flag gate, owner from session ----------
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
        # owner identity is derived from the authed user, never from request body
        _assert('user.get("user_id")' in src,
                f"{fn} must derive owner from session user")
        # funding writes never touch the legacy service or the unsafe ad-wallet path
        _assert("pulse_ads_service" not in src,
                f"{fn} must not touch legacy pulse_ads_service")


# 4 -- flag-off dark behaviour: advertiser routes 404 when the flag is off --
def test_advertiser_routes_dark_when_off():
    for fn in list(ADVERTISER_READ_FUNCS) + list(ADVERTISER_WRITE_FUNCS):
        src = _func_source(TREE, fn)
        # the flag gate returns a 404 (dark) rather than leaking a 409/403
        _assert("404" in src, f"{fn} must return 404 when flag off (dark)")


# 5 -- admin funding routes: owner guard + flag gate, read-only -------------
def test_admin_funding_guards():
    for fn, required in ADMIN_FUNCS.items():
        src = _func_source(TREE, fn)
        _assert(src is not None, f"missing admin route {fn}")
        for token in required:
            _assert(token in src, f"{fn} missing guard {token}")
        # admin routes gate the flag AFTER the owner guard -> 409, not dark 404
        _assert("409" in src, f"{fn} must return 409 when flag off (post owner-guard)")
        # read-only: no new reconcile mechanism, no balance-fabricating audit write
        _assert("log_admin_audit" not in src,
                f"{fn} must be read-only (no audit/state write)")


# 6 -- new routes live under the canonical namespace; legacy intact ---------
def test_separate_from_legacy():
    for verb in ("funding", "budget", "reserve", "release"):
        path = f'/api/business-os/advertising/campaigns/<campaign_id>/{verb}"'
        _assert(path in SRC, f"missing canonical advertiser funding path {verb}")
    _assert('/admin/business-os/advertising/funding"' in SRC,
            "missing canonical admin funding list path")
    _assert('/admin/business-os/advertising/campaigns/<campaign_id>/funding"' in SRC,
            "missing canonical admin funding view path")
    # legacy pulse ads routes still present and untouched (separate namespace)
    _assert('"/api/pulse/ads/placements"' in SRC, "legacy pulse ads route must remain")


# 7 -- every new funding route delegates to the tested controller -----------
def test_routes_delegate_to_controller():
    funcs = (list(ADVERTISER_READ_FUNCS) + list(ADVERTISER_WRITE_FUNCS)
             + list(ADMIN_FUNCS))
    for fn in funcs:
        src = _func_source(TREE, fn)
        _assert("advertising import api" in src,
                f"{fn} must delegate to advertising.api")


# 8 -- no new reconcile route was added under the funding surface -----------
def test_no_new_reconcile_route():
    # reconciliation must reuse the existing protected mechanism; the funding
    # slice must not introduce a route that lets admins fabricate balances.
    _assert('/admin/business-os/advertising/funding/reconcile"' not in SRC,
            "slice 4 must not add a new funding reconcile route")
    _assert('/funding/reconcile"' not in SRC,
            "slice 4 must not add a new funding reconcile route")


# --- standalone runner -----------------------------------------------------
def _run_standalone():
    tests = [
        test_bot_parses,
        test_advertiser_read_guards,
        test_advertiser_write_guards,
        test_advertiser_routes_dark_when_off,
        test_admin_funding_guards,
        test_separate_from_legacy,
        test_routes_delegate_to_controller,
        test_no_new_reconcile_route,
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
