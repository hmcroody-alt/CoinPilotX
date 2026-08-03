"""Seller money read surface — bot.py route wiring, checked structurally.

bot.py cannot be imported in the hermetic sandbox (stripe/flask/telegram, no
PyPI), so the request-time guards on the three new money GETs are verified here
by parsing the bot.py source. The behaviour behind them is covered in-process by
test_marketplace_money_read.py and test_ledger_account_history.py.

What is asserted, and why each one matters for a *money* endpoint specifically:

  1. every route exists, is registered at the canonical path, and is GET-only —
     a money read that also answers POST is a write surface waiting to happen
  2. every route is behind the marketplace flag and returns 404, not 403, when
     it is off — a disabled surface should not advertise that it exists
  3. every route authenticates before it touches money
  4. **the seller id comes from the session, never from the request.** This is
     the load-bearing one: a single ``request.args.get("seller_id")`` here would
     turn all three endpoints into a way to read somebody else's balances.
  5. no route calls a write primitive

    python tests/business_os/test_marketplace_money_routes.py   # no pytest needed
"""

import ast
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

_BOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "bot.py"))
SRC = open(_BOT, encoding="utf-8").read()
TREE = ast.parse(SRC)

ROUTES = {
    "api_business_os_marketplace_money":
        "/api/business-os/marketplace/money",
    "api_business_os_marketplace_money_activity":
        "/api/business-os/marketplace/money/activity",
    "api_business_os_marketplace_money_disputes":
        "/api/business-os/marketplace/money/disputes",
}

# The controller each route is allowed to reach. A money read route that called
# anything else would be composing a financial figure in the web layer.
CONTROLLERS = {
    "api_business_os_marketplace_money": "seller_money_overview",
    "api_business_os_marketplace_money_activity": "seller_activity",
    "api_business_os_marketplace_money_disputes": "seller_disputes",
}


def _funcs():
    out = {}
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name in ROUTES:
            out[node.name] = node
    return out


FUNCS = _funcs()


def _decorator_route(fn):
    """The (path, methods) the flask decorator registers, or (None, None)."""
    for dec in fn.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        target = dec.func
        if not (isinstance(target, ast.Attribute) and target.attr == "route"):
            continue
        path = dec.args[0].value if dec.args else None
        methods = None
        for kw in dec.keywords:
            if kw.arg == "methods":
                methods = [e.value for e in kw.value.elts]
        return path, methods
    return None, None


def _calls(fn):
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def _source(fn):
    return ast.get_source_segment(SRC, fn) or ""


# --- 1. the routes exist, at the right paths, GET only ----------------------
def test_all_three_routes_are_registered_as_get_only():
    missing = sorted(set(ROUTES) - set(FUNCS))
    assert not missing, "route handlers absent from bot.py: %s" % missing

    for name, expected_path in ROUTES.items():
        path, methods = _decorator_route(FUNCS[name])
        assert path == expected_path, (name, path, expected_path)
        assert methods == ["GET"], (
            "%s must be GET-only; a money read that accepts writes is a second "
            "payment path in disguise (got %r)" % (name, methods))


# --- 2. flag off is a 404, not a 403 -----------------------------------------
def test_every_route_is_dark_when_the_flag_is_off():
    for name in ROUTES:
        src = _source(FUNCS[name])
        assert "_business_os_marketplace_enabled" in src, name
        assert "404" in src, (
            "%s must 404 when the marketplace is disabled — a 403 would confirm "
            "the endpoint exists" % name)
        # The gate has to be the first thing, before any user lookup.
        assert src.index("_business_os_marketplace_enabled") < \
            src.index("pulse_ads_api_user_required"), (
                "%s checks the flag after authenticating" % name)


# --- 3. authentication precedes any money access -----------------------------
def test_every_route_authenticates_before_reading_money():
    for name, controller in CONTROLLERS.items():
        src = _source(FUNCS[name])
        assert "pulse_ads_api_user_required" in src, name
        assert src.index("pulse_ads_api_user_required") < src.index(controller), (
            "%s calls %s before authenticating" % (name, controller))
        assert "if denied" in src or "denied:" in src, (
            "%s must return the denial rather than continue" % name)


# --- 4. the seller id is the session user, never a parameter -----------------
def test_seller_identity_is_never_taken_from_the_request():
    for name in ROUTES:
        fn = FUNCS[name]
        src = _source(fn)
        assert 'user.get("user_id")' in src, (
            "%s must scope to the session user" % name)
        for forbidden in ("seller_id", "seller_user_id", "user_id="):
            assert 'request.args.get("%s")' % forbidden not in src, (
                "%s reads %s from the query string — that is an IDOR on somebody "
                "else's balances" % (name, forbidden))
        # Whatever else the query string carries, it must not reach the
        # controller as the identity argument.
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in CONTROLLERS.values():
                assert node.args, "%s must pass an explicit identity" % name
                first = node.args[0]
                assert isinstance(first, ast.Call) and \
                    isinstance(first.func, ast.Attribute) and \
                    first.func.attr == "get", (
                        "%s passes something other than user.get(...) as the "
                        "seller identity" % name)


# --- 5. read-only means read-only -------------------------------------------
def test_no_route_calls_a_write_primitive():
    forbidden = {
        "post_entry", "pay_order", "refund_order", "complete_order",
        "resolve_dispute", "create_order", "cancel_order",
    }
    for name in ROUTES:
        hit = _calls(FUNCS[name]) & forbidden
        assert not hit, "%s calls write primitive(s): %s" % (name, sorted(hit))


def test_each_route_reaches_exactly_its_own_controller():
    for name, controller in CONTROLLERS.items():
        called = _calls(FUNCS[name])
        assert controller in called, "%s never calls %s" % (name, controller)
        others = set(CONTROLLERS.values()) - {controller}
        assert not (called & others), (
            "%s reaches a controller that is not its own" % name)


# --- 6. the parameters that do exist are handled defensively ----------------
def test_activity_tolerates_a_junk_limit_and_passes_a_cursor_through():
    src = _source(FUNCS["api_business_os_marketplace_money_activity"])
    assert "ValueError" in src, (
        "a non-numeric ?limit must fall back to a default, not 500")
    assert "cursor" in src and "before_cursor" in src or "cursor=cursor" in src, (
        "the pagination cursor must be forwarded")


def test_disputes_widening_is_explicit_not_accidental():
    src = _source(FUNCS["api_business_os_marketplace_money_disputes"])
    assert '"open"' in src, "the default filter must be the narrow one"
    assert '"all"' in src, (
        "widening to every dispute must require the caller to say 'all' — a "
        "dropped query string must not silently widen the result set")


# --- 7. the controller layer agrees with what the routes call ---------------
def test_controller_functions_actually_exist_with_matching_signatures():
    import inspect
    os.environ.setdefault("BUSINESS_OS_MARKETPLACE", "0")
    from services.business_os.marketplace import api as mktapi

    expected = {
        "seller_money_overview": {"currency"},
        "seller_activity": {"currency", "limit", "cursor", "entry_types"},
        "seller_disputes": {"status", "limit"},
    }
    for fname, kwargs in expected.items():
        fn = getattr(mktapi, fname, None)
        assert callable(fn), "api.%s is missing" % fname
        params = set(inspect.signature(fn).parameters) - {"seller_user_id"}
        assert kwargs <= params, (
            "api.%s is missing keyword(s) the route passes: %s"
            % (fname, sorted(kwargs - params)))


def test_controllers_are_dark_when_the_flag_is_off():
    os.environ["BUSINESS_OS_MARKETPLACE"] = "0"
    from services.business_os.marketplace import api as mktapi
    for fname in ("seller_money_overview", "seller_activity", "seller_disputes"):
        status, body = getattr(mktapi, fname)(1)
        assert status == 404, (fname, status)
        assert body.get("ok") is False, (fname, body)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("ok  %s" % fn.__name__)
    print("\n%d passed" % len(fns))
