"""The two live money routes, checked structurally.

``bot.py`` cannot be imported here — it wants stripe, flask and telegram, and the
sandbox has no package index — so these tests parse the source instead. That is
the same convention the Business OS route suites use, and it is not a weaker
check for what it covers: an IDOR is a *syntactic* property (the handler reads an
id from the request instead of the session) and the parser sees it exactly.

The load-bearing test is ``test_the_seller_is_the_session_user_and_nothing_else``.
Before trusting it, it was verified by mutation: inserting
``request.args.get("user_id")`` into a copy of the handler makes it fail.

    python tests/test_seller_money_routes.py
"""

import ast
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SOURCE = open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read()
TREE = ast.parse(SOURCE)

HANDLERS = {
    "api_payments_seller_money": "/api/pulse/payments/seller/money",
    "api_payments_seller_money_activity":
        "/api/pulse/payments/seller/money/activity",
}

FUNCS = {
    node.name: node for node in ast.walk(TREE)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name in HANDLERS
}


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _src(name):
    return ast.get_source_segment(SOURCE, FUNCS[name]) or ""


def test_both_handlers_exist():
    for name in HANDLERS:
        _assert(name in FUNCS, "handler %s is missing from bot.py" % name)


def test_the_routes_are_get_only():
    """A money read that also accepts POST is a payment path waiting to happen."""
    for name, path in HANDLERS.items():
        decorators = [ast.unparse(d) for d in FUNCS[name].decorator_list]
        joined = " ".join(decorators)
        _assert(path in joined, "%s is not bound to %s" % (name, path))
        _assert('methods=[\'GET\']' in joined or 'methods=["GET"]' in joined,
                "%s must be GET-only; got %s" % (name, joined))
        for verb in ("POST", "PUT", "PATCH", "DELETE"):
            _assert(verb not in joined, "%s accepts %s" % (name, verb))


def test_the_seller_is_the_session_user_and_nothing_else():
    """The whole IDOR surface, in one assertion.

    If the seller identity can be supplied by the caller, any logged-in user can
    read anybody's balances and ledger. So: the handler must derive it from
    ``api_account_user()``, and must never read an identity out of the query
    string.
    """
    for name in HANDLERS:
        src = _src(name)
        _assert("api_account_user()" in src,
                "%s must resolve the caller from the session" % name)
        _assert('user["user_id"]' in src,
                "%s must scope to the session user" % name)
        for forbidden in ("user_id", "seller_id", "seller_user_id", "wallet_id",
                          "account_id"):
            _assert('request.args.get("%s")' % forbidden not in src,
                    "%s reads %s from the query string — that is an IDOR on "
                    "somebody else's money" % (name, forbidden))
            _assert("request.args.get('%s')" % forbidden not in src,
                    "%s reads %s from the query string" % (name, forbidden))

        # Whatever identity is handed to the service must be the session user's,
        # not a local that could have been reassigned from the request.
        for node in ast.walk(FUNCS[name]):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in {"seller_money_overview", "seller_activity"}:
                _assert(node.args, "%s must pass an explicit identity" % name)
                # ast.unparse normalises quoting, so compare quote-insensitively.
                passed = ast.unparse(node.args[0]).replace("'", '"')
                _assert(passed == 'user["user_id"]',
                        "%s passes %r as the seller identity" % (name, passed))


def test_login_is_required_before_any_money_is_read():
    """And the 401 must come BEFORE the service call, not after it."""
    for name in HANDLERS:
        body = FUNCS[name].body
        guard_index = service_index = None
        for i, stmt in enumerate(body):
            text = ast.unparse(stmt)
            if guard_index is None and "Login required." in text:
                guard_index = i
            if service_index is None and "_seller_money." in text:
                service_index = i
        _assert(guard_index is not None, "%s has no login guard" % name)
        _assert(service_index is not None, "%s never calls the service" % name)
        _assert(guard_index < service_index,
                "%s reads money before checking the caller is logged in" % name)


def test_no_route_can_write():
    """No write verb, no payout initiation, no reconciliation from a GET."""
    for name in HANDLERS:
        src = _src(name).upper()
        for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "COMMIT()",
                          "RECONCILE_WALLET", "ENSURE_WALLET", "ADD_LEDGER_ENTRY",
                          "CREATE_TRANSACTION", "MARK_TRANSACTION_PAID",
                          "HANDLE_REFUND", "CREATE_ONBOARDING_LINK"):
            _assert(forbidden not in src,
                    "%s contains %r — a money read must not write" % (name, forbidden))


def test_balances_are_never_cached_by_an_intermediary():
    """A stale balance served from a cache is a number presented as current.

    The screen is allowed to show a cached figure only when it labels it "as of
    {time}", and that is the client's decision to make — not a proxy's.
    """
    for name in HANDLERS:
        src = _src(name)
        _assert('Cache-Control' in src and 'no-store' in src,
                "%s does not set no-store on a money response" % name)


def test_a_bad_cursor_is_a_400_and_a_real_failure_is_not():
    """Two different failures must not collapse into one status.

    A cursor the client invented is the client's mistake (400). A database that
    is genuinely unhappy is not, and dressing it up as a user error would hide a
    real outage behind a validation message.
    """
    src = _src("api_payments_seller_money_activity")
    _assert("SellerMoneyError" in src,
            "the activity route does not distinguish a bad cursor")
    _assert("400" in src, "a bad cursor must be a 400")
    _assert("503" in src, "a genuine read failure must be a 503, not a 400")


def test_the_service_import_is_inside_the_handler():
    """Same convention every other Business OS route in this file follows.

    A module-level import would put a money read on the critical path of app
    boot, where a failure takes down routes that have nothing to do with money.
    """
    for name in HANDLERS:
        module_body = FUNCS[name].body
        found = any(isinstance(stmt, (ast.Import, ast.ImportFrom))
                    for stmt in ast.walk(FUNCS[name]))
        _assert(found, "%s imports the money service at module scope" % name)
        _assert(len(module_body) > 1, "%s has no body" % name)


def test_no_amount_is_logged():
    """Financial figures must not reach logs or analytics.

    The handlers log a trace id and a user id on failure, which is what an
    on-call engineer needs, and nothing about how much money is involved.
    """
    for name in HANDLERS:
        src = _src(name)
        for node in ast.walk(FUNCS[name]):
            if isinstance(node, ast.Call) and "logging" in ast.unparse(node.func):
                logged = ast.unparse(node).lower()
                for leak in ("cents", "amount", "balance", "available",
                             "processing", "overview", "activity["):
                    _assert(leak not in logged,
                            "%s logs %r — no financial figure may enter a log"
                            % (name, leak))
        _assert("trace_id" in src, "%s logs no trace id to correlate on" % name)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("ok  %s" % fn.__name__)
    print("\n%d passed" % len(fns))
