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


#: Distinguishes "caller did not pass call_kind" from "caller passed None".
#: `_usage()` must be able to build a dict with **no** `call_kind` key at all,
#: because that is the shape `undx_router._normalise_usage` actually produces
#: today and so the shape every historical row was written from. A default of
#: `None` would look equivalent - `normalize_call_kind` maps both to `chat` - but
#: it would quietly convert the omission tests into None-handling tests, and the
#: compatibility claim being made is about a *missing key*.
_OMITTED = object()


def _usage(provider="meta", cost_usd=0.001873, input_tokens=100, output_tokens=200,
           call_kind=_OMITTED, model="muse-spark-1.3"):
    usage = {"provider": provider, "model": model,
             "input_tokens": input_tokens, "output_tokens": output_tokens,
             "total_tokens": input_tokens + output_tokens,
             "reasoning_tokens": 0, "cached_tokens": 0,
             "cost_usd": cost_usd, "cost_reported": False}
    if call_kind is not _OMITTED:
        usage["call_kind"] = call_kind
    return usage


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

    def _indexes(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
                (undx_cost.LEDGER_TABLE,))}
        finally:
            conn.close()

    def test_the_unique_index_exists_so_the_upsert_can_work(self):
        """`ON CONFLICT (month, provider, call_kind, model) DO UPDATE` is not a
        hint; without the index it is a syntax error at runtime and every write
        fails.

        Asserted as set membership rather than a substring check on purpose, and
        the choice has now paid off twice. Each generation of this name contains
        the previous one as a **prefix** — `..._month_provider`, then
        `..._month_provider_kind`, now `..._month_provider_kind_model` — so an
        `in` against the joined names would have passed unchanged through both
        migrations while asserting nothing. Same trap as
        `"available" in "unavailable"`. This test failed on the `model` widening,
        which is what a name assertion is supposed to do.
        """
        undx_cost.ensure_schema()
        self.assertIn(f"ux_{undx_cost.LEDGER_TABLE}_month_provider_kind_model",
                      self._indexes())

    def test_the_narrow_indexes_are_dropped_only_after_the_wide_one_exists(self):
        """Both narrower predecessors must go, and must go last.

        Each is strictly narrower than the current index, so while one stands the
        first row differing only in the newest column collides and the write
        fails: `(month, provider)` blocked the first embedding row for a provider
        that already had a chat row, and `(month, provider, call_kind)` blocks the
        second *model* for a provider's chat spend. Dropping them is part of the
        migration, not tidying.

        The ordering half matters too: if the wide index failed to create and the
        narrow ones were already gone, the table would have no unique index at
        all, and `ON CONFLICT` against a non-existent constraint is a runtime
        error on *every* write rather than only on the new dimension. Verified by
        reading the statement order rather than by trusting the comment, since a
        comment cannot fail.
        """
        undx_cost.ensure_schema()
        names = self._indexes()
        self.assertNotIn(f"ux_{undx_cost.LEDGER_TABLE}_month_provider", names)
        self.assertNotIn(f"ux_{undx_cost.LEDGER_TABLE}_month_provider_kind", names)
        statements = list(undx_cost._SCHEMA_STATEMENTS)
        creates = next(i for i, s in enumerate(statements) if "CREATE UNIQUE INDEX" in s)
        drops = [i for i, s in enumerate(statements) if "DROP INDEX" in s]
        self.assertEqual(len(drops), 2, "both predecessors should be dropped")
        self.assertLess(creates, min(drops),
                        "a narrow index is dropped before its replacement exists")

    def test_an_old_table_is_migrated_and_its_rows_are_backfilled_as_chat(self):
        """Production had rows before this column existed; they are chat.

        `undx_cost_ledger` in production held one row when the column was added
        (`('2026-09', 'openai', 13 calls)`). Every call site that could have
        written it was chat, so `chat` is the only backfill value that does not
        misattribute history — and it has to agree with both the column DEFAULT
        and `normalize_call_kind(None)`, or a row written by an old worker mid
        deploy would land under a different name than the same call written by a
        new one.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(f"""
                DROP TABLE IF EXISTS {undx_cost.LEDGER_TABLE};
                CREATE TABLE {undx_cost.LEDGER_TABLE} (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, month TEXT NOT NULL,
                  provider TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0,
                  input_tokens INTEGER NOT NULL DEFAULT 0,
                  output_tokens INTEGER NOT NULL DEFAULT 0,
                  reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                  cost_micro_usd INTEGER NOT NULL DEFAULT 0,
                  uncosted_calls INTEGER NOT NULL DEFAULT 0,
                  updated_at TEXT NOT NULL DEFAULT (datetime('now')));
                CREATE UNIQUE INDEX ux_{undx_cost.LEDGER_TABLE}_month_provider
                  ON {undx_cost.LEDGER_TABLE}(month, provider);
                INSERT INTO {undx_cost.LEDGER_TABLE}
                  (month, provider, calls, uncosted_calls)
                  VALUES ('{undx_cost.current_month()}', 'openai', 13, 13);
            """)
            conn.commit()
        finally:
            conn.close()

        undx_cost.ensure_schema()

        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                f"SELECT call_kind, calls FROM {undx_cost.LEDGER_TABLE}").fetchone()
        finally:
            conn.close()
        self.assertEqual(row, (undx_cost.CALL_KIND_CHAT, 13))
        self.assertEqual(undx_cost.normalize_call_kind(None), undx_cost.CALL_KIND_CHAT)

        # And the migrated table now accepts what the old index made impossible.
        undx_cost.record(_usage(provider="openai", call_kind="embedding", cost_usd=None))
        snapshot = undx_cost.month_snapshot()
        self.assertEqual(snapshot["providers"]["openai"]["calls"], 14)
        self.assertEqual(sorted(snapshot["kinds"]), ["chat", "embedding"])


class CallKindTest(_LedgerCase):
    """Which *sort* of AI call the money went on (§21-22).

    A ledger keyed only on provider can say "OpenAI cost $40" and cannot say
    whether that was chat, embeddings or images — the one question a spend
    decision turns on. The census found four paid non-chat call sites reaching no
    ledger at all, so the column is the place they land.
    """

    def test_a_provider_can_now_carry_more_than_one_kind(self):
        """The whole point of widening the key. Under `(month, provider)` the
        second kind for a provider was a unique-constraint collision."""
        undx_cost.record(_usage(provider="openai", call_kind="chat", cost_usd=None))
        undx_cost.record(_usage(provider="openai", call_kind="embedding", cost_usd=None))
        undx_cost.record(_usage(provider="openai", call_kind="image", cost_usd=None))
        snapshot = undx_cost.month_snapshot()
        self.assertEqual(snapshot["providers"]["openai"]["calls"], 3)
        self.assertEqual(sorted(snapshot["kinds"]), ["chat", "embedding", "image"])

    def test_provider_totals_still_sum_across_kinds(self):
        """Adding a dimension to a measurement must not change the measurement.

        Every budget, refusal and dashboard in this module reads
        `snapshot["providers"]`. If that started meaning "chat only", a provider's
        month-to-date would silently *shrink* the day embeddings began being
        recorded — a budget reporting more headroom than exists, caused by better
        instrumentation. So `providers` is summed across kinds and `kinds` is
        reported alongside it, never instead of it.
        """
        undx_cost.record(_usage(provider="openai", call_kind="chat", cost_usd=0.001))
        undx_cost.record(_usage(provider="openai", call_kind="embedding", cost_usd=0.002))
        providers = undx_cost.month_snapshot()["providers"]
        self.assertEqual(providers["openai"]["cost_micro_usd"], 3000)
        self.assertEqual(providers["openai"]["calls"], 2)

    def test_an_omitted_kind_is_chat_and_an_unrecognised_one_is_not(self):
        """The two must not collapse, and `chat` is the wrong home for a typo.

        Absent means chat: every call site predating the column was chat, and the
        column default and the backfill both say so. Present-but-unrecognised
        means `unknown`: mapping `emmbedding` onto the largest existing bucket is
        precisely how non-chat spend would get laundered into the chat total and
        stay invisible, which is the failure the column exists to end. `unknown`
        reads as a defect in a report, which is the correct amount of ugly for
        spend nobody classified.
        """
        undx_cost.record(_usage(provider="meta", cost_usd=None))
        undx_cost.record(_usage(provider="openai", call_kind="emmbedding", cost_usd=None))
        kinds = undx_cost.month_snapshot()["kinds"]
        self.assertEqual(sorted(kinds), ["chat", "unknown"])
        self.assertEqual(kinds["chat"]["calls"], 1)
        self.assertEqual(kinds["unknown"]["calls"], 1)

    def test_an_unrecognised_kind_is_recorded_rather_than_dropped(self):
        """Refusing the row would trade an unclassified dollar for a missing one.

        The money is spent by the time `record` is called. A ledger that discards
        what it cannot classify reports a smaller total than reality and calls it
        clean, which is worse than a total with an `unknown` line in it.
        """
        undx_cost.record(_usage(provider="openai", call_kind="nonsense", cost_usd=0.005))
        snapshot = undx_cost.month_snapshot()
        self.assertEqual(snapshot["providers"]["openai"]["cost_micro_usd"], 5000)
        self.assertEqual(snapshot["kinds"]["unknown"]["cost_micro_usd"], 5000)

    def test_is_known_call_kind_is_a_separate_question_from_normalising(self):
        """`normalize` cannot double as detection — the same reason
        `undx_privacy.is_known` exists next to the ranking function. A function
        that maps an unrecognised name onto a working default has, by that point,
        destroyed the evidence that the name was unrecognised."""
        self.assertTrue(undx_cost.is_known_call_kind("embedding"))
        self.assertTrue(undx_cost.is_known_call_kind("  EMBEDDING  "))
        self.assertFalse(undx_cost.is_known_call_kind("emmbedding"))
        self.assertFalse(undx_cost.is_known_call_kind(None))
        self.assertFalse(
            undx_cost.is_known_call_kind(undx_cost.CALL_KIND_UNKNOWN),
            "`unknown` is the bucket for unclassifiable spend, not a kind a "
            "caller may select; if it were selectable, declaring it would look "
            "like a classification while meaning the absence of one",
        )

    def test_the_kinds_axis_survives_a_ledger_outage(self):
        """The process mirror keeps both axes, or a ledger outage silently
        un-classifies every call made during it."""
        def boom():
            raise sqlite3.OperationalError("no such database")
        with mock.patch.object(undx_cost, "_connect", side_effect=boom):
            undx_cost.record(_usage(provider="openai", call_kind="embedding", cost_usd=None))
            snapshot = undx_cost.month_snapshot()
        self.assertEqual(snapshot["source"], "process")
        self.assertEqual(snapshot["kinds"]["embedding"]["calls"], 1)

    def test_ensure_schema_is_idempotent(self):
        undx_cost.ensure_schema()
        undx_cost.ensure_schema()
        undx_cost.record(_usage())
        self.assertEqual(undx_cost.month_snapshot()["providers"]["meta"]["calls"], 1)


class ModelDimensionTest(_LedgerCase):
    """Which model the money went to, which the ledger used to discard.

    `record()` has always been handed a `model` — `undx_router` puts it in the usage
    dict, the image pipeline and the embedding adapter pass it explicitly — and the
    table had nowhere to put it. So `gpt-image-1` and whatever replaces it at a
    different price were the same row, and a report could say OpenAI's image spend
    without being able to say what produced it.

    The risk in widening the key is not the new column, it is the old readings. Each
    dimension turns one row into several, and a per-provider total is now a sum over
    rows rather than a row. That invariant gets the first test.
    """

    def _rows(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(
                f"SELECT provider, call_kind, model, calls FROM {undx_cost.LEDGER_TABLE} "
                "ORDER BY provider, call_kind, model").fetchall()
        finally:
            conn.close()

    def test_a_providers_total_survives_the_model_split(self):
        """Adding a dimension to a measurement must not change the measurement.

        Two models, one provider, one kind. The provider's total has to be the sum
        of both rows — a reader that assigned instead of accumulating would report
        whichever row the database returned last, and with two rows of equal size
        that is a 50% understatement that looks like a plausible number.
        """
        undx_cost.record(_usage(model="muse-spark-1.3"))
        undx_cost.record(_usage(model="muse-ember-2.0"))

        snapshot = undx_cost.month_snapshot()
        self.assertEqual(len(self._rows()), 2, "two models are two rows")
        self.assertEqual(snapshot["providers"]["meta"]["calls"], 2)
        self.assertEqual(snapshot["providers"]["meta"]["cost_micro_usd"], 3746)
        self.assertEqual(snapshot["kinds"]["chat"]["calls"], 2)

    def test_the_same_model_twice_is_one_row(self):
        """The pairing. Without it the test above is satisfied by a ledger that
        inserts a fresh row per call and never updates anything — every total would
        still be right, and nothing would look wrong until someone counted rows."""
        undx_cost.record(_usage(model="muse-spark-1.3"))
        undx_cost.record(_usage(model="muse-spark-1.3"))

        self.assertEqual(self._rows(), [("meta", "chat", "muse-spark-1.3", 2)])

    def test_casing_does_not_split_a_models_spend(self):
        """`GPT-4o` and `gpt-4o` are one model and must be one row.

        The `provider` column has always been lowercased, so a call site writing the
        model as the provider's docs spell it and another writing it as the config
        holds it would otherwise halve one model's apparent spend into two
        affordable-looking halves. Casing is the difference most likely to vary
        between two call sites recording the same thing.
        """
        undx_cost.record(_usage(provider="openai", model="GPT-4o"))
        undx_cost.record(_usage(provider="openai", model="gpt-4o"))

        self.assertEqual(self._rows(), [("openai", "chat", "gpt-4o", 2)])

    def test_the_column_is_not_nullable(self):
        """Nullable would multiply rows on PostgreSQL and nowhere else.

        PostgreSQL treats NULLs as distinct in a unique index, so an upsert for an
        unnamed model would miss its own conflict target and INSERT every time. The
        totals would stay correct, so the only symptom is unbounded row growth in
        production and nothing at all in the SQLite tests. Asserted against the
        column metadata because that is where the protection lives.
        """
        undx_cost.ensure_schema()
        conn = sqlite3.connect(self.db_path)
        try:
            columns = {row[1]: row for row in
                       conn.execute(f"PRAGMA table_info({undx_cost.LEDGER_TABLE})")}
        finally:
            conn.close()
        self.assertEqual(columns["model"][3], 1, "model must be NOT NULL")
        self.assertEqual(columns["model"][4], "''", "and must default to the empty string")

    def test_a_kind_with_no_models_records_an_empty_model(self):
        """A Brave query bills against an endpoint. There is no model to name, and
        `undeclared` would be a false accusation."""
        undx_cost.record({"provider": "brave", "call_kind": "research",
                          "cost_micro_usd": None})
        self.assertEqual(self._rows(), [("brave", "research", "", 1)])

    def test_a_model_bearing_kind_with_no_model_is_undeclared(self):
        """The other half of the same distinction. Something chose a model here and
        did not say which, which is a gap in the accounting rather than an absence
        of the question — same shape as `call_kind` becoming `unknown` rather than
        `chat`."""
        undx_cost.record({"provider": "openai", "call_kind": "image",
                          "cost_micro_usd": None})
        self.assertEqual(self._rows(), [("openai", "image", "undeclared", 1)])

    def test_normalize_model_answers_the_two_absences_differently(self):
        """Read directly, because the distinction is the design and a test that only
        went through `record()` would pass on a function that returned `''` for both
        if nothing happened to look at a research row that day."""
        self.assertEqual(undx_cost.normalize_model("", "research"), "")
        self.assertEqual(undx_cost.normalize_model("", "translation"), "")
        self.assertEqual(undx_cost.normalize_model(None, "chat"), "undeclared")
        self.assertEqual(undx_cost.normalize_model(None, "embedding"), "undeclared")
        self.assertEqual(undx_cost.normalize_model("  Sonar-Pro ", "chat"), "sonar-pro")

    def test_an_unrecognised_kind_still_gets_a_model(self):
        """`unknown` is not in `MODEL_BEARING_CALL_KINDS`, so a typo'd kind with no
        model records `''`. That is the right answer — we do not know whether that
        kind has models — but a typo'd kind *with* a model must still keep it, or
        the one row that reads as a defect would also lose its only clue."""
        undx_cost.record({"provider": "openai", "call_kind": "emmbedding",
                          "model": "text-embedding-3-large", "cost_micro_usd": None})
        self.assertEqual(self._rows(),
                         [("openai", "unknown", "text-embedding-3-large", 1)])

    def test_a_long_model_name_is_bounded(self):
        """`record()` is a public entry point and the value becomes an index key."""
        undx_cost.record(_usage(model="x" * 500))
        self.assertEqual(len(self._rows()[0][2]), 120)

    def test_the_models_axis_is_keyed_by_provider_and_model(self):
        """A model name is not globally unique. An open-weights model served by two
        providers at two prices merged under one key would produce a total that
        belongs to no invoice anyone receives."""
        undx_cost.record(_usage(provider="groq", model="llama-3.3-70b", cost_usd=0.001))
        undx_cost.record(_usage(provider="meta", model="llama-3.3-70b", cost_usd=0.002))

        models = undx_cost.month_snapshot()["models"]
        self.assertEqual(sorted(models), ["groq/llama-3.3-70b", "meta/llama-3.3-70b"])
        self.assertEqual(models["groq/llama-3.3-70b"]["cost_micro_usd"], 1000)
        self.assertEqual(models["meta/llama-3.3-70b"]["cost_micro_usd"], 2000)

    def test_the_models_axis_omits_kinds_with_no_model_but_keeps_undeclared(self):
        """A per-model report that hides unattributed spend is the report that lets
        it stay unattributed. An endpoint-billed call is a different case and does
        not belong in a per-model view at all."""
        undx_cost.record({"provider": "brave", "call_kind": "research",
                          "cost_micro_usd": None})
        undx_cost.record({"provider": "openai", "call_kind": "image",
                          "cost_micro_usd": None})

        models = undx_cost.month_snapshot()["models"]
        self.assertEqual(sorted(models), ["openai/undeclared"])

    def test_the_degraded_snapshot_still_has_the_key(self):
        """Empty, not missing. The process mirror carries no per-model tally — no
        budget in this module is per-model — but a caller must not get an
        AttributeError only during a database incident. `source` is how it tells
        empty from unavailable."""
        def boom():
            raise sqlite3.OperationalError("no such database")
        with mock.patch.object(undx_cost, "_connect", side_effect=boom):
            undx_cost.record(_usage())
            snapshot = undx_cost.month_snapshot()
        self.assertEqual(snapshot["source"], "process")
        self.assertEqual(snapshot["models"], {})
        self.assertEqual(snapshot["providers"]["meta"]["calls"], 1)

    def test_an_old_table_is_migrated_and_model_bearing_rows_are_backfilled(self):
        """Rows written before this column had a model and nowhere to put it.

        `ADD COLUMN ... DEFAULT ''` gives them the empty string, which in this
        scheme claims something false: that those kinds have no model dimension. So
        the model-bearing ones are moved to `undeclared`, and the research row —
        which genuinely has no model, and which exists because research metering
        shipped before this column — is left alone. That asymmetry is the whole
        reason the backfill is a separate statement rather than a different
        `ADD COLUMN` default.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(f"""
                DROP TABLE IF EXISTS {undx_cost.LEDGER_TABLE};
                CREATE TABLE {undx_cost.LEDGER_TABLE} (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, month TEXT NOT NULL,
                  provider TEXT NOT NULL,
                  call_kind TEXT NOT NULL DEFAULT 'chat',
                  calls INTEGER NOT NULL DEFAULT 0,
                  input_tokens INTEGER NOT NULL DEFAULT 0,
                  output_tokens INTEGER NOT NULL DEFAULT 0,
                  reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                  cost_micro_usd INTEGER NOT NULL DEFAULT 0,
                  uncosted_calls INTEGER NOT NULL DEFAULT 0,
                  updated_at TEXT NOT NULL DEFAULT (datetime('now')));
                CREATE UNIQUE INDEX ux_{undx_cost.LEDGER_TABLE}_month_provider_kind
                  ON {undx_cost.LEDGER_TABLE}(month, provider, call_kind);
                INSERT INTO {undx_cost.LEDGER_TABLE} (month, provider, call_kind, calls)
                  VALUES ('{undx_cost.current_month()}', 'openai', 'chat', 13),
                         ('{undx_cost.current_month()}', 'brave', 'research', 4);
            """)
            conn.commit()
        finally:
            conn.close()

        undx_cost.ensure_schema()
        self.assertEqual(self._rows(), [("brave", "research", "", 4),
                                        ("openai", "chat", "undeclared", 13)])

    def test_the_backfill_is_idempotent_and_does_not_touch_new_rows(self):
        """It runs on every boot. A second pass has nothing to match, because a new
        write never produces `''` for a model-bearing kind — which is also why the
        `UPDATE` cannot collide with a row it is about to duplicate."""
        undx_cost.record(_usage(model="muse-spark-1.3"))
        undx_cost.record({"provider": "openai", "call_kind": "image",
                          "cost_micro_usd": None})
        before = self._rows()

        undx_cost.ensure_schema()
        undx_cost.ensure_schema()

        self.assertEqual(self._rows(), before)


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


class CostFieldsTest(_LedgerCase):
    """How one call's price is read, and why there is only one function doing it.

    Two input forms exist: chat providers report dollars (`cost_usd`), non-chat
    spend is priced by `undx_capabilities` in integer micro-USD already
    (`cost_micro_usd`). Both must produce the same two numbers the ledger stores —
    a micro amount, and whether this call counts as one the amount excludes.

    The reason this is one function rather than two is the subject of
    :meth:`test_the_mirror_and_the_ledger_agree_about_the_micro_form`. `_apply`
    feeds the process mirror and `record` feeds the durable row; before the
    extraction each derived the pair separately from `cost_usd`, so teaching only
    one of them the micro form would have left the degraded path and the durable
    path disagreeing about what a call cost — visible only during a database
    outage, which is the worst moment to discover it.
    """

    def test_a_known_zero_and_an_unknown_price_are_not_the_same_row(self):
        """The whole §34 distinction, at the narrowest point it exists.

        DuckDuckGo is keyless and free, so 0 is a *measurement*. `gpt-image-1`
        has no price in the table, so 0 would be a *guess*. Both add nothing to
        the dollar total, and if that were all the ledger stored they would be
        indistinguishable — the difference is entirely in `uncosted_calls`.
        """
        self.assertEqual(undx_cost._cost_fields({"cost_micro_usd": 0}), (0, 0))
        self.assertEqual(undx_cost._cost_fields({"cost_micro_usd": None}), (0, 1))
        self.assertNotEqual(undx_cost._cost_fields({"cost_micro_usd": 0}),
                            undx_cost._cost_fields({"cost_micro_usd": None}))

    def test_an_absent_price_is_unknown_rather_than_free(self):
        """Five of seven chat providers reach here with no price at all."""
        self.assertEqual(undx_cost._cost_fields({}), (0, 1))
        self.assertEqual(undx_cost._cost_fields({"cost_usd": None}), (0, 1))

    def test_the_two_forms_agree_on_the_same_amount(self):
        """$0.001873 is the figure `_usage()` defaults to, i.e. a real Meta call."""
        self.assertEqual(undx_cost._cost_fields({"cost_usd": 0.001873}), (1873, 0))
        self.assertEqual(undx_cost._cost_fields({"cost_micro_usd": 1873}), (1873, 0))

    def test_the_micro_form_wins_when_both_are_present(self):
        """Deliberate precedence, not an accident of ordering. A caller that
        computed micro-USD did so from the capability table, which is the
        authority for non-chat prices; a chat caller never sets the micro field.
        Pinned so that if the two ever do arrive together the winner is the one
        that was chosen rather than the one that happened to be checked first."""
        self.assertEqual(
            undx_cost._cost_fields({"cost_usd": 99.0, "cost_micro_usd": 7}), (7, 0))

    def test_a_malformed_price_is_unknown_in_both_forms(self):
        """`to_micro_usd` returns 0 for junk by contract and leaves the uncosted
        decision to its caller, so a dollar branch that delegated to it would
        record an unparseable price as $0.00 spent — the one reading §34 rules
        out — while the micro branch recorded the same junk as unknown."""
        for form in ("cost_usd", "cost_micro_usd"):
            with self.subTest(form=form):
                self.assertEqual(undx_cost._cost_fields({form: "not a number"}), (0, 1))
                self.assertEqual(undx_cost._cost_fields({form: object()}), (0, 1))

    def test_the_mirror_and_the_ledger_agree_about_the_micro_form(self):
        """Record the same call twice — once with the database reachable, once
        with `_connect` broken so only the mirror answers — and require the two
        replies to carry the same money. This is the split-brain the extraction
        exists to prevent, and it fails if `record` and `_apply` stop sharing
        `_cost_fields`.

        Two providers rather than one because the mirror accumulates within a
        process and would otherwise report the second call on top of the first.
        """
        usage = {"provider": "perplexity", "model": "sonar", "call_kind": "embedding",
                 "input_tokens": 1000, "output_tokens": 0, "cost_micro_usd": 4000}
        from_ledger = undx_cost.record(usage)

        degraded_usage = dict(usage, provider="brave")
        with mock.patch.object(undx_cost, "_connect",
                               side_effect=sqlite3.OperationalError("gone")):
            from_mirror = undx_cost.record(degraded_usage)

        for field in ("calls", "cost_micro_usd", "uncosted_calls"):
            with self.subTest(field=field):
                self.assertEqual(from_ledger[field], from_mirror[field])
        self.assertEqual(from_ledger["cost_micro_usd"], 4000)

    def test_an_unknown_price_reaches_the_durable_row_as_uncosted(self):
        """Through `record`, not just `_cost_fields`, because the pair has to
        survive the upsert's arithmetic to be readable in a spend report."""
        undx_cost.record({"provider": "openai", "model": "gpt-image-1",
                          "call_kind": "image", "cost_micro_usd": None})
        snapshot = undx_cost.month_snapshot()
        self.assertEqual(snapshot["source"], "ledger")
        row = snapshot["kinds"]["image"]
        self.assertEqual((row["calls"], row["cost_micro_usd"], row["uncosted_calls"]),
                         (1, 0, 1))


if __name__ == "__main__":
    unittest.main()
