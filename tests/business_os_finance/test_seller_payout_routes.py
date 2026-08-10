"""Seller payout routes + webhook wiring — bot.py checked structurally.

bot.py cannot be imported in the hermetic sandbox (flask/stripe absent), so
the Wave B surface is verified by parsing the bot.py source — the same
approach as tests/business_os_finance/test_finance_routes.py. The behaviour
behind the routes lives in services/business_os/payments/seller_payouts.py and
connect_accounts.py and is covered in-process by test_seller_payouts.py and
test_connect_accounts.py.

    python3 -m unittest tests.business_os_finance.test_seller_payout_routes -v
"""

import ast
import os
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_BOT = os.path.join(_ROOT, "bot.py")
_HANDLER = os.path.join(
    _ROOT, "services", "business_os", "payments", "stripe_ledger_handler.py")

ROUTES = {
    "api_pulse_seller_payouts":
        ("/api/pulse/payments/seller/payouts", ["GET", "POST"]),
    "api_pulse_seller_connect_status":
        ("/api/pulse/payments/seller/connect/status", ["GET"]),
    "api_pulse_finance_payouts":
        ("/api/pulse/finance/payouts", ["GET"]),
}

SELLER_ROUTES = ("api_pulse_seller_payouts", "api_pulse_seller_connect_status")

_FUNCS_WANTED = set(ROUTES) | {
    "stripe_webhook", "pulse_submit_seller_payout", "pulse_seller_connect_state",
}


def _load():
    src = open(_BOT, encoding="utf-8").read()
    tree = ast.parse(src)
    funcs = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in _FUNCS_WANTED:
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
    def test_all_routes_exist_at_the_canonical_paths(self):
        missing = sorted(set(ROUTES) - set(FUNCS))
        self.assertFalse(missing, f"route handlers absent from bot.py: {missing}")
        for name, (expected_path, expected_methods) in ROUTES.items():
            path, methods = _decorator_route(FUNCS[name])
            self.assertEqual(path, expected_path, name)
            self.assertEqual(methods, expected_methods, name)


class AuthGateTests(unittest.TestCase):
    def test_seller_routes_require_login_before_the_finance_layer(self):
        for name in SELLER_ROUTES:
            src = _source(FUNCS[name])
            self.assertIn("api_account_user", src, name)
            self.assertIn("Login required", src, name)
            # The 401 comes before any finance code is touched — either a
            # direct engine import or the shared connect-state helper.
            finance_touch = min(
                index for index in (
                    src.find("services.business_os.payments"),
                    src.find("pulse_seller_connect_state"),
                ) if index != -1
            )
            self.assertLess(
                src.index("api_account_user"), finance_touch,
                f"{name} touches the finance layer before the login gate",
            )

    def test_payout_post_is_csrf_checked_and_rate_limited(self):
        src = _source(FUNCS["api_pulse_seller_payouts"])
        called = _calls(FUNCS["api_pulse_seller_payouts"])
        self.assertIn("pulse_ads_verify_write", called)
        self.assertIn("pulse_ads_rate_limited", called)
        self.assertIn("403", src)
        self.assertIn("429", src)
        # The write gates come before the payout request is recorded.
        self.assertLess(src.index("pulse_ads_verify_write"),
                        src.index("request_payout"))

    def test_admin_payout_read_requires_billing_view_first(self):
        src = _source(FUNCS["api_pulse_finance_payouts"])
        self.assertIn('require_admin_api("billing.view")', src)
        self.assertIn("if denied", src)
        self.assertLess(src.index("require_admin_api"),
                        src.index("services.business_os.payments"))


class MoneyDisciplineTests(unittest.TestCase):
    def test_routes_never_call_ledger_primitives_directly(self):
        forbidden = {
            "post_entry", "recompute_balance", "adjust_balance", "set_balance",
            "credit_wallet", "debit_wallet",
        }
        for name in list(ROUTES) + ["pulse_submit_seller_payout",
                                    "pulse_seller_connect_state"]:
            hit = _calls(FUNCS[name]) & forbidden
            self.assertFalse(
                hit,
                f"{name} calls ledger primitive(s) {sorted(hit)} — all money "
                "movement must go through the seller_payouts engine",
            )

    def test_post_records_intent_then_optionally_submits(self):
        called = _calls(FUNCS["api_pulse_seller_payouts"])
        self.assertIn("request_payout", called)
        self.assertIn("pulse_submit_seller_payout", called)
        src = _source(FUNCS["api_pulse_seller_payouts"])
        self.assertLess(src.index("request_payout"),
                        src.index("pulse_submit_seller_payout"),
                        "the intent must be recorded before any Stripe call")

    def test_stripe_submission_helper_is_gated_on_the_key_and_fails_safe(self):
        src = _source(FUNCS["pulse_submit_seller_payout"])
        called = _calls(FUNCS["pulse_submit_seller_payout"])
        self.assertIn("STRIPE_SECRET_KEY", src)
        self.assertIn("build_stripe_payout_args", called)
        self.assertIn("mark_payout_submitted", called)
        self.assertIn("fail_payout", called,
                      "a Stripe rejection must reverse the fenced funds")

    def test_balance_reads_are_never_cacheable(self):
        for name in SELLER_ROUTES:
            self.assertIn("no-store", _source(FUNCS[name]), name)

    def test_error_funnel_is_the_finance_handler(self):
        for name in ROUTES:
            self.assertIn("pulse_finance_error_response", _calls(FUNCS[name]),
                          f"{name} must not leak raw exceptions")


class WebhookWiringTests(unittest.TestCase):
    def test_stripe_webhook_dispatches_to_the_wave_b_appliers(self):
        called = _calls(FUNCS["stripe_webhook"])
        for applier in ("apply_stripe_payout_event",
                        "apply_stripe_transfer_event",
                        "apply_account_updated_event"):
            self.assertIn(applier, called,
                          f"stripe_webhook never calls {applier}")

    def test_wave_b_wiring_is_defensive(self):
        src = _source(FUNCS["stripe_webhook"])
        start = src.index("apply_stripe_payout_event")
        # Both new dispatch sites log rather than raise, so a Wave B failure
        # can never break the legacy webhook processing that follows.
        self.assertIn("BOS_SELLER_PAYOUT_EVENT_FAILED", src)
        self.assertIn("BOS_CONNECT_ACCOUNT_EVENT_FAILED", src)
        guard = src.rfind("try:", 0, start)
        self.assertGreater(guard, -1)
        self.assertLess(start - guard, 500,
                        "the payout applier is not inside its own try/except")

    def test_wave_b_runs_before_the_legacy_connect_branch(self):
        src = _source(FUNCS["stripe_webhook"])
        legacy = src.index('"account.updated", "payout.paid", "payout.failed"')
        self.assertLess(src.index("apply_stripe_payout_event"), legacy)
        self.assertLess(src.index("apply_account_updated_event"), legacy)

    def test_inbox_replay_path_delegates_too(self):
        handler_src = open(_HANDLER, encoding="utf-8").read()
        self.assertIn("apply_stripe_payout_event", handler_src)
        self.assertIn("apply_stripe_transfer_event", handler_src)
        self.assertIn("apply_account_updated_event", handler_src)


if __name__ == "__main__":
    unittest.main()
