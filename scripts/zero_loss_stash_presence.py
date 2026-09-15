"""Phase 6: do the four stashes hold work that main does not?

A stash is not a normal commit and the difference matters here. `git stash push`
writes a *merge* commit whose first parent is the HEAD you stashed from, second
parent is the index state, and optional third is the untracked set. `git show`
on a merge renders **combined diff** format: one prefix column per parent, so an
added line arrives as `++text`, not `+text`. Stripping one character the way you
would for an ordinary commit leaves a literal `+` glued to the front of every
line, and every subsequent content search looks for a string that cannot exist.
The failure is silent and it is maximally misleading: it reports 0/50 present,
which reads as "this is unshipped work" — the exact opposite of the truth.

So this asks for the two-dot diff explicitly, ``git diff stash^ stash``, which
is an ordinary one-column patch against the commit the stash was taken from.

The rest of the question is the same one Phase 4b asked of dangling commits: of
the distinctive lines this stash *adds*, how many are in main's tree today? A
stash whose lines are all present is a stale working copy of work that shipped;
a stash whose lines are absent is either genuinely unfinished work or work that
was abandoned, and only reading it can tell you which.

Nothing here drops, pops, or applies a stash. The mission forbids it and it is
the wrong instrument anyway: the question is what the stash *contains*, and that
is answerable without touching the working tree.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPSTREAM = "origin/main"
MIN_SAMPLE = 8

BOILERPLATE = re.compile(
    r"^(from __future__ import annotations|import os|import sys|import json|"
    r"import re|import time|try:|except Exception:|else:|return|pass|break|"
    r"continue|\}|\)|\];?|\{|#.*|\"\"\"|'''|</\w+>|};?)$")


def git(*args: str) -> str:
    out = subprocess.run(("git",) + args, cwd=REPO, capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else ""


def stash_refs() -> list[tuple[str, str]]:
    refs = []
    for line in git("stash", "list", "--format=%gd|%s").splitlines():
        ref, _, subject = line.partition("|")
        if ref.strip():
            refs.append((ref.strip(), subject.strip()))
    return refs


def distinctive_added_lines(ref: str, cap: int = 60) -> list[str]:
    # Two-dot diff against the stash's first parent: a plain one-column patch.
    # `git show <stash>` would give combined-diff format and a second prefix
    # column, which is the bug this script exists to not repeat.
    diff = git("diff", ref + "^", ref)
    seen: list[str] = []
    for raw in diff.splitlines():
        if not raw.startswith("+") or raw.startswith("+++"):
            continue
        line = raw[1:].strip()
        if line.startswith("+"):
            # Defensive: a surviving second marker means the diff was not the
            # shape assumed above. Refuse to measure rather than measure wrong.
            raise SystemExit("combined-diff marker survived on %s: %r" % (ref, raw))
        if len(line) < 30:
            continue
        if BOILERPLATE.match(line):
            continue
        if len(re.findall(r"[A-Za-z_]{4,}", line)) < 2:
            continue
        seen.append(line)
        if len(seen) >= cap * 4:
            break
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
    results = []
    for ref, subject in stash_refs():
        stat = git("diff", "--stat", ref + "^", ref).strip().splitlines()
        lines = distinctive_added_lines(ref)
        record = {"ref": ref, "subject": subject,
                  "summary": stat[-1].strip() if stat else "",
                  "files": len(stat) - 1 if stat else 0}
        if len(lines) < MIN_SAMPLE:
            record.update(sampled=len(lines), found=0, ratio=None,
                          verdict="inconclusive")
            print("%-10s INCONCLUSIVE (only %d distinctive lines)  %s"
                  % (ref, len(lines), subject[:50]))
        else:
            missing = [l for l in lines if not present_in_upstream(l)]
            found = len(lines) - len(missing)
            ratio = found / float(len(lines))
            verdict = ("present" if ratio >= 0.90 else
                       "partial" if ratio >= 0.20 else "absent")
            record.update(sampled=len(lines), found=found,
                          ratio=round(ratio, 3), verdict=verdict,
                          missing_examples=missing[:12])
            print("%-10s %-11s %3d/%-3d  %s"
                  % (ref, verdict.upper(), found, len(lines), subject[:50]))
            for m in missing[:6]:
                print("      MISSING: %s" % m[:110])
        results.append(record)

    out_dir = os.path.join(REPO, "reports", "zero-loss-reconciliation", "phase6")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "stash_presence.json"), "w") as handle:
        json.dump(results, handle, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
