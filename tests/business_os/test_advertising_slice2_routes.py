"""Advertising slice 2 — bot.py route-wiring structural checks.

bot.py cannot be imported in the hermetic sandbox (stripe/flask/telegram + no
PyPI), so the request-time guards that live in the Flask adapters — authentication,
CSRF, the owner RBAC guard, the administrative audit call, and the flag-off dark
behaviour — are verified here by parsing the bot.py source and asserting each
canonical advertising route wires the required guard. The decision logic itself is
covered in-process by test_advertising_slice2_api.py.

This also asserts the canonical surface is a NEW, separate namespace that does not
redirect or replace the legacy pulse_ads portal.

    python tests/business_os/test_advertising_slice2_routes.py   # no pytest needed
"""

import ast
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

_BOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "bot.py"))
SRC = open(_BOT, encoding="utf-8").read()

# Advertiser route function -> substrings that MUST appear in its body.
ADVERTISER_FUNCS = {
    "api_business_os_advertising_eligibility": ["_business_os_advertising_enabled", "pulse_ads_api_user_required"],
    "api_business_os_advertising_register": ["_business_os_advertising_enabled", "pulse_ads_api_user_required", "pulse_ads_verify_write"],
    "api_business_os_advertising_create_campaign": ["_business_os_advertising_enabled", "pulse_ads_api_user_required", "pulse_ads_verify_write"],
    "api_business_os_advertising_list_campaigns": ["_business_os_advertising_enabled", "pulse_ads_api_user_required"],
    "api_business_os_advertising_get_campaign": ["_business_os_advertising_enabled", "pulse_ads_api_user_required"],
    "api_business_os_advertising_update_campaign": ["_business_os_advertising_enabled", "pulse_ads_api_user_required", "pulse_ads_verify_write"],
    "api_business_os_advertising_archive_campaign": ["_business_os_advertising_enabled", "pulse_ads_api_user_required", "pulse_ads_verify_write"],
    "api_business_os_advertising_restore_campaign": ["_business_os_advertising_enabled", "pulse_ads_api_user_required", "pulse_ads_verify_write"],
}

# Admin route function -> substrings that MUST appear in its body.
ADMIN_FUNCS = {
    "admin_business_os_advertising_list_advertisers": ["require_owner_api", "_business_os_advertising_enabled"],
    "admin_business_os_advertising_get_advertiser": ["require_owner_api", "_business_os_advertising_enabled"],
    "admin_business_os_advertising_set_advertiser_status": ["require_owner_api", "_business_os_advertising_enabled", "_business_os_ent_csrf_ok", "log_admin_audit"],
    "admin_business_os_advertising_list_campaigns": ["require_owner_api", "_business_os_advertising_enabled"],
    "admin_business_os_advertising_get_campaign": ["require_owner_api", "_business_os_advertising_enabled"],
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


# 1 -- bot.py parses (syntactically valid after the edit) --------------------
def test_bot_parses():
    _assert(TREE is not None, "bot.py failed to parse")


# 2 -- advertiser routes: auth guard (+ CSRF on writes) + flag gate ----------
def test_advertiser_route_guards():
    for fn, required in ADVERTISER_FUNCS.items():
        src = _func_source(TREE, fn)
        _assert(src is not None, f"missing advertiser route {fn}")
        for token in required:
            _assert(token in src, f"{fn} missing guard {token}")
        # owner identity is derived from the authed user, never from request body
        _assert("user.get(\"user_id\")" in src, f"{fn} must derive owner from session user")


# 3 -- admin routes: owner guard + flag gate (+ CSRF/audit on the writer) -----
def test_admin_route_guards():
    for fn, required in ADMIN_FUNCS.items():
        src = _func_source(TREE, fn)
        _assert(src is not None, f"missing admin route {fn}")
        for token in required:
            _assert(token in src, f"{fn} missing guard {token}")


# 4 -- the admin status writer records the full audit trail ------------------
def test_admin_status_audit_fields():
    src = _func_source(TREE, "admin_business_os_advertising_set_advertiser_status")
    for token in ("before_status", "after_status", "reason", "request_ref",
                  "business_os_advertiser_status"):
        _assert(token in src, f"admin status route missing audit field {token}")
    # actions are mapped server-side, not taken as a raw status
    _assert("_BO_AD_ADMIN_ACTION_TO_STATUS" in src, "admin status route must map action->status")


# 5 -- canonical surface is a NEW, separate namespace (no legacy hijack) ------
def test_separate_from_legacy():
    # canonical advertiser paths live under /api/business-os/advertising/
    _assert('"/api/business-os/advertising/campaigns"' in SRC, "missing canonical campaigns path")
    _assert('"/admin/business-os/advertising/advertisers"' in SRC, "missing canonical admin path")
    # legacy pulse ads routes still present and untouched (separate namespace)
    _assert('"/api/pulse/ads/placements"' in SRC, "legacy pulse ads route must remain")
    # canonical routes must not reference the legacy service module
    for fn in list(ADVERTISER_FUNCS) + list(ADMIN_FUNCS):
        src = _func_source(TREE, fn)
        _assert("pulse_ads_service" not in src, f"{fn} must not touch legacy pulse_ads_service")


# 6 -- every canonical route delegates to the tested controller --------------
def test_routes_delegate_to_controller():
    for fn in list(ADVERTISER_FUNCS) + list(ADMIN_FUNCS):
        src = _func_source(TREE, fn)
        _assert("advertising import api" in src, f"{fn} must delegate to advertising.api")


# --- standalone runner ------------------------------------------------------
def _run_standalone():
    tests = [
        test_bot_parses,
        test_advertiser_route_guards,
        test_admin_route_guards,
        test_admin_status_audit_fields,
        test_separate_from_legacy,
        test_routes_delegate_to_controller,
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
