#!/usr/bin/env python3
"""Paced canonical index + frozen holdout, for a rate-limited embedding provider.

Why this exists rather than ``scripts/undx_semantic_live_acceptance.py`` alone: that
script hands the whole corpus to ``embed.embed_texts`` in one call, which batches to the
provider's *documented* ceiling — 512 inputs per request. Perplexity answered the first
such request with HTTP 429, and the client's in-request backoff is capped at two seconds
because it is designed to sit inside a user-facing request where a fast fallback beats a
slow answer. Three attempts inside 1.6 seconds is the right behaviour for a query and
the wrong behaviour for a bulk load, so the bulk load gets its own pacing here instead of
weakening the shared client.

The run is chunked, paced, and backs off in tens of seconds rather than milliseconds.
Every chunk that succeeds is durable: ``undx_embedding_cache`` is keyed by content hash
plus model plus dimensionality, so a chunk that lands is never paid for again and a run
that dies halfway resumes for free.

It also refuses to report a benchmark over an empty index. The acceptance script's
``build_index`` returns PASS whenever ``index_documents`` returns at all — including the
run where every provider call failed and zero documents were written — and a benchmark
over an empty index produces semantic 0.0 and hybrid exactly equal to lexical, which
reads like "semantic retrieval does not help" when it actually means "semantic retrieval
was never measured". That distinction is the whole point of the exercise, so it is
enforced rather than described.

Reads ``PERPLEXITY_API_KEY`` and ``DATABASE_URL`` from the environment. Neither is
printed. Output is one compact ``RPT|<section>|<json>`` line per fact, because Railway
drops log lines above 500/sec per replica and pretty-printed JSON is unreadable through
its log API.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _locate_root() -> Path:
    """Find the repository root by looking for a module we know lives in it."""
    candidates = [Path(os.environ.get("APP_ROOT", "")), Path.cwd(), Path("/app")]
    candidates += list(Path(__file__).resolve().parents)
    for candidate in candidates:
        try:
            if candidate and (candidate / "services" / "undx_semantic_retrieval.py").exists():
                return candidate.resolve()
        except OSError:
            continue
    raise SystemExit("RPT|fatal|{\"reason\":\"repository root not found\"}")


ROOT = _locate_root()
for path in (str(ROOT), str(ROOT / "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)

# The shared client's fast in-request retry is actively unhelpful for a bulk load: it
# burns two of the provider's rate-limited slots inside a second and then gives up. The
# outer loop below does the waiting instead, in units a rate limit actually respects.
os.environ.setdefault("UNDX_EMBEDDING_MAX_RETRIES", "0")

from services import undx_embedding_service as embed  # noqa: E402
from services import undx_semantic_retrieval as semantic  # noqa: E402

import undx_semantic_live_acceptance as acceptance  # noqa: E402  (sibling script)


CHUNK_SIZE = int(os.environ.get("INDEX_CHUNK_SIZE", "16"))
PACE_SECONDS = float(os.environ.get("INDEX_PACE_SECONDS", "1.5"))
#: Seconds to wait before each retry of a chunk. A rate limit is measured in tens of
#: seconds; retrying in milliseconds just spends attempts.
BACKOFF_SECONDS = [10.0, 30.0, 60.0, 120.0, 300.0]
#: If this many chunks fail back to back the provider is not throttling, it is refusing.
#: Continuing would spend the remaining hour proving the same thing.
CONSECUTIVE_FAILURE_ABORT = 4


def emit(section: str, payload) -> None:
    print(
        "RPT|%s|%s" % (section, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
        flush=True,
    )


def _delta(before: dict, after: dict, name: str) -> int:
    return int(after.get(name, 0) or 0) - int(before.get(name, 0) or 0)


def run_paced_index() -> dict:
    documents = semantic.canonical_documents()
    total = len(documents)
    before = dict(embed.telemetry_snapshot())
    started = time.perf_counter()

    attempted = 0
    indexed = 0
    failed = 0
    cached_hits = 0
    embedded = 0
    tokens = 0
    failed_offsets: list[int] = []
    consecutive_failures = 0
    aborted = ""

    for offset in range(0, total, CHUNK_SIZE):
        chunk = documents[offset : offset + CHUNK_SIZE]
        attempted += len(chunk)
        settled = False

        for attempt, wait in enumerate([0.0] + BACKOFF_SECONDS):
            if wait:
                time.sleep(wait)
            try:
                result = semantic.index_documents(chunk)
            except semantic.ForbiddenContent as exc:
                # Fatal by design: a corpus that smuggles private content must stop the
                # run, not skip a chunk.
                emit("fatal", {"reason": "forbidden_content", "detail": str(exc)[:200]})
                raise
            except Exception as exc:  # noqa: BLE001 - storage/transport, keep going
                emit("chunk_error", {"offset": offset, "attempt": attempt,
                                     "error": type(exc).__name__})
                continue

            if result.ok:
                settled = True
                indexed += result.documents
                embedded += result.embedded
                cached_hits += result.cached
                tokens += result.tokens
                break

            emit("chunk_retry", {"offset": offset, "attempt": attempt,
                                 "note": (result.notes[0] if result.notes else "")[:90]})

        if settled:
            consecutive_failures = 0
        else:
            failed += len(chunk)
            failed_offsets.append(offset)
            consecutive_failures += 1
            if consecutive_failures >= CONSECUTIVE_FAILURE_ABORT:
                aborted = "provider refused %d consecutive chunks" % consecutive_failures
                emit("abort", {"reason": aborted, "offset": offset})
                break

        if offset % (CHUNK_SIZE * 10) == 0:
            emit("progress", {"attempted": attempted, "indexed": indexed, "failed": failed,
                              "elapsed_s": round(time.perf_counter() - started, 1)})

        if PACE_SECONDS:
            time.sleep(PACE_SECONDS)

    after = dict(embed.telemetry_snapshot())
    semantic.invalidate_cache()
    status = semantic.index_status()

    return {
        "documents_attempted": attempted,
        "documents_total": total,
        "documents_indexed": indexed,
        "documents_failed": failed,
        "newly_embedded": embedded,
        "served_from_cache": cached_hits,
        "tokens": _delta(before, after, "embedding_tokens_embedded"),
        "provider_calls": _delta(before, after, "embedding_requests"),
        "failed_calls": _delta(before, after, "embedding_provider_errors"),
        "rate_limited_429": _delta(before, after, "embedding_429"),
        "timeouts": _delta(before, after, "embedding_timeouts"),
        "budget_blocks": _delta(before, after, "embedding_budget_blocks"),
        "cache_hits": _delta(before, after, "embedding_cache_hits"),
        "cache_misses": _delta(before, after, "embedding_cache_misses"),
        "duration_seconds": round(time.perf_counter() - started, 1),
        "estimated_cost_usd": embed.estimated_cost_usd(
            _delta(before, after, "embedding_tokens_embedded")
        ),
        "chunk_size": CHUNK_SIZE,
        "pace_seconds": PACE_SECONDS,
        "failed_offsets": failed_offsets[:20],
        "aborted": aborted,
        "index_status": status,
    }


def main() -> int:
    embed.reset_telemetry()

    probe = acceptance.probe()
    emit("probe", {k: probe.get(k) for k in
                   ("status", "dimensions", "latency_ms", "unit_normalised", "provider_error")})
    if probe.get("status") != "PASS":
        return 2

    estimate = acceptance.estimate_index_cost()
    emit("estimate", {k: estimate.get(k) for k in
                      ("documents", "estimated_tokens", "model", "estimated_cost_usd")})

    index = run_paced_index()
    emit("index", {k: v for k, v in index.items() if k != "index_status"})
    emit("index_status", index["index_status"])

    # The gate that makes the benchmark meaningful. Semantic scoring 0.0 across a corpus
    # that was never embedded is not a measurement of semantic retrieval, and reporting it
    # as one would be the exact false-success the mission asks to be guarded against.
    loaded = bool(index["index_status"].get("loaded"))
    stored = int(index["index_status"].get("documents_indexed") or 0)
    if not loaded or stored == 0:
        emit("holdout", {"status": "NOT_MEASURED",
                         "reason": "index is empty; semantic and hybrid cannot be measured",
                         "documents_in_index": stored})
        emit("decision", {"status": "NOT_MEASURED",
                          "recommendation": "RERUN_INDEX",
                          "reason": "no provider evidence about retrieval quality was produced"})
        return 4
    if stored < index["documents_total"]:
        emit("partial_index", {"stored": stored, "expected": index["documents_total"],
                               "note": "benchmark runs against a partial index; recall is a floor"})

    # Scoped to this process only. Nothing about any deployment's flags changes here.
    os.environ["UNDX_SEMANTIC_RETRIEVAL_STAGE"] = "production"
    os.environ.setdefault("UNDX_AGENT_QA_USER_IDS", "")
    semantic.invalidate_cache()

    holdout = acceptance.run_holdout()
    emit("holdout_meta", {"positive_cases": holdout.get("positive_cases"),
                          "negative_cases": holdout.get("negative_cases")})
    for mode, summary in (holdout.get("modes") or {}).items():
        emit("mode_%s" % mode, {
            "recall_at_1": summary.get("recall_at_1"),
            "recall_at_3": summary.get("recall_at_3"),
            "recall_at_5": summary.get("recall_at_5"),
            "mrr": summary.get("mrr"),
            "by_language": summary.get("recall_at_5_by_language"),
            "by_category": summary.get("recall_at_5_by_category"),
            "miss_count": summary.get("miss_count"),
            "negative_leaks": len((summary.get("negatives") or {}).get("leaked") or []),
            "provider_calls": summary.get("provider_calls"),
        })

    decision = acceptance.decide(holdout)
    emit("decision", {k: decision.get(k) for k in
                      ("status", "control_recall_at_5", "control_mrr", "hybrid_recall_at_5",
                       "hybrid_mrr", "semantic_recall_at_5", "semantic_mrr", "recall_at_5_gain",
                       "mrr_gain", "hybrid_negative_leaks", "gates", "recommendation")})
    emit("decision_multilingual", decision.get("multilingual"))
    emit("decision_indirect", decision.get("indirect"))
    emit("telemetry", embed.telemetry_snapshot())
    emit("done", {"status": "COMPLETE"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
