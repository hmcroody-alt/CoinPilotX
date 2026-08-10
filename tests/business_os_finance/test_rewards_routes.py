"""Rewards routes (Wave D) — bot.py checked structurally.

bot.py cannot be imported in the hermetic sandbox (flask/stripe absent), so
the Wave D surface is verified by parsing the bot.py source — the same
approach as tests/business_os_finance/test_seller_payout_routes.py. The
behaviour behind the routes lives in services/business_os/rewards/engine.py
and is covered in-process by test_rewards_engine.py.

    python3 -m unittest tests.business_os_finance.test_rewards_routes -v
"""

import ast
import os
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_BOT = os.path.join(_ROOT, "bot.py")
_SELLER_PAYOUTS = os.path.join(
    _ROOT, "services", "business_os", "payments", "seller_payouts.py")
_RECONCILIATION = os.path.join(
    _ROOT, "services", "business_os", "payments", "reconciliation.py")

ROUTES = {
    "api_pulse_rewards":
        ("/api/pulse/rewards", ["GET"]),
    "api_pulse_rewards_credit_ledger":
        ("/api/pulse/rewards/credits/ledger", ["GET"]),
    "api_pulse_rewards_credits_redeem":
        ("/api/pulse/rewards/credits/redeem", ["POST"]),
    "api_pulse_rewards_claim":
        ("/api/pulse/rewards/<int:reward_id>/claim", ["POST"]),
    "api_pulse_finance_rewards":
        ("/api/pulse/finance/rewards", ["GET"]),
    "api_pulse_finance_rewards_grant":
        ("/api/pulse/finance/rewards/grant", ["POST"]),
    "api_pulse_finance_rewards_fraud":
        ("/api/pulse/finance/rewards/<int:reward_id>/fraud", ["POST"]),
    "api_pulse_finance_rewards_approve":
        ("/api/pulse/finance/rewards/<int:reward_id>/approve", ["POST"]),
}

MEMBER_ROUTES = (
    "api_pulse_rewards", "api_pulse_rewards_credit_ledger",
    "api_pulse_rewards_credits_redeem", "api_pulse_rewards_claim",
)
MEMBER_WRITE_ROUTES = (
    "api_pulse_rewards_credits_redeem", "api_pulse_rewards_claim",
)
ADMIN_WRITE_ROUTES = (
    "api_pulse_finance_rewards_grant", "api_pulse_finance_rewards_fraud",
    "api_pulse_finance_rewards_approve",
)

_FUNCS_WANTED = set(ROUTES)


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
    def test_member_routes_require_login_before_the_rewards_engine(self):
        for name in MEMBER_ROUTES:
            src = _source(FUNCS[name])
            self.assertIn("api_account_user", src, name)
            self.assertIn("Login required", src, name)
            self.assertLess(
                src.index("api_account_user"),
                src.index("services.business_os.rewards"),
                f"{name} touches the rewards engine before the login gate",
            )

    def test_member_writes_are_csrf_checked_and_rate_limited(self):
        for name in MEMBER_WRITE_ROUTES:
            src = _source(FUNCS[name])
            called = _calls(FUNCS[name])
            self.assertIn("pulse_ads_verify_write", called, name)
            self.assertIn("pulse_ads_rate_limited", called, name)
            self.assertIn("403", src, name)
            self.assertIn("429", src, name)
            self.assertLess(
                src.index("pulse_ads_verify_write"),
                src.index("services.business_os.rewards"),
                f"{name} touches the rewards engine before the CSRF gate",
            )

    def test_claim_checks_ownership_before_disbursing(self):
        src = _source(FUNCS["api_pulse_rewards_claim"])
        called = _calls(FUNCS["api_pulse_rewards_claim"])
        self.assertIn("get_reward", called)
        self.assertIn("disburse_cash_reward", called)
        self.assertLess(src.index("get_reward"),
                        src.index("disburse_cash_reward"),
                        "ownership must be checked before disbursement")
        self.assertIn("user_id", src)
        self.assertIn("404", src)

    def test_admin_reads_require_billing_view_first(self):
        src = _source(FUNCS["api_pulse_finance_rewards"])
        self.assertIn('require_admin_api("billing.view")', src)
        self.assertIn("if denied", src)
        self.assertLess(src.index("require_admin_api"),
                        src.index("services.business_os.rewards"))

    def test_admin_writes_require_billing_repair_plus_write_gates(self):
        for name in ADMIN_WRITE_ROUTES:
            src = _source(FUNCS[name])
            called = _calls(FUNCS[name])
            self.assertIn('require_admin_api("billing.repair")', src, name)
            self.assertIn("pulse_ads_verify_write", called, name)
            self.assertIn("pulse_ads_rate_limited", called, name)
            self.assertLess(src.index("require_admin_api"),
                            src.index("services.business_os.rewards"), name)

    def test_admin_writes_are_audited(self):
        for name in ADMIN_WRITE_ROUTES:
            self.assertIn("log_admin_audit", _calls(FUNCS[name]),
                          f"{name} must leave an admin audit trail")


class MoneyDisciplineTests(unittest.TestCase):
    def test_routes_never_call_ledger_primitives_directly(self):
        forbidden = {
            "post_entry", "recompute_balance", "adjust_balance", "set_balance",
            "credit_wallet", "debit_wallet", "grant_promotional_credits",
            "_append_credit_row", "_insert_transaction",
        }
        for name in ROUTES:
            hit = _calls(FUNCS[name]) & forbidden
            self.assertFalse(
                hit,
                f"{name} calls money primitive(s) {sorted(hit)} — all money "
                "movement must go through the rewards engine",
            )

    def test_claim_handles_lazy_onboarding_both_ways(self):
        src = _source(FUNCS["api_pulse_rewards_claim"])
        self.assertIn("needs_onboarding", src)
        self.assertIn("STRIPE_SECRET_KEY", src)
        self.assertIn("create_onboarding_link", src)
        self.assertIn("setup_required", src,
                      "without Stripe keys the claim must degrade honestly")

    def test_claim_submits_the_payout_via_the_shared_helper(self):
        called = _calls(FUNCS["api_pulse_rewards_claim"])
        self.assertIn("pulse_submit_seller_payout", called)
        src = _source(FUNCS["api_pulse_rewards_claim"])
        self.assertLess(src.index("disburse_cash_reward"),
                        src.index("pulse_submit_seller_payout"),
                        "the intent must be recorded before any Stripe call")

    def test_balance_reads_are_never_cacheable(self):
        for name in MEMBER_ROUTES:
            self.assertIn("no-store", _source(FUNCS[name]), name)

    def test_error_funnel_is_the_finance_handler(self):
        for name in ROUTES:
            self.assertIn("pulse_finance_error_response", _calls(FUNCS[name]),
                          f"{name} must not leak raw exceptions")


class EngineWiringTests(unittest.TestCase):
    def test_payout_engine_notifies_rewards_defensively(self):
        with open(_SELLER_PAYOUTS, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("_notify_rewards_engine", src)
        self.assertIn("reward_payout:", src)
        self.assertIn("sync_from_payout", src)
        # lazy import inside the helper, wrapped so a rewards bug can never
        # break payout webhook processing
        helper = src[src.index("def _notify_rewards_engine"):]
        helper = helper[:helper.index("\ndef ")]
        self.assertIn("from services.business_os.rewards import engine", helper)
        self.assertIn("except Exception", helper)
        # both terminal paths call it
        applier = src[src.index("def apply_stripe_payout_event"):]
        applier = applier[:applier.index("\ndef ", 10)]
        self.assertIn("_notify_rewards_engine", applier)
        failer = src[src.index("def fail_payout"):]
        failer = failer[:failer.index("\ndef ", 10)]
        self.assertIn("_notify_rewards_engine", failer)

    def test_reconciliation_registers_the_rewards_check(self):
        with open(_RECONCILIATION, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("def reconcile_rewards", src)
        self.assertIn('("rewards", reconcile_rewards)', src)


if __name__ == "__main__":
    unittest.main()
