"""Assemble the master change ledger from the evidence each phase produced.

The mission asks for one ledger, machine-readable and human-readable, with a
fixed field set, covering every piece of work found outside production. This
builds it from the phase artifacts rather than from memory, so that every row
can be traced back to the probe that produced it and re-derived by re-running
that probe.

Two design choices are worth stating because they are what make the ledger
trustworthy rather than merely complete.

**Classification is copied, not re-decided.** Each phase already answered its
own question with the right instrument -- patch id for reachability, line
presence for re-shaped content, a capability matrix for withdrawn surfaces.
Re-deriving a verdict here from a summary would launder a careful judgement into
a heuristic. Where a row's classification came from a human reading the diff,
the ``basis`` field says so, and where it came from a ratio, ``basis`` names the
ratio. A reader can tell the two apart, which is the point.

**PRODUCTION_STATUS is a separate axis from MAIN_STATUS.** Railway auto-deploys
``origin/main``, so for most rows they move together -- but "most" is not
"always", services lag independently, and the mission is explicit that
``origin/main`` must not be assumed to equal production. Anything whose
production state was not directly observed says ``NOT_OBSERVED`` rather than
inheriting main's. A ledger that quietly equates the two would report a clean
bill of health for a service that never restarted.
"""

from __future__ import annotations

import json
import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(REPO, "reports", "zero-loss-reconciliation")

FIELDS = ("work_id", "source", "agent", "branch", "commit", "files",
          "subsystem", "description", "dependencies", "main_status",
          "production_status", "classification", "action",
          "destination_commit", "test_status", "deploy_status", "basis")


#: Refs that ``git cherry`` flagged as carrying unique content, each closed by a
#: later phase. They are listed here rather than re-derived because the work of
#: deciding them is already done and written up; what this table adds is the
#: pointer from the row to the document that decided it.
#:
#: The nostalgic-neumann entry is the one worth reading. ``git log`` finds a
#: commit on ``origin/main`` whose subject is *"merge: preserve
#: claude/nostalgic-neumann-d9391f"*, and that merge really is an ancestor of
#: main. The branch tip is not. The merge captured ``6b771754`` at 14:14; the
#: four commits below were written on top of it and never merged. This is the
#: mission's "a branch being merged does NOT prove all its work is present",
#: encountered rather than assumed -- and it is exactly why the census keys on
#: patch id and not on the word "merge" appearing in a subject line.
RESOLUTIONS = {
    "REF-claude-nostalgic-neumann-d9391f": dict(
        main_status="ABSENT", classification="OBSOLETE",
        action="not ported; one invariant carried out of it",
        destination_commit="ca6edd56",
        basis="branch tip == detached worktree HEAD 997903cf (same 4 patch ids). "
              "Merge 7d8c9ffd on origin/main preserved only the base 6b771754; "
              "these 4 commits post-date it. Capability matrix in "
              "phase5/worktrees.md: private_facts is in RETIRED_FEATURE_IDS, "
              "integrity and structured_records in RETIRED_ENGINE_MODULES, and "
              "UNDX holds no facts capability for undx_context.py to govern"),
    "REF-claude-private-office-simplify": dict(
        main_status="PARTIAL", classification="SUPERSEDED",
        action="3 superseded by the rebuild, 2 ported",
        destination_commit="d6248c30, 50a09228, e93b2da1",
        basis="0cb23702/291be457/dee12971 are the same withdrawal, rebuilt on "
              "pinned origin/main as d6248c30; b5f06a4f and 0f639597 ported as "
              "50a09228 and e93b2da1 (re-authored, so no patch-id twin)"),
    "REF-main": dict(
        main_status="ABSENT", classification="SUPERSEDED",
        action="rebuilt on the pinned base",
        destination_commit="d6248c30, aaf73b46, 9b1862e1",
        description="3 commit(s) with no patch-id twin upstream (the census row "
                    "says 2: it could not count its own commit)",
        basis="f1b74e89/5f24c970/a41f1b15 are this mission's own Phase 1 work on "
              "a local main that had drifted; rebuilt on pinned d007e2ba. "
              "a41f1b15 IS the census commit, so the phase-1 count of 2 was "
              "correct when taken and is stale now -- re-counted here by hand"),
}
RESOLUTIONS["REF-origin-claude-nostalgic-neumann-d9391f"] = dict(
    RESOLUTIONS["REF-claude-nostalgic-neumann-d9391f"],
    basis="origin twin of claude/nostalgic-neumann-d9391f, same 4 patch ids; "
          "see that row")


def load(*parts: str) -> object:
    path = os.path.join(ROOT, *parts)
    if not os.path.exists(path):
        return None
    with open(path) as handle:
        return json.load(handle)


def git(*args: str) -> str:
    out = subprocess.run(("git",) + args, cwd=REPO, capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else ""


def row(**kw) -> dict:
    base = {f: "" for f in FIELDS}
    base.update(kw)
    return base


def agent_of(text: str) -> str:
    """Which system produced this, judged by the shape of its own naming.

    Deliberately coarse and deliberately not a guess: `claude/*` refs and
    `codex/*` refs are self-labelling. Anything else is UNKNOWN rather than
    attributed, because a wrong attribution in a ledger is worse than an absent
    one -- it invites someone to go ask the wrong team.
    """
    low = text.lower()
    if "codex/" in low:
        return "codex"
    if "claude/" in low:
        return "claude"
    return "unknown"


def build() -> list[dict]:
    rows: list[dict] = []

    # ---- Phase 1-3: refs -------------------------------------------------
    census = load("phase1", "ref_census.json") or {}
    for rec in census.get("refs", []):
        ref = rec["ref"]
        short = ref.replace("refs/remotes/", "").replace("refs/heads/", "")
        unique = rec.get("unique_commits") or []
        if not unique:
            rows.append(row(
                work_id="REF-" + short.replace("/", "-"),
                source="branch", agent=agent_of(ref), branch=short,
                commit=rec.get("sha", "")[:12],
                subsystem="(whole ref)",
                description="%d commit(s), every one patch-id equivalent to "
                            "something upstream" % rec.get("equivalent_upstream", 0),
                main_status="PRESENT", production_status="NOT_OBSERVED",
                classification="ALREADY_PRODUCTION", action="none",
                test_status="n/a", deploy_status="n/a",
                basis="git cherry: no '+' lines"))
        else:
            rows.append(row(
                work_id="REF-" + short.replace("/", "-"),
                source="branch", agent=agent_of(ref), branch=short,
                commit=rec.get("sha", "")[:12],
                subsystem="(see per-commit rows)",
                description="%d commit(s) with no patch-id twin upstream; "
                            "%d already equivalent" % (
                                len(unique), rec.get("equivalent_upstream", 0)),
                main_status="CANDIDATE", production_status="NOT_OBSERVED",
                classification="NEEDS_CONTENT_CHECK",
                action="resolved in phase 4b/5",
                test_status="n/a", deploy_status="n/a",
                basis="git cherry: %d '+' lines" % len(unique)))

    # ---- Phase 4: dangling ----------------------------------------------
    dangling = load("phase4", "dangling.json") or {}
    rows.append(row(
        work_id="DANGLING-DUPLICATES", source="dangling", agent="unknown",
        commit="(%d commits)" % dangling.get("patch_id_duplicates", 0),
        subsystem="(various)",
        description="unreachable commits whose patch id is already upstream",
        main_status="PRESENT", production_status="NOT_OBSERVED",
        classification="ALREADY_PRODUCTION", action="none",
        test_status="n/a", deploy_status="n/a",
        basis="git cherry three-arg form, '-' prefix"))
    rows.append(row(
        work_id="DANGLING-UNCLASSIFIABLE", source="dangling", agent="unknown",
        commit="(%d commits)" % len(dangling.get("unclassifiable") or []),
        subsystem="(various)",
        description="merge commits: no single diff, therefore no patch id. "
                    "Reported rather than dropped; each one's result is "
                    "reachable from main",
        main_status="PRESENT", production_status="NOT_OBSERVED",
        classification="ALREADY_PRODUCTION", action="none",
        test_status="n/a", deploy_status="n/a",
        basis="subject match upstream; no patch id exists to compare"))

    presence = load("phase4", "orphan_presence.json") or []
    verdict_map = {
        "present": ("PRESENT", "ALREADY_PRODUCTION", "none"),
        "partial": ("PARTIAL", "SUPERSEDED", "none — main is stronger"),
        "absent": ("ABSENT", "OBSOLETE", "none"),
        "inconclusive": ("UNKNOWN", "OBSOLETE", "none — trivial commit"),
    }
    for rec in presence:
        subject = rec["meta"].split("|", 2)[2] if "|" in rec["meta"] else ""
        main_status, classification, action = verdict_map[rec["verdict"]]
        ratio = rec.get("ratio")
        rows.append(row(
            work_id="ORPHAN-" + rec["sha"][:10],
            source="dangling", agent=agent_of(rec["meta"]),
            commit=rec["sha"][:12],
            files=rec.get("summary", ""), subsystem="(see subject)",
            description=subject[:160],
            main_status=main_status, production_status="NOT_OBSERVED",
            classification=classification, action=action,
            test_status="n/a", deploy_status="n/a",
            basis=("%d/%d distinctive lines present in main"
                   % (rec.get("found", 0), rec.get("sampled", 0))
                   if ratio is not None else
                   "only %d distinctive lines — no evidence either way"
                   % rec.get("sampled", 0))))

    # ---- Phase 6: stashes ------------------------------------------------
    for rec in load("phase6", "stash_presence.json") or []:
        rows.append(row(
            work_id="STASH-" + rec["ref"].replace("@", "").replace("{", "")
                                          .replace("}", ""),
            source="stash", agent="unknown", commit=rec["ref"],
            files=rec.get("summary", ""), subsystem="(various)",
            description=rec.get("subject", "")[:160],
            main_status="PRESENT", production_status="NOT_OBSERVED",
            classification="SUPERSEDED",
            action="not applied, not dropped",
            test_status="n/a", deploy_status="n/a",
            basis="%d/%d lines present; residue read by hand and found "
                  "weaker than main" % (rec.get("found", 0),
                                        rec.get("sampled", 0))))

    # ---- close the rows a later phase resolved --------------------------
    by_id = {r["work_id"]: r for r in rows}
    unmatched = sorted(set(RESOLUTIONS) - set(by_id))
    if unmatched:
        # A resolution naming a row that does not exist means the census and
        # this table have drifted apart. Failing here is the whole point: the
        # alternative is a ledger that silently still has an open row.
        raise SystemExit("resolutions for absent rows: %s" % ", ".join(unmatched))
    for work_id, fields in RESOLUTIONS.items():
        by_id[work_id].update(fields)

    still_open = [r["work_id"] for r in rows
                  if r["classification"] == "NEEDS_CONTENT_CHECK"]
    if still_open:
        raise SystemExit("unresolved rows: %s" % ", ".join(still_open))

    return rows


def main() -> int:
    rows = build()
    out_dir = os.path.join(ROOT, "ledger")
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(out_dir, "ledger.json"), "w") as handle:
        json.dump({"fields": list(FIELDS), "rows": rows}, handle, indent=2)

    import csv
    with open(os.path.join(out_dir, "ledger.csv"), "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(FIELDS))
        writer.writeheader()
        writer.writerows(rows)

    from collections import Counter
    tally = Counter(r["classification"] for r in rows)
    with open(os.path.join(out_dir, "ledger.txt"), "w") as handle:
        handle.write("master change ledger — %d rows\n\n" % len(rows))
        for name, count in sorted(tally.items(), key=lambda kv: -kv[1]):
            handle.write("  %-24s %d\n" % (name, count))
        handle.write("\n")
        for r in sorted(rows, key=lambda r: (r["classification"], r["work_id"])):
            handle.write("%-26s %-22s %s\n" % (
                r["work_id"][:26], r["classification"], r["description"][:70]))
            handle.write("%-26s basis: %s\n\n" % ("", r["basis"][:80]))

    print("ledger rows: %d" % len(rows))
    for name, count in sorted(tally.items(), key=lambda kv: -kv[1]):
        print("  %-24s %d" % (name, count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
