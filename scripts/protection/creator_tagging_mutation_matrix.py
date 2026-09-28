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

BOT = "bot.py"
TAGGING = "services/commerce_discovery/tagging.py"
ENGINE = "services/commerce_discovery/engine.py"
RELATIONSHIP = "services/commerce_discovery/relationship.py"
ROUTES = "services/commerce_discovery_routes.py"

TAGS = "tests/commerce_discovery/test_a_creator_can_tag_their_own_products.py"
GATE = "tests/commerce_discovery/test_suitability_gate_is_wired.py"
PICKER = "tests/commerce_discovery/test_the_composer_is_told_what_will_actually_serve.py"

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
    dict(
        name="serve-route-resolves-tags-early",
        control=(
            "The serve route does not resolve a tag itself; the engine does, below "
            "the suitability refusal. Written with an *alias* deliberately, because "
            "the structural half of this control is a string check that an alias "
            "defeats — what has to kill this is the behavioural test watching "
            "`tagging.tagged_listing_ids` for calls on a refused post."
        ),
        path=ROUTES,
        old="            content_post_id = _content_post_id(payload)\n",
        new=("            content_post_id = _content_post_id(payload)\n"
             "            from services.commerce_discovery import tagging as _early\n"
             "            _early.tagged_listing_ids(\n"
             "                cur, content_type=\"post\", content_id=content_post_id,\n"
             "            )\n"),
        suites=[TAGS],
    ),
    # --- the composer's picker ------------------------------------------------
    # `/taggable-products` is the only place in the system where "you may tag this"
    # and "tagging this will show something" are separate answers. Every mutation
    # below collapses them back into one, which is the state the feature was in
    # before the endpoint existed and is invisible from any single response.
    dict(
        name="picker-filters-instead-of-explaining",
        control=(
            "An ineligible listing is returned with its reason, not filtered out. "
            "Filtering is the obvious implementation and it recreates the silence: "
            "the creator's product vanishes from the picker and nothing tells them "
            "that adding a cover photo is the whole fix."
        ),
        path=ROUTES,
        old="            blocked = eligibility.gate(row, bot.parse_price_label_to_cents)\n",
        new=("            blocked = eligibility.gate(row, bot.parse_price_label_to_cents)\n"
             "            if blocked:\n                continue\n"),
        suites=[PICKER],
    ),
    dict(
        name="picker-shows-every-sellers-catalogue",
        control=(
            "The query is filtered to the signed-in seller. `tagging.attach` "
            "re-checks ownership per row, so a wider picker writes no bad tag — but "
            "it discloses another seller's catalogue, which the write path's refusal "
            "does nothing about."
        ),
        path=ROUTES,
        old='        "WHERE COALESCE(l.seller_user_id,0)=? "',
        # Still one parameter, so the query runs; it has simply stopped being about
        # ownership. A mutation that broke the parameter count would be killed by
        # the driver rather than by a test.
        new='        "WHERE COALESCE(l.status,\'\')<>? "',
        suites=[PICKER],
    ),
    dict(
        name="picker-reports-an-error-as-an-empty-store",
        control=(
            "A read failure is a 500 with a code, not `200 {\"products\": []}`. "
            "Everywhere else in this package an empty list is a truthful rendering "
            "of nothing-to-show; here it is a claim about the creator's own "
            "inventory, and a dropped connection must not be able to make it."
        ),
        path=ROUTES,
        old=('        return _error(\n'
             '            "We could not load your products. Please try again.",\n'
             '            500, code="TAGGABLE_PRODUCTS_UNAVAILABLE",\n'
             '        )'),
        new='        return _json({"ok": True, "products": []})',
        suites=[PICKER],
    ),
    dict(
        name="picker-serves-the-pipeline-columns",
        control=(
            "`engine.buyer_safe` is applied even though the reader is the seller. "
            "`seller_risk_score` is an internal assessment *of that seller*, and "
            "being its subject is not an entitlement to it."
        ),
        path=ROUTES,
        old="bot.pulse_marketplace_listing_payload(engine.buyer_safe(row))",
        new="bot.pulse_marketplace_listing_payload(dict(row))",
        suites=[PICKER],
    ),
    dict(
        name="picker-hardcodes-the-cap",
        control=(
            "`max_per_content` is read from `tagging.MAX_TAGGED_PER_CONTENT` on "
            "every request. A literal is a second copy that goes stale in the "
            "permissive direction: the picker offers six, the sixth is refused "
            "after the post is already published."
        ),
        path=ROUTES,
        # The mutation that survived every other test in the picker suite, because
        # both sides of `== tagging.MAX_TAGGED_PER_CONTENT` were the same number
        # today. `test_the_cap_tracks_the_constant_rather_than_equalling_it_today`
        # exists because of this entry, not the other way round.
        old='            "max_per_content": tagging.MAX_TAGGED_PER_CONTENT,',
        new='            "max_per_content": 5,',
        suites=[PICKER],
    ),
    dict(
        name="picker-offers-a-card-it-could-not-render",
        control=(
            "A row the serializer raised on is dropped, not emitted with an empty "
            "product. The picker would otherwise draw a tappable card with no title "
            "and no price, and a creator selecting it would tag a product they "
            "could not identify."
        ),
        path=ROUTES,
        old="                # selectable blank is worse than an absence.\n                continue",
        new="                # selectable blank is worse than an absence.\n                card = {}",
        suites=[PICKER],
    ),
    dict(
        name="picker-lets-the-client-choose-the-page-size",
        control=(
            "`limit` is clamped to TAGGABLE_PAGE_MAX. Unclamped, one request can "
            "ask for a seller's entire catalogue and be serialized row by row."
        ),
        path=ROUTES,
        old="        limit = max(1, min(TAGGABLE_PAGE_MAX, int(request.args.get(\"limit\") or TAGGABLE_PAGE_MAX)))",
        new="        limit = max(1, int(request.args.get(\"limit\") or TAGGABLE_PAGE_MAX))",
        suites=[PICKER],
    ),
    # The composer's own request, in `bot.py`. These three were added after a
    # `product_listing_ids` key reached `createPost` on the native client and the
    # multipart branch of the same route was found to drop it — the defect these
    # mutations restore, not a hypothetical one.
    dict(
        name="form-post-payload-omits-the-tag-ids",
        control=(
            "The multipart branch of POST /api/pulse/posts rebuilds `payload` as a "
            "dict literal, so a key it does not name never reaches "
            "`pulse_product_tag_ids_from_payload`. Omitting the tag ids discards "
            "every product a form-based composer tags, with no error on either side."
        ),
        path=BOT,
        old='                "product_listing_ids": product_listing_ids,\n',
        new="",
        suites=[TAGS],
    ),
    dict(
        name="form-and-helper-key-names-drift",
        control=(
            "The helper's accepted key names and the form branch's read names are "
            "the same claim written twice. A name added to one and not the other is "
            "accepted on the JSON path and silently dropped on the form path."
        ),
        path=BOT,
        # Adds a fourth alias to the helper only. Anchored on the preceding line
        # because the helper's own `for` line is a substring of the form branch's.
        old='    payload = payload or {}\n    for key in ("product_listing_ids", "listing_ids", "product_ids"):',
        new='    payload = payload or {}\n    for key in ("product_listing_ids", "listing_ids", "product_ids", "tagged_listing_ids"):',
        suites=[TAGS],
    ),
    dict(
        name="form-post-keeps-only-the-first-ticked-product",
        control=(
            "Tag ids are read with `form.getlist`. A checkbox picker submits one "
            "field name repeatedly and `form.get` returns only its first value, so "
            "this is a partial silent loss — harder to notice than a total one."
        ),
        path=BOT,
        old="                values = [value for value in form.getlist(key) if str(value).strip()]",
        new="                values = [value for value in [form.get(key)] if str(value).strip()]",
        suites=[TAGS],
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
