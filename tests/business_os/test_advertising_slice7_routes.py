"""Advertising slice 7 — delivery / impression / click route-wiring structural checks.

bot.py cannot be imported in the hermetic sandbox (stripe/flask/telegram + no
PyPI), so the request-time guards on the new delivery adapters — authentication,
CSRF on writes, the owner RBAC guard, the flag-off dark behaviour, and the
read-only nature of the admin visibility surface — are verified here by parsing
the bot.py source and asserting each new canonical route wires the required
guard. The decision logic itself is covered in-process by
test_advertising_slice7_delivery.py (service) and test_advertising_slice7_api.py
(controller).

    python tests/business_os/test_advertising_slice7_routes.py   # no pytest needed
"""

import ast
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

_BOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "bot.py"))
SRC = open(_BOT, encoding="utf-8").read()

# Viewer writes: flag gate (dark 404) + auth (owner from session) + write CSRF.
# All three delivery-surface viewer endpoints are POSTs that mutate/derive state.
VIEWER_WRITE_FUNCS = {
    "api_business_os_advertising_request_delivery": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required",
        "pulse_ads_verify_write"],
    "api_business_os_advertising_record_impression": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required",
        "pulse_ads_verify_write"],
    "api_business_os_advertising_record_click": [
        "_business_os_advertising_enabled", "pulse_ads_api_user_required",
        "pulse_ads_verify_write"],
}
# Admin reads: owner guard + flag gate, read-only (no audit / state write).
ADMIN_READ_FUNCS = {
    "admin_business_os_advertising_list_deliveries": [
        "require_owner_api", "_business_os_advertising_enabled"],
    "admin_business_os_advertising_get_delivery": [
        "require_owner_api", "_business_os_advertising_enabled"],
    "admin_business_os_advertising_list_impressions": [
        "require_owner_api", "_business_os_advertising_enabled"],
    "admin_business_os_advertising_list_clicks": [
        "require_owner_api", "_business_os_advertising_enabled"],
}


TREE = ast.parse(SRC)

# Index FunctionDef nodes by name once; bot.py is ~104k lines so walking per lookup
# is prohibitively slow. Map name -> node, then slice source lazily + cache it.
_FUNC_NODE = {}
for _node in ast.walk(TREE):
    if isinstance(_node, ast.FunctionDef) and _node.name not in _FUNC_NODE:
        _FUNC_NODE[_node.name] = _node

_SRC_LINES = SRC.splitlines(keepends=True)
_FUNC_SRC_CACHE = {}


def _func_source(name):
    if name in _FUNC_SRC_CACHE:
        return _FUNC_SRC_CACHE[name]
    node = _FUNC_NODE.get(name)
    if node is None:
        _FUNC_SRC_CACHE[name] = None
        return None
    src = "".join(_SRC_LINES[node.lineno - 1:node.end_lineno])
    _FUNC_SRC_CACHE[name] = src
    return src


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


# 1 -- bot.py still parses after the slice-7 edits --------------------------
def test_bot_parses():
    _assert(TREE is not None, "bot.py failed to parse")


# 2 -- viewer write routes: auth + write CSRF + flag gate -------------------
def test_viewer_write_guards():
    for fn, required in VIEWER_WRITE_FUNCS.items():
        src = _func_source(fn)
        _assert(src is not None, f"missing viewer route {fn}")
        for token in required:
            _assert(token in src, f"{fn} missing guard {token}")
        _assert('user.get("user_id")' in src,
                f"{fn} must derive viewer identity from session user")
        _assert("pulse_ads_service" not in src,
                f"{fn} must not touch legacy pulse_ads_service")


# 3 -- flag-off dark behaviour: viewer routes 404 when the flag is off ------
def test_viewer_routes_dark_when_off():
    for fn in VIEWER_WRITE_FUNCS:
        src = _func_source(fn)
        _assert("404" in src, f"{fn} must return 404 when flag off (dark)")


# 4 -- admin read routes: owner guard + flag gate, read-only ----------------
def test_admin_read_guards():
    for fn, required in ADMIN_READ_FUNCS.items():
        src = _func_source(fn)
        _assert(src is not None, f"missing admin route {fn}")
        for token in required:
            _assert(token in src, f"{fn} missing guard {token}")
        # admin routes gate the flag AFTER the owner guard -> 409, not dark 404
        _assert("409" in src, f"{fn} must return 409 when flag off (post owner-guard)")
        _assert("log_admin_audit" not in src,
                f"{fn} must be read-only (no audit/state write)")


# 5 -- viewer supplies NO hierarchy/destination; server owns identity -------
def test_viewer_supplies_no_authoritative_facts():
    # The impression token is the only advertising secret the client echoes back;
    # the destination is server-resolved, and the viewer id comes from the session
    # (never the body). Assert the click route never READS a client destination
    # field (the docstring may mention the word; a field access must not appear).
    click = _func_source("api_business_os_advertising_record_click")
    for bad in ('.get("destination")', "['destination']", '["destination"]'):
        _assert(bad not in click,
                "click route must not read a client-supplied destination field")
    # request/impression/click must all pass the SESSION user id downstream, not a
    # body-supplied one.
    for fn in VIEWER_WRITE_FUNCS:
        src = _func_source(fn)
        _assert('user.get("user_id")' in src,
                f"{fn} must pass the session viewer id downstream")


# 6 -- idempotency header is folded for the two event routes ----------------
def test_event_routes_fold_idempotency():
    for fn in ("api_business_os_advertising_record_impression",
               "api_business_os_advertising_record_click"):
        src = _func_source(fn)
        _assert("_bo_ad_fold_idempotency" in src,
                f"{fn} must fold the Idempotency-Key header into the payload")
    # the request-delivery route does NOT need idempotency folding (it mints a new
    # delivery each call) -> assert it doesn't accidentally carry it.
    req = _func_source("api_business_os_advertising_request_delivery")
    _assert("_bo_ad_fold_idempotency" not in req,
            "request-delivery must not fold idempotency (it mints new deliveries)")


# 7 -- new routes live under the canonical namespace; legacy intact ---------
def test_separate_from_legacy():
    canonical = [
        '/api/business-os/advertising/delivery/<placement>"',
        '/api/business-os/advertising/deliveries/<delivery_id>/impression"',
        '/api/business-os/advertising/deliveries/<delivery_id>/click"',
        '/admin/business-os/advertising/deliveries"',
        '/admin/business-os/advertising/deliveries/<delivery_id>"',
        '/admin/business-os/advertising/impressions"',
        '/admin/business-os/advertising/clicks"',
    ]
    for path in canonical:
        _assert(path in SRC, f"missing canonical route path {path}")
    # legacy pulse ads impression/click routes still present and untouched
    _assert('"/api/pulse/ads/impression"' in SRC,
            "legacy pulse ads impression route must remain")
    _assert('"/api/pulse/ads/click"' in SRC,
            "legacy pulse ads click route must remain")


# 8 -- every new route delegates to the tested controller -------------------
def test_routes_delegate_to_controller():
    funcs = list(VIEWER_WRITE_FUNCS) + list(ADMIN_READ_FUNCS)
    for fn in funcs:
        src = _func_source(fn)
        _assert("advertising import api" in src,
                f"{fn} must delegate to advertising.api")


# 9 -- no spend / money surface leaked into any slice-7 route ---------------
def test_no_spend_in_routes():
    funcs = list(VIEWER_WRITE_FUNCS) + list(ADMIN_READ_FUNCS)
    # Money-movement call tokens only. Words like "auction"/"spend" legitimately
    # appear as NEGATIONS in the route docstrings ("No spend, no auction"), so they
    # are not banned here; the no-auction guarantee is enforced at the service layer.
    banned = ("post_entry", "reserve_funds", "release_funds", "deduct",
              "consume_escrow")
    for fn in funcs:
        src = _func_source(fn)
        for token in banned:
            _assert(token not in src,
                    f"{fn} must not spend or move money ({token})")


# 10 -- admin surface is strictly READ (no delivery/impression/click write) -
def test_admin_surface_read_only():
    # every admin delivery route is a GET; no POST/PUT/DELETE method on them.
    for fn in ADMIN_READ_FUNCS:
        node = _FUNC_NODE.get(fn)
        _assert(node is not None, f"missing admin route {fn}")
        # the decorator immediately above must be a GET-only route
        src = "".join(_SRC_LINES[node.lineno - 3:node.lineno])
        _assert('methods=["GET"]' in src or "methods=['GET']" in src,
                f"{fn} must be a GET-only (read-only) route")


# --- standalone runner -----------------------------------------------------
def _run_standalone():
    tests = [
        test_bot_parses,
        test_viewer_write_guards,
        test_viewer_routes_dark_when_off,
        test_admin_read_guards,
        test_viewer_supplies_no_authoritative_facts,
        test_event_routes_fold_idempotency,
        test_separate_from_legacy,
        test_routes_delegate_to_controller,
        test_no_spend_in_routes,
        test_admin_surface_read_only,
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
