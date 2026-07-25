"""Advertising slice 5 — bot.py operational route-wiring structural checks.

bot.py cannot be imported in the hermetic sandbox (stripe/flask/telegram + no
PyPI), so the request-time guards on the new operational adapters —
authentication, CSRF on writes, the owner RBAC guard, the flag-off dark
behaviour, and the administrative audit trail on admin interventions — are
verified here by parsing the bot.py source and asserting each new canonical
operational route wires the required guard. The decision logic itself is covered
in-process by test_advertising_slice5_api.py.

    python tests/business_os/test_advertising_slice5_routes.py   # no pytest needed
"""

import ast
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

_BOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "bot.py"))
SRC = open(_BOT, encoding="utf-8").read()

# Advertiser operational routes -> substrings that MUST appear in the body.
# get_operational is a read: flag gate + auth only.
# schedule/activate/pause/resume/cancel are owned writes: flag + auth + write CSRF.
ADVERTISER_READ_FUNCS = {
    "api_business_os_advertising_get_operational": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required"],
}
ADVERTISER_WRITE_FUNCS = {
    "api_business_os_advertising_schedule_campaign": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required",
        "pulse_ads_verify_write"],
    "api_business_os_advertising_activate_campaign": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required",
        "pulse_ads_verify_write"],
    "api_business_os_advertising_pause_campaign": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required",
        "pulse_ads_verify_write"],
    "api_business_os_advertising_resume_campaign": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required",
        "pulse_ads_verify_write"],
    "api_business_os_advertising_cancel_campaign": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required",
        "pulse_ads_verify_write"],
}

# Admin operational reads -> owner guard + flag gate, read-only (no audit write).
ADMIN_READ_FUNCS = {
    "admin_business_os_advertising_list_operations": [
        "require_owner_api", "_business_os_advertising_enabled"],
    "admin_business_os_advertising_get_operational": [
        "require_owner_api", "_business_os_advertising_enabled"],
}
# Admin operational interventions -> owner guard + flag gate + CSRF + audit write.
ADMIN_WRITE_FUNCS = {
    "admin_business_os_advertising_op_pause": [
        "require_owner_api", "_business_os_advertising_enabled",
        "_business_os_ent_csrf_ok", "log_admin_audit"],
    "admin_business_os_advertising_op_cancel": [
        "require_owner_api", "_business_os_advertising_enabled",
        "_business_os_ent_csrf_ok", "log_admin_audit"],
    "admin_business_os_advertising_op_complete": [
        "require_owner_api", "_business_os_advertising_enabled",
        "_business_os_ent_csrf_ok", "log_admin_audit"],
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


# 1 -- bot.py still parses after the slice-5 edits --------------------------
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
        # read-only: no state-mutating audit write
        _assert("log_admin_audit" not in src,
                f"{fn} must be read-only (no audit/state write)")


# 6 -- admin intervention routes: owner guard + flag + CSRF + AUDIT ----------
def test_admin_write_guards():
    for fn, required in ADMIN_WRITE_FUNCS.items():
        src = _func_source(TREE, fn)
        _assert(src is not None, f"missing admin route {fn}")
        for token in required:
            _assert(token in src, f"{fn} missing guard {token}")
        _assert("409" in src, f"{fn} must return 409 when flag off (post owner-guard)")
        # the administrative audit records the acting admin + campaign target
        _assert('admin["id"]' in src, f"{fn} must audit the acting admin id")
        _assert("request_ref" in src, f"{fn} must record a request reference")


# 7 -- new routes live under the canonical namespace; legacy intact ---------
def test_separate_from_legacy():
    for verb in ("operational", "schedule", "activate", "pause", "resume", "cancel"):
        path = f'/api/business-os/advertising/campaigns/<campaign_id>/{verb}"'
        _assert(path in SRC, f"missing canonical advertiser operational path {verb}")
    _assert('/admin/business-os/advertising/operations"' in SRC,
            "missing canonical admin operations list path")
    _assert('/admin/business-os/advertising/campaigns/<campaign_id>/operational"' in SRC,
            "missing canonical admin operational view path")
    for verb in ("pause", "cancel", "complete"):
        path = f'/admin/business-os/advertising/campaigns/<campaign_id>/operational/{verb}"'
        _assert(path in SRC, f"missing canonical admin operational {verb} path")
    # legacy pulse ads routes still present and untouched (separate namespace)
    _assert('"/api/pulse/ads/placements"' in SRC, "legacy pulse ads route must remain")


# 8 -- every new operational route delegates to the tested controller -------
def test_routes_delegate_to_controller():
    funcs = (list(ADVERTISER_READ_FUNCS) + list(ADVERTISER_WRITE_FUNCS)
             + list(ADMIN_READ_FUNCS) + list(ADMIN_WRITE_FUNCS))
    for fn in funcs:
        src = _func_source(TREE, fn)
        _assert("advertising import api" in src,
                f"{fn} must delegate to advertising.api")


# 9 -- no delivery / spend surface leaked into any operational route ---------
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
