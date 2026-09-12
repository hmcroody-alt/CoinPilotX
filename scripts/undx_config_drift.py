#!/usr/bin/env python3
"""Report configuration that will make the fabric behave differently than it reads.

    python3 scripts/undx_config_drift.py            # runtime + source
    python3 scripts/undx_config_drift.py --runtime  # environment only
    python3 scripts/undx_config_drift.py --source   # repository only
    python3 scripts/undx_config_drift.py --json     # machine-readable

Unlike `undx_model_audit`, this calls nothing and costs nothing: the runtime
half reads environment variables and the source half parses files. It is safe
to run anywhere, including in CI, and safe to run from every worker.

The two halves answer different questions. The runtime half asks what *this*
process would do with the configuration it has — a key it cannot send, a
budget that can only see two of seven providers, a model override that quietly
lowers a privacy ceiling. The source half asks which code bypasses the fabric
altogether: a module with its own model default is a second answer to "which
model does this deployment send", and in every case found so far it was the
visible symptom of a call going straight to the vendor, outside the ledger, the
breaker and the privacy ceilings.

Exit status is 1 when anything is CRITICAL, so a pipeline can gate on it.
Warnings do not fail the run — they are true, they are worth fixing, and a
check that fails a build on them is a check people route around.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import undx_config_drift as drift  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _print(title: str, result: dict) -> None:
    print(f"{title} — {result['critical']} critical, "
          f"{result['warning']} warning, {result['info']} info")
    if not result["findings"]:
        print("  nothing to report")
        return
    for finding in result["findings"]:
        print(f"\n  [{finding['severity']}] {finding['code']}  {finding['where']}")
        print(f"    {finding['detail']}")
        print(f"    fix: {finding['fix']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runtime", action="store_true",
                        help="only check this process's environment")
    parser.add_argument("--source", action="store_true",
                        help="only check the repository")
    parser.add_argument("--root", default=REPO, help="tree to scan")
    parser.add_argument("--json", action="store_true", help="emit the raw result")
    args = parser.parse_args()

    both = not (args.runtime or args.source)
    out: dict[str, dict] = {}
    if args.runtime or both:
        out["runtime"] = drift.check()
    if args.source or both:
        findings = drift.scan_source(args.root)
        out["source"] = drift.summarise(findings)

    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        for index, (name, result) in enumerate(out.items()):
            if index:
                print()
            _print(f"UNDX configuration drift — {name}", result)

    return 0 if all(r["ok"] for r in out.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
