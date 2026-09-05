"""Expired Premium closes UNDX crypto intelligence — proved at execution.

A padlock is a claim about the product, not a fact about it. So nothing here
asserts an icon, a label or a route: every case drives a *real* executor or the
real grounding builder and then asks whether the thing being sold was actually
produced. The spy backing the market layer records every read, and the central
assertion in the locked cases is that the spy was never touched — a member whose
membership has lapsed does not merely see a different screen, they cause no
premium read to happen at all.

The matrix is the fourteen entitlement states the addendum names, and each one
is expressed the way the server sees it: a canonical tier answer, resolved
through ``services.crypto_premium_gate``. Two of them exist specifically to
catch the bypasses that motivated this work:

* **stale client premium=true** — the client's own belief is fed in and must
  count for nothing, because the resolver is asked and the client is not.
* **parked crypto_asset context** — a Market Pulse handoff envelope, parked
  while the membership was still live and outliving it. It names WHICH coin the
  member means. It is not a grant, and the gate runs before it is consulted.

The remaining bypass this file pins is the quieter one: ``grounding_block``
injects live prices straight into the model's knowledge list without any tool
call, so a gate on the executors alone would have left the whole capability
reachable by simply asking in prose.

Free UNDX is asserted untouched in both directions — a non-crypto turn never
reaches the resolver, and a free-tier capability is never denied by this work.
"""

from __future__ import annotations

import json
import os
import sys
import types
import unittest
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

import services  # noqa: E402
from services import undx_agent_tools as tools  # noqa: E402


CAP_INTELLIGENCE = "premium.crypto.intelligence"
CAP_ADVANCED = "premium.crypto.advanced_alerts"
CAP_PORTFOLIO = "premium.crypto.portfolio_intelligence"

#: The parked Market Pulse handoff. Well-formed, plausible, and — this is the
#: point — completely silent on entitlement.
#: ``attached_at`` is the server's own stamp, and it is not decoration: an
#: envelope without one is *expired by definition* (``is_expired`` treats a
#: missing stamp as infinitely old), so an unstamped fixture never reaches the
#: deixis branch of ``resolve_asset`` and "how is it doing" resolves to nothing
#: at all. That would make the bypass case below pass for the wrong reason —
#: no lock, just an envelope the resolver silently discarded. Six-hour TTL, so
#: stamping once at import is stable for any plausible run.
PARKED_BTC_CONTEXT = {
    "type": "crypto_asset",
    "source": "asset_detail",
    "asset": {"id": "bitcoin", "symbol": "BTC", "displayName": "Bitcoin", "name": "Bitcoin"},
    "attached_at": datetime.now(timezone.utc).isoformat(),
}


class MarketSpy:
    """Stands in for ``services.undx_market_context`` and remembers everything.

    Every method returns data a member would consider worth paying for, so a
    test that reaches one and passes is a test that leaked. ``reads`` is the
    assertion surface: empty means no premium work was done.

    The double stands in for the *whole* module, so it has to satisfy the whole
    contract the executors call through it — including the success-shaped keys
    (``ok``, ``available``) they check before returning. A spy that returns
    plausible-looking data without them makes every allowed case fail as though
    the gate had wrongly denied it, which is the opposite of what went wrong.
    """

    def __init__(self) -> None:
        self.reads: list[str] = []

    def active_context_for_user(self, user_id):
        self.reads.append("active_context_for_user")
        return PARKED_BTC_CONTEXT

    def normalize_range(self, value):
        # Deliberately NOT recorded as a read: it consults no market data, and
        # counting it would let `assertNotEqual(spy.reads, [])` pass for the
        # history tool without a single figure having been fetched.
        return str(value or "24H").upper()

    def quote(self, symbol):
        self.reads.append(f"quote:{symbol}")
        return {"symbol": symbol, "price": 70123.45, "change24h": 1.2}

    def history_pack(self, symbol, period="7d"):
        self.reads.append(f"history_pack:{symbol}")
        return {"ok": True, "symbol": symbol, "range": period,
                "points": [{"t": 1, "price": 70000.0}]}

    def overview(self):
        self.reads.append("overview")
        return {"available": True, "total_market_cap": 2.4e12, "btc_dominance": 54.1}

    def overlay(self, user_id, symbol=None):
        self.reads.append("overlay")
        return {"watchlists": ["Majors"], "alerts": [{"id": 1}]}


# ---------------------------------------------------------------------------
# The fourteen entitlement states, as the server resolves them
# ---------------------------------------------------------------------------

#: ``(label, effective_premium)``. ``effective_premium`` is what the canonical
#: resolver concludes — an active subscription, an active trial or an active
#: higher tier that inherits Premium all resolve True; everything else False.
#: These are the addendum's fourteen cases, in its order.
ENTITLEMENT_MATRIX: tuple[tuple[str, bool], ...] = (
    ("free_never_subscribed", False),
    ("trial_active", True),
    ("trial_expired", False),
    ("premium_active", True),
    ("premium_cancelled_within_paid_period", True),
    ("premium_expired", False),
    ("premium_revoked", False),
    ("premium_expired_private_active", True),
    ("premium_expired_private_office_active", True),
    ("stale_client_premium_true_backend_expired", False),
    ("direct_route_while_expired", False),
    ("deep_link_while_expired", False),
    ("market_pulse_context_handoff_while_expired", False),
    ("reactivated", True),
)

ALLOWED_CASES = tuple(case for case in ENTITLEMENT_MATRIX if case[1])
LOCKED_CASES = tuple(case for case in ENTITLEMENT_MATRIX if not case[1])


class EntitlementHarness(unittest.TestCase):
    """Swaps in a gate and a market layer, and restores both afterwards."""

    _MISSING = object()
    _SWAPPED = ("crypto_premium_gate", "undx_market_context")

    def setUp(self) -> None:
        self._saved_modules: dict[str, object] = {}
        self._saved_attrs: dict[str, object] = {}
        for short in self._SWAPPED:
            full = f"services.{short}"
            self._saved_modules[full] = sys.modules.pop(full, self._MISSING)
            self._saved_attrs[short] = getattr(services, short, self._MISSING)
            if hasattr(services, short):
                delattr(services, short)
        self.spy = MarketSpy()
        self.gate_calls: list[tuple[int, str]] = []

    def tearDown(self) -> None:
        for full, value in self._saved_modules.items():
            if value is self._MISSING:
                sys.modules.pop(full, None)
            else:
                sys.modules[full] = value
        for short, value in self._saved_attrs.items():
            if value is self._MISSING:
                if hasattr(services, short):
                    delattr(services, short)
            else:
                setattr(services, short, value)

    def install(self, short: str, module: object) -> None:
        sys.modules[f"services.{short}"] = module
        setattr(services, short, module)

    def install_gate(self, *, premium: bool) -> None:
        """Install the canonical gate with a fixed resolved answer.

        The signature mirrors the real ``has_crypto_capability`` exactly, and
        every call is recorded so a test can assert the resolver was consulted
        with the *authenticated* user rather than anything from the arguments.
        """

        def has_crypto_capability(user_id, capability):
            self.gate_calls.append((user_id, capability))
            return premium

        module = types.ModuleType("services.crypto_premium_gate")
        module.CAP_CRYPTO_INTELLIGENCE = CAP_INTELLIGENCE
        module.CAP_CRYPTO_ADVANCED_ALERTS = CAP_ADVANCED
        module.CAP_CRYPTO_PORTFOLIO = CAP_PORTFOLIO
        module.has_crypto_capability = has_crypto_capability
        module.premium_required_response = lambda capability: {
            "ok": False,
            "code": "premium_required",
            "capability": capability,
            "message": "PulseSoc Premium is needed for crypto intelligence.",
        }
        self.install("crypto_premium_gate", module)

    def install_market(self) -> None:
        self.install("undx_market_context", self.spy)


# ---------------------------------------------------------------------------
# Stage 4 + 10 — the four market executors, intercepted at execution
# ---------------------------------------------------------------------------

#: ``(executor, arguments)``. Arguments carry an explicit symbol so that a
#: failure to gate cannot be masked by the "which coin?" error path — if the
#: gate is missing, these calls succeed and return live prices.
MARKET_EXECUTORS = (
    ("quote", tools.crypto_market_quote, {"symbol": "BTC"}),
    ("history", tools.crypto_market_history, {"symbol": "BTC", "period": "7d"}),
    # `compare` needs both sides named. Passing only `symbol` fails with
    # `missing_arguments` *before* the gate result can matter, which would hide
    # a leak in the locked cases behind an unrelated error.
    ("compare", tools.crypto_market_compare, {"symbol": "BTC", "versus": "ETH"}),
    ("overview", tools.crypto_market_overview, {}),
)


class TestMarketExecutorsUnderExpiry(EntitlementHarness):
    def test_locked_states_execute_nothing(self):
        for label, _ in LOCKED_CASES:
            for name, executor, arguments in MARKET_EXECUTORS:
                with self.subTest(case=label, tool=name):
                    self.spy = MarketSpy()
                    self.gate_calls = []
                    self.install_gate(premium=False)
                    self.install_market()

                    result = executor(7, dict(arguments))

                    self.assertFalse(result.ok)
                    self.assertEqual(result.error_code, "premium_required")
                    # The capability named in the refusal is the one that was
                    # actually resolved, so the upsell cannot advertise a
                    # different product from the one that was withheld.
                    self.assertEqual(result.data.get("capability"), CAP_INTELLIGENCE)
                    # The whole assertion: no market read happened at all.
                    self.assertEqual(self.spy.reads, [])
                    # And no figure escaped inside the refusal.
                    self.assertNotIn("70123", json.dumps(result.data))

    def test_allowed_states_execute_normally(self):
        for label, _ in ALLOWED_CASES:
            for name, executor, arguments in MARKET_EXECUTORS:
                with self.subTest(case=label, tool=name):
                    self.spy = MarketSpy()
                    self.gate_calls = []
                    self.install_gate(premium=True)
                    self.install_market()

                    result = executor(7, dict(arguments))

                    self.assertTrue(result.ok, f"{label}/{name}: {result.error_code}")
                    self.assertNotEqual(self.spy.reads, [],
                                        "an allowed read must actually read")

    def test_gate_is_asked_about_the_authenticated_user_only(self):
        """A ``user_id`` in the arguments is data, not identity."""
        self.install_gate(premium=False)
        self.install_market()
        tools.crypto_market_quote(7, {"symbol": "BTC", "user_id": 999})
        self.assertEqual(self.gate_calls, [(7, CAP_INTELLIGENCE)])

    def test_higher_tier_inheritance_executes(self):
        """PRIVATE and PRIVATE_OFFICE inherit Premium, so they must not be locked.

        The inheritance itself belongs to the resolver and is tested there; what
        this pins is that the executor asks the resolver and obeys it, rather
        than testing the tier string for equality with "PREMIUM" — the mistake
        that would lock the two tiers that paid the most.
        """
        for label in ("premium_expired_private_active",
                      "premium_expired_private_office_active"):
            with self.subTest(case=label):
                self.spy = MarketSpy()
                self.install_gate(premium=True)
                self.install_market()
                result = tools.crypto_market_quote(7, {"symbol": "BTC"})
                self.assertTrue(result.ok)
                self.assertIn("quote:BTC", self.spy.reads)

    def test_reactivation_reopens_without_restart(self):
        """Same process, same module objects — only the resolver's answer moves."""
        self.install_gate(premium=False)
        self.install_market()
        self.assertFalse(tools.crypto_market_quote(7, {"symbol": "BTC"}).ok)
        self.assertEqual(self.spy.reads, [])

        self.install_gate(premium=True)
        reopened = tools.crypto_market_quote(7, {"symbol": "BTC"})
        self.assertTrue(reopened.ok)
        self.assertIn("quote:BTC", self.spy.reads)


class TestParkedContextIsNotAGrant(EntitlementHarness):
    """Stage 5 — the Market Pulse handoff names a subject, not a permission."""

    def test_context_symbol_is_never_resolved_while_locked(self):
        self.install_gate(premium=False)
        self.install_market()
        # No symbol argument at all: the only way to learn the coin is the
        # parked envelope. If the gate ran after resolution, the spy would
        # record `active_context_for_user` here.
        result = tools.crypto_market_quote(7, {})
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "premium_required")
        self.assertEqual(self.spy.reads, [])

    def test_context_still_supplies_the_symbol_when_entitled(self):
        self.install_gate(premium=True)
        self.install_market()
        result = tools.crypto_market_quote(7, {})
        self.assertTrue(result.ok)
        self.assertEqual(self.spy.reads[0], "active_context_for_user")
        self.assertIn("quote:BTC", self.spy.reads)


class TestStaleClientClaimCountsForNothing(EntitlementHarness):
    """Stage 2 — the client's belief is an argument, and arguments are data."""

    def test_client_asserted_premium_does_not_unlock(self):
        self.install_gate(premium=False)
        self.install_market()
        for claim in ({"is_premium": True}, {"premium": True},
                      {"tier": "PREMIUM"}, {"entitlement": {"premium": True}}):
            with self.subTest(claim=claim):
                self.spy = MarketSpy()
                self.install_market()
                arguments = {"symbol": "BTC"}
                arguments.update(claim)
                result = tools.crypto_market_quote(7, arguments)
                self.assertFalse(result.ok)
                self.assertEqual(result.error_code, "premium_required")
                self.assertEqual(self.spy.reads, [])


class TestGateFailsClosed(EntitlementHarness):
    """An unresolvable entitlement is not an entitlement."""

    def test_missing_gate_module_denies(self):
        sys.modules["services.crypto_premium_gate"] = None
        self.install_market()
        result = tools.crypto_market_quote(7, {"symbol": "BTC"})
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "premium_gate_unavailable")
        self.assertEqual(self.spy.reads, [])

    def test_exploding_gate_denies(self):
        module = types.ModuleType("services.crypto_premium_gate")
        module.CAP_CRYPTO_INTELLIGENCE = CAP_INTELLIGENCE

        def boom(user_id, capability):
            raise RuntimeError("resolver down")

        module.has_crypto_capability = boom
        module.premium_required_response = lambda capability: {}
        self.install("crypto_premium_gate", module)
        self.install_market()
        result = tools.crypto_market_quote(7, {"symbol": "BTC"})
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "premium_gate_error")
        self.assertEqual(self.spy.reads, [])


# ---------------------------------------------------------------------------
# Stage 5 — the prompt-grounding path, which needs no tool call at all
# ---------------------------------------------------------------------------


class TestGroundingBlockUnderExpiry(unittest.TestCase):
    """``grounding_block`` injects prices directly into the model's knowledge.

    It is reached by asking a question, not by invoking a capability, so it is
    the one path where "the tool is gated" would have been no defence at all.
    """

    _MISSING = object()

    def setUp(self) -> None:
        from services import undx_market_context

        self.module = undx_market_context
        self._saved = sys.modules.pop("services.crypto_premium_gate", self._MISSING)
        self._saved_attr = getattr(services, "crypto_premium_gate", self._MISSING)
        if hasattr(services, "crypto_premium_gate"):
            delattr(services, "crypto_premium_gate")

    def tearDown(self) -> None:
        if self._saved is self._MISSING:
            sys.modules.pop("services.crypto_premium_gate", None)
        else:
            sys.modules["services.crypto_premium_gate"] = self._saved
        if self._saved_attr is self._MISSING:
            if hasattr(services, "crypto_premium_gate"):
                delattr(services, "crypto_premium_gate")
        else:
            services.crypto_premium_gate = self._saved_attr

    def install_gate(self, *, premium: bool) -> None:
        module = types.ModuleType("services.crypto_premium_gate")
        module.CAP_CRYPTO_INTELLIGENCE = CAP_INTELLIGENCE
        module.has_crypto_capability = lambda user_id, capability: premium
        module.premium_required_response = lambda capability: {}
        sys.modules["services.crypto_premium_gate"] = module
        services.crypto_premium_gate = module

    def test_locked_block_carries_no_figures(self):
        self.install_gate(premium=False)
        for body, context in (
            ("what is the price of bitcoin", None),
            ("how is it doing", PARKED_BTC_CONTEXT),
            ("what is the crypto market doing today", None),
        ):
            with self.subTest(body=body):
                block = self.module.grounding_block(7, body, context)
                self.assertIsNotNone(block, "silence would make UNDX deny crypto exists")
                payload = json.loads(block["body"])
                self.assertTrue(payload.get("premium_required"))
                self.assertEqual(payload.get("capability"), CAP_INTELLIGENCE)
                # The locked reply cannot contain the thing being sold.
                for leaked in ("price", "quote", "history", "overview",
                               "watchlists", "alerts"):
                    self.assertNotIn(leaked, payload,
                                     f"{leaked} must not survive into a locked block")

    def test_non_crypto_turns_never_reach_the_resolver(self):
        """Stage 6 — general UNDX is untouched, proved by the gate never running."""
        asked: list[int] = []
        module = types.ModuleType("services.crypto_premium_gate")
        module.CAP_CRYPTO_INTELLIGENCE = CAP_INTELLIGENCE

        def has_crypto_capability(user_id, capability):
            asked.append(user_id)
            return False

        module.has_crypto_capability = has_crypto_capability
        module.premium_required_response = lambda capability: {}
        sys.modules["services.crypto_premium_gate"] = module
        services.crypto_premium_gate = module

        for body in ("Hello", "who am I following", "summarise my unread messages",
                     "draft a post about my weekend"):
            with self.subTest(body=body):
                self.assertIsNone(self.module.grounding_block(7, body, None))
        self.assertEqual(asked, [], "a non-crypto turn must not resolve entitlement")


if __name__ == "__main__":
    unittest.main()
