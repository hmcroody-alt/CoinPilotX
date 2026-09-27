#!/usr/bin/env python3
"""Prove the creator-tagging controls are observed, by deleting each one.

A creator tag is the only edge in the commerce discovery system that is a
*statement* rather than an inference: the person who made the post says this is
the product in it. That makes it the most useful signal available and the one
with the least excuse for being wrong, which is why `services/commerce_discovery/
tagging.py` is mostly refusals — and why "the tests pass" is not the claim worth
making about them. The claim worth making is that the tests would go red if the
refusal were deleted.

This script does that: for each control, it edits the control out of the real
source, runs the suites that are supposed to be watching, and asserts they fail.
A mutation that survives is a control nothing observes, and it fails this
script's exit code.

Two traps this script exists to avoid, both of which caught the ad-hoc version:

* **Suites are listed narrowly, never as a directory.** The two mutations that
  originally looked like survivors (`route-resolves-post-id-everywhere`,
  `suitability-refusal-never-called`) were run against the tagging suite alone.
  The second turned out to be killed by 41 tests in
  `test_suitability_gate_is_wired.py` — so the "survivor" was a gap in one file,
  not in the suite. The first survived the entire 826-test package and was a real
  hole. Naming suites per mutation is what makes that difference visible instead
  of letting an unrelated file take the credit.

* **`__pycache__` is cleared around every mutation.** A `.pyc` records its
  source's mtime at *one-second* granularity, so rewriting one file several times
  inside a second can serve a stale cache and attribute a result to the wrong
  mutation. That happened, and it cost an hour of chasing a phantom.

What this does not claim: killing a mutant proves the control is *observed*, not
that it is *correct*. A test pinning the wrong behaviour still goes red when the
wrong behaviour is removed.

Usage:  python3 scripts/protection/creator_tagging_mutation_matrix.py [--verbose]
Exit 0 only when every mutation is killed.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

TAGGING = "services/commerce_discovery/tagging.py"
ENGINE = "services/commerce_discovery/engine.py"
RELATIONSHIP = "services/commerce_discovery/relationship.py"
ROUTES = "services/commerce_discovery_routes.py"

TAGS = "tests/commerce_discovery/test_a_creator_can_tag_their_own_products.py"
GATE = "tests/commerce_discovery/test_suitability_gate_is_wired.py"

MUTATIONS = [
    dict(
        name="stale-authorisation-served",
        control=(
            "A tag is dropped once the listing changes hands. `seller_user_id` is "
            "the seller the tag was authorised against; when the live listing "
            "disagrees, nobody living granted this permission."
        ),
        path=TAGGING,
        old='            "AND l.seller_user_id = p.seller_user_id "\n',
        new="",
        suites=[TAGS],
    ),
    dict(
        name="reader-trusts-the-write-cap",
        control=(
            "The read path caps at MAX_TAGGED_PER_CONTENT independently of the "
            "write path. A reader that trusts a write-time invariant is trusting "
            "every past version of the writer, including one with a different cap."
        ),
        path=TAGGING,
        old='            "ORDER BY p.id ASC LIMIT ?",\n            (kind, content_ref, MAX_TAGGED_PER_CONTENT),',
        new='            "ORDER BY p.id ASC",\n            (kind, content_ref),',
        suites=[TAGS],
    ),
    dict(
        name="anybody-may-tag-anybodys-listing",
        control=(
            "A creator may only tag a listing they own. Tagging someone else's "
            "product is affiliate marketing: it needs a commission model and a "
            "disclosure obligation, neither of which is an engineering decision."
        ),
        path=TAGGING,
        old="    if not seller_ref or seller_ref != owner_ref:",
        new="    if not seller_ref and False:",
        suites=[TAGS],
    ),
    dict(
        name="write-cap-removed",
        control="One piece of content may carry at most MAX_TAGGED_PER_CONTENT products.",
        path=TAGGING,
        old="    if existing >= MAX_TAGGED_PER_CONTENT:",
        new="    if existing >= MAX_TAGGED_PER_CONTENT and False:",
        suites=[TAGS],
    ),
    dict(
        name="lossy-id-truncated-not-refused",
        control=(
            "A non-integral reference is refused, not truncated. 2.5 is not post 2; "
            "a tag filed against a truncated id points at a stranger's content and "
            "shows no symptom, because content_id spaces overlap across these tables."
        ),
        path=TAGGING,
        old="        if value != int(value):\n            return 0\n",
        new="",
        suites=[TAGS],
    ),
    dict(
        name="authority-not-recorded",
        control=(
            "AUTHORITY_OWNER is stamped on every row, so the day a second authority "
            "exists the rows written under this one are still distinguishable."
        ),
        path=TAGGING,
        old="owner_ref, seller_ref, AUTHORITY_OWNER, subject.now_iso()),",
        new='owner_ref, seller_ref, "affiliate", subject.now_iso()),',
        suites=[TAGS],
    ),
    dict(
        name="tagged-no-longer-outranks-contextual",
        control=(
            "A row retrieved by a tag is labelled CREATOR_TAGGED even when it also "
            "matched the content. For \"how many cards matched nothing about the "
            "content?\" to have a true answer, the more specific fact must win."
        ),
        path=RELATIONSHIP,
        old='    if str(candidate_source or "").strip().lower() == SOURCE_TAGGED:\n        return CREATOR_TAGGED\n',
        new="",
        suites=[TAGS],
    ),
    dict(
        name="select-no-longer-prefers-the-tags",
        control=(
            "Creator tags are a precedence tier in `_select`, taken ahead of the "
            "relevance floor and the diversity caps. Without it a tagged product on "
            "a one-slot surface loses to whatever the scorer liked better, and the "
            "creator's statement is retrieved and then discarded."
        ),
        path=ENGINE,
        old="    for pair in tagged_pairs:\n        if len(chosen) >= budget:\n            break",
        new="    for pair in tagged_pairs:\n        if True:\n            break",
        suites=[TAGS],
    ),
    dict(
        name="tagged-lookup-behind-the-personalisation-gate",
        control=(
            "The tagged lookup sits outside the `policy.personalized` gate. A "
            "creator tag says nothing about the viewer — it is identical for "
            "everyone reading the post — so the personalisation opt-out must not "
            "suppress it."
        ),
        path=ENGINE,
        old="    tagged_listing_ids = tagging.tagged_listing_ids(\n        cur, content_type=\"post\", content_id=content_post_id,\n    )",
        new="    tagged_listing_ids = tagging.tagged_listing_ids(\n        cur, content_type=\"post\", content_id=content_post_id,\n    ) if policy.personalized else ()",
        suites=[TAGS],
    ),
    dict(
        name="route-resolves-post-id-everywhere",
        control=(
            "Only the three surfaces with a post on screen supply a content post "
            "id. Messenger and Marketplace have no post, so honouring a post_id in "
            "their body would put a creator's product in a private conversation "
            "under a claim about content that is not there."
        ),
        path=ROUTES,
        old='        content_post_id = 0\n        if surface == "product_detail":',
        new='        content_post_id = _content_post_id(payload)\n        if surface == "product_detail":',
        # This is the mutation that survived the whole package until the tagging
        # suite grew a behavioural test for it. Both suites are named because the
        # gate suite is the one a reader would expect to cover it, and it does not.
        suites=[TAGS, GATE],
    ),
    dict(
        name="suitability-refusal-never-called",
        control=(
            "The suitability refusal returns before retrieval. A creator tag must "
            "not be able to force commerce onto a bereavement, medical or distress "
            "post; retrieval simply never runs on a refused request."
        ),
        path=ROUTES,
        old="        verdict = _content_refusal(cur, payload, context, surface)",
        new="        verdict = None if engine.serve else _content_refusal(cur, payload, context, surface)",
        suites=[TAGS, GATE],
    ),
    dict(
        name="absent-post-permitted-instead-of-refused",
        control=(
            "A post the server cannot see is refused, not treated as no evidence. "
            "Deletion in PulseSoc is soft and there is no cascade — for this table "
            "or for pulse_content_music — so this refusal is the only thing that "
            "stops a deleted post serving the products its creator tagged on it."
        ),
        path=ROUTES,
        old="    outcome, row = _content_post(cur, post_id)\n    if outcome == _ROW_UNREADABLE:\n        return None",
        new="    outcome, row = _content_post(cur, post_id)\n    if outcome in (_ROW_UNREADABLE, _ROW_ABSENT):\n        return None",
        suites=[TAGS, GATE],
    ),
]


def clear_bytecode() -> None:
    """Remove every `__pycache__` under the repo.

    Not hygiene. `.pyc` staleness is decided by the source's mtime at one-second
    resolution, and this script rewrites the same file twice in well under a
    second, so without this a mutation can be run against the *previous*
    mutation's bytecode.
    """
    for directory in ROOT.rglob("__pycache__"):
        shutil.rmtree(directory, ignore_errors=True)


def run_suites(suites, verbose: bool):
    """``(green, suite, tail)``. One pytest process per file.

    Per-file, because test modules in this repo set DB state at import time and
    batching them produces failures that belong to the batching.
    """
    for suite in suites:
        clear_bytecode()
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", suite, "-q", "-p", "no:randomly",
             "--no-header", "--tb=no"],
            cwd=ROOT, capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        tail = lines[-1] if lines else f"(no output, exit {proc.returncode})"
        if verbose:
            print(f"      {suite}: exit {proc.returncode} | {tail}")
        if proc.returncode != 0:
            if verbose:
                for line in proc.stdout.splitlines():
                    if line.startswith("FAILED"):
                        print(f"        {line.split('::', 1)[-1]}")
            return False, suite, tail
    return True, None, ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    print("Baseline: every named suite must be green before a mutation means anything.\n")
    baseline = sorted({suite for entry in MUTATIONS for suite in entry["suites"]})
    green, suite, tail = run_suites(baseline, args.verbose)
    if not green:
        print(f"ABORT: {suite} is already red ({tail}).\n"
              f"       Fix that first — a red baseline makes every mutation look killed.")
        return 2
    print(f"  {len(baseline)} suites green.\n")

    survivors: list[str] = []
    for entry in MUTATIONS:
        path = ROOT / entry["path"]
        original = path.read_text()
        occurrences = original.count(entry["old"])
        if occurrences != 1:
            label = "DRIFTED " if occurrences == 0 else "AMBIGUOUS"
            print(f"  {label} {entry['name']}")
            print(f"           anchor appears {occurrences} times in {entry['path']}.")
            print("           The control moved or was rewritten; re-point this entry "
                  "before it can claim anything.")
            survivors.append(entry["name"])
            continue

        path.write_text(original.replace(entry["old"], entry["new"]))
        if path.read_text() == original:  # pragma: no cover - defensive
            print(f"  ABORT: {entry['name']} left the file unchanged.")
            survivors.append(entry["name"])
            continue
        clear_bytecode()
        try:
            still_green, _, _ = run_suites(entry["suites"], args.verbose)
        finally:
            path.write_text(original)
            clear_bytecode()

        if still_green:
            print(f"  SURVIVED {entry['name']}")
            print(f"           {entry['control']}")
            print("           Deleting it changed no test result. Nothing observes this.")
            survivors.append(entry["name"])
        else:
            print(f"  killed   {entry['name']}")

    print()
    if survivors:
        print(f"FAIL: {len(survivors)} of {len(MUTATIONS)} mutations survived: "
              f"{', '.join(survivors)}")
        return 1
    print(f"PASS: all {len(MUTATIONS)} mutations killed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
