#!/usr/bin/env python3
"""Freeze the pre-change lexical retrieval result as an immutable comparison control.

Stage 0 of the live-activation mission says the existing lexical numbers are *the*
control. A control that is re-derived after the implementation changes is not a control
— it silently moves with the thing it is supposed to measure. So this script writes the
numbers to disk once, together with the SHA-256 of every input that could change them:
the lexical module, the manifest it reads, and the holdout.

``--verify`` re-hashes those inputs and re-runs the benchmark against the frozen file.
It is expected to FAIL after Stage 14 edits the lexical module — that failure is the
signal that the frozen numbers are now historical rather than reproducible, which is
precisely what makes them a control.

Usage:
    python3 scripts/undx_freeze_lexical_baseline.py
    python3 scripts/undx_freeze_lexical_baseline.py --verify
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "data/undx/baseline_lexical_results.json"
BENCHMARK = ROOT / "scripts/undx_semantic_retrieval_benchmark.py"

# Everything whose content can move the lexical numbers. If a file here changes, the
# frozen result is no longer reproducible and must be treated as history, not as truth
# about the current code.
PROVENANCE_FILES = [
    "services/undx_platform_knowledge.py",
    "data/undx/semantic_retrieval_holdout.json",
    "scripts/undx_semantic_retrieval_benchmark.py",
]


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def provenance() -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in PROVENANCE_FILES:
        target = ROOT / rel
        out[rel] = sha256_of(target) if target.exists() else "MISSING"
    return out


def run_benchmark() -> dict[str, Any]:
    """Run the lexical mode only, in a clean subprocess with the flag forced off.

    A subprocess rather than an import so no module state left over from this script
    can influence the measurement.
    """
    completed = subprocess.run(
        [sys.executable, str(BENCHMARK), "--modes", "lexical", "--per-case"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "UNDX_SEMANTIC_RETRIEVAL_STAGE": "off",
            "HOME": "/tmp",
        },
        timeout=900,
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr[-4000:])
        raise SystemExit(f"benchmark failed with exit code {completed.returncode}")
    return json.loads(completed.stdout)


def freeze() -> int:
    report = run_benchmark()
    lexical = report["modes"]["lexical"]
    payload = {
        "control_name": "lexical_baseline_pre_stage_14",
        "purpose": (
            "Immutable comparison control for UNDX semantic retrieval. Frozen before any "
            "change to the lexical implementation. Do not regenerate to make a later "
            "result look better."
        ),
        "frozen_at_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(timespec="seconds"),
        "provenance_sha256": provenance(),
        "holdout": report["holdout"],
        "positive_cases": report["positive_cases"],
        "negative_cases": report["negative_cases"],
        "top_k": report["top_k"],
        "headline": {
            "recall_at_1": lexical["recall_at_1"],
            "recall_at_3": lexical["recall_at_3"],
            "recall_at_5": lexical["recall_at_5"],
            "mrr": lexical["mrr"],
            "empty_result_cases": lexical["empty_result_cases"],
            "miss_count": lexical["miss_count"],
            "latency_p50_ms": lexical["latency_p50_ms"],
            "latency_p95_ms": lexical["latency_p95_ms"],
        },
        "recall_at_5_by_category": lexical["recall_at_5_by_category"],
        "recall_at_5_by_language": lexical["recall_at_5_by_language"],
        "negative_controls": lexical["negatives"],
        "per_case": lexical["per_case"],
    }
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"froze {len(payload['per_case'])} cases -> {BASELINE_PATH.relative_to(ROOT)}")
    print(json.dumps(payload["headline"], indent=2))
    return 0


def verify() -> int:
    if not BASELINE_PATH.exists():
        raise SystemExit(f"no frozen control at {BASELINE_PATH}")
    frozen = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    now = provenance()
    drifted = [k for k, v in frozen["provenance_sha256"].items() if now.get(k) != v]

    report = run_benchmark()
    lexical = report["modes"]["lexical"]
    deltas = {
        key: round(lexical[key] - value, 4)
        for key, value in frozen["headline"].items()
        if key in lexical and isinstance(value, (int, float))
    }
    changed_cases = []
    frozen_ranks = {c["id"]: c["rank"] for c in frozen["per_case"]}
    for case in lexical["per_case"]:
        before = frozen_ranks.get(case["id"], "ABSENT")
        if before != case["rank"]:
            changed_cases.append({"id": case["id"], "was": before, "now": case["rank"]})

    result = {
        "provenance_drift": drifted,
        "headline_deltas": deltas,
        "cases_with_changed_rank": changed_cases,
        "identical_to_control": not drifted and not changed_cases,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    return verify() if args.verify else freeze()


if __name__ == "__main__":
    raise SystemExit(main())
