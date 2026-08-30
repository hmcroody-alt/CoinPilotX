#!/usr/bin/env python3
"""One-command live acceptance run for UNDX semantic retrieval.

Everything the acceptance mission asks for that requires a real provider, in a single
bounded execution: the minimal auth probe, a cost estimate the operator approves before
anything is spent, the canonical index, the frozen 74-case holdout measured three ways,
the negative controls, the multilingual slices, and a decision computed from those
numbers rather than asserted.

Why this exists as a script rather than as steps: the run costs money and writes to a
database, so it must be reproducible, bounded, and reviewable before it is executed. It
also has to be runnable somewhere other than where it was written — the environment that
authored it has no route to api.perplexity.ai — so the whole procedure travels as one
file that needs nothing but ``PERPLEXITY_API_KEY`` in the environment.

The key is read from the environment by the provider client and never printed, logged,
returned, or written to the report. The only fact this script will state about it is
whether it is present.

Order is deliberate and each phase gates the next:

    probe -> cost estimate -> (approval) -> index -> benchmark -> decision

A failed probe stops before indexing. An estimate above the ceiling stops before
spending. A decision is computed from the frozen control, not chosen.

Usage:
    python3 scripts/undx_semantic_live_acceptance.py --probe-only
    python3 scripts/undx_semantic_live_acceptance.py --estimate-only
    python3 scripts/undx_semantic_live_acceptance.py --confirm-spend --max-index-cost-usd 1.00
    python3 scripts/undx_semantic_live_acceptance.py --confirm-spend --json out.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# The benchmark is a sibling script, not a package module. Import it by adding scripts/ to
# the path rather than by copying its scoring code — the whole point of the acceptance run
# is that lexical, semantic and hybrid are scored by the identical matcher.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from services import undx_embedding_service as embed  # noqa: E402
from services import undx_platform_knowledge as lexical  # noqa: E402
from services import undx_semantic_retrieval as semantic  # noqa: E402

import undx_semantic_retrieval_benchmark as bench  # noqa: E402  (sibling script)

BASELINE_PATH = ROOT / "data/undx/baseline_lexical_results.json"

# The mission's promotion bar, as numbers rather than as prose. "Materially" is given a
# value here so the decision cannot drift with whoever reads the report: a gain smaller
# than this is inside the noise of a 70-case holdout and is not evidence.
MATERIAL_RECALL_GAIN = 0.05
MATERIAL_MRR_GAIN = 0.03
MAX_ACCEPTABLE_NEGATIVE_LEAKS = 2  # the lexical baseline already leaks 2 of 4


def _fail(phase: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"phase": phase, "status": "FAIL", "reason": reason, **extra}


# ------------------------------------------------------------------------------- probe


def probe() -> dict[str, Any]:
    """One minimal embedding request. The cheapest possible question: does this work?

    Deliberately a single short input, so a misconfiguration costs a fraction of a cent
    rather than the price of a full corpus pass.
    """
    result: dict[str, Any] = {
        "phase": "probe",
        "api_key_present": embed.api_key_configured(),
        "model": embed.configured_model(),
        "endpoint": embed.configured_endpoint(),
        "requested_dimensions": embed.configured_dimensions(),
    }
    if not result["api_key_present"]:
        return {**result, "status": "FAIL", "auth": "FAIL",
                "reason": f"{embed.API_KEY_ENV} is not set in this environment"}

    started = time.perf_counter()
    try:
        vector = embed.embed_one("PulseSoc notification preferences", purpose="probe")
    except embed.EmbeddingUnavailable as exc:
        return {
            **result,
            "status": "FAIL",
            "auth": "FAIL",
            "vector_returned": False,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            # exc.reason is the client's sanitised classification, never the raw body
            "provider_error": exc.reason,
            "retryable": exc.retryable,
        }

    latency = round((time.perf_counter() - started) * 1000, 1)
    dimensions = len(vector)
    norm = embed.l2_norm(vector)
    return {
        **result,
        "status": "PASS",
        "auth": "PASS",
        "vector_returned": True,
        "dimensions": dimensions,
        "dimensions_match_config": dimensions == embed.configured_dimensions(),
        "latency_ms": latency,
        "unit_normalised": abs(norm - 1.0) < 1e-6,
        "provider_error": "NONE",
    }


# ---------------------------------------------------------------------------- cost bound


def estimate_index_cost() -> dict[str, Any]:
    """Exact document and token counts before a single byte is embedded.

    Counted from the same ``canonical_documents()`` the indexer will use, so the estimate
    describes the actual work rather than a guess about it.
    """
    documents = semantic.canonical_documents()
    # embed_text() is exactly what the indexer sends to the provider, so the estimate is
    # counted over the same string that will be billed — not over the raw body.
    tokens = sum(embed.estimate_tokens(d.embed_text()) for d in documents)
    return {
        "phase": "estimate",
        "status": "PASS",
        "documents": len(documents),
        "estimated_tokens": tokens,
        "model": embed.configured_model(),
        "estimated_cost_usd": embed.estimated_cost_usd(tokens),
        "price_per_million_tokens_usd": embed.PRICE_PER_MILLION_TOKENS_USD.get(
            embed.configured_model(), embed._UNKNOWN_MODEL_PRICE_USD
        ),
        "monthly_budget_usd": embed.configured_monthly_budget_usd(),
        "note": (
            "Cost is incurred once. Unchanged documents are served from the content-hash "
            "cache on every later pass, so a re-run is free unless the corpus or the "
            "model configuration changed."
        ),
    }


# ------------------------------------------------------------------------------- index


def build_index() -> dict[str, Any]:
    documents = semantic.canonical_documents()
    started = time.perf_counter()
    before = dict(embed.telemetry_snapshot())
    try:
        outcome = semantic.index_documents(documents)
    except semantic.ForbiddenContent as exc:
        return _fail("index", f"corpus rejected by the content guard: {exc}")
    except embed.EmbeddingUnavailable as exc:
        return _fail("index", exc.reason, retryable=exc.retryable)
    after = dict(embed.telemetry_snapshot())

    def delta(name: str) -> int:
        return int(after.get(name, 0)) - int(before.get(name, 0))

    tokens = delta("embedding_tokens_embedded")
    semantic.invalidate_cache()
    return {
        "phase": "index",
        "status": "PASS",
        "documents": len(documents),
        "embedded": outcome.embedded,
        "cached": outcome.cached,
        "tokens": tokens,
        "cache_hits": delta("embedding_cache_hits"),
        "cache_misses": delta("embedding_cache_misses"),
        "provider_calls": delta("embedding_requests"),
        "failed_calls": delta("embedding_provider_errors"),
        "indexing_seconds": round(time.perf_counter() - started, 2),
        "estimated_cost_usd": embed.estimated_cost_usd(tokens),
        "index_status": semantic.index_status(),
    }


# --------------------------------------------------------------------------- benchmark


def run_holdout(modes: tuple[str, ...] = ("lexical", "semantic", "hybrid")) -> dict[str, Any]:
    """The frozen holdout, unmodified, measured identically for all three modes."""
    holdout = bench.load_holdout(bench.HOLDOUT_PATH)
    positives = [c for c in holdout.get("cases") or [] if c.get("targets")]
    negatives = holdout.get("negative_cases") or []

    out: dict[str, Any] = {
        "phase": "holdout",
        "status": "PASS",
        "positive_cases": len(positives),
        "negative_cases": len(negatives),
        "modes": {},
    }
    for mode in modes:
        before = dict(embed.telemetry_snapshot())
        summary, misses, per_case = bench.run_mode(mode, positives)
        after = dict(embed.telemetry_snapshot())
        summary["provider_calls"] = int(after.get("embedding_requests", 0)) - int(
            before.get("embedding_requests", 0)
        )
        summary["cache_hits"] = int(after.get("embedding_cache_hits", 0)) - int(
            before.get("embedding_cache_hits", 0)
        )
        summary["negatives"] = bench.run_negatives(mode, negatives)
        summary["per_case"] = per_case
        summary["example_misses"] = misses[:10]
        summary["miss_count"] = len(misses)
        out["modes"][mode] = summary
    return out


# ---------------------------------------------------------------------------- decision


def decide(holdout: dict[str, Any]) -> dict[str, Any]:
    """Compute the promotion decision from the frozen control. No judgement calls.

    The mission's rule is that paying for the API is not evidence. So this compares
    against ``baseline_lexical_results.json`` — the numbers frozen before any of this ran
    — rather than against the lexical mode measured in the same session, which could
    itself have drifted.
    """
    if not BASELINE_PATH.exists():
        return _fail("decision", "no frozen control; run scripts/undx_freeze_lexical_baseline.py")
    control = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    base = control["headline"]

    modes = holdout.get("modes") or {}
    if "hybrid" not in modes:
        return _fail("decision", "hybrid was not measured")
    hybrid = modes["hybrid"]
    sem = modes.get("semantic") or {}

    recall_gain = round(hybrid["recall_at_5"] - base["recall_at_5"], 4)
    mrr_gain = round(hybrid["mrr"] - base["mrr"], 4)

    control_lang = control["recall_at_5_by_language"]
    control_cat = control["recall_at_5_by_category"]
    multilingual = {
        language: {
            "control": control_lang.get(language),
            "hybrid": hybrid["recall_at_5_by_language"].get(language),
            "delta": round(
                (hybrid["recall_at_5_by_language"].get(language) or 0.0)
                - (control_lang.get(language) or 0.0),
                4,
            ),
        }
        for language in sorted(set(control_lang) | set(hybrid["recall_at_5_by_language"]))
    }
    indirect = {
        "control": control_cat.get("indirect"),
        "hybrid": hybrid["recall_at_5_by_category"].get("indirect"),
        "delta": round(
            (hybrid["recall_at_5_by_category"].get("indirect") or 0.0)
            - (control_cat.get("indirect") or 0.0),
            4,
        ),
    }

    non_english_gain = max(
        (multilingual[l]["delta"] for l in ("ht", "fr", "es") if l in multilingual),
        default=0.0,
    )
    hybrid_leaks = len((hybrid.get("negatives") or {}).get("leaked") or [])

    gates = {
        "recall_at_5_materially_better": recall_gain >= MATERIAL_RECALL_GAIN,
        "mrr_materially_better": mrr_gain >= MATERIAL_MRR_GAIN,
        "multilingual_or_indirect_materially_better": (
            non_english_gain >= MATERIAL_RECALL_GAIN or indirect["delta"] >= MATERIAL_RECALL_GAIN
        ),
        "negative_controls_no_worse": hybrid_leaks <= MAX_ACCEPTABLE_NEGATIVE_LEAKS,
    }
    passed = all(gates.values())
    return {
        "phase": "decision",
        "status": "PASS",
        "control_source": str(BASELINE_PATH.relative_to(ROOT)),
        "control_recall_at_5": base["recall_at_5"],
        "control_mrr": base["mrr"],
        "hybrid_recall_at_5": hybrid["recall_at_5"],
        "hybrid_mrr": hybrid["mrr"],
        "semantic_recall_at_5": sem.get("recall_at_5"),
        "semantic_mrr": sem.get("mrr"),
        "recall_at_5_gain": recall_gain,
        "mrr_gain": mrr_gain,
        "indirect": indirect,
        "multilingual": multilingual,
        "hybrid_negative_leaks": hybrid_leaks,
        "control_negative_leaks": len((control["negative_controls"] or {}).get("leaked") or []),
        "thresholds": {
            "material_recall_gain": MATERIAL_RECALL_GAIN,
            "material_mrr_gain": MATERIAL_MRR_GAIN,
            "max_acceptable_negative_leaks": MAX_ACCEPTABLE_NEGATIVE_LEAKS,
        },
        "gates": gates,
        "recommendation": "ENABLE_SHADOW" if passed else "KEEP_OFF",
        "rationale": (
            "Hybrid beat the frozen control on every gate."
            if passed
            else "At least one gate failed. Paying for the provider is not evidence; keep it off."
        ),
    }


# ------------------------------------------------------------------------------ driver


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--estimate-only", action="store_true")
    parser.add_argument(
        "--confirm-spend",
        action="store_true",
        help="required before any bulk indexing; without it the run stops after the estimate",
    )
    parser.add_argument("--max-index-cost-usd", type=float, default=1.00)
    parser.add_argument("--json", dest="json_out", default="")
    args = parser.parse_args()

    embed.reset_telemetry()
    report: dict[str, Any] = {
        "run": "undx_semantic_live_acceptance",
        "started_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(timespec="seconds"),
        "secret_value_exposed": False,
        "provider": embed.describe_for_report(),
        "stage_flag": semantic.stage(),
    }

    def finish(code: int) -> int:
        report["telemetry"] = embed.telemetry_snapshot()
        print(json.dumps(report, indent=2, ensure_ascii=False))
        if args.json_out:
            Path(args.json_out).write_text(
                json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        return code

    # A cost estimate is arithmetic over the local corpus, so it must not require the
    # provider. Keeping --estimate-only ahead of the probe lets the spend be reviewed from
    # anywhere, including an environment with no route to the API.
    if args.estimate_only:
        report["estimate"] = estimate_index_cost()
        report["halted_at"] = "estimate_only"
        return finish(0)

    report["probe"] = probe()
    if report["probe"]["status"] != "PASS":
        report["halted_at"] = "probe"
        report["halt_reason"] = report["probe"].get("reason") or report["probe"].get("provider_error")
        return finish(2)
    if args.probe_only:
        return finish(0)

    report["estimate"] = estimate_index_cost()
    if report["estimate"]["estimated_cost_usd"] > args.max_index_cost_usd:
        report["halted_at"] = "estimate"
        report["halt_reason"] = (
            f"estimated ${report['estimate']['estimated_cost_usd']} exceeds the "
            f"${args.max_index_cost_usd} ceiling; raise --max-index-cost-usd deliberately"
        )
        return finish(3)
    if args.estimate_only or not args.confirm_spend:
        report["halted_at"] = "estimate"
        report["halt_reason"] = "--confirm-spend was not given; nothing was embedded"
        return finish(0)

    report["index"] = build_index()
    if report["index"]["status"] != "PASS":
        report["halted_at"] = "index"
        report["halt_reason"] = report["index"].get("reason")
        return finish(4)

    # The benchmark must exercise the real semantic path, which only runs when the stage
    # allows it. This is scoped to this process only — it does not touch any deployment.
    os.environ["UNDX_SEMANTIC_RETRIEVAL_STAGE"] = "production"
    os.environ.setdefault("UNDX_AGENT_QA_USER_IDS", "")
    semantic.invalidate_cache()

    report["holdout"] = run_holdout()
    report["decision"] = decide(report["holdout"])
    return finish(0)


if __name__ == "__main__":
    raise SystemExit(main())
