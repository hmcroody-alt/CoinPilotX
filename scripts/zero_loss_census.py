"""Phase 1-3 of the zero-loss reconciliation: what work exists outside origin/main.

The question this answers is deliberately narrow and deliberately not the one a
branch list answers. "Is `claude/foo` merged?" is cheap and wrong: a branch can
be merged and still carry commits whose content never reached main (dropped in a
conflict resolution, reverted later, squashed lossily), and a branch can be
unmerged while every line it holds was re-implemented on main under different
names. The mission text is explicit about it — *A branch being "merged" does NOT
prove all its work is present* — so nothing here reads a merge flag.

What it reads instead is `git cherry`, which compares *patch ids*. A patch id is
a hash of the diff with whitespace and line numbers normalised away, so a commit
that was cherry-picked, rebased, or re-authored with a different message and a
different SHA still matches its twin upstream. `git cherry upstream head` prints
one line per commit on `head` that is not reachable from `upstream`, prefixed:

    -  an equivalent patch IS already upstream  -> content present, ignore
    +  no equivalent patch upstream             -> candidate unshipped work

The `+` set is the only thing worth a human's attention, and it is still only a
*candidate*: patch-id matching is exact, so a commit whose content arrived
upstream in a different shape (split across two commits, folded into a larger
refactor, or re-implemented) shows as `+` while being genuinely present. That
residual is what the later semantic phases exist to resolve; this phase's job is
to shrink 136 branches down to the handful that could possibly matter, and to do
it without ever trusting a name.

Output is both machine-readable (JSON, one record per ref) and human-readable,
because the ledger has to survive being read by a person and diffed by a script.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPSTREAM = "origin/main"


def git(*args: str, cwd: str = REPO) -> str:
    out = subprocess.run(("git",) + args, cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        return ""
    return out.stdout


def all_refs() -> list[tuple[str, str]]:
    """(kind, refname) for every local and remote branch, excluding the upstream itself."""
    refs: list[tuple[str, str]] = []
    for line in git("for-each-ref", "--format=%(refname)", "refs/heads/").splitlines():
        refs.append(("local", line.strip()))
    for line in git("for-each-ref", "--format=%(refname)",
                    "refs/remotes/").splitlines():
        name = line.strip()
        if not name or name.endswith("/HEAD"):
            continue
        # `ro` is a second remote pointing at the same GitHub repository as
        # `origin`. Walking both would double every record for no new content.
        if name.startswith("refs/remotes/ro/"):
            continue
        if name == "refs/remotes/origin/main":
            continue
        refs.append(("remote", name))
    return refs


def classify(ref: str) -> dict:
    sha = git("rev-parse", ref).strip()
    base = git("merge-base", UPSTREAM, ref).strip()
    record = {
        "ref": ref,
        "sha": sha,
        "merge_base": base,
        "unique_commits": [],
        "equivalent_upstream": 0,
        "error": None,
    }
    if not sha or not base:
        record["error"] = "unresolvable"
        return record

    # `git cherry` walks base..ref. If the ref is an ancestor of upstream the
    # range is empty and the branch is fully contained -- the common case, and
    # the one that makes this cheap.
    cherry = git("cherry", "-v", UPSTREAM, ref)
    plus: list[dict] = []
    minus = 0
    for line in cherry.splitlines():
        if line.startswith("- "):
            minus += 1
        elif line.startswith("+ "):
            rest = line[2:]
            csha, _, subject = rest.partition(" ")
            plus.append({"sha": csha, "subject": subject})
    record["unique_commits"] = plus
    record["equivalent_upstream"] = minus
    return record


def main() -> int:
    refs = all_refs()
    records = []
    for kind, ref in refs:
        rec = classify(ref)
        rec["kind"] = kind
        records.append(rec)
        print("%-7s %-60s +%-4d -%-4d" % (
            kind, ref.replace("refs/remotes/", "").replace("refs/heads/", ""),
            len(rec["unique_commits"]), rec["equivalent_upstream"]),
            file=sys.stderr, flush=True)

    out_dir = os.path.join(REPO, "reports", "zero-loss-reconciliation", "phase1")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "ref_census.json"), "w") as handle:
        json.dump({"upstream": UPSTREAM,
                   "upstream_sha": git("rev-parse", UPSTREAM).strip(),
                   "refs": records}, handle, indent=2)

    carrying = [r for r in records if r["unique_commits"]]
    carrying.sort(key=lambda r: -len(r["unique_commits"]))
    with open(os.path.join(out_dir, "ref_census.txt"), "w") as handle:
        handle.write("zero-loss census against %s (%s)\n" % (
            UPSTREAM, git("rev-parse", UPSTREAM).strip()[:12]))
        handle.write("refs examined: %d\n" % len(records))
        handle.write("refs fully contained upstream by patch-id: %d\n"
                     % (len(records) - len(carrying)))
        handle.write("refs carrying candidate unshipped commits: %d\n\n"
                     % len(carrying))
        for r in carrying:
            handle.write("%s  (+%d unique, %d already upstream)\n" % (
                r["ref"].replace("refs/remotes/", "").replace("refs/heads/", ""),
                len(r["unique_commits"]), r["equivalent_upstream"]))
            for c in r["unique_commits"][:40]:
                handle.write("    %s %s\n" % (c["sha"][:12], c["subject"]))
            if len(r["unique_commits"]) > 40:
                handle.write("    ... %d more\n" % (len(r["unique_commits"]) - 40))
            handle.write("\n")

    print("\nrefs: %d   carrying unique content: %d" % (len(records), len(carrying)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
