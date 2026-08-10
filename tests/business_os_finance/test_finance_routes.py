"""Finance admin routes — bot.py wiring, checked structurally.

bot.py cannot be imported in the hermetic sandbox (flask/stripe absent, no
PyPI), so the request-time guards on the four finance endpoints are verified by
parsing the bot.py source — the same approach as
tests/business_os/test_marketplace_money_routes.py. The behaviour behind the
routes (listing, resolving, reconciling) is covered in-process by
test_incidents.py and test_reconciliation.py.

What is asserted, and why it matters for a *finance* surface:

  1. all four routes exist at the canonical paths with the right verbs
  2. every route requires admin (billing.view to read, billing.repair to
     write) as its FIRST act, and returns the denial — the non-admin-403 path
  3. both write routes pass the CSRF write check and are rate-limited
  4. no route calls a balance-mutating primitive — reconciliation observes,
     it never repairs

    python3 -m unittest tests.business_os_finance.test_finance_routes -v
"""

import ast
import os
import unittest

_BOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "bot.py"))

ROUTES = {
    "api_pulse_finance_incidents":
        ("/api/pulse/finance/incidents", ["GET"], "billing.view"),
    "api_pulse_finance_incident_status":
        ("/api/pulse/finance/incidents/<int:incident_id>/status", ["POST"],
         "billing.repair"),
    "api_pulse_finance_reconcile":
        ("/api/pulse/finance/reconcile", ["POST"], "billing.repair"),
    "api_pulse_finance_reconcile_status":
        ("/api/pulse/finance/reconcile/status", ["GET"], "billing.view"),
}

WRITE_ROUTES = ("api_pulse_finance_incident_status", "api_pulse_finance_reconcile")


def _load():
    src = open(_BOT, encoding="utf-8").read()
    tree = ast.parse(src)
    funcs = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ROUTES:
            funcs[node.name] = node
    return src, funcs


SRC, FUNCS = _load()


def _decorator_route(fn):
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


class RouteRegistrationTests(unittest.TestCase):
    def test_all_four_routes_exist_at_the_canonical_paths(self):
        missing = sorted(set(ROUTES) - set(FUNCS))
        self.assertFalse(missing, f"route handlers absent from bot.py: {missing}")
        for name, (expected_path, expected_methods, _perm) in ROUTES.items():
            path, methods = _decorator_route(FUNCS[name])
            self.assertEqual(path, expected_path, name)
            self.assertEqual(methods, expected_methods, name)


class AdminGateTests(unittest.TestCase):
    def test_every_route_checks_admin_first_and_returns_the_denial(self):
        for name, (_path, _methods, perm) in ROUTES.items():
            src = _source(FUNCS[name])
            self.assertIn(f'require_admin_api("{perm}")', src, name)
            # Admin gate before any finance module is touched — this is what
            # makes a non-admin request a 403/redirect, never a data read.
            self.assertLess(
                src.index("require_admin_api"),
                src.index("services.business_os.payments"),
                f"{name} touches the finance layer before the admin gate",
            )
            self.assertIn("if denied", src,
                          f"{name} must return the denial rather than continue")
            self.assertLess(src.index("if denied"),
                            src.index("services.business_os.payments"), name)

    def test_write_routes_are_csrf_checked_and_rate_limited(self):
        for name in WRITE_ROUTES:
            src = _source(FUNCS[name])
            called = _calls(FUNCS[name])
            self.assertIn("pulse_ads_verify_write", called, name)
            self.assertIn("pulse_ads_rate_limited", called, name)
            self.assertIn("403", src, name)
            self.assertIn("429", src, name)
            # CSRF + throttle sit between the admin gate and the work.
            self.assertLess(src.index("require_admin_api"),
                            src.index("pulse_ads_verify_write"), name)
            self.assertLess(src.index("pulse_ads_verify_write"),
                            src.index("services.business_os.payments"), name)

    def test_write_routes_leave_an_audit_trail(self):
        for name in WRITE_ROUTES:
            self.assertIn("log_admin_audit", _calls(FUNCS[name]), name)


class ReadOnlyMoneyTests(unittest.TestCase):
    def test_no_route_calls_a_balance_mutating_primitive(self):
        forbidden = {
            "post_entry", "recompute_balance", "adjust_balance", "set_balance",
            "credit_wallet", "debit_wallet", "pay_order", "refund_order",
        }
        for name in ROUTES:
            hit = _calls(FUNCS[name]) & forbidden
            self.assertFalse(
                hit,
                f"{name} calls balance-mutating primitive(s): {sorted(hit)} — "
                "reconciliation observes, it never repairs",
            )

    def test_reconcile_route_reaches_run_all_and_nothing_deeper(self):
        called = _calls(FUNCS["api_pulse_finance_reconcile"])
        self.assertIn("run_all", called)
        for deep in ("reconcile_ledger_balances", "reconcile_ad_wallets",
                     "reconcile_stripe_snapshot"):
            self.assertNotIn(deep, called,
                             "the route must go through the orchestrator only")

    def test_status_route_reads_history_not_the_sweep(self):
        called = _calls(FUNCS["api_pulse_finance_reconcile_status"])
        self.assertIn("last_run", called)
        self.assertNotIn("run_all", called,
                         "a GET must never trigger a reconciliation sweep")


class ErrorSurfaceTests(unittest.TestCase):
    def test_every_route_funnels_errors_through_the_finance_handler(self):
        for name in ROUTES:
            self.assertIn("pulse_finance_error_response", _calls(FUNCS[name]),
                          f"{name} must not leak raw exceptions")

    def test_error_handler_maps_incident_errors_to_their_status_code(self):
        src = SRC[SRC.index("def pulse_finance_error_response"):]
        src = src[:src.index("\n@")]
        self.assertIn("status_code", src)
        self.assertIn("400", src)
        self.assertIn("500", src)


if __name__ == "__main__":
    unittest.main()
