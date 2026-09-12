"""The cost budget: what has been spent, and when UNDX stops calling providers.

Three ways a budget can pass its own tests and restrain nothing, each with a
test below that fails when the corresponding line is removed.

* **It can be counted per process.** Nine processes serve this app; nine tallies
  against one limit is nine times the limit. A module-level dict looks perfectly
  shared inside one interpreter, so a single-process test cannot even fail on
  it. :func:`ShieldedLedgerTest.test_the_total_is_shared_across_real_processes`
  forks real subprocesses against a real database file, and its paired test
  demonstrates that the harness genuinely detects a non-shared tally — without
  that pair, a green result would prove only that the harness runs.

* **It can be denominated too coarsely.** Cents is the platform's usual money
  unit and would be catastrophic here: a measured $0.00006 call rounds to zero,
  so the counter sits at $0.00 while money leaves the account.

* **It can cover providers nobody can price.** Five of seven providers return
  `cost_usd: None` by deliberate policy. A dollar budget summed over those is
  enforced against Meta and silently blind to everyone else, while reporting
  itself as enforced. `uncovered_providers` and the token budget exist for that,
  and `BudgetCoverageTest` is where the claim is falsifiable.

Run:

    .venv/bin/python3 -m pytest tests/test_undx_cost_budget.py
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import undx_router  # noqa: E402
from services import undx_cost  # noqa: E402


# --------------------------------------------------------------------- helpers

class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise undx_router.requests.HTTPError(f"{self.status_code} Server Error")

    def json(self):
        return self._payload


def _chat(content="ok"):
    return {"choices": [{"message": {"content": content, "role": "assistant"},
                         "finish_reason": "stop"}],
            "model": "test-model"}


#: Every budget knob cleared. Without this a developer's own exported
#: `UNDX_MONTHLY_COST_BUDGET_USD` would leak into every assertion here, and the
#: suite would pass or fail depending on whose shell ran it.
_CLEAR_BUDGETS = {name: "" for name in undx_cost.BUDGET_ENV_VARS}


def _all_keys(**overrides):
    """Every provider credentialed and every budget cleared, then overrides.

    A test about budgets that accidentally exercises "not_configured" proves
    nothing about budgets.
    """
    base = {config.key_env: "k" * 40 for config in undx_router.PROVIDERS.values()}
    base["Gemini_AI_API"] = "k" * 40
    base["UNDX_DEFAULT_REQUEST_PRIVACY"] = ""
    base.update(_CLEAR_BUDGETS)
    base.update(overrides)
    return mock.patch.dict(os.environ, base)


def _usage(provider="meta", cost_usd=0.001873, input_tokens=100, output_tokens=200):
    return {"provider": provider, "model": "muse-spark-1.3",
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "reasoning_tokens": 0, "cached_tokens": 0,
            "cost_usd": cost_usd, "cost_reported": False}


def _snapshot(**providers):
    """A ledger snapshot built by hand, so budget arithmetic can be tested
    without a database standing between the input and the assertion."""
    out = {}
    for name, spec in providers.items():
        out[name] = {"calls": spec.get("calls", 1),
                     "input_tokens": spec.get("input_tokens", 0),
                     "output_tokens": spec.get("output_tokens", 0),
                     "reasoning_tokens": 0,
                     "cost_micro_usd": spec.get("cost_micro_usd", 0),
                     "uncosted_calls": spec.get("uncosted_calls", 0)}
    return {"month": undx_cost.current_month(), "providers": out, "source": "ledger"}


class _LedgerCase(unittest.TestCase):
    """Base: one real SQLite file, wired in as the platform database."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._dir.name, "ledger.db")
        undx_cost.reset_for_tests()
        self._env = mock.patch.dict(os.environ, {"DATABASE_URL": f"sqlite:///{self.db_path}"})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.addCleanup(self._dir.cleanup)
        self.addCleanup(undx_cost.reset_for_tests)


# ------------------------------------------------------------- price authority

class PriceAuthorityTest(unittest.TestCase):

    def test_there_is_one_price_table_not_two(self):
        """`undx_router.PRICE_PER_MILLION_USD` must *be* the cost module's table,
        not a copy of it. A copy would keep working while disagreeing, and the
        disagreement would only surface as a wrong budget decision."""
        self.assertIs(undx_router.PRICE_PER_MILLION_USD,
                      undx_cost.PRICE_PER_MILLION_USD)

    def test_an_unpriced_model_costs_none_rather_than_zero(self):
        self.assertIsNone(undx_cost.estimate_cost_usd("llama-3.1-8b-instant", 1000, 1000))
        self.assertFalse(undx_cost.is_priced("llama-3.1-8b-instant"))

    def test_the_router_still_costs_what_it_used_to(self):
        """The move of the table must not change any figure. Meta Standard at
        100 in / 200 out: 100*1.25/1e6 + 200*4.25/1e6."""
        usage = undx_router._normalise_usage(
            "meta", "muse-spark-1.3",
            {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300})
        self.assertAlmostEqual(usage["cost_usd"], 0.000975, places=6)


class MoneyUnitTest(unittest.TestCase):
    """Cents would make this control vacuous. That is worth a test, not a note."""

    def test_a_sixty_microdollar_call_is_not_rounded_away(self):
        """The cheapest cost measured live was $0.00006 of Perplexity tokens."""
        self.assertEqual(undx_cost.to_micro_usd(0.00006), 60)

    def test_cents_would_have_lost_it(self):
        """The falsifying comparison, written out: this is what the obvious
        choice would have produced, and why it was not made."""
        self.assertEqual(int(round(0.00006 * 100)), 0)
        self.assertGreater(undx_cost.to_micro_usd(0.00006), 0)

    def test_a_missing_cost_is_zero_money_but_the_caller_must_count_it(self):
        self.assertEqual(undx_cost.to_micro_usd(None), 0)

    def test_a_small_limit_is_not_formatted_as_zero(self):
        """A refusal reading "$0.0037 of $0.00 spent" says the limit was zero.
        It is false, and it is how a working control gets reported as a bug."""
        snapshot = _snapshot(meta={"cost_micro_usd": 3746})
        with mock.patch.dict(os.environ, {**_CLEAR_BUDGETS,
                                          "UNDX_MONTHLY_COST_BUDGET_USD": "0.003"}):
            reason = undx_cost.refusal(snapshot, "meta", "muse-spark-1.3")
        self.assertIn("$0.003", reason)
        self.assertNotIn("$0.00 ", reason)


# -------------------------------------------------------------------- ledger

class LedgerTest(_LedgerCase):

    def test_a_recorded_call_lands_in_the_shared_table(self):
        undx_cost.record(_usage())
        snapshot = undx_cost.month_snapshot()
        self.assertEqual(snapshot["source"], "ledger")
        self.assertEqual(snapshot["providers"]["meta"]["calls"], 1)
        self.assertEqual(snapshot["providers"]["meta"]["cost_micro_usd"], 1873)

    def test_two_calls_accumulate_rather_than_replace(self):
        undx_cost.record(_usage())
        undx_cost.record(_usage())
        row = undx_cost.month_snapshot()["providers"]["meta"]
        self.assertEqual(row["calls"], 2)
        self.assertEqual(row["cost_micro_usd"], 3746)

    def test_an_unpriced_call_is_counted_as_uncosted_not_as_free(self):
        """This is the difference between "we spent nothing" and "we do not know
        what we spent". A budget cannot tell those apart from the dollar column
        alone, which is exactly why there is a second column."""
        undx_cost.record(_usage(provider="groq", cost_usd=None))
        row = undx_cost.month_snapshot()["providers"]["groq"]
        self.assertEqual(row["cost_micro_usd"], 0)
        self.assertEqual(row["uncosted_calls"], 1)
        self.assertTrue(undx_cost.budget_state()["spend_is_a_floor"])

    def test_providers_are_counted_separately(self):
        undx_cost.record(_usage(provider="meta"))
        undx_cost.record(_usage(provider="openai", cost_usd=None))
        providers = undx_cost.month_snapshot()["providers"]
        self.assertEqual(sorted(providers), ["meta", "openai"])

    def test_the_unique_index_exists_so_the_upsert_can_work(self):
        """`ON CONFLICT (month, provider) DO UPDATE` is not a hint; without the
        index it is a syntax error at runtime and every write fails."""
        undx_cost.ensure_schema()
        conn = sqlite3.connect(self.db_path)
        try:
            names = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
                (undx_cost.LEDGER_TABLE,))}
        finally:
            conn.close()
        self.assertIn(f"ux_{undx_cost.LEDGER_TABLE}_month_provider", names)

    def test_ensure_schema_is_idempotent(self):
        undx_cost.ensure_schema()
        undx_cost.ensure_schema()
        undx_cost.record(_usage())
        self.assertEqual(undx_cost.month_snapshot()["providers"]["meta"]["calls"], 1)


class LedgerFailureTest(_LedgerCase):
    """A database fault must not become an outage *or* a silent open door."""

    def _broken(self):
        def boom():
            raise sqlite3.OperationalError("no such database")
        return mock.patch.object(undx_cost, "_connect", side_effect=boom)

    def test_a_write_failure_does_not_raise(self):
        with self._broken():
            undx_cost.record(_usage())  # must not raise

    def test_a_write_failure_degrades_to_process_memory_and_admits_it(self):
        with self._broken():
            undx_cost.record(_usage())
            snapshot = undx_cost.month_snapshot()
        self.assertEqual(snapshot["source"], "process")
        self.assertEqual(snapshot["providers"]["meta"]["cost_micro_usd"], 1873)
        self.assertGreaterEqual(undx_cost.stats()["degraded"], 1)

    def test_the_source_field_distinguishes_an_empty_month_from_a_broken_ledger(self):
        """Both look like "nothing spent". They need opposite reactions, so the
        snapshot has to say which one it is."""
        empty = undx_cost.month_snapshot()
        self.assertEqual(empty["source"], "ledger")
        self.assertEqual(empty["providers"], {})
        with self._broken():
            broken = undx_cost.month_snapshot()
        self.assertEqual(broken["source"], "process")

    def test_a_degraded_budget_still_enforces_on_what_it_can_see(self):
        """Degrading must not mean giving up. The process mirror under-counts,
        so the limit is reached later than it should be — but it is still
        reached, which is the difference between a lenient control and none."""
        with self._broken():
            for _ in range(3):
                undx_cost.record(_usage(cost_usd=1.0))
            snapshot = undx_cost.month_snapshot()
        with mock.patch.dict(os.environ, {**_CLEAR_BUDGETS,
                                          "UNDX_MONTHLY_COST_BUDGET_USD": "2"}):
            self.assertIn("cost_budget", undx_cost.refusal(snapshot, "meta", "muse-spark-1.3"))


class ShieldedLedgerTest(unittest.TestCase):
    """The only property that matters: is the total shared between processes?

    Nothing running inside one interpreter can answer this. A module-level dict
    is indistinguishable from a shared table until a second OS process exists.
    """

    _CHILD = textwrap.dedent("""
        import os, sys
        sys.path.insert(0, {repo!r})
        os.environ["DATABASE_URL"] = "sqlite:///" + {db!r}
        for name in ("UNDX_MONTHLY_COST_BUDGET_USD", "UNDX_PROVIDER_COST_BUDGET_USD",
                     "UNDX_MONTHLY_TOKEN_BUDGET", "UNDX_PROVIDER_TOKEN_BUDGET"):
            os.environ.pop(name, None)
        from services import undx_cost
        for _ in range({calls}):
            undx_cost.record({{"provider": "meta", "model": "muse-spark-1.3",
                              "input_tokens": 10, "output_tokens": 20,
                              "reasoning_tokens": 0, "cost_usd": 0.001}})
        print(undx_cost.month_snapshot()["providers"]["meta"]["calls"])
    """)

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db_path = os.path.join(self._dir.name, "shared.db")

    def _run_child(self, calls):
        code = self._CHILD.format(repo=REPO_ROOT, db=self.db_path, calls=calls)
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                              text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return int(proc.stdout.strip().splitlines()[-1])

    def test_the_total_is_shared_across_real_processes(self):
        """Three separate interpreters, five calls each. The last one must see
        fifteen. A per-process tally sees five and this fails."""
        seen = [self._run_child(5) for _ in range(3)]
        self.assertEqual(seen, [5, 10, 15])

    def test_a_process_local_tally_would_fail_this_harness(self):
        """Proof the harness detects the thing it claims to detect. Without
        this, a green result above would only show that subprocesses run."""
        code = textwrap.dedent("""
            counts = {"n": 0}
            for _ in range(5):
                counts["n"] += 1
            print(counts["n"])
        """)
        seen = []
        for _ in range(3):
            proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                  text=True, timeout=60)
            seen.append(int(proc.stdout.strip()))
        self.assertEqual(seen, [5, 5, 5])
        self.assertNotEqual(seen, [5, 10, 15])

    def test_the_total_survives_a_restart(self):
        """A budget that a deploy resets is a budget that a busy deploy day
        turns off."""
        self.assertEqual(self._run_child(4), 4)
        self.assertEqual(self._run_child(0), 4)


# -------------------------------------------------------------------- budgets

class BudgetConfigTest(unittest.TestCase):

    def test_nothing_is_enforced_until_something_is_configured(self):
        with mock.patch.dict(os.environ, _CLEAR_BUDGETS):
            self.assertFalse(undx_cost.budgets_configured())
            self.assertEqual(undx_cost.refusal(_snapshot(meta={"cost_micro_usd": 10 ** 9}),
                                               "meta", "muse-spark-1.3"), "")

    def test_a_global_dollar_budget_refuses_once_reached(self):
        snapshot = _snapshot(meta={"cost_micro_usd": 5_000_000})  # $5.00
        with mock.patch.dict(os.environ, {**_CLEAR_BUDGETS,
                                          "UNDX_MONTHLY_COST_BUDGET_USD": "5"}):
            reason = undx_cost.refusal(snapshot, "meta", "muse-spark-1.3")
        self.assertIn("cost_budget", reason)
        self.assertIn("$5.00", reason)

    def test_a_budget_not_yet_reached_permits_the_call(self):
        snapshot = _snapshot(meta={"cost_micro_usd": 4_999_999})
        with mock.patch.dict(os.environ, {**_CLEAR_BUDGETS,
                                          "UNDX_MONTHLY_COST_BUDGET_USD": "5"}):
            self.assertEqual(undx_cost.refusal(snapshot, "meta", "muse-spark-1.3"), "")

    def test_a_provider_budget_refuses_only_that_provider(self):
        snapshot = _snapshot(meta={"cost_micro_usd": 3_000_000},
                             openai={"cost_micro_usd": 0})
        with mock.patch.dict(os.environ, {**_CLEAR_BUDGETS,
                                          "UNDX_PROVIDER_COST_BUDGET_USD": "meta=2"}):
            self.assertIn("cost_budget", undx_cost.refusal(snapshot, "meta", "muse-spark-1.3"))
            self.assertEqual(undx_cost.refusal(snapshot, "openai", "gpt-4o-mini"), "")

    def test_a_malformed_entry_does_not_disable_the_others(self):
        """The reason this is not a JSON variable. A typo must cost one
        provider's budget, not the whole map — a broken JSON document parses to
        nothing and turns every configured budget off at once, which is the one
        failure a cost control cannot afford."""
        parsed = undx_cost._parse_per_provider("meta=2,garbage,openai=,claude=3")
        self.assertEqual(parsed, {"meta": 2.0, "claude": 3.0})

    def test_a_negative_or_zero_budget_is_treated_as_unset(self):
        with mock.patch.dict(os.environ, {**_CLEAR_BUDGETS,
                                          "UNDX_MONTHLY_COST_BUDGET_USD": "-5"}):
            self.assertEqual(undx_cost.global_cost_budget_usd(), 0.0)
            self.assertFalse(undx_cost.budgets_configured())


class TokenBudgetTest(unittest.TestCase):
    """The budget that covers everyone, because tokens are always reported."""

    def test_a_token_budget_restrains_a_provider_with_no_price(self):
        snapshot = _snapshot(groq={"input_tokens": 600, "output_tokens": 600,
                                   "uncosted_calls": 12})
        with mock.patch.dict(os.environ, {**_CLEAR_BUDGETS,
                                          "UNDX_MONTHLY_TOKEN_BUDGET": "1000"}):
            reason = undx_cost.refusal(snapshot, "groq", "llama-3.1-8b-instant")
        self.assertIn("token_budget", reason)

    def test_a_dollar_budget_alone_would_not_have(self):
        """Same snapshot, same unpriced provider, dollar budget instead. It
        permits the call — which is the vacuity this module is built around,
        demonstrated rather than asserted."""
        snapshot = _snapshot(groq={"input_tokens": 600, "output_tokens": 600,
                                   "uncosted_calls": 12})
        with mock.patch.dict(os.environ, {**_CLEAR_BUDGETS,
                                          "UNDX_MONTHLY_COST_BUDGET_USD": "0.01"}):
            self.assertEqual(undx_cost.refusal(snapshot, "groq", "llama-3.1-8b-instant"), "")

    def test_a_provider_token_budget_refuses_only_that_provider(self):
        snapshot = _snapshot(groq={"input_tokens": 900, "output_tokens": 200},
                             openai={"input_tokens": 10, "output_tokens": 10})
        with mock.patch.dict(os.environ, {**_CLEAR_BUDGETS,
                                          "UNDX_PROVIDER_TOKEN_BUDGET": "groq=1000"}):
            self.assertIn("token_budget", undx_cost.refusal(snapshot, "groq", "llama-3.1-8b-instant"))
            self.assertEqual(undx_cost.refusal(snapshot, "openai", "gpt-4o-mini"), "")


class BudgetCoverageTest(unittest.TestCase):
    """What the dollar ceiling is not watching, stated rather than implied."""

    MODELS = {"meta": "muse-spark-1.3", "openai": "gpt-4o-mini",
              "groq": "llama-3.1-8b-instant"}

    def test_unpriced_providers_are_named(self):
        self.assertEqual(undx_cost.uncovered_providers(self.MODELS), ["groq", "openai"])

    def test_the_budget_state_publishes_them(self):
        with mock.patch.dict(os.environ, {**_CLEAR_BUDGETS,
                                          "UNDX_MONTHLY_COST_BUDGET_USD": "5"}):
            state = undx_cost.budget_state(_snapshot(meta={"cost_micro_usd": 1}),
                                           self.MODELS)
        self.assertEqual(state["uncovered_providers"], ["groq", "openai"])
        self.assertTrue(state["enforced"])

    def test_the_router_reports_coverage_for_the_models_it_would_send(self):
        """Read through `_model()`, so flipping an env var changes the answer.
        Coverage computed from the defaults would describe a deployment that is
        not this one."""
        with _all_keys(META_MUSE_MODEL="muse-spark-1.3-contributor"):
            self.assertNotIn("meta", undx_router.budget_state()["uncovered_providers"])
        with _all_keys(META_MUSE_MODEL="some-unlisted-model"):
            self.assertIn("meta", undx_router.budget_state()["uncovered_providers"])

    def test_strict_mode_is_off_by_default(self):
        with mock.patch.dict(os.environ, _CLEAR_BUDGETS):
            self.assertFalse(undx_cost.strict_cost_budget())

    def test_strict_mode_refuses_what_it_cannot_measure(self):
        snapshot = _snapshot(meta={"cost_micro_usd": 1})
        env = {**_CLEAR_BUDGETS, "UNDX_MONTHLY_COST_BUDGET_USD": "5",
               "UNDX_COST_BUDGET_STRICT": "true"}
        with mock.patch.dict(os.environ, env):
            self.assertIn("cost_budget_strict",
                          undx_cost.refusal(snapshot, "groq", "llama-3.1-8b-instant"))
            self.assertEqual(undx_cost.refusal(snapshot, "meta", "muse-spark-1.3"), "")

    def test_strict_mode_does_nothing_without_a_dollar_budget(self):
        """It answers "can this spend be counted", and with no dollar budget
        there is nothing to count it against. Refusing there would take five
        providers offline for no benefit at all."""
        env = {**_CLEAR_BUDGETS, "UNDX_COST_BUDGET_STRICT": "true",
               "UNDX_MONTHLY_TOKEN_BUDGET": "1000000"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(
                undx_cost.refusal(_snapshot(), "groq", "llama-3.1-8b-instant"), "")

    def test_the_strict_refusal_does_not_claim_the_budget_was_spent(self):
        """"Over budget" would be a lie: nothing was spent, the spend simply
        cannot be seen. An operator who reads the first will go looking for
        usage that is not there."""
        env = {**_CLEAR_BUDGETS, "UNDX_MONTHLY_COST_BUDGET_USD": "5",
               "UNDX_COST_BUDGET_STRICT": "true"}
        with mock.patch.dict(os.environ, env):
            reason = undx_cost.refusal(_snapshot(), "groq", "llama-3.1-8b-instant")
        self.assertIn("no verified price", reason)


# -------------------------------------------------------------------- routing

class RoutingEnforcementTest(unittest.TestCase):
    """The gate has to be at the egress point, like the privacy ceiling, or the
    two paths that skip `provider_priority()` skip it too."""

    def setUp(self):
        undx_router.reset_provider_health()
        undx_cost.reset_for_tests()
        self.addCleanup(undx_router.reset_provider_health)
        self.addCleanup(undx_cost.reset_for_tests)
        self._snap = mock.patch.object(
            undx_cost, "month_snapshot",
            return_value=_snapshot(openai={"cost_micro_usd": 9_000_000},
                                   gemini={"cost_micro_usd": 9_000_000}))
        self._snap.start()
        self.addCleanup(self._snap.stop)

    def test_naming_a_provider_explicitly_does_not_bypass_the_budget(self):
        """`route_structured_request(providers=["openai"])` never calls
        `provider_priority`. If the gate lived there, this would spend."""
        with _all_keys(UNDX_MONTHLY_COST_BUDGET_USD="5"), \
                mock.patch.object(undx_router.requests, "post") as post:
            result = undx_router.route_structured_request(
                1, "sys", "hi", providers=["openai"], privacy_class="PUBLIC")
        post.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual([a["status"] for a in result["attempts"]], ["budget_exceeded"])

    def test_disabling_the_router_does_not_bypass_the_budget(self):
        """With `UNDX_ROUTER_ENABLED` off the plan collapses to the default
        provider without consulting `provider_priority` either."""
        with _all_keys(UNDX_ROUTER_ENABLED="0", UNDX_DEFAULT_AI_PROVIDER="gemini",
                       UNDX_MONTHLY_COST_BUDGET_USD="5"), \
                mock.patch.object(undx_router.requests, "post") as post:
            result = undx_router.route_structured_request(
                1, "sys", "hi", privacy_class="PUBLIC")
        post.assert_not_called()
        self.assertFalse(result["ok"])

    def test_a_permitted_provider_still_answers(self):
        """The control must not be a blanket refusal wearing a budget's name."""
        with _all_keys(UNDX_ROUTER_ENABLED="1", UNDX_DEFAULT_AI_PROVIDER="openai"), \
                mock.patch.object(undx_router.requests, "post",
                                  return_value=_FakeResponse(_chat("hello"))):
            result = undx_router.route_structured_request(
                1, "sys", "hi", providers=["openai"], privacy_class="PUBLIC")
        self.assertTrue(result["ok"])

    def test_a_budget_refusal_is_402_not_502(self):
        """Not an outage. Reporting a deliberate spend limit as a bad gateway
        sends an operator to look at provider uptime, and the reflex that
        follows is to raise the limit to clear the alert."""
        with _all_keys(UNDX_ROUTER_ENABLED="0", UNDX_DEFAULT_AI_PROVIDER="openai",
                       UNDX_MONTHLY_COST_BUDGET_USD="5"), \
                mock.patch.object(undx_router.requests, "post"):
            result = undx_router.route_undx_request(1, "hi", privacy_class="PUBLIC")
        self.assertEqual(result["status"], 402)
        self.assertIn("spend limit", result["error"])

    def test_the_402_says_it_is_our_limit_not_the_vendor_s(self):
        """DeepSeek returns a real upstream 402 when its account is unfunded.
        The two must be distinguishable without opening the ledger."""
        with _all_keys(UNDX_ROUTER_ENABLED="0", UNDX_DEFAULT_AI_PROVIDER="openai",
                       UNDX_MONTHLY_COST_BUDGET_USD="5"), \
                mock.patch.object(undx_router.requests, "post"):
            result = undx_router.route_undx_request(1, "hi", privacy_class="PUBLIC")
        self.assertIn("configured for this deployment", result["error"])
        self.assertIn("cost_budget", result["router"]["attempts"][0]["detail"])

    def test_a_refused_provider_costs_no_request_and_no_breaker_failure(self):
        """A budget refusal is not a provider fault. Counting it as one would
        open the circuit on a healthy provider and keep it shut after the
        budget was raised."""
        with _all_keys(UNDX_ROUTER_ENABLED="0", UNDX_DEFAULT_AI_PROVIDER="openai",
                       UNDX_MONTHLY_COST_BUDGET_USD="5"), \
                mock.patch.object(undx_router.requests, "post") as post:
            for _ in range(5):
                undx_router.route_undx_request(1, "hi", privacy_class="PUBLIC")
        post.assert_not_called()
        health = undx_router.provider_runtime_health().get("openai", {})
        self.assertEqual(health.get("consecutive_failures", 0), 0)

    def test_privacy_is_decided_before_budget(self):
        """Order matters. If cost were asked first, a spend limit could be the
        reason a disclosure did not happen — and the day the limit was raised,
        it would happen. The refusal must name the ceiling, not the money."""
        with _all_keys(UNDX_ROUTER_ENABLED="0", UNDX_DEFAULT_AI_PROVIDER="gemini",
                       UNDX_MONTHLY_COST_BUDGET_USD="5"), \
                mock.patch.object(undx_router.requests, "post") as post:
            result = undx_router.route_structured_request(
                1, "sys", "hi", providers=["gemini"], privacy_class="RESTRICTED")
        post.assert_not_called()
        self.assertEqual([a["status"] for a in result["attempts"]], ["privacy_refused"])

    def test_a_mixed_chain_is_not_reported_as_a_pure_budget_failure(self):
        """One refused on privacy and one on budget is neither story. Calling it
        402 would hide the ceiling; calling it 403 would hide the limit."""
        with _all_keys(UNDX_ROUTER_ENABLED="1", UNDX_MULTI_MODEL_MODE="1",
                       UNDX_MONTHLY_COST_BUDGET_USD="5"), \
                mock.patch.object(undx_router.requests, "post"):
            result = undx_router.route_undx_request(1, "what happened today?",
                                                    privacy_class="CONFIDENTIAL")
        statuses = {a["status"] for a in result["router"]["attempts"]}
        self.assertEqual(statuses, {"privacy_refused", "budget_exceeded"})
        self.assertNotIn(result["status"], (402, 403))

    def test_the_envelope_says_whether_a_budget_was_enforced(self):
        """Present with `enforced: false` when nothing is configured. An absent
        key is what a budget that has quietly stopped running also looks like."""
        with _all_keys(UNDX_ROUTER_ENABLED="0", UNDX_DEFAULT_AI_PROVIDER="meta"), \
                mock.patch.object(undx_router.requests, "post",
                                  return_value=_FakeResponse(_chat("hello"))):
            result = undx_router.route_undx_request(1, "hi", privacy_class="PUBLIC")
        self.assertTrue(result["ok"])
        self.assertIn("budget", result["router"])
        self.assertFalse(result["router"]["budget"]["enforced"])


class NoBudgetNoDatabaseTest(unittest.TestCase):
    """An unconfigured budget must not put a query on the request path."""

    def test_no_budget_means_no_ledger_read(self):
        with _all_keys(UNDX_ROUTER_ENABLED="0", UNDX_DEFAULT_AI_PROVIDER="openai"), \
                mock.patch.object(undx_cost, "month_snapshot") as snapshot, \
                mock.patch.object(undx_router.requests, "post",
                                  return_value=_FakeResponse(_chat("hi"))):
            undx_router.route_undx_request(1, "hi", privacy_class="PUBLIC")
        snapshot.assert_not_called()

    def test_a_configured_budget_reads_the_ledger_once_not_once_per_provider(self):
        with _all_keys(UNDX_ROUTER_ENABLED="1", UNDX_MULTI_MODEL_MODE="1",
                       UNDX_MONTHLY_COST_BUDGET_USD="5"), \
                mock.patch.object(undx_cost, "month_snapshot",
                                  return_value=_snapshot()) as snapshot, \
                mock.patch.object(undx_router.requests, "post",
                                  return_value=_FakeResponse(_chat("hi"))):
            undx_router.route_undx_request(1, "hi", privacy_class="PUBLIC")
        self.assertEqual(snapshot.call_count, 1)


class RecordingTest(_LedgerCase):
    """A successful call must reach the ledger, or the budget counts nothing."""

    def test_a_successful_route_is_written_to_the_ledger(self):
        """Meta, because it is one of only two providers with a verified price —
        so this can assert the money and not merely the row."""
        undx_router.reset_provider_health()
        self.addCleanup(undx_router.reset_provider_health)
        payload = dict(_chat("hello"))
        payload["usage"] = {"prompt_tokens": 100, "completion_tokens": 200,
                            "total_tokens": 300}
        with _all_keys(DATABASE_URL=f"sqlite:///{self.db_path}"), \
                mock.patch.object(undx_router.requests, "post",
                                  return_value=_FakeResponse(payload)):
            result = undx_router.route_structured_request(
                1, "sys", "hi", providers=["meta"], privacy_class="PUBLIC")
        self.assertTrue(result["ok"])
        row = undx_cost.month_snapshot()["providers"]["meta"]
        self.assertEqual(row["calls"], 1)
        # 100 * 1.25/1e6 + 200 * 4.25/1e6 = $0.000975 = 975 micro-USD.
        self.assertEqual(row["cost_micro_usd"], 975)

    def test_the_other_route_writes_too(self):
        """`route_undx_request` is a second, separately written loop. A ledger
        wired into only one of them would look complete from either end."""
        undx_router.reset_provider_health()
        self.addCleanup(undx_router.reset_provider_health)
        with _all_keys(DATABASE_URL=f"sqlite:///{self.db_path}"), \
                mock.patch.object(undx_router.requests, "post",
                                  return_value=_FakeResponse(_chat("hi"))):
            result = undx_router.route_undx_request(1, "hi", privacy_class="PUBLIC")
        self.assertTrue(result["ok"])
        self.assertEqual(sum(p["calls"] for p in
                             undx_cost.month_snapshot()["providers"].values()), 1)

    def test_the_kill_switch_path_ignores_the_configured_default(self):
        """Not a budget claim — a fact found while writing these, pinned so it
        stops being a surprise. `route_structured_request` falls back to
        `default_provider()` when the router is off; `route_undx_request` falls
        back to the literal "openai". So the two disabled-router paths disagree
        about which provider a deployment considers its default, and §1's "do
        not promote any provider to global default" is already half-violated by
        a hardcode nobody declared."""
        undx_router.reset_provider_health()
        self.addCleanup(undx_router.reset_provider_health)
        with _all_keys(UNDX_ROUTER_ENABLED="0", UNDX_DEFAULT_AI_PROVIDER="meta",
                       DATABASE_URL=f"sqlite:///{self.db_path}"), \
                mock.patch.object(undx_router.requests, "post",
                                  return_value=_FakeResponse(_chat("hi"))):
            result = undx_router.route_undx_request(1, "hi", privacy_class="PUBLIC")
        self.assertEqual(result["provider"], "openai")
        self.assertIn("openai", undx_cost.month_snapshot()["providers"])

    def test_a_ledger_outage_does_not_fail_the_request(self):
        """The money is spent either way. Turning a bookkeeping fault into a
        user-visible error would be strictly the worse outcome."""
        undx_router.reset_provider_health()
        self.addCleanup(undx_router.reset_provider_health)
        with _all_keys(UNDX_ROUTER_ENABLED="0", UNDX_DEFAULT_AI_PROVIDER="meta"), \
                mock.patch.object(undx_cost, "_connect",
                                  side_effect=sqlite3.OperationalError("gone")), \
                mock.patch.object(undx_router.requests, "post",
                                  return_value=_FakeResponse(_chat("hello"))):
            result = undx_router.route_undx_request(1, "hi", privacy_class="PUBLIC")
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(undx_cost.stats()["write_failures"], 1)


if __name__ == "__main__":
    unittest.main()
