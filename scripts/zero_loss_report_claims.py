"""Phase 7: do the mission writeups describe work that is actually in main?

The repository root holds 43 ``*_REPORT.md`` files and the ``reports/`` tree
holds hundreds more. Each is an agent's account of a mission it finished. An
account is not evidence: a report can describe a route that was renamed before
merge, a module that was folded into another, or -- the case this phase exists
for -- work that was written up and never landed at all.

Reading 900 documents is not a plan, and skimming them is worse than not
reading them, because a skim produces confidence without coverage. So this asks
each report a question it cannot answer rhetorically: **of the code identifiers
you name, how many exist?**

A report says things like ``services/foo_bar.py``, ``def resolve_entitlement``,
``/api/private-office/relationships``. Those are checkable. Prose is not, and is
ignored. The identifiers are extracted from inline code spans only -- a bare
word in a sentence is too ambiguous to search for, and searching for it would
produce matches that mean nothing.

Three things keep this honest:

* **A minimum sample.** A report naming two identifiers tells you nothing about
  itself; it is reported as thin rather than scored, the same discipline the
  dangling-commit probe uses.
* **Filenames are checked against the tree, not grepped.** ``git grep`` for
  ``services/foo.py`` finds the string in *other documents* that mention it,
  which would let a cluster of reports vouch for each other. A path is resolved
  with ``git cat-file``, so only the file's real existence counts.
* **The output is a ratio.** A report at 100% is consistent with main. A report
  at 30% needs a human, because the missing 70% is either renamed work,
  abandoned work, or a plan that was written as though it were a result -- and
  those are different problems with different fixes.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPSTREAM = "origin/main"
MIN_SAMPLE = 6

#: Inline code spans. Reports use single backticks almost exclusively; fenced
#: blocks are sample output and shell transcripts, which are not claims about
#: what exists.
SPAN = re.compile(r"`([^`\n]{3,120})`")

#: A path into the tree. Checked by resolving it, never by grepping.
PATH = re.compile(r"^[\w./-]+\.(py|ts|tsx|js|json|yml|yaml|html|swift|md)$")

#: A Python/JS identifier worth searching for. Requires an underscore or a dot
#: or camelCase so that ordinary English words in backticks do not qualify.
IDENT = re.compile(r"^[A-Za-z_][\w.]{5,}$")

#: An HTTP route.
ROUTE = re.compile(r"^/[\w/<>:.-]{4,}$")


def git(*args: str) -> tuple[int, str]:
    out = subprocess.run(("git",) + args, cwd=REPO, capture_output=True, text=True)
    return out.returncode, out.stdout


def claims(text: str, cap: int = 40) -> list[tuple[str, str]]:
    """(kind, value) for each checkable identifier, de-duplicated, capped."""
    seen: dict[str, tuple[str, str]] = {}
    for raw in SPAN.findall(text):
        value = raw.strip()
        if PATH.match(value):
            kind = "path"
        elif ROUTE.match(value.split()[-1]) and " " in value:
            # "GET /api/foo" -> take the route half
            value, kind = value.split()[-1], "route"
        elif ROUTE.match(value):
            kind = "route"
        elif IDENT.match(value) and ("_" in value or "." in value):
            kind = "ident"
        else:
            continue
        seen.setdefault(value, (kind, value))
        if len(seen) >= cap:
            break
    return list(seen.values())


def exists(kind: str, value: str) -> bool:
    if kind == "path":
        # Resolve, do not grep: grepping would let reports cite each other.
        code, _ = git("cat-file", "-e", "%s:%s" % (UPSTREAM, value))
        return code == 0
    needle = value if kind == "route" else value.split(".")[-1]
    out = subprocess.run(
        ["git", "grep", "--fixed-strings", "--quiet", needle, UPSTREAM,
         "--", "*.py", "*.ts", "*.tsx", "*.js", "*.json", "*.html", "*.swift"],
        cwd=REPO, capture_output=True, text=True)
    return out.returncode == 0


def main() -> int:
    targets: list[str] = []
    for name in sorted(os.listdir(REPO)):
        if name.endswith("_REPORT.md"):
            targets.append(name)
    for root, _dirs, files in os.walk(os.path.join(REPO, "reports")):
        for name in files:
            if name.endswith(".md"):
                targets.append(os.path.relpath(os.path.join(root, name), REPO))

    results = []
    for rel in targets:
        try:
            text = open(os.path.join(REPO, rel), encoding="utf-8",
                        errors="replace").read()
        except OSError:
            continue
        found_claims = claims(text)
        if len(found_claims) < MIN_SAMPLE:
            results.append({"report": rel, "sampled": len(found_claims),
                            "verdict": "thin"})
            continue
        missing = [v for k, v in found_claims if not exists(k, v)]
        present = len(found_claims) - len(missing)
        ratio = present / float(len(found_claims))
        results.append({
            "report": rel, "sampled": len(found_claims), "present": present,
            "ratio": round(ratio, 3), "missing": missing[:15],
            "verdict": ("consistent" if ratio >= 0.90 else
                        "drifted" if ratio >= 0.60 else "unsupported"),
        })

    out_dir = os.path.join(REPO, "reports", "zero-loss-reconciliation", "phase7")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "report_claims.json"), "w") as handle:
        json.dump(results, handle, indent=2)

    from collections import Counter
    tally = Counter(r["verdict"] for r in results)
    print("reports examined: %d" % len(results))
    for verdict in ("consistent", "drifted", "unsupported", "thin"):
        print("  %-12s %d" % (verdict, tally.get(verdict, 0)))
    print("\nlowest-scoring, needing a human:")
    scored = [r for r in results if r["verdict"] in ("unsupported", "drifted")]
    scored.sort(key=lambda r: r["ratio"])
    for r in scored[:25]:
        print("  %5.0f%%  %-62s %s" % (
            r["ratio"] * 100, r["report"][:62],
            ", ".join(r["missing"][:3])[:60]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
