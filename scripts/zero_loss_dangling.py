"""Phase 4: are any of the unreachable commits carrying work that never landed?

`git fsck` reports 381 unreachable commits in this repository. Almost all of
them are ordinary debris: the pre-image of every `commit --amend`, every
abandoned rebase step, every reset. Debris is the expected case and reading 381
diffs by hand to confirm it is not a plan.

The discriminator is the same one Phase 1 used on branches, for the same reason:
content, not provenance. A dangling commit whose *patch id* already appears in
``origin/main`` is by definition carrying no line main lacks — it is the old
copy of something that landed. Only a dangling commit with no patch-id twin
upstream could be lost work, and that set is small enough to read.

Two tests, cheapest and strictest first:

1. **Reachability.** If the commit is an ancestor of ``origin/main`` it is on
   main by identity, not merely by content, and fsck listed it only because no
   *ref* points at it. Nothing more to ask.
2. **Patch id.** ``git cherry <upstream> <sha> <sha>^`` classifies exactly one
   commit — the three-argument form limits the walk to ``sha^..sha`` instead of
   re-walking the whole branch — and prints ``-`` when an equivalent patch is
   already upstream, ``+`` when none is.

A merge commit has no single diff and therefore no patch id; ``git cherry``
omits merges, so a merge that fails test 1 is reported as unclassifiable rather
than silently dropped. That matters here: silently dropping is the one failure
mode this whole phase exists to prevent.

What this deliberately does not do is resurrect anything. It classifies and
reports; deciding what to do with a genuine orphan needs the diff in front of a
person.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPSTREAM = "origin/main"


def git(*args: str) -> tuple[int, str]:
    out = subprocess.run(("git",) + args, cwd=REPO, capture_output=True, text=True)
    return out.returncode, out.stdout


def main() -> int:
    dangling: list[str] = []
    with open("/tmp/dangling.txt") as handle:
        for line in handle:
            parts = line.split()
            if len(parts) >= 3:
                dangling.append(parts[2])
    print("dangling commits: %d" % len(dangling), file=sys.stderr, flush=True)

    ancestors = 0
    duplicates = 0
    orphans: list[dict] = []
    unclassifiable: list[dict] = []

    for n, sha in enumerate(dangling, 1):
        if n % 50 == 0:
            print("  %d/%d" % (n, len(dangling)), file=sys.stderr, flush=True)

        code, _ = git("merge-base", "--is-ancestor", sha, UPSTREAM)
        if code == 0:
            ancestors += 1
            continue

        code, _ = git("rev-parse", "--verify", sha + "^")
        if code != 0:
            # A root commit, or a parent that no longer exists. Either way the
            # limited cherry below cannot be formed; report rather than skip.
            unclassifiable.append({"sha": sha, "why": "no parent"})
            continue

        code, out = git("cherry", UPSTREAM, sha, sha + "^")
        line = out.strip()
        if not line:
            # `git cherry` prints nothing for a merge commit. Not debris we can
            # dismiss, so it is carried through to the report.
            unclassifiable.append({"sha": sha, "why": "merge or empty diff"})
            continue
        if line.startswith("- "):
            duplicates += 1
            continue

        _, meta = git("log", "-1", "--format=%ci|%an|%s", sha)
        _, stat = git("show", "--stat", "--format=", sha)
        rows = [l for l in stat.splitlines() if l.strip()]
        orphans.append({
            "sha": sha,
            "meta": meta.strip(),
            "summary": rows[-1].strip() if rows else "",
        })

    out_dir = os.path.join(REPO, "reports", "zero-loss-reconciliation", "phase4")
    os.makedirs(out_dir, exist_ok=True)
    payload = {
        "upstream": UPSTREAM,
        "total": len(dangling),
        "ancestors_of_upstream": ancestors,
        "patch_id_duplicates": duplicates,
        "unclassifiable": unclassifiable,
        "orphans": orphans,
    }
    with open(os.path.join(out_dir, "dangling.json"), "w") as handle:
        json.dump(payload, handle, indent=2)

    with open(os.path.join(out_dir, "dangling.txt"), "w") as handle:
        handle.write("dangling commit triage against %s\n\n" % UPSTREAM)
        handle.write("total unreachable ...... %d\n" % len(dangling))
        handle.write("already ancestors ...... %d\n" % ancestors)
        handle.write("patch-id duplicates .... %d\n" % duplicates)
        handle.write("unclassifiable ......... %d\n" % len(unclassifiable))
        handle.write("ORPHANS (candidates) ... %d\n\n" % len(orphans))
        for o in orphans:
            handle.write("%s  %s\n    %s\n" % (o["sha"][:12], o["meta"], o["summary"]))
        if unclassifiable:
            handle.write("\nunclassifiable:\n")
            for u in unclassifiable:
                handle.write("  %s  %s\n" % (u["sha"][:12], u["why"]))

    print("\ntotal=%d ancestors=%d duplicates=%d unclassifiable=%d ORPHANS=%d"
          % (len(dangling), ancestors, duplicates, len(unclassifiable), len(orphans)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
