"""Advertising slice 3 — bot.py route-wiring structural checks.

bot.py cannot be imported in the hermetic sandbox (stripe/flask/telegram + no
PyPI), so the request-time guards in the new lifecycle adapters — authentication,
CSRF, the owner RBAC guard, the administrative audit call, and the flag-off dark
behaviour — are verified here by parsing the bot.py source and asserting each new
canonical route wires the required guard. The decision logic itself is covered
in-process by test_advertising_slice3_api.py.

    python tests/business_os/test_advertising_slice3_routes.py   # no pytest needed
"""

import ast
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

_BOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "bot.py"))
SRC = open(_BOT, encoding="utf-8").read()

# New advertiser lifecycle routes -> substrings that MUST appear in the body.
# submit/withdraw/reopen are owned writes: flag gate + auth + write CSRF.
ADVERTISER_FUNCS = {
    "api_business_os_advertising_submit_campaign": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required",
        "pulse_ads_verify_write"],
    "api_business_os_advertising_withdraw_campaign": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required",
        "pulse_ads_verify_write"],
    "api_business_os_advertising_reopen_campaign": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required",
        "pulse_ads_verify_write"],
}

# New admin review route -> substrings that MUST appear in the body.
ADMIN_FUNCS = {
    "admin_business_os_advertising_review_campaign": [
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


# 1 -- bot.py still parses after the slice-3 edits ---------------------------
def test_bot_parses():
    _assert(TREE is not None, "bot.py failed to parse")


# 2 -- advertiser lifecycle routes: auth + write CSRF + flag gate ------------
def test_advertiser_lifecycle_guards():
    for fn, required in ADVERTISER_FUNCS.items():
        src = _func_source(TREE, fn)
        _assert(src is not None, f"missing advertiser route {fn}")
        for token in required:
            _assert(token in src, f"{fn} missing guard {token}")
        # owner identity is derived from the authed user, never from request body
        _assert('user.get("user_id")' in src,
                f"{fn} must derive owner from session user")
        # advertiser routes never touch the legacy service
        _assert("pulse_ads_service" not in src,
                f"{fn} must not touch legacy pulse_ads_service")


# 3 -- admin review route: owner guard + flag + CSRF + audit -----------------
def test_admin_review_guards():
    for fn, required in ADMIN_FUNCS.items():
        src = _func_source(TREE, fn)
        _assert(src is not None, f"missing admin route {fn}")
        for token in required:
            _assert(token in src, f"{fn} missing guard {token}")


# 4 -- the admin review writer records the full audit trail ------------------
def test_admin_review_audit_fields():
    src = _func_source(TREE, "admin_business_os_advertising_review_campaign")
    for token in ("decision", "before_status", "after_status", "reason",
                  "request_ref", "business_os_campaign_review"):
        _assert(token in src, f"admin review route missing audit field {token}")


# 5 -- new routes live under the canonical namespace; legacy intact ----------
def test_separate_from_legacy():
    for verb in ("submit", "withdraw", "reopen"):
        path = f'/api/business-os/advertising/campaigns/<campaign_id>/{verb}"'
        _assert(path in SRC, f"missing canonical advertiser path {verb}")
    _assert('/admin/business-os/advertising/campaigns/<campaign_id>/review"' in SRC,
            "missing canonical admin review path")
    # legacy pulse ads routes still present and untouched (separate namespace)
    _assert('"/api/pulse/ads/placements"' in SRC, "legacy pulse ads route must remain")


# 6 -- every new route delegates to the tested controller --------------------
def test_routes_delegate_to_controller():
    for fn in list(ADVERTISER_FUNCS) + list(ADMIN_FUNCS):
        src = _func_source(TREE, fn)
        _assert("advertising import api" in src,
                f"{fn} must delegate to advertising.api")


# --- standalone runner ------------------------------------------------------
def _run_standalone():
    tests = [
        test_bot_parses,
        test_advertiser_lifecycle_guards,
        test_admin_review_guards,
        test_admin_review_audit_fields,
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
