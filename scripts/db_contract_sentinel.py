#!/usr/bin/env python3
"""Database Contract Sentinel -- does the schema our logic depends on exist?

Answers two questions about this repo, statically, without booting the app:

  1. Do the database objects our security / money / privacy / business logic
     depends on actually exist?
  2. Can a database error silently remove a security decision?

This is the human-facing CLI. The gate that actually runs in CI is
``tests/protection/test_database_contract.py`` -- there is no special script
anyone has to remember to run. This exists for reading the detail.

    python3 scripts/db_contract_sentinel.py              # the gate's view
    python3 scripts/db_contract_sentinel.py --full       # + known defects
    python3 scripts/db_contract_sentinel.py --dead       # declared, never used
    python3 scripts/db_contract_sentinel.py --json

Exit status is 1 if the contract is broken, 0 otherwise. Known, triaged
defects do not fail the run -- they are listed, with their owner, so that the
sentinel can land on a repo that already has defects without pretending those
defects are acceptable.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db_contract import contract  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWLIST = os.path.join(ROOT, "config", "db_contract_allowlist.json")

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "known": 3}


def _sorted(findings):
    return sorted(findings, key=lambda f: (SEVERITY_ORDER.get(f.severity, 9),
                                           f.reference.location))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=ROOT)
    parser.add_argument("--allowlist", default=ALLOWLIST)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--full", action="store_true",
                        help="also list triaged known defects")
    parser.add_argument("--dead", action="store_true",
                        help="list declared-but-never-referenced tables "
                             "(reported, never deleted)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        report, allowlist = contract.analyse(args.root, args.allowlist)

    config_problems = allowlist.validate()
    findings = _sorted(report.findings)
    broken = bool(findings or config_problems)

    if args.as_json:
        json.dump({
            "ok": not broken,
            "files_scanned": report.files_scanned,
            "seconds": round(report.seconds, 2),
            "references": len(report.references),
            "declared_tables": len(report.declarations),
            "guarded": [r.table for r in report.guarded],
            "config_problems": config_problems,
            "findings": [{
                "kind": f.kind, "severity": f.severity,
                "location": f.reference.location,
                "func": f.reference.func,
                "table": getattr(f.reference, "table", None),
                "domains": list(f.domains), "detail": f.detail,
            } for f in findings],
            "known_defects": [{
                "table": f.reference.table, "location": f.reference.location,
                "detail": f.detail,
            } for f in _sorted(report.known)],
            "dead_schema": _dead(report),
        }, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 1 if broken else 0

    if args.dead:
        dead = _dead(report)
        print(f"Declared but never referenced by runtime code: {len(dead)}")
        print("Reported, not deleted -- a table can be live and only written by "
              "a worker, a migration, or an operator.\n")
        by_authority = collections.Counter()
        for item in dead:
            by_authority[item["authority"]] += 1
        for name, count in by_authority.most_common():
            print(f"  {count:4d}  {name}")
        print()
        for item in dead:
            print(f"  {item['table']:48s} {item['declared_at']}")
        return 0

    if not args.quiet:
        print(f"Database Contract Sentinel")
        print(f"  files scanned         : {report.files_scanned}")
        print(f"  executed statements   : {len(report.statements)}")
        print(f"  table references      : {len(report.references)}")
        print(f"  tables declared       : {len(report.declarations)}")
        print(f"  existence-guarded     : {len(report.guarded)}"
              f"  {sorted({r.table for r in report.guarded})}")
        print(f"  elapsed               : {report.seconds:.1f}s")
        print()

    for problem in config_problems:
        print(f"CONFIG   {problem}")
    if config_problems:
        print()

    if findings:
        print(f"{len(findings)} finding(s):\n")
        for finding in findings:
            print(finding.render())
            print()
    else:
        print("No contract violations: every static runtime table reference "
              "resolves to a recognised schema authority.\n")

    if report.known:
        counts = collections.Counter(f.reference.table for f in report.known)
        print(f"{len(report.known)} reference(s) to {len(counts)} known, triaged "
              f"defect table(s) -- tracked in config/db_contract_allowlist.json:")
        for table, count in counts.most_common():
            print(f"  {count:3d}x  {table}")
        if args.full:
            print()
            for finding in _sorted(report.known):
                print(finding.render())
                print()
        print()

    return 1 if broken else 0


def _dead(report):
    """Brief section 26: the contract in reverse."""
    referenced = {r.table for r in report.references}
    out = []
    for table in sorted(set(report.declarations) - referenced):
        decl = report.declarations[table][0]
        out.append({
            "table": table,
            "declared_at": f"{decl.path}:{decl.line}",
            "authority": contract.authority_family(decl.path)[0],
        })
    return out


if __name__ == "__main__":
    raise SystemExit(main())
