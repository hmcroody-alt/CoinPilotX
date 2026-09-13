"""The Perplexity embeddings wire contract, pinned.

These tests exist because of a specific production incident: the live acceptance probe
reached Perplexity, got a clean HTTP 200 with a valid vector in it, and the client threw
``"provider returned an empty vector"`` anyway. The parser had been written against the
OpenAI shape — ``data[i].embedding`` as a JSON array of floats — but Perplexity's
documented default is ``base64_int8``, a base64 **string** decoding to signed int8 bytes.
``isinstance(raw, list)`` was False, so a good response was rejected as a bad one.

The lesson is that a shape assumption about a third party needs a test that encodes the
third party's *documented* shape, not the shape we expected. Everything below is built
from the published OpenAPI schema for ``POST /v1/embeddings``:

* ``embedding`` is a base64 string; for ``base64_int8`` it decodes to signed int8 values
  of length ``dimensions``
* ``encoding_format`` accepts only ``base64_int8`` and ``base64_binary`` — **there is no
  float option**, so "just ask for floats" is not an available fix
* embeddings are unnormalised and must be compared with cosine similarity

No network. The provider edge is driven through a fake ``requests`` module so the real
status-code classification in ``_post`` and the real decoding in ``_parse`` both run.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import patch

from services import undx_capabilities as cap
from services import undx_cost
from services import undx_embedding_service as embed


DIMENSIONS = 64

BASE_ENV = {
    "PERPLEXITY_API_KEY": "test-key-not-a-real-credential",
    "UNDX_EMBEDDING_DIMENSIONS": str(DIMENSIONS),
    "UNDX_EMBEDDING_MAX_RETRIES": "0",
    "UNDX_EMBEDDING_TIMEOUT_SECONDS": "1",
    "UNDX_EMBEDDING_MONTHLY_BUDGET_USD": "5",
}


def int8_vector(seed: int, dimensions: int = DIMENSIONS) -> list[int]:
    """A deterministic, non-constant int8 vector. Never all-zero: a zero vector has no
    direction, so it would hide a normalisation bug behind a division guard."""
    return [(((seed + index) * 37) % 255) - 127 for index in range(dimensions)]


def encode_int8(values: list[int]) -> str:
    """Exactly what Perplexity puts on the wire for ``base64_int8``."""
    return base64.b64encode(struct.pack(f"{len(values)}b", *values)).decode("ascii")


def wire_response(vectors: list[list[int]], *, tokens: int = 12) -> dict:
    """A response byte-for-byte shaped like the published example."""
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": index, "embedding": encode_int8(values)}
            for index, values in enumerate(vectors)
        ],
        "model": "pplx-embed-v1-0.6b",
        "usage": {
            "prompt_tokens": tokens,
            "total_tokens": tokens,
            "cost": {"input_cost": 4.8e-08, "total_cost": 4.8e-08, "currency": "USD"},
        },
    }


class FakeResponse:
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body
        self.headers = {"content-type": "application/json"}

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body

    @property
    def text(self):
        return self._body if isinstance(self._body, str) else json.dumps(self._body)


class FakeRequests:
    """Captures the outgoing payload so the request contract can be asserted too."""

    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls: list[dict] = []

    def post(self, url, headers=None, data=None, timeout=None):
        self.calls.append(
            {"url": url, "headers": headers or {}, "payload": json.loads(data), "timeout": timeout}
        )
        return self.response


#: A throwaway ledger for the tests in this file that are *not* about the ledger.
#:
#: Every successful `embed_texts` records spend, and `services.db` falls back to the
#: relative path `coinpilotx.db` when `DATABASE_URL` is unset — so before this existed
#: the wire-contract and request-contract classes below, which care about nothing but
#: vectors, were quietly writing rows into the developer's own dev database. The file
#: size does not change (SQLite reuses free pages), so only a checksum shows it.
#:
#: Applied as a *fallback* rather than as part of `BASE_ENV`, because `SpendIsMetered`
#: and `BudgetIsSharedNotPerProcess` pin their own per-test ledger and `BASE_ENV` is
#: merged last inside these helpers — overriding their pin would silently pool every
#: test's rows into one file and turn each assertion into a statement about execution
#: order.
_LEDGER_DIR = tempfile.TemporaryDirectory()
_FALLBACK_LEDGER_URL = "sqlite:///" + os.path.join(_LEDGER_DIR.name, "fallback.db")


def _env_for(extra: dict | None = None) -> dict:
    env = {**BASE_ENV, **(extra or {})}
    if not (os.environ.get("DATABASE_URL") or "").strip():
        env["DATABASE_URL"] = _FALLBACK_LEDGER_URL
    return env


def run_with(response: FakeResponse, texts=("hello world",), env: dict | None = None):
    """Drive one `embed_texts` against a fake provider.

    `env` is merged *after* `BASE_ENV`, which is the only way to change a value
    `BASE_ENV` already sets. A caller that wrapped this in `patch.dict` instead would
    have its override silently reverted for the duration of the call — which is how
    two budget tests in this file first passed for the wrong reason.
    """
    fake = FakeRequests(response)
    with patch.dict("os.environ", _env_for(env), clear=False):
        with patch.dict("sys.modules", {"requests": fake}):
            batch = embed.embed_texts(list(texts), purpose="test")
    return batch, fake


def expect_failure(response: FakeResponse, texts=("hello world",), env: dict | None = None):
    fake = FakeRequests(response)
    with patch.dict("os.environ", _env_for(env), clear=False):
        with patch.dict("sys.modules", {"requests": fake}):
            try:
                embed.embed_texts(list(texts), purpose="test")
            except embed.EmbeddingUnavailable as exc:
                return exc
    raise AssertionError("expected EmbeddingUnavailable, got a successful batch")


class DocumentedResponseIsAccepted(unittest.TestCase):
    """The regression that caused the incident: a good 200 must produce a vector."""

    def test_base64_int8_response_yields_a_vector(self):
        original = int8_vector(1)
        batch, _ = run_with(FakeResponse(200, wire_response([original])))

        self.assertEqual(len(batch.vectors), 1)
        self.assertEqual(len(batch.vectors[0]), DIMENSIONS)
        self.assertEqual(batch.tokens, 12)

    def test_vector_is_l2_normalised_and_preserves_direction(self):
        """Perplexity returns unnormalised int8. We normalise on the way in so that the
        stored index can be compared with a dot product, which is what cosine similarity
        reduces to once both sides are unit length."""
        original = int8_vector(2)
        batch, _ = run_with(FakeResponse(200, wire_response([original])))
        vector = batch.vectors[0]

        norm = sum(value * value for value in vector) ** 0.5
        self.assertAlmostEqual(norm, 1.0, places=9)

        expected_norm = sum(value * value for value in original) ** 0.5
        for index, raw in enumerate(original):
            self.assertAlmostEqual(vector[index], raw / expected_norm, places=9)

    def test_batch_order_follows_the_index_field_not_arrival_order(self):
        """``data`` is not promised to be sorted. A short or shuffled list must not
        silently misalign vectors against the documents they were computed for."""
        first, second, third = int8_vector(3), int8_vector(4), int8_vector(5)
        body = wire_response([first, second, third])
        body["data"] = [body["data"][2], body["data"][0], body["data"][1]]

        batch, _ = run_with(FakeResponse(200, body), texts=("a", "b", "c"))

        for position, original in enumerate((first, second, third)):
            expected_norm = sum(value * value for value in original) ** 0.5
            self.assertAlmostEqual(batch.vectors[position][0], original[0] / expected_norm, places=9)


class RequestContractIsExplicit(unittest.TestCase):
    def test_encoding_format_is_sent_and_is_base64_int8(self):
        """Sent explicitly rather than relying on the provider's default, so a change to
        that default cannot silently swap the vector space under a stored index."""
        _, fake = run_with(FakeResponse(200, wire_response([int8_vector(6)])))
        payload = fake.calls[0]["payload"]

        self.assertEqual(payload["encoding_format"], "base64_int8")
        self.assertEqual(payload["model"], "pplx-embed-v1-0.6b")
        self.assertEqual(payload["dimensions"], DIMENSIONS)
        self.assertEqual(payload["input"], ["hello world"])

    def test_binary_encoding_is_not_requested(self):
        """``base64_binary`` is one bit per dimension and needs Hamming distance. The
        retrieval scorer does cosine, so asking for it would be a silent correctness bug
        rather than a loud failure."""
        _, fake = run_with(FakeResponse(200, wire_response([int8_vector(7)])))
        self.assertNotEqual(fake.calls[0]["payload"]["encoding_format"], "base64_binary")

    def test_credential_goes_in_the_authorization_header_only(self):
        _, fake = run_with(FakeResponse(200, wire_response([int8_vector(8)])))
        call = fake.calls[0]

        self.assertEqual(call["headers"]["Authorization"], "Bearer test-key-not-a-real-credential")
        self.assertNotIn("test-key-not-a-real-credential", json.dumps(call["payload"]))
        self.assertNotIn("test-key-not-a-real-credential", call["url"])


class MalformedResponsesStillFailClosed(unittest.TestCase):
    """Accepting base64 must not turn the parser into something that accepts anything.
    Each of these has a distinct message, because "it failed" and "it failed *this way*"
    are different amounts of information at three in the morning."""

    def test_undecodable_base64_is_reported_as_such(self):
        body = wire_response([int8_vector(9)])
        body["data"][0]["embedding"] = "!!!not base64!!!"
        self.assertIn("not valid base64", expect_failure(FakeResponse(200, body)).reason)

    def test_empty_string_is_an_empty_vector(self):
        body = wire_response([int8_vector(10)])
        body["data"][0]["embedding"] = ""
        self.assertIn("empty vector", expect_failure(FakeResponse(200, body)).reason)

    def test_missing_embedding_key_is_named_precisely(self):
        body = wire_response([int8_vector(11)])
        del body["data"][0]["embedding"]
        self.assertIn("no embedding", expect_failure(FakeResponse(200, body)).reason)

    def test_null_embedding_reports_the_type(self):
        body = wire_response([int8_vector(12)])
        body["data"][0]["embedding"] = None
        self.assertIn("NoneType", expect_failure(FakeResponse(200, body)).reason)

    def test_wrong_dimension_count_is_rejected_not_padded(self):
        """A vector of the wrong length must never be silently accepted — it would sit in
        the index looking like poor model quality forever."""
        body = wire_response([int8_vector(13, dimensions=DIMENSIONS // 2)])
        reason = expect_failure(FakeResponse(200, body)).reason
        self.assertIn(f"expected {DIMENSIONS}", reason)

    def test_short_data_list_is_rejected(self):
        body = wire_response([int8_vector(14)])
        reason = expect_failure(FakeResponse(200, body), texts=("a", "b")).reason
        self.assertIn("for 2 inputs", reason)

    def test_float_array_is_still_accepted_for_proxy_compatibility(self):
        """Not the documented shape, but a proxy that speaks floats is not a reason to
        break. Explicitly tested so the tolerance is deliberate rather than accidental."""
        body = wire_response([int8_vector(15)])
        body["data"][0]["embedding"] = [0.5] * DIMENSIONS
        batch, _ = run_with(FakeResponse(200, body))
        self.assertEqual(len(batch.vectors[0]), DIMENSIONS)

    def test_non_finite_float_array_is_rejected(self):
        body = wire_response([int8_vector(16)])
        body["data"][0]["embedding"] = [float("nan")] * DIMENSIONS
        self.assertIn("non-finite", expect_failure(FakeResponse(200, body)).reason)


class StatusClassificationIsUnchanged(unittest.TestCase):
    """The decode fix must not have blurred the distinction between "the provider said
    no" and "the provider said yes and we could not read it" — mistaking one for the
    other is exactly what cost the previous acceptance run."""

    def test_401_is_a_credential_rejection(self):
        exc = expect_failure(FakeResponse(401, {"error": "unauthorized"}))
        self.assertIn("rejected the credential", exc.reason)
        self.assertFalse(exc.retryable)

    def test_429_is_retryable(self):
        self.assertTrue(expect_failure(FakeResponse(429, {"error": "slow down"})).retryable)

    def test_500_is_retryable(self):
        self.assertTrue(expect_failure(FakeResponse(503, {"error": "unavailable"})).retryable)

    def test_422_reports_the_status(self):
        exc = expect_failure(FakeResponse(422, {"detail": [{"msg": "bad model"}]}))
        self.assertIn("422", exc.reason)
        self.assertFalse(exc.retryable)

    def test_non_json_body_is_named(self):
        self.assertIn("non-JSON", expect_failure(FakeResponse(200, "<html>gateway</html>")).reason)


class SpendIsMetered(unittest.TestCase):
    """Embedding calls must land in the shared cost ledger under `embedding`.

    §22: no unclassified AI spend. Until this was wired, the only record that a
    Perplexity embedding batch had been paid for was a counter and a per-process
    budget dict that resets on deploy — so the month's spend report showed chat
    only, and showed it as the total.

    **Every test here pins `DATABASE_URL` at a temp file**, and that is not
    boilerplate. `services.db` falls back to the relative path `coinpilotx.db`
    when the variable is unset, so a ledger test without this writes into the
    developer's own dev database. That is how it was discovered: wiring the
    metering and running the four embedding suites left 461 `embedding` rows and
    851 `chat` rows of test data in the local `coinpilotx.db`. It proved the
    wiring reached the ledger, and it also means any assertion on a month total
    without this isolation is really an assertion about how many times the suite
    has been run.
    """

    #: Big enough that the reported figure and the table figure cannot be confused
    #: at micro-USD resolution. The published example's 4.8e-08 is a *sub*-micro
    #: charge, which floors to 0 — correct behaviour, but useless for telling which
    #: of the two sources was consulted.
    MANY_TOKENS = 1_000_000
    REPORTED_USD = 0.02          # 20,000 micro-USD
    TABLE_MICRO_FOR_MANY = 4_000  # pplx-embed-v1-0.6b at $0.004/M

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        undx_cost.reset_for_tests()
        env = patch.dict(
            "os.environ",
            {"DATABASE_URL": "sqlite:///" + os.path.join(self._dir.name, "ledger.db")},
        )
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self._dir.cleanup)
        self.addCleanup(undx_cost.reset_for_tests)

    def _row(self):
        snapshot = undx_cost.month_snapshot()
        self.assertEqual(snapshot["source"], "ledger")
        return snapshot["kinds"].get(cap.CALL_KIND_EMBEDDING)

    def test_a_successful_batch_is_recorded_as_embedding_not_chat(self):
        """The specific laundering §22 is about: this spend must not arrive in the
        chat bucket, where it would be invisible because chat is the number
        everyone already reads."""
        run_with(FakeResponse(200, wire_response([int8_vector(1)])))
        snapshot = undx_cost.month_snapshot()
        self.assertEqual(snapshot["kinds"][cap.CALL_KIND_EMBEDDING]["calls"], 1)
        self.assertNotIn(undx_cost.CALL_KIND_CHAT, snapshot["kinds"])
        self.assertIn("perplexity", snapshot["providers"])

    def test_the_embedding_model_is_named_in_the_durable_record(self):
        """Embedding is a model-bearing kind, so `undeclared` here would be a hole.

        Worth its own test rather than another assertion on the row above, because
        the two failures look nothing alike: the row above going wrong means spend
        landed in the wrong *bucket*, while this going wrong means the spend is in
        the right bucket and cannot be attributed to the model that incurred it.
        The second is the one that survives a review, since every total still adds
        up. `budget_state`'s unpriced-model fallback is downstream of this name.
        """
        run_with(FakeResponse(200, wire_response([int8_vector(1)])))
        models = undx_cost.month_snapshot()["models"]
        self.assertEqual(sorted(models),
                         ["perplexity/" + embed.configured_model().lower()])
        self.assertNotIn(undx_cost.MODEL_UNDECLARED,
                         [key.split("/", 1)[1] for key in models])

    def test_the_provider_reported_cost_beats_the_price_table(self):
        """A table is a price someone read on a date; a reported cost is a
        measurement of the call that just happened. Asserted by making the two
        disagree by 5x, so a green result cannot mean "both happened to match"."""
        body = wire_response([int8_vector(2)], tokens=self.MANY_TOKENS)
        body["usage"]["cost"]["total_cost"] = self.REPORTED_USD
        run_with(FakeResponse(200, body))

        row = self._row()
        self.assertEqual(row["cost_micro_usd"], 20_000)
        self.assertNotEqual(row["cost_micro_usd"], self.TABLE_MICRO_FOR_MANY)
        self.assertEqual(row["uncosted_calls"], 0)

    def test_the_price_table_is_used_when_the_provider_says_nothing(self):
        """The fallback has to still work, or preferring the report would have
        traded one blind spot for another. Same token count as the test above, so
        the two figures are directly comparable and the 5x gap is the only
        difference between them."""
        body = wire_response([int8_vector(3)], tokens=self.MANY_TOKENS)
        del body["usage"]["cost"]
        run_with(FakeResponse(200, body))

        row = self._row()
        self.assertEqual(row["cost_micro_usd"], self.TABLE_MICRO_FOR_MANY)
        self.assertEqual(row["uncosted_calls"], 0)

    def test_a_negative_reported_cost_falls_back_rather_than_crediting_the_month(self):
        """A refund is not something this ledger can represent, and subtracting it
        would understate the month. Treated as no report at all."""
        body = wire_response([int8_vector(4)], tokens=self.MANY_TOKENS)
        body["usage"]["cost"]["total_cost"] = -5.0
        run_with(FakeResponse(200, body))

        self.assertEqual(self._row()["cost_micro_usd"], self.TABLE_MICRO_FOR_MANY)

    def test_an_unparseable_reported_cost_falls_back_to_the_table(self):
        """A garbled cost block is not a reason to refuse a usable embedding, and
        it is also not a reason to forget the price.

        The first version of this test asserted the opposite — `(0, 1)`, unknown —
        on the reasoning that a provider contradicting the table makes neither
        figure trustworthy. It failed, and the failure was right. An unreadable
        cost block is evidence about the provider's serialization, not about
        whether $0.004 per million tokens is still the price; dropping a sourced
        estimate on that basis *understates* the month, which is the outcome §34
        exists to prevent rather than an instance of obeying it. And
        `uncosted_calls` is the number that enumerates the pricing gap, so putting
        a call with a perfectly good table price into it would make that number
        stop meaning what `unpriced_providers()` says it means.

        The parse problem is not swallowed: both layers log a warning.
        """
        body = wire_response([int8_vector(5)], tokens=self.MANY_TOKENS)
        body["usage"]["cost"]["total_cost"] = "quite a lot"
        batch, _ = run_with(FakeResponse(200, body))

        self.assertEqual(len(batch.vectors), 1)
        row = self._row()
        self.assertEqual((row["cost_micro_usd"], row["uncosted_calls"]),
                         (self.TABLE_MICRO_FOR_MANY, 0))

    def test_the_metered_token_count_is_the_one_the_budget_restrains_on(self):
        """If these two ever diverge, the in-process budget and the durable ledger
        disagree about how much was bought, and the one that blocks spend is not
        the one anybody reads in a report."""
        body = wire_response([int8_vector(6)], tokens=4_242)
        run_with(FakeResponse(200, body))
        self.assertEqual(self._row()["input_tokens"], 4_242)

    def test_a_failed_call_is_not_recorded_as_spend(self):
        """A 500 is not a charge. Recording it would inflate the month and could
        trip a budget on money that was never taken — and the retry loop means one
        logical call can produce several of these."""
        expect_failure(FakeResponse(503, {"error": "unavailable"}))
        self.assertIsNone(self._row())

    def test_an_empty_input_list_records_nothing(self):
        """`embed_texts([])` short-circuits before the provider. A row here would
        mean the ledger counts calls that were never made."""
        with patch.dict("os.environ", BASE_ENV, clear=False):
            batch = embed.embed_texts(["", "   "], purpose="test")
        self.assertEqual(batch.vectors, ())
        self.assertIsNone(self._row())


class BudgetIsSharedNotPerProcess(unittest.TestCase):
    """`UNDX_EMBEDDING_MONTHLY_BUDGET_USD` has to mean the deployment, not a worker.

    The guard was real but its month-to-date lived in a module-level dict, so the
    ceiling that was actually enforced was the configured one multiplied by the
    number of processes that embed — four gunicorn web workers plus four of the five
    other Procfile processes, eight in production — and it went back to zero on every
    deploy. Both failures point the same way: the guard was weakest when it mattered
    most, because a runaway indexing loop is exactly the thing someone redeploys
    repeatedly to fix.

    Every test here proves the figure came from the **ledger** and not from this
    process, by clearing the in-process mirror after seeding the row. Without that
    step a green result would be indistinguishable from the old behaviour, since the
    local dict would have had the same spend in it. That is the whole point: the
    property under test is *whose* spend counts, and the only way to see it is to
    make this process's own count empty while the shared figure is not.
    """

    #: $6.00 of embedding spend, against a $5.00 ceiling. Chosen so the overage is
    #: unambiguous at micro-USD resolution rather than a rounding argument.
    OTHER_PROCESS_MICRO = 6_000_000
    BUDGET_USD = "5"

    #: 200M tokens at the *unknown-model* rate of $0.05/M is $10.00; at the default
    #: model's $0.004/M it is $0.80. A budget of $5.00 sits between the two, so which
    #: rate the guard applies to an unpriced call decides the outcome.
    UNPRICED_TOKENS = 200_000_000

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        undx_cost.reset_for_tests()
        embed.reset_budget()
        self._env = patch.dict(
            "os.environ",
            {**BASE_ENV,
             "DATABASE_URL": "sqlite:///" + os.path.join(self._dir.name, "ledger.db"),
             "UNDX_EMBEDDING_MONTHLY_BUDGET_USD": self.BUDGET_USD},
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        self.addCleanup(self._dir.cleanup)
        self.addCleanup(undx_cost.reset_for_tests)
        self.addCleanup(embed.reset_budget)

    def _seed_another_process(self, **usage):
        """Record spend in the ledger, then erase every trace of it from this process.

        `reset_for_tests` drops the process mirror and the schema latch but not the
        table, so what survives is exactly what a *different* worker's write would
        have left behind.
        """
        undx_cost.record({"provider": "perplexity", "call_kind": "embedding", **usage})
        undx_cost.reset_for_tests()
        embed.reset_budget()

    def test_spend_by_another_worker_counts_against_this_workers_budget(self):
        """The defect, stated as a test: $6.00 already spent elsewhere, a $5.00
        ceiling, and a process that has itself spent nothing must refuse."""
        self._seed_another_process(input_tokens=1_000_000,
                                   cost_micro_usd=self.OTHER_PROCESS_MICRO)

        with self.assertRaises(embed.EmbeddingUnavailable) as caught:
            run_with(FakeResponse(200, wire_response([int8_vector(1)])))
        self.assertIn("budget", caught.exception.reason)
        self.assertFalse(caught.exception.retryable, "retrying will not make it cheaper")

    def test_the_same_call_is_allowed_when_the_shared_figure_is_under_the_ceiling(self):
        """The pairing that makes the test above evidence rather than a coincidence.

        Identical in every respect except the amount already spent — $4.00 instead of
        $6.00 against the same $5.00 ceiling. A guard that refused everything, or one
        that had crashed on the ledger read and been caught somewhere, would pass the
        refusal test and fail this one.
        """
        self._seed_another_process(input_tokens=1_000_000, cost_micro_usd=4_000_000)

        batch, fake = run_with(FakeResponse(200, wire_response([int8_vector(1)])))
        self.assertEqual(len(batch.vectors), 1)
        self.assertEqual(len(fake.calls), 1, "the provider should have been reached")

    def test_a_restart_does_not_refund_the_month(self):
        """The deploy-reset half of the defect, which the dict could not survive.

        `reset_budget()` is precisely what a process start looks like to this module,
        so a fresh worker must still see the month's spend. Asserted through
        `budget_state()` rather than through a refusal so the *figure* is pinned and
        not merely its consequence.
        """
        self._seed_another_process(input_tokens=1_000_000,
                                   cost_micro_usd=self.OTHER_PROCESS_MICRO)

        state = embed.budget_state()
        self.assertEqual(state["estimated_spend_usd"], 6.0)
        self.assertEqual(state["process_tokens_embedded"], 0,
                         "this process has embedded nothing; the figure is not its own")
        self.assertTrue(state["shared"])
        self.assertEqual(state["source"], "ledger")
        self.assertEqual(state["remaining_usd"], -1.0)

    def test_an_unpriced_model_is_charged_at_the_highest_known_rate(self):
        """§34 inverted for the blocking direction, and the one place it must be.

        A recorded call against a model the price table does not know contributes
        $0.00 and `uncosted_calls=1` to the ledger, which is right for *reporting* —
        inventing a cost would report money nobody was charged. Reading that $0.00 as
        the budget position would be wrong for the opposite reason: an unrecognised
        model would then be free to spend without limit, and a model rename is the
        most likely way for one to appear.

        200M unpriced tokens is $10.00 at the highest known rate and $0.80 at the
        default model's, against a $5.00 ceiling — so this test distinguishes the two
        rates rather than merely asserting that something blocked.
        """
        self._seed_another_process(input_tokens=self.UNPRICED_TOKENS)

        row = undx_cost.month_snapshot()["kinds"][cap.CALL_KIND_EMBEDDING]
        self.assertEqual((row["cost_micro_usd"], row["uncosted_calls"]), (0, 1),
                         "the ledger must still report an unknown as unknown")

        state = embed.budget_state()
        self.assertEqual(state["estimated_spend_usd"], 10.0)
        self.assertTrue(state["spend_is_a_floor"])
        with self.assertRaises(embed.EmbeddingUnavailable):
            run_with(FakeResponse(200, wire_response([int8_vector(2)])))

    def test_an_unreachable_ledger_falls_back_to_this_processs_own_count(self):
        """The guard must never end up *weaker* than the dict it replaced.

        Broken at the database connection, which is a state the system really enters,
        rather than by stubbing the accounting layer — that layer is documented never
        to raise. With the shared figure unavailable the local floor is all there is,
        and it has to still stop a runaway pass.
        """
        runaway = ["canonical platform documentation " * 125] * 50  # ~50k tokens
        tight = {"UNDX_EMBEDDING_MONTHLY_BUDGET_USD": "0.0001"}

        def broken(*_args, **_kwargs):
            raise sqlite3.OperationalError("ledger gone")

        with patch.object(undx_cost, "_connect", broken):
            with patch.dict("os.environ", tight):
                state = embed.budget_state()
                self.assertEqual(state["source"], "process")
                self.assertFalse(state["shared"],
                                 "a degraded figure must not be reported as the shared one")
            # The ceiling goes through `run_with`'s `env=` and not through the
            # `patch.dict` above, because `run_with` re-applies `BASE_ENV` — which sets
            # this same variable to $5.00 — with `clear=False`. Wrapping the call
            # instead silently reverts the override for exactly its duration, and this
            # test then passed while the guard had done nothing.
            with self.assertRaises(embed.EmbeddingUnavailable) as caught:
                run_with(FakeResponse(200, wire_response([int8_vector(3)])),
                         texts=runaway, env=tight)
        self.assertIn("budget", caught.exception.reason)

    def test_the_ledger_can_tighten_the_budget_but_never_loosen_it(self):
        """A shared figure smaller than this process's own count must not win.

        Reachable in practice: a ledger write that failed still bumped the process
        mirror, and a ledger that was emptied or is being read mid-month-rollover
        reports less than this worker knows it spent. `max(ledger, local)` is why the
        wiring can only ever move the ceiling down.

        Simulated by letting a real call accumulate a local count and then deleting
        the row it wrote, which is the same end state as a lost write.
        """
        run_with(FakeResponse(200, wire_response([int8_vector(4)], tokens=1_000_000)))

        connection = sqlite3.connect(os.path.join(self._dir.name, "ledger.db"))
        connection.execute(f"DELETE FROM {undx_cost.LEDGER_TABLE}")
        connection.commit()
        connection.close()
        undx_cost.reset_for_tests()   # drop the mirror; keep `embed`'s local floor

        state = embed.budget_state()
        self.assertEqual(state["source"], "ledger", "the ledger is readable, just empty")
        self.assertEqual(state["process_tokens_embedded"], 1_000_000)
        self.assertEqual(state["estimated_spend_usd"], 0.004,
                         "1M tokens at $0.004 per *million*, from the local floor the "
                         "ledger lost — a thousandth of what a per-1k reading of the "
                         "same rate would give, which is the arithmetic this pins")

    def test_a_zero_budget_is_still_an_opt_out(self):
        """The env contract cannot change just because the figure got better. Zero
        disables the guard, and it has to keep doing that with $6.00 recorded — the
        one setting where a ledger read must not even happen."""
        self._seed_another_process(input_tokens=1_000_000,
                                   cost_micro_usd=self.OTHER_PROCESS_MICRO)

        off = {"UNDX_EMBEDDING_MONTHLY_BUDGET_USD": "0"}
        with patch.dict("os.environ", off):
            self.assertFalse(embed.budget_state()["enforced"])
        # `env=` again rather than the wrapper: `BASE_ENV`'s $5.00 would otherwise be
        # restored inside the call, and the test would pass because the *ledger* figure
        # happened to be under a ceiling it was never supposed to consult.
        batch, _ = run_with(FakeResponse(200, wire_response([int8_vector(5)])), env=off)
        self.assertEqual(len(batch.vectors), 1)


if __name__ == "__main__":
    unittest.main()
