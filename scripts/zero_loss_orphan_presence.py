"""Phase 4b: did an orphan commit's *content* reach main, under any shape?

Patch id already answered the easy version of this question and left 16 commits
unresolved. Patch id is exact: it matches a commit that was rebased or
cherry-picked, because those preserve the diff, but it cannot match a commit
whose work arrived split across two commits, folded into a larger refactor,
amended with one extra line, or re-typed from scratch. Every one of those is
"the content is present" and every one of them looks identical to "the content
is lost" if all you have is a hash of the diff.

So this asks a weaker question that survives those transformations: of the
distinctive lines this commit *added*, how many exist in main's tree today?

"Distinctive" is doing real work in that sentence. A commit's diff is mostly
lines that would appear in any file — closing braces, `import os`, a blank
`return`, a log call. Counting those measures how much Python looks like Python,
and every commit would score near 100%. The filter below drops anything short,
anything that is pure punctuation, and the handful of universal one-liners, and
then requires a minimum sample size — a commit whose added lines are *all*
boilerplate yields no evidence either way and is reported as inconclusive rather
than silently scored.

The output is a ratio, not a verdict. A high ratio means the work is present and
the commit is an old copy; a low ratio means the lines genuinely are not in main
and a human should read the diff. The middle is exactly where judgement is
required, which is why this prints the number instead of a boolean.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPSTREAM = "origin/main"
MIN_SAMPLE = 8

#: Lines that carry no identity. Matching one of these tells you the language,
#: not the change.
BOILERPLATE = re.compile(
    r"^(from __future__ import annotations|import os|import sys|import json|"
    r"import re|import time|try:|except Exception:|else:|return|pass|break|"
    r"continue|\}|\)|\];?|\{|#.*|\"\"\"|'''|</\w+>|};?)$")


def git(*args: str) -> str:
    out = subprocess.run(("git",) + args, cwd=REPO, capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else ""


def distinctive_added_lines(sha: str, cap: int = 60) -> list[str]:
    diff = git("show", "--format=", "--unified=0", sha)
    seen: list[str] = []
    for raw in diff.splitlines():
        if not raw.startswith("+") or raw.startswith("+++"):
            continue
        line = raw[1:].strip()
        if len(line) < 30:
            continue
        if BOILERPLATE.match(line):
            continue
        # A line that is only punctuation and keywords is not identifying.
        if len(re.findall(r"[A-Za-z_]{4,}", line)) < 2:
            continue
        seen.append(line)
        if len(seen) >= cap * 4:
            break
    # Spread the sample across the diff rather than taking the first N, which
    # would concentrate on one file and measure that file's fate, not the
    # commit's.
    if len(seen) <= cap:
        return seen
    step = len(seen) / float(cap)
    return [seen[int(i * step)] for i in range(cap)]


def present_in_upstream(line: str) -> bool:
    out = subprocess.run(
        ["git", "grep", "--fixed-strings", "--quiet", line, UPSTREAM],
        cwd=REPO, capture_output=True, text=True)
    return out.returncode == 0


def main() -> int:
    path = os.path.join(REPO, "reports", "zero-loss-reconciliation",
                        "phase4", "dangling.json")
    data = json.load(open(path))
    subjects = set(git("log", "--format=%s", UPSTREAM).splitlines())

    real = [o for o in data["orphans"]
            if not o["meta"].split("|")[-1].startswith(("index on ", "WIP on ", "On "))]
    unresolved = [o for o in real if o["meta"].split("|", 2)[2] not in subjects]

    results = []
    for o in unresolved:
        sha = o["sha"]
        lines = distinctive_added_lines(sha)
        if len(lines) < MIN_SAMPLE:
            results.append({**o, "sampled": len(lines), "found": 0,
                            "ratio": None, "verdict": "inconclusive"})
            print("%s  INCONCLUSIVE (only %d distinctive lines)"
                  % (sha[:10], len(lines)))
            continue
        found = sum(1 for l in lines if present_in_upstream(l))
        ratio = found / float(len(lines))
        verdict = ("present" if ratio >= 0.90 else
                   "partial" if ratio >= 0.20 else "absent")
        results.append({**o, "sampled": len(lines), "found": found,
                        "ratio": round(ratio, 3), "verdict": verdict})
        print("%s  %-11s %3d/%-3d  %s"
              % (sha[:10], verdict.upper(), found, len(lines),
                 o["meta"].split("|", 2)[2][:70]))

    out_path = os.path.join(REPO, "reports", "zero-loss-reconciliation",
                            "phase4", "orphan_presence.json")
    with open(out_path, "w") as handle:
        json.dump(results, handle, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
