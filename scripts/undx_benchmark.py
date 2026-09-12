#!/usr/bin/env python3
"""Grade the golden corpus against real providers, or explain a routing decision.

    python3 scripts/undx_benchmark.py                    # what a run would cost
    python3 scripts/undx_benchmark.py --run              # actually spend money
    python3 scripts/undx_benchmark.py --run --providers claude,groq
    python3 scripts/undx_benchmark.py --explain "debug this file"
    python3 scripts/undx_benchmark.py --coverage
    python3 scripts/undx_benchmark.py --compare results.json

`--run` is required to make a single network call. Everything else here —
estimating, explaining, coverage — is free and offline. The default has to be
the free one: the alternative is a tool where typing the name of the script
sends 147 paid completions, and somebody eventually types it in a loop.

This is an operator action, not a request path. Do not wire it into a worker:
nine processes each running it is a 1,323-call herd against providers that may
already be failing, which is the harm the breaker exists to prevent.

Exit status is 1 when a run produced no evidence, when `--explain` describes a
request that would fail before reaching a vendor, or when the routing table
contradicts the benchmark. Those are the three "look at this" conditions; a
clean benchmark exits 0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import undx_benchmark as bench  # noqa: E402
from services import undx_eval_corpus as corpus  # noqa: E402
from services import undx_routing_evidence as evidence  # noqa: E402


def _usd(value):
    return "unknown" if value is None else f"${value:,.6f}"


def _print_estimate(report: dict) -> None:
    print(f"Corpus v{report['corpus_version']}: {report['cases']} cases, "
          f"{report['calls']} calls across {len(report['providers'])} providers")
    for name, row in sorted(report["providers"].items()):
        print(f"  {name:<12} {row['model']:<28} {_usd(row['cost_usd'])}")
    print(f"  {'TOTAL':<12} {'':<28} {_usd(report['cost_usd'])}"
          f"{'' if report['cost_complete'] else '  (floor: some models unpriced)'}")
    if report["unpriced_providers"]:
        print(f"\nNo verified price for: {', '.join(report['unpriced_providers'])}. "
              f"Their real cost is unknown, not zero.")
    print("\nNothing was called. Re-run with --run to spend this.")


def _print_run(result: dict, report: dict) -> None:
    print(f"Corpus v{result['corpus_version']}: {len(result['case_ids'])} cases, "
          f"{result['elapsed_ms']}ms, {_usd(result['cost_usd'])}"
          f"{'' if result['cost_complete'] else ' (floor)'}\n")
    print(f"{'provider':<12} {'score':>7} {'answered':>9} {'median ms':>10}  notes")
    for name, row in sorted(result["providers"].items()):
        if row["skipped"]:
            print(f"{name:<12} {'—':>7} {'—':>9} {'—':>10}  skipped: {row['skipped']}")
            continue
        score = "—" if row["score"] is None else f"{row['score']:.3f}"
        latency = "—" if row["median_latency_ms"] is None else row["median_latency_ms"]
        notes = ", ".join(f"{count} {reason}"
                          for reason, count in sorted(row["unanswered_reasons"].items()))
        print(f"{name:<12} {score:>7} {row['answered']:>4}/{row['attempted']:<4} "
              f"{latency:>10}  {notes}")

    print(f"\nOrder is descriptive. {len(report['separated'])} pair(s) cleared "
          f"the significance test (p < {bench.ALPHA}).")
    for verdict in report["separated"]:
        print(f"  {verdict['better']} > "
              f"{verdict['b'] if verdict['better'] == verdict['a'] else verdict['a']}"
              f"  p={verdict['p_value']:.4f} over {verdict['shared_cases']} shared cases")
    if not report["evidence"]:
        print("  (none — this run does not support any routing change)")


def _print_explain(record: dict) -> None:
    print(f"category: {record['category']}  ->  lane: {record['lane']}")
    if record["signals"]:
        print(f"signals:  {', '.join(record['signals'])}")
    print(f"table:    {' > '.join(record['lane_table'])}")
    print(f"router_enabled={record['router_enabled']} "
          f"multi_model={record['multi_model']} "
          f"default={record['default_provider']}\n")
    for step in record["steps"]:
        gate = f"  BLOCKED: {step['gate']}" if step["gate"] else ""
        print(f"  {step['position']}. {step['provider']:<12} "
              f"({step['reason']}){gate}")
    if not record["consistent"]:
        print("\n!! The explainer's reconstruction does not match the router's "
              "plan.\n   The reasons above are stale and must not be trusted; "
              "`plan` is the truth.")
    if record["would_fail"]:
        print("\n!! Every provider is gated. This request would fail before "
              "reaching a vendor.")
    else:
        print(f"\nwould try: {' -> '.join(record['would_try'])}")


def _print_coverage(report: dict) -> None:
    print(f"Corpus v{report['corpus_version']}, "
          f"{report['min_lane_cases']} cases needed per lane\n")
    for lane, row in report["lanes"].items():
        mark = "ok " if row["sufficient"] else "THIN"
        print(f"  {mark} {lane:<16} {row['cases']} case(s)")
    if report["uncovered"]:
        print(f"\nNo cases at all: {', '.join(report['uncovered'])}. "
              f"A frozen corpus cannot grade freshness, so no benchmark will "
              f"ever speak to current_web.")


def _print_contradictions(report: dict) -> None:
    if not report.get("corpus_version"):
        print(f"Cannot compare: {report['reason']}")
        return
    print(f"{report['separated_pairs']} separated pair(s), scope: {report['scope']}\n")
    for row in report["findings"]:
        print(f"  CONTRADICTS {row['lane']}: table ranks {row['ranked_ahead']} "
              f"above {row['ranked_behind']}, benchmark favours "
              f"{row['benchmark_favours']} (p={row['p_value']}, "
              f"{row['lane_cases']} cases in this lane)")
    for row in report["supported"]:
        print(f"  supports    {row['lane']}: {row['ranked_ahead']} over "
              f"{row['ranked_behind']} (p={row['p_value']})")
    if not report["findings"] and not report["supported"]:
        print("  nothing separated; this run does not speak to the routing table")
    print(f"\n{report['note']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", action="store_true",
                        help="actually call providers and spend money")
    parser.add_argument("--providers", default="",
                        help="comma separated; default is every configured provider")
    parser.add_argument("--lane", default="", help="restrict to one routing lane")
    parser.add_argument("--tag", default="", help="restrict to one case tag")
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--explain", default="",
                        help="show how this message would be routed; calls nothing")
    parser.add_argument("--privacy-class", default="",
                        help="evaluate --explain under this privacy class")
    parser.add_argument("--detail", action="store_true",
                        help="include refusal sentences, which quote configuration")
    parser.add_argument("--coverage", action="store_true",
                        help="corpus cases per routing lane")
    parser.add_argument("--compare", default="",
                        help="a saved run to check the routing table against")
    parser.add_argument("--save", default="", help="write the run to this path")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    providers = [p.strip() for p in args.providers.split(",") if p.strip()]

    if args.explain:
        record = evidence.explain(args.explain,
                                  privacy_class=args.privacy_class or None,
                                  detail=args.detail)
        if args.json:
            print(json.dumps(record, indent=2))
        else:
            _print_explain(record)
        return 1 if record["would_fail"] or not record["consistent"] else 0

    if args.coverage:
        report = evidence.coverage()
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            _print_coverage(report)
        return 0

    if args.compare:
        with open(args.compare, encoding="utf-8") as handle:
            saved = json.load(handle)
        report = evidence.contradictions(saved)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            _print_contradictions(report)
        return 0 if report["ok"] else 1

    sound = corpus.self_check()
    if not sound["ok"]:
        # Refused rather than run. Grading providers with a broken grader
        # attributes the author's mistake to a vendor.
        print(f"Corpus is unsound, refusing to run: {sound['broken']}",
              file=sys.stderr)
        return 2

    if not args.run:
        report = bench.estimate(providers or None, lane=args.lane or None,
                                tag=args.tag or None)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            _print_estimate(report)
        return 0

    result = bench.run(providers or None, lane=args.lane or None,
                       tag=args.tag or None, max_cases=args.max_cases or None)
    report = bench.ranking(result)
    if args.save:
        with open(args.save, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
    if args.json:
        print(json.dumps({"result": result, "ranking": report}, indent=2))
    else:
        _print_run(result, report)
    return 0 if report["evidence"] else 1


if __name__ == "__main__":
    sys.exit(main())
