#!/usr/bin/env python3
"""Holdout benchmark for UNDX platform-knowledge retrieval.

Measures the *existing* lexical baseline first, then — only if a provider key and a
built index are actually present — semantic-only and hybrid retrieval on the identical
holdout, with identical scoring.

The rule this script exists to enforce is the mission's own: semantic retrieval is not
superior merely because it exists. Every number here comes from the same holdout, the
same matcher and the same top-k, so the three modes are comparable. When semantic
retrieval cannot run, this script reports NOT MEASURED rather than a placeholder — a
fabricated improvement is worse than an absent one.

Usage:
    python3 scripts/undx_semantic_retrieval_benchmark.py
    python3 scripts/undx_semantic_retrieval_benchmark.py --json out.json
    python3 scripts/undx_semantic_retrieval_benchmark.py --modes lexical
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import undx_embedding_service as embed  # noqa: E402
from services import undx_platform_knowledge as lexical  # noqa: E402
from services import undx_semantic_retrieval as semantic  # noqa: E402

HOLDOUT_PATH = ROOT / "data/undx/semantic_retrieval_holdout.json"
TOP_K = (1, 3, 5)
MAX_K = max(TOP_K)


# --------------------------------------------------------------------------- scoring


def result_name(title: str) -> str:
    """``PulseSoc native surface: NotificationPreferences`` -> ``notificationpreferences``.

    Retrieval is scored on the manifest entry a result names, not on the prose around
    it, so the same matcher can score a lexical result and a semantic one without
    favouring either side's rendering.
    """
    text = str(title or "")
    if ":" in text:
        text = text.split(":", 1)[1]
    return text.strip().lower()


def rank_of_first_hit(names: Sequence[str], targets: Iterable[str]) -> int | None:
    wanted = {str(t).strip().lower() for t in targets if str(t).strip()}
    for position, name in enumerate(names, start=1):
        if name in wanted:
            return position
    return None


class Accumulator:
    """Recall@k, MRR and latency for one mode, sliceable by category and language."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.cases = 0
        self.hits = {k: 0 for k in TOP_K}
        self.reciprocal = 0.0
        self.latencies: list[float] = []
        self.empty_results = 0
        self.by_category: dict[str, list[int]] = {}
        self.by_language: dict[str, list[int]] = {}

    def add(self, case: dict[str, Any], names: Sequence[str], latency_ms: float) -> int | None:
        rank = rank_of_first_hit(names[:MAX_K], case.get("targets") or [])
        self.cases += 1
        self.latencies.append(latency_ms)
        if not names:
            self.empty_results += 1
        for k in TOP_K:
            if rank is not None and rank <= k:
                self.hits[k] += 1
        self.reciprocal += (1.0 / rank) if rank else 0.0
        hit_at_max = 1 if (rank is not None and rank <= MAX_K) else 0
        self.by_category.setdefault(str(case.get("category") or "?"), []).append(hit_at_max)
        self.by_language.setdefault(str(case.get("language") or "?"), []).append(hit_at_max)
        return rank

    @staticmethod
    def _percentile(values: Sequence[float], fraction: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
        return round(ordered[index], 2)

    def summary(self) -> dict[str, Any]:
        n = max(1, self.cases)
        return {
            "mode": self.mode,
            "cases": self.cases,
            "recall_at_1": round(self.hits[1] / n, 4),
            "recall_at_3": round(self.hits[3] / n, 4),
            "recall_at_5": round(self.hits[5] / n, 4),
            "mrr": round(self.reciprocal / n, 4),
            "latency_p50_ms": self._percentile(self.latencies, 0.50),
            "latency_p95_ms": self._percentile(self.latencies, 0.95),
            "latency_mean_ms": round(statistics.fmean(self.latencies), 2) if self.latencies else 0.0,
            "empty_result_cases": self.empty_results,
            "recall_at_5_by_category": {
                key: round(sum(v) / len(v), 4) for key, v in sorted(self.by_category.items())
            },
            "recall_at_5_by_language": {
                key: round(sum(v) / len(v), 4) for key, v in sorted(self.by_language.items())
            },
        }


# --------------------------------------------------------------------------- modes


def run_lexical(query: str) -> tuple[list[str], float]:
    started = time.perf_counter()
    results = lexical.retrieve(query, limit=lexical.MAX_RESULTS)
    elapsed = (time.perf_counter() - started) * 1000
    return [result_name(item.get("title", "")) for item in results], elapsed


def run_semantic(query: str) -> tuple[list[str], float]:
    started = time.perf_counter()
    candidates = semantic.semantic_candidates(query, limit=MAX_K)
    elapsed = (time.perf_counter() - started) * 1000
    return [result_name(c.title) for c in candidates], elapsed


def run_hybrid(query: str) -> tuple[list[str], float]:
    started = time.perf_counter()
    results, _ = semantic.retrieve_with_diagnostics(query, user_id=None, limit=lexical.MAX_RESULTS)
    elapsed = (time.perf_counter() - started) * 1000
    return [result_name(item.get("title", "")) for item in results], elapsed


RUNNERS = {"lexical": run_lexical, "semantic": run_semantic, "hybrid": run_hybrid}


# --------------------------------------------------------------------------- readiness


def semantic_readiness() -> dict[str, Any]:
    """Whether semantic retrieval can be measured at all, and if not, precisely why.

    Kept separate from the run so the report can distinguish "measured and no better"
    from "never ran" — two results that mean opposite things.
    """
    provider = embed.describe_for_report()
    status = semantic.index_status()
    reasons: list[str] = []
    if provider.get("api_key") != "set":
        reasons.append("PERPLEXITY_API_KEY is unset")
    if not status.get("documents_indexed"):
        reasons.append("semantic index is empty")
    return {
        "ready": not reasons,
        "blocked_by": reasons,
        "provider": provider,
        "index": status,
    }


# --------------------------------------------------------------------------- driver


def load_holdout(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected a JSON object")
    return data


def run_mode(
    mode: str, cases: Sequence[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    accumulator = Accumulator(mode)
    runner = RUNNERS[mode]
    misses: list[dict[str, Any]] = []
    per_case: list[dict[str, Any]] = []
    for case in cases:
        names, latency = runner(str(case.get("query") or ""))
        rank = accumulator.add(case, names, latency)
        per_case.append({
            "id": case.get("id"),
            "language": case.get("language"),
            "category": case.get("category"),
            "rank": rank,
            "returned": list(names[:MAX_K]),
        })
        if rank is None:
            misses.append({
                "id": case.get("id"),
                "query": case.get("query"),
                "language": case.get("language"),
                "category": case.get("category"),
                "targets": case.get("targets"),
                "returned": names[:3],
            })
    return accumulator.summary(), misses, per_case


def run_negatives(mode: str, cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Negative cases must stay empty. A retriever that always answers is not a retriever."""
    runner = RUNNERS[mode]
    leaks: list[dict[str, Any]] = []
    for case in cases:
        names, _ = runner(str(case.get("query") or ""))
        if names:
            leaks.append({"id": case.get("id"), "query": case.get("query"), "returned": names[:3]})
    return {
        "cases": len(cases),
        "stayed_empty": len(cases) - len(leaks),
        "leaked": leaks,
        "pass": not leaks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", default=str(HOLDOUT_PATH))
    parser.add_argument("--modes", default="lexical,semantic,hybrid")
    parser.add_argument("--json", dest="json_out", default="")
    parser.add_argument("--misses", type=int, default=0, help="print N example misses per mode")
    parser.add_argument(
        "--per-case",
        action="store_true",
        help="record the rank and returned names for every holdout case (needed to freeze a control)",
    )
    args = parser.parse_args()

    holdout = load_holdout(Path(args.holdout))
    positives = [c for c in holdout.get("cases") or [] if c.get("targets")]
    negatives = holdout.get("negative_cases") or []
    requested = [m.strip() for m in args.modes.split(",") if m.strip() in RUNNERS]

    embed.reset_telemetry()
    readiness = semantic_readiness()

    report: dict[str, Any] = {
        "holdout": str(Path(args.holdout).relative_to(ROOT)) if Path(args.holdout).is_relative_to(ROOT) else args.holdout,
        "positive_cases": len(positives),
        "negative_cases": len(negatives),
        "top_k": list(TOP_K),
        "stage": semantic.stage(),
        "similarity_floor": semantic.similarity_floor(),
        "semantic_readiness": readiness,
        "modes": {},
    }

    for mode in requested:
        if mode in ("semantic", "hybrid") and not readiness["ready"]:
            report["modes"][mode] = {
                "mode": mode,
                "status": "NOT MEASURED",
                "blocked_by": readiness["blocked_by"],
            }
            continue
        before = dict(embed.telemetry_snapshot())
        summary, misses, per_case = run_mode(mode, positives)
        after = dict(embed.telemetry_snapshot())
        calls = int(after.get("embedding_requests", 0)) - int(before.get("embedding_requests", 0))
        hits = int(after.get("embedding_cache_hits", 0)) - int(before.get("embedding_cache_hits", 0))
        misses_count = int(after.get("embedding_cache_misses", 0)) - int(before.get("embedding_cache_misses", 0))
        lookups = hits + misses_count
        summary["status"] = "MEASURED"
        summary["provider_calls"] = calls
        summary["cache_hits"] = hits
        summary["cache_misses"] = misses_count
        summary["cache_hit_rate"] = round(hits / lookups, 4) if lookups else None
        tokens = int(after.get("embedding_tokens_embedded", 0)) - int(
            before.get("embedding_tokens_embedded", 0)
        )
        summary["tokens_embedded"] = tokens
        summary["estimated_cost_usd"] = embed.estimated_cost_usd(tokens)
        summary["negatives"] = run_negatives(mode, negatives)
        summary["example_misses"] = misses[: max(0, args.misses)]
        summary["miss_count"] = len(misses)
        if args.per_case:
            summary["per_case"] = per_case
        report["modes"][mode] = summary

    measured = {m: s for m, s in report["modes"].items() if s.get("status") == "MEASURED"}
    if "lexical" in measured and "hybrid" in measured:
        report["recall_at_5_improvement"] = round(
            measured["hybrid"]["recall_at_5"] - measured["lexical"]["recall_at_5"], 4
        )
        report["mrr_improvement"] = round(measured["hybrid"]["mrr"] - measured["lexical"]["mrr"], 4)
    else:
        report["recall_at_5_improvement"] = None
        report["mrr_improvement"] = None

    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
