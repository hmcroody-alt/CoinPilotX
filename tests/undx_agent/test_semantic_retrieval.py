"""Failure-mode, authority and rollout tests for embedding-backed semantic retrieval.

The thing being protected here is not retrieval quality — it is the guarantee that UNDX
keeps working, and keeps telling the truth, when the embedding provider does not. Every
test below asserts on the *degraded* path: what the user gets when the key is missing,
when Perplexity returns 429, when it times out, when it returns a response that is
almost but not quite right, and when an operator mistypes a rollout flag.

No test in this file touches the network. The provider edge is exercised through a fake
``requests`` module so that ``_post``'s real status-code classification runs, and the
retrieval path is exercised through a deterministic hash embedder so the index, cache,
fusion and authority filter all run for real.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import undx_embedding_service as embed
from services import undx_platform_knowledge as lexical
from services import undx_semantic_retrieval as semantic


BASE_ENV = {
    "PERPLEXITY_API_KEY": "test-key-not-a-real-credential",
    "UNDX_EMBEDDING_DIMENSIONS": "64",
    "UNDX_EMBEDDING_MAX_RETRIES": "2",
    "UNDX_EMBEDDING_TIMEOUT_SECONDS": "1",
    "UNDX_EMBEDDING_MONTHLY_BUDGET_USD": "5",
}


# ------------------------------------------------------------------- fake provider edge


class _FakeResponse:
    def __init__(self, status_code: int, body, *, raw: str | None = None):
        self.status_code = status_code
        self._body = body
        self._raw = raw

    def json(self):
        if self._raw is not None:
            return json.loads(self._raw)  # deliberately raises for malformed raw text
        return self._body


class _FakeRequests:
    """Stands in for the ``requests`` module inside ``embed._post``."""

    class Timeout(Exception):
        pass

    def __init__(self, responder):
        self._responder = responder
        self.calls = 0

    def post(self, url, headers=None, data=None, timeout=None):
        self.calls += 1
        self.last_headers = dict(headers or {})
        self.last_payload = json.loads(data) if data else {}
        return self._responder(self.calls, self.last_payload)


def _deterministic_vector(text: str, dimensions: int) -> list[float]:
    """A stable pseudo-embedding. Not a model — it exists to exercise plumbing.

    Deliberately *not* presented as a quality signal anywhere: tests that use it assert
    on shape, ordering stability and failure behaviour, never on relevance.
    """
    values: list[float] = []
    counter = 0
    while len(values) < dimensions:
        digest = hashlib.sha256(f"{text}|{counter}".encode("utf-8")).digest()
        values.extend((byte - 127.5) / 127.5 for byte in digest)
        counter += 1
    return values[:dimensions]


def _ok_body(payload: dict) -> dict:
    dimensions = int(payload.get("dimensions") or 64)
    inputs = payload.get("input") or []
    return {
        "data": [
            {"index": index, "embedding": _deterministic_vector(text, dimensions)}
            for index, text in enumerate(inputs)
        ],
        "usage": {"total_tokens": sum(max(1, len(t) // 4) for t in inputs)},
    }


class _ProviderCase:
    """Context manager: install a fake ``requests`` and a clean env for one scenario."""

    def __init__(self, responder, env: dict | None = None):
        self.requests = _FakeRequests(responder)
        self.env = {**BASE_ENV, **(env or {})}
        self._patches: list = []

    def __enter__(self):
        embed.reset_telemetry()
        embed.reset_budget()
        self._patches = [
            patch.dict(os.environ, self.env, clear=True),
            patch.dict("sys.modules", {"requests": self.requests}),
        ]
        for item in self._patches:
            item.start()
        return self.requests

    def __exit__(self, *exc):
        for item in reversed(self._patches):
            item.stop()
        return False


# ------------------------------------------------------------------------ provider edge


class ProviderFailureModes(unittest.TestCase):
    def test_missing_key_is_unavailable_not_an_exception_class_of_its_own(self):
        with _ProviderCase(lambda n, p: _FakeResponse(200, _ok_body(p)), {"PERPLEXITY_API_KEY": ""}) as fake:
            with self.assertRaises(embed.EmbeddingUnavailable) as caught:
                embed.embed_one("where do I change my notification settings")
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(fake.calls, 0, "a missing key must not produce a network call")

    def test_429_retries_a_bounded_number_of_times_then_gives_up(self):
        with _ProviderCase(lambda n, p: _FakeResponse(429, {})) as fake:
            with self.assertRaises(embed.EmbeddingUnavailable) as caught:
                embed.embed_one("marketplace orders")
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(fake.calls, 3, "1 attempt + UNDX_EMBEDDING_MAX_RETRIES=2")
        self.assertEqual(embed.telemetry_snapshot()["embedding_429"], 3)

    def test_429_that_clears_on_retry_succeeds(self):
        def responder(call, payload):
            return _FakeResponse(429, {}) if call == 1 else _FakeResponse(200, _ok_body(payload))

        with _ProviderCase(responder) as fake:
            vector = embed.embed_one("marketplace orders")
        self.assertEqual(fake.calls, 2)
        self.assertEqual(len(vector), 64)

    def test_server_error_is_retryable_and_bounded(self):
        with _ProviderCase(lambda n, p: _FakeResponse(503, {})) as fake:
            with self.assertRaises(embed.EmbeddingUnavailable):
                embed.embed_one("live stream")
        self.assertEqual(fake.calls, 3)

    def test_timeout_is_counted_and_retried(self):
        def responder(call, payload):
            raise _FakeRequests.Timeout("read timeout")

        with _ProviderCase(responder) as fake:
            with self.assertRaises(embed.EmbeddingUnavailable):
                embed.embed_one("premium")
        self.assertEqual(fake.calls, 3)
        self.assertEqual(embed.telemetry_snapshot()["embedding_timeouts"], 3)

    def test_rejected_credential_is_not_retried_and_does_not_echo_the_body(self):
        body = {"error": {"message": "invalid api key sk-live-should-never-be-logged"}}
        with _ProviderCase(lambda n, p: _FakeResponse(401, body)) as fake:
            with self.assertRaises(embed.EmbeddingUnavailable) as caught:
                embed.embed_one("premium")
        self.assertEqual(fake.calls, 1, "a rejected credential will not become valid on retry")
        self.assertNotIn("sk-live", caught.exception.reason)

    def test_short_vector_list_raises_rather_than_misaligning_documents(self):
        def responder(call, payload):
            body = _ok_body(payload)
            body["data"] = body["data"][:-1]
            return _FakeResponse(200, body)

        with _ProviderCase(lambda n, p: responder(n, p)):
            with self.assertRaises(embed.EmbeddingUnavailable):
                embed.embed_texts(["alpha", "beta", "gamma"])

    def test_wrong_dimensionality_raises(self):
        def responder(call, payload):
            body = _ok_body(payload)
            body["data"][0]["embedding"] = body["data"][0]["embedding"][:10]
            return _FakeResponse(200, body)

        with _ProviderCase(responder):
            with self.assertRaises(embed.EmbeddingUnavailable):
                embed.embed_one("alpha")

    def test_non_numeric_vector_raises(self):
        def responder(call, payload):
            body = _ok_body(payload)
            body["data"][0]["embedding"][0] = "not-a-number"
            return _FakeResponse(200, body)

        with _ProviderCase(responder):
            with self.assertRaises(embed.EmbeddingUnavailable):
                embed.embed_one("alpha")

    def test_non_json_body_raises_and_is_not_retried(self):
        with _ProviderCase(lambda n, p: _FakeResponse(200, None, raw="<html>gateway</html>")) as fake:
            with self.assertRaises(embed.EmbeddingUnavailable):
                embed.embed_one("alpha")
        self.assertEqual(fake.calls, 1)

    def test_returned_vectors_are_unit_length(self):
        with _ProviderCase(lambda n, p: _FakeResponse(200, _ok_body(p))):
            vector = embed.embed_one("alpha")
        self.assertAlmostEqual(math.sqrt(sum(v * v for v in vector)), 1.0, places=6)

    def test_budget_guard_blocks_a_runaway_indexing_batch_before_spending(self):
        # Shaped like the failure it exists to stop: a large indexing pass, not one
        # oversized string. A budget of exactly zero *disables* the guard by design, so
        # asserting on "0" would have tested the opt-out and called it enforcement.
        runaway = ["canonical platform documentation " * 125] * 50  # ~50k tokens, ~$0.0002
        with _ProviderCase(
            lambda n, p: _FakeResponse(200, _ok_body(p)),
            {"UNDX_EMBEDDING_MONTHLY_BUDGET_USD": "0.0001"},
        ) as fake:
            with self.assertRaises(embed.EmbeddingUnavailable) as caught:
                embed.embed_texts(runaway, purpose="index")
        self.assertEqual(fake.calls, 0, "the guard must run before the request, not after")
        self.assertIn("budget", caught.exception.reason)
        self.assertFalse(caught.exception.retryable, "retrying will not make it cheaper")

    def test_zero_budget_is_documented_as_an_opt_out_not_a_lockout(self):
        with _ProviderCase(
            lambda n, p: _FakeResponse(200, _ok_body(p)),
            {"UNDX_EMBEDDING_MONTHLY_BUDGET_USD": "0"},
        ):
            self.assertFalse(embed.budget_state()["enforced"])
            self.assertEqual(len(embed.embed_one("alpha")), 64)


class SecretAndContentHygiene(unittest.TestCase):
    def test_key_is_sent_in_the_header_and_never_in_the_payload(self):
        with _ProviderCase(lambda n, p: _FakeResponse(200, _ok_body(p))) as fake:
            embed.embed_one("alpha")
        self.assertIn("Bearer ", fake.last_headers["Authorization"])
        self.assertNotIn(BASE_ENV["PERPLEXITY_API_KEY"], json.dumps(fake.last_payload))

    def test_report_description_reveals_only_whether_a_key_exists(self):
        with _ProviderCase(lambda n, p: _FakeResponse(200, _ok_body(p))):
            described = json.dumps(embed.describe_for_report())
        self.assertIn('"set"', described)
        self.assertNotIn(BASE_ENV["PERPLEXITY_API_KEY"], described)

    def test_telemetry_contains_no_content_and_no_credential(self):
        with _ProviderCase(lambda n, p: _FakeResponse(200, _ok_body(p))):
            embed.embed_one("my private message about my bank account")
            snapshot = json.dumps(embed.telemetry_snapshot())
        self.assertNotIn("bank account", snapshot)
        self.assertNotIn(BASE_ENV["PERPLEXITY_API_KEY"], snapshot)

    def test_forbidden_content_classes_cannot_be_indexed(self):
        for content_class in ("private_message", "credential", "payment_information", "precise_location"):
            with self.subTest(content_class=content_class):
                with self.assertRaises(semantic.ForbiddenContent):
                    semantic._reject_forbidden([
                        semantic.IndexDocument(
                            doc_id="x", title="t", body="b", content_class=content_class
                        )
                    ])

    def test_undeclared_content_class_is_rejected_rather_than_defaulted(self):
        with self.assertRaises(semantic.ForbiddenContent):
            semantic._reject_forbidden([
                semantic.IndexDocument(doc_id="x", title="t", body="b", content_class="")
            ])

    def test_canonical_corpus_contains_no_private_manifest_entries(self):
        private = {
            str(item.get("id"))
            for item in (lexical.load_manifest().get("entries") or [])
            if isinstance(item, dict) and item.get("public") is False
        }
        indexed = {document.doc_id for document in semantic.canonical_documents()}
        self.assertEqual(private & indexed, set())


def _load_manifest_generator():
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "scripts/generate_pulsesoc_platform_manifest.py"
    spec = importlib.util.spec_from_file_location("_manifest_generator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CanonicalDocumentIdentity(unittest.TestCase):
    """``doc_id`` is the index primary key, so a repeat is a lost row, not a dup row.

    ``index_documents`` deletes by ``doc_id`` before inserting. A corpus with a repeated
    id therefore stores fewer rows than it reports writing — the first live run wrote
    1,673 documents into an index that held 1,667 — and nothing in the write path
    notices, because it counts what it sent rather than what survived.
    """

    def test_every_canonical_document_has_a_unique_id(self):
        ids = [document.doc_id for document in semantic.canonical_documents()]
        duplicates = sorted({key for key in ids if ids.count(key) > 1})
        self.assertEqual(duplicates, [], "duplicate doc_id would silently overwrite")
        self.assertEqual(len(ids), len(set(ids)))

    def test_identical_repeats_collapse_to_one_document(self):
        document = semantic.IndexDocument(
            doc_id="dup", title="t", body="b", content_class="canonical_public")
        self.assertEqual(len(semantic._resolve_duplicate_ids([document, document])), 1)

    def test_conflicting_repeats_raise_rather_than_overwrite(self):
        first = semantic.IndexDocument(
            doc_id="dup", title="t", body="one", content_class="canonical_public")
        second = semantic.IndexDocument(
            doc_id="dup", title="t", body="two", content_class="canonical_public")
        with self.assertRaises(semantic.DuplicateDocumentId):
            semantic._resolve_duplicate_ids([first, second])

    def test_manifest_generator_disambiguates_routes_sharing_a_path(self):
        # Two Flask handlers may be registered on one method+path; Werkzeug serves the
        # first and the rest are dead code, but both are real records in the inventory
        # and must not overwrite each other on the way into the index.
        entries = [
            {"id": "server_route:bot.py:POST /x", "kind": "server_route",
             "name": "POST /x", "source": "bot.py", "handler": "first"},
            {"id": "server_route:bot.py:POST /x", "kind": "server_route",
             "name": "POST /x", "source": "bot.py", "handler": "second"},
        ]
        resolved = _load_manifest_generator()._dedupe_and_disambiguate(entries)
        self.assertEqual(len(resolved), 2)
        self.assertEqual(len({entry["id"] for entry in resolved}), 2)
        self.assertTrue(all("#" in entry["id"] for entry in resolved))

    def test_manifest_generator_collapses_byte_identical_repeats(self):
        # One navigable surface declared as both a tab and a stack screen is one record
        # seen twice, not two records. Indexing it twice would dilute the corpus.
        entry = {"id": "native_surface:nav.tsx:Reels", "kind": "native_surface",
                 "name": "Reels", "source": "nav.tsx", "component": "ReelsScreen"}
        resolved = _load_manifest_generator()._dedupe_and_disambiguate([dict(entry), dict(entry)])
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["id"], "native_surface:nav.tsx:Reels")

    def test_checked_in_manifest_carries_no_duplicate_ids(self):
        ids = [str(item.get("id")) for item in (lexical.load_manifest().get("entries") or [])
               if isinstance(item, dict)]
        duplicates = sorted({key for key in ids if ids.count(key) > 1})
        self.assertEqual(duplicates, [])


class CacheIdentity(unittest.TestCase):
    def test_cache_key_is_deterministic(self):
        args = dict(model="pplx-embed-v1-0.6b", model_version="1", dimensions=256)
        self.assertEqual(embed.cache_key("hello", **args), embed.cache_key("hello", **args))

    def test_cache_key_changes_with_every_identity_component(self):
        base = dict(model="pplx-embed-v1-0.6b", model_version="1", dimensions=256)
        original = embed.cache_key("hello", **base)
        variants = [
            embed.cache_key("hello ", **base),
            embed.cache_key("hello", **{**base, "model": "pplx-embed-v1-4b"}),
            embed.cache_key("hello", **{**base, "model_version": "2"}),
            embed.cache_key("hello", **{**base, "dimensions": 512}),
        ]
        # ``hello `` normalises to ``hello`` — whitespace is not a new document, and
        # paying twice for the same text with a trailing space would be a real cost bug.
        self.assertEqual(variants[0], original)
        self.assertEqual(len(set(variants[1:]) | {original}), 4)


# --------------------------------------------------------------------- retrieval paths


class _IndexedCase:
    """Builds a real on-disk index with the deterministic embedder, then restores state."""

    def __init__(self, env: dict | None = None):
        self.env = {**BASE_ENV, **(env or {})}
        self._dir = None
        self._patches: list = []

    def __enter__(self):
        self._dir = tempfile.TemporaryDirectory()
        path = str(Path(self._dir.name) / "semantic.db")
        self._connection_factory = lambda: sqlite3.connect(path)
        embed.reset_telemetry()
        embed.reset_budget()
        semantic.invalidate_cache()
        self._patches = [
            patch.dict(os.environ, self.env, clear=True),
            patch.dict("sys.modules", {"requests": _FakeRequests(lambda n, p: _FakeResponse(200, _ok_body(p)))}),
            patch.object(semantic, "_connect", self._connection_factory),
        ]
        for item in self._patches:
            item.start()
        return self

    def index(self, documents):
        return semantic.index_documents(documents)

    def __exit__(self, *exc):
        for item in reversed(self._patches):
            item.stop()
        semantic.invalidate_cache()
        self._dir.cleanup()
        return False


def _sample_documents(count: int = 40):
    return semantic.canonical_documents()[:count]


class CacheEconomics(unittest.TestCase):
    def test_second_index_pass_embeds_nothing(self):
        with _IndexedCase() as case:
            documents = _sample_documents()
            first = case.index(documents)
            second = case.index(documents)
        self.assertGreater(first.embedded, 0)
        self.assertEqual(second.embedded, 0, "unchanged canonical material must not be re-paid for")
        self.assertEqual(second.cached, len(documents))


class OrphanedRowPruning(unittest.TestCase):
    """A record whose ``doc_id`` changes leaves its old row behind, still retrievable.

    ``index_documents`` deletes only the id it is about to write. Disambiguating a
    collision changes an id by definition, so without a prune the pre-fix row survives in
    the vector index describing a capability under a key nothing regenerates. It is not
    inert — it is still a candidate the retriever can return.
    """

    def _stored_ids(self, case) -> set[str]:
        conn = case._connection_factory()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT doc_id FROM undx_semantic_index WHERE corpus = ?",
                (semantic.CANONICAL_CORPUS,),
            )
            return {str(row[0]) for row in (cursor.fetchall() or [])}
        finally:
            conn.close()

    def test_prune_removes_rows_the_corpus_no_longer_declares(self):
        with _IndexedCase() as case:
            documents = _sample_documents(12)
            case.index(documents)
            self.assertEqual(len(self._stored_ids(case)), len(documents))

            survivors = documents[:-3]
            dropped = {document.doc_id for document in documents[-3:]}
            removed = semantic.prune_missing_documents(
                survivors, conn=case._connection_factory()
            )

            self.assertEqual(set(removed), dropped)
            stored = self._stored_ids(case)
            self.assertEqual(stored, {document.doc_id for document in survivors})
            self.assertFalse(stored & dropped, "an orphan row is still a live candidate")

    def test_prune_is_a_no_op_when_every_stored_row_is_still_declared(self):
        with _IndexedCase() as case:
            documents = _sample_documents(12)
            case.index(documents)
            removed = semantic.prune_missing_documents(
                documents, conn=case._connection_factory()
            )
        self.assertEqual(removed, [], "a clean corpus must not delete anything")

    def test_prune_refuses_an_empty_corpus_rather_than_emptying_the_index(self):
        # The dangerous call is the accidental one: an upstream failure that yields zero
        # documents must not read as an instruction to wipe every indexed row.
        with _IndexedCase() as case:
            documents = _sample_documents(8)
            case.index(documents)
            with self.assertRaises(ValueError):
                semantic.prune_missing_documents([], conn=case._connection_factory())
            self.assertEqual(len(self._stored_ids(case)), len(documents))

    def test_prune_leaves_other_corpora_untouched(self):
        # Prune is scoped by corpus; a shared table must not lose another corpus's rows.
        with _IndexedCase() as case:
            documents = _sample_documents(6)
            case.index(documents)
            conn = case._connection_factory()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE undx_semantic_index SET corpus = ? WHERE doc_id = ?",
                    ("some_other_corpus", documents[0].doc_id),
                )
                conn.commit()
            finally:
                conn.close()

            semantic.prune_missing_documents(
                documents[1:], conn=case._connection_factory()
            )

            conn = case._connection_factory()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(*) FROM undx_semantic_index WHERE corpus = ?",
                    ("some_other_corpus",),
                )
                foreign = int(cursor.fetchone()[0])
            finally:
                conn.close()
        self.assertEqual(foreign, 1)


def _load_paced_indexer():
    import importlib.util  # noqa: PLC0415 - a script, not an importable package member

    path = Path(__file__).resolve().parents[2] / "scripts/undx_paced_canonical_index.py"
    spec = importlib.util.spec_from_file_location("_paced_canonical_index", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PruneGating(unittest.TestCase):
    """The bulk indexer may only prune after a run that saw the whole corpus.

    Pruning is the one step in the indexer that deletes. A run that aborted or lost
    chunks cannot tell "this id was removed from the corpus" apart from "this id failed
    to write this time", and acting on that confusion would turn a provider outage into
    real data loss.
    """

    def setUp(self):
        self.paced = _load_paced_indexer()

    def _run(self, *, chunk_results, documents):
        calls: list = []

        def fake_index(chunk):
            calls.append(list(chunk))
            return chunk_results.pop(0)

        pruned_with: list = []

        def fake_prune(docs):
            pruned_with.append(list(docs))
            return ["orphan:one"]

        with patch.object(self.paced.semantic, "canonical_documents", lambda: documents), \
             patch.object(self.paced.semantic, "index_documents", fake_index), \
             patch.object(self.paced.semantic, "prune_missing_documents", fake_prune), \
             patch.object(self.paced.semantic, "invalidate_cache", lambda: None), \
             patch.object(self.paced.semantic, "index_status", lambda: {"loaded": True}), \
             patch.object(self.paced, "PACE_SECONDS", 0.0), \
             patch.object(self.paced, "CHUNK_SIZE", 2), \
             patch.object(self.paced, "BACKOFF_SECONDS", []):
            result = self.paced.run_paced_index()
        return result, pruned_with

    @staticmethod
    def _ok(count):
        return type("R", (), {"ok": True, "documents": count, "embedded": count,
                              "cached": 0, "tokens": count, "notes": []})()

    @staticmethod
    def _fail():
        return type("R", (), {"ok": False, "documents": 0, "embedded": 0,
                              "cached": 0, "tokens": 0, "notes": ["refused"]})()

    def test_complete_run_prunes_against_the_whole_corpus(self):
        documents = semantic.canonical_documents()[:4]
        result, pruned_with = self._run(
            chunk_results=[self._ok(2), self._ok(2)], documents=documents
        )
        self.assertEqual(result["documents_failed"], 0)
        self.assertEqual(len(pruned_with), 1, "a clean run must prune exactly once")
        self.assertEqual(
            [doc.doc_id for doc in pruned_with[0]],
            [doc.doc_id for doc in documents],
            "prune must see the whole corpus, never the chunk that just landed",
        )
        self.assertEqual(result["orphans_pruned"], 1)
        self.assertEqual(result["prune_skipped"], "")

    def test_run_with_a_failed_chunk_does_not_prune(self):
        documents = semantic.canonical_documents()[:4]
        result, pruned_with = self._run(
            chunk_results=[self._ok(2), self._fail()], documents=documents
        )
        self.assertEqual(result["documents_failed"], 2)
        self.assertEqual(pruned_with, [], "an incomplete run must never delete rows")
        self.assertEqual(result["orphans_pruned"], 0)
        self.assertTrue(result["prune_skipped"])


class FallbackLadder(unittest.TestCase):
    def test_stage_off_is_byte_identical_to_the_existing_lexical_path(self):
        query = "how do I change my notification settings"
        with patch.dict(os.environ, {"UNDX_SEMANTIC_RETRIEVAL_STAGE": "off"}, clear=True):
            served = semantic.retrieve(query)
        self.assertEqual(served, lexical.retrieve(query))

    def test_unknown_stage_value_resolves_to_off(self):
        for value in ("prod", "production ", "PRODUCTION_READY", "1", "yes"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"UNDX_SEMANTIC_RETRIEVAL_STAGE": value}, clear=True):
                    self.assertIn(semantic.stage(), (semantic.STAGE_OFF, semantic.STAGE_PRODUCTION))
        with patch.dict(os.environ, {"UNDX_SEMANTIC_RETRIEVAL_STAGE": "prod"}, clear=True):
            self.assertEqual(semantic.stage(), semantic.STAGE_OFF)

    def test_missing_key_in_production_stage_serves_lexical(self):
        query = "how do I change my notification settings"
        env = {"UNDX_SEMANTIC_RETRIEVAL_STAGE": "production", "PERPLEXITY_API_KEY": ""}
        with _IndexedCase(env) as case:
            case.index(_sample_documents())
            with patch.dict(os.environ, {"PERPLEXITY_API_KEY": ""}):
                results, diagnostics = semantic.retrieve_with_diagnostics(query, user_id=1)
        self.assertEqual(diagnostics.served, "lexical")
        self.assertTrue(diagnostics.fell_back)
        self.assertEqual(results, lexical.retrieve(query))

    def test_provider_outage_in_production_stage_serves_lexical(self):
        query = "marketplace orders"
        env = {"UNDX_SEMANTIC_RETRIEVAL_STAGE": "production"}
        with _IndexedCase(env) as case:
            case.index(_sample_documents())
            outage = _FakeRequests(lambda n, p: _FakeResponse(503, {}))
            with patch.dict("sys.modules", {"requests": outage}):
                results, diagnostics = semantic.retrieve_with_diagnostics(query, user_id=1)
        self.assertEqual(diagnostics.served, "lexical")
        self.assertTrue(diagnostics.fell_back)
        self.assertEqual(results, lexical.retrieve(query))

    def test_empty_index_in_production_stage_serves_lexical(self):
        query = "marketplace orders"
        with _IndexedCase({"UNDX_SEMANTIC_RETRIEVAL_STAGE": "production"}):
            results, diagnostics = semantic.retrieve_with_diagnostics(query, user_id=1)
        self.assertEqual(diagnostics.served, "lexical")
        self.assertEqual(results, lexical.retrieve(query))

    def test_retrieve_never_raises_even_when_the_module_is_broken(self):
        with patch.object(semantic, "retrieve_with_diagnostics", side_effect=RuntimeError("boom")):
            with patch.dict(os.environ, {"UNDX_SEMANTIC_RETRIEVAL_STAGE": "production"}, clear=True):
                results = semantic.retrieve("marketplace orders", user_id=1)
        self.assertEqual(results, lexical.retrieve("marketplace orders"))


class RolloutStages(unittest.TestCase):
    def test_shadow_computes_semantic_but_serves_todays_answer(self):
        query = "marketplace orders"
        with _IndexedCase({"UNDX_SEMANTIC_RETRIEVAL_STAGE": "shadow"}) as case:
            case.index(_sample_documents())
            results, diagnostics = semantic.retrieve_with_diagnostics(query, user_id=1)
        self.assertEqual(diagnostics.stage, semantic.STAGE_SHADOW)
        self.assertEqual(diagnostics.served, "lexical")
        self.assertGreater(diagnostics.semantic_count, 0, "shadow must actually run the new path")
        self.assertEqual(results, lexical.retrieve(query))

    def test_qa_stage_serves_hybrid_only_to_the_cohort(self):
        query = "marketplace orders"
        env = {"UNDX_SEMANTIC_RETRIEVAL_STAGE": "qa", "UNDX_AGENT_QA_USER_IDS": "7"}
        with _IndexedCase(env) as case:
            case.index(_sample_documents())
            inside, inside_diagnostics = semantic.retrieve_with_diagnostics(query, user_id=7)
            outside, outside_diagnostics = semantic.retrieve_with_diagnostics(query, user_id=8)
        self.assertEqual(inside_diagnostics.served, "hybrid")
        self.assertEqual(outside_diagnostics.served, "lexical")
        self.assertEqual(outside, lexical.retrieve(query))
        self.assertIsInstance(inside, list)

    def test_qa_stage_excludes_anonymous_callers(self):
        env = {"UNDX_SEMANTIC_RETRIEVAL_STAGE": "qa", "UNDX_AGENT_QA_USER_IDS": "7"}
        with _IndexedCase(env) as case:
            case.index(_sample_documents())
            _, diagnostics = semantic.retrieve_with_diagnostics("marketplace orders", user_id=None)
        self.assertEqual(diagnostics.served, "lexical")


class AuthorityBoundary(unittest.TestCase):
    def test_hybrid_output_has_exactly_the_shape_the_lexical_path_has(self):
        with _IndexedCase({"UNDX_SEMANTIC_RETRIEVAL_STAGE": "production"}) as case:
            case.index(_sample_documents())
            results, diagnostics = semantic.retrieve_with_diagnostics("marketplace orders", user_id=1)
        self.assertEqual(diagnostics.served, "hybrid")
        self.assertTrue(results)
        for item in results:
            self.assertEqual(sorted(item), ["body", "category", "id", "title"])
            self.assertEqual(item["category"], "source_derived_platform_knowledge")

    def test_similarity_score_never_reaches_the_prompt(self):
        with _IndexedCase({"UNDX_SEMANTIC_RETRIEVAL_STAGE": "production"}) as case:
            case.index(_sample_documents())
            results, _ = semantic.retrieve_with_diagnostics("marketplace orders", user_id=1)
        rendered = json.dumps(results)
        self.assertNotIn("score", rendered)
        self.assertNotIn("similarity", rendered)
        self.assertNotIn("confidence", rendered)

    def test_module_declares_no_authority(self):
        self.assertEqual(semantic.AUTHORITY, "none")
        self.assertEqual(semantic.health()["authority"], "none")

    def test_hybrid_respects_the_same_result_and_character_bounds(self):
        with _IndexedCase({"UNDX_SEMANTIC_RETRIEVAL_STAGE": "production"}) as case:
            case.index(_sample_documents(120))
            results, _ = semantic.retrieve_with_diagnostics("marketplace orders", user_id=1)
        self.assertLessEqual(len(results), lexical.MAX_RESULTS)
        total = sum(len(item["title"]) + len(item["body"]) for item in results)
        self.assertLessEqual(total, lexical.MAX_CONTEXT_CHARS)

    def test_no_source_paths_leak_into_results(self):
        with _IndexedCase({"UNDX_SEMANTIC_RETRIEVAL_STAGE": "production"}) as case:
            case.index(_sample_documents(120))
            results, _ = semantic.retrieve_with_diagnostics("marketplace orders", user_id=1)
        rendered = json.dumps(results)
        for marker in (".py", "services/", "mobile-native/", "bot.py"):
            self.assertNotIn(marker, rendered)


class MultilingualBehaviour(unittest.TestCase):
    """The gap this work exists to close, asserted as behaviour rather than as a claim.

    The manifest's ``search_text`` is English CamelCase identifiers, so the lexical
    matcher cannot match a Haitian Creole or French query at all. These tests assert the
    honest current state and that the semantic path at least *runs* for those queries —
    not that it answers them well, which cannot be known without the real model.
    """

    QUERIES = {
        "ht": "kijan pou m chanje paramèt notifikasyon yo",
        "fr": "comment modifier mes paramètres de notification",
        "es": "cómo cambio la configuración de notificaciones",
    }

    def test_non_english_queries_survive_the_pipeline_without_error(self):
        with _IndexedCase({"UNDX_SEMANTIC_RETRIEVAL_STAGE": "production"}) as case:
            case.index(_sample_documents())
            for language, query in self.QUERIES.items():
                with self.subTest(language=language):
                    results = semantic.retrieve(query, user_id=1)
                    self.assertIsInstance(results, list)

    def test_accented_text_normalises_to_a_stable_cache_key(self):
        args = dict(model="m", model_version="1", dimensions=64)
        first = embed.cache_key(self.QUERIES["fr"], **args)
        second = embed.cache_key(f"  {self.QUERIES['fr']}  ", **args)
        self.assertEqual(first, second)


class HealthSurface(unittest.TestCase):
    def test_health_is_operator_safe(self):
        with _ProviderCase(lambda n, p: _FakeResponse(200, _ok_body(p))):
            rendered = json.dumps(semantic.health())
        self.assertNotIn(BASE_ENV["PERPLEXITY_API_KEY"], rendered)
        for counter in (
            "embedding_requests",
            "embedding_cache_hits",
            "embedding_cache_misses",
            "embedding_provider_errors",
            "embedding_429",
            "embedding_latency_ms",
            "semantic_retrieval_latency_ms",
            "semantic_fallback_count",
        ):
            self.assertIn(counter, rendered, f"{counter} must be observable")


if __name__ == "__main__":
    unittest.main()
