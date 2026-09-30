#!/usr/bin/env python3
"""Prove the composer's product-tagging controls are observed, by breaking each one.

The three client files this covers went green on their first run — 35 tests, no
iteration — and that is a symptom rather than a result. A suite that has never
been red has not yet demonstrated it can be. Every control below is a decision
whose *absence* produces a working app: the post still publishes, the picker
still renders, nothing throws. That is precisely the class of control a green
suite cannot vouch for.

The hazard this whole feature addresses has the same shape at every layer. A tag
that will serve and a tag that can never serve both answer ``ok: true``. A failed
catalogue read and an empty store both produce ``[]``. A composer that forwards
the creator's choice and one that silently drops it both publish successfully. So
for each control, this script edits it out of the real source, runs only the
suites that are supposed to be watching, and asserts they go red.

Three notes on method, each earned:

* **Suites are named per mutation, never as a directory.** ``TaggableProductPicker
  .test.tsx`` mocks the API client, so a mutation in ``taggableProducts.ts``
  cannot be killed there no matter how wrong it is. Listing suites narrowly is
  what makes "nothing observes this" distinguishable from "a different file took
  the credit".

* **Anchors are asserted unique before the edit.** An anchor that matches zero
  times means the control moved and this entry is now claiming something about
  code that no longer exists; an anchor that matches twice means the edit is not
  the edit described. Both are reported as failures rather than as kills, because
  a harness that silently mutates nothing reports a perfect score.

* **The mutant must parse.** A syntax error fails every suite and looks exactly
  like a kill, so each replacement below is written to leave valid TypeScript.
  ``--verbose`` prints the failing test names, which is how you tell a real kill
  from a broken mutant.

One entry here (``notice-is-only-a-note``) is not hypothetical. It was the
original implementation: the composer called ``setNote`` when a mode switch
dropped the creator's tags, and the panel that renders notes only mounts when
there is an error, a recovered draft, a failed publish or queued media. In the
common case the message was written to a string nobody rendered. The test found
it, which is the only reason that entry can be listed as killed rather than as a
bug still in the file.

What this does not claim: killing a mutant proves the control is *observed*, not
that it is *correct*. A test pinning the wrong behaviour still goes red when the
wrong behaviour is removed.

Usage:  python3 scripts/protection/composer_product_picker_mutation_matrix.py [--verbose]
Exit 0 only when every mutation is killed.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP = ROOT / "mobile-native"

CLIENT = "src/api/taggableProducts.ts"
PICKER = "src/commerce/TaggableProductPicker.tsx"
COMPOSER = "src/components/HomePulseComposer.tsx"

API_SUITE = "src/api/__tests__/taggableProducts.test.ts"
UI_SUITE = "src/commerce/__tests__/TaggableProductPicker.test.tsx"
COMPOSER_SUITE = "src/components/__tests__/HomePulseComposer.productTags.test.tsx"

MUTATIONS = [
    dict(
        name="failure-becomes-an-empty-store",
        control=(
            "The read lets a failure throw. Its sibling commerceDiscovery.ts "
            "promises the opposite and is right to, because [] there means 'no "
            "products here'. Here it means 'you have no products to tag' — a "
            "claim about the seller's own store, and a lie when the query failed."
        ),
        path=CLIENT,
        old="  const data = await pulseApi<TaggableProductsResponse>(`${API_PREFIX}/taggable-products${query}`);\n",
        new=(
            "  let data: TaggableProductsResponse;\n"
            "  try {\n"
            "    data = await pulseApi<TaggableProductsResponse>(`${API_PREFIX}/taggable-products${query}`);\n"
            "  } catch {\n"
            "    data = { products: [] };\n"
            "  }\n"
        ),
        suites=[API_SUITE],
    ),
    dict(
        name="serves-trusted-alongside-the-reason",
        control=(
            "`serves` is derived from the reason rather than read beside it. Two "
            "sources of truth for one fact fail asymmetrically: trusting "
            "`serves: true` over a present reason shows a product as fine while "
            "printing why it is broken underneath it."
        ),
        path=CLIENT,
        old='    serves: reason === "",\n',
        new="    serves: raw.serves !== false,\n",
        suites=[API_SUITE],
    ),
    dict(
        name="unknown-code-rendered-raw",
        control=(
            "An unrecognised code becomes `unknown_reason`, never the wire "
            "string. The label is half an i18n key, so a code this build has "
            "never heard of would render as a bare server identifier under a "
            "product card in every locale."
        ),
        path=CLIENT,
        old=(
            "  return BLOCKED_REASONS.includes(code as TaggableBlockedReason)\n"
            "    ? (code as TaggableBlockedReason)\n"
            '    : "unknown_reason";\n'
        ),
        new="  return code as TaggableBlockedReason;\n",
        suites=[API_SUITE],
    ),
    dict(
        name="cap-read-from-a-client-literal",
        control=(
            "`max_per_content` is read from the response. A client-side copy of "
            "tagging.MAX_TAGGED_PER_CONTENT diverges the moment the server's "
            "value changes, and it presents as a seller promised five tags when "
            "three will be stored."
        ),
        path=CLIENT,
        old="    maxPerContent: Number(data.max_per_content) || FALLBACK_MAX_PER_CONTENT,\n",
        new="    maxPerContent: FALLBACK_MAX_PER_CONTENT,\n",
        suites=[API_SUITE],
    ),
    dict(
        name="error-renders-as-an-empty-store",
        control=(
            "The picker keeps 'we could not read your store' and 'you have no "
            "products' in separate branches. Collapsing them is the single "
            "failure the 500 response, the throwing client and this component's "
            "three-state machine all exist to prevent."
        ),
        path=PICKER,
        old='      setResult(null);\n      setState("error");\n',
        new='      setResult({ products: [], maxPerContent: 0, requestLimit: 0 });\n      setState("ready");\n',
        suites=[UI_SUITE],
    ),
    dict(
        name="unservable-listings-filtered-out",
        control=(
            "Every owned listing is rendered, including the ones that cannot "
            "serve. Filtering them is the intuitive design and the bug: a product "
            "missing from your own picker teaches you nothing, while one labelled "
            "'Needs a cover photo' tells you what to fix."
        ),
        path=PICKER,
        old="            {result.products.map((row) => (\n",
        new="            {result.products.filter((row) => row.serves).map((row) => (\n",
        suites=[UI_SUITE],
    ),
    dict(
        name="blocked-listing-made-unselectable",
        control=(
            "A blocked listing stays selectable. eligibility.gate() runs per serve "
            "request on the tagged listing, so the tag is never frozen at tag "
            "time — it starts serving once the listing is fixed. Disabling the row "
            "discards a true statement the seller is entitled to make."
        ),
        path=PICKER,
        old="                selectable={selected.has(row.listingId) || !atCap}\n",
        new="                selectable={row.serves && (selected.has(row.listingId) || !atCap)}\n",
        suites=[UI_SUITE],
    ),
    dict(
        name="picker-hardcodes-the-cap",
        control=(
            "The picker counts against the cap the server sent. The same "
            "divergence as above, one layer up, and this is the layer where the "
            "seller sees the number."
        ),
        path=PICKER,
        old="  const maxPerContent = result?.maxPerContent ?? 0;\n",
        new="  const maxPerContent = result ? 5 : 0;\n",
        suites=[UI_SUITE],
    ),
    dict(
        name="cap-also-blocks-deselection",
        control=(
            "At the cap an unselected row stops being selectable, but a selected "
            "one never does. Blocking both makes the seller's last choice "
            "permanent until they discard the draft, which is a trap rather than "
            "a limit."
        ),
        path=PICKER,
        old=(
            "      if (next.has(listingId)) next.delete(listingId);\n"
            "      else if (!atCap) next.add(listingId);\n"
            "      else return;\n"
        ),
        new=(
            "      if (atCap) return;\n"
            "      if (next.has(listingId)) next.delete(listingId);\n"
            "      else next.add(listingId);\n"
        ),
        suites=[UI_SUITE],
    ),
    dict(
        name="blocked-reason-omitted",
        control=(
            "The reason is rendered, not just the verdict. 'Won't be shown' on its "
            "own is a dead end; the reason is the only string on the row that says "
            "how to clear it."
        ),
        path=PICKER,
        old=(
            "            <Text style={styles.blockedReason}>\n"
            '              {t(`commerce:discovery.tagging.blocked.${row.blockedReason || "unknown_reason"}`)}\n'
            "            </Text>\n"
        ),
        new="",
        suites=[UI_SUITE],
    ),
    dict(
        name="composer-drops-the-creators-choice",
        control=(
            "The publish payload carries the selected ids. createPost builds its "
            "body from a whitelist, so this link failing is invisible on both "
            "sides: the post publishes, the creator sees success, and nothing "
            "logs. That bug was really in this file."
        ),
        path=COMPOSER,
        old="        product_listing_ids: productListingIds\n",
        new="        product_listing_ids: []\n",
        suites=[COMPOSER_SUITE],
    ),
    dict(
        name="mode-switch-drops-tags-silently",
        control=(
            "Switching to a mode that cannot carry tags says so. Dropping them at "
            "publish instead would publish a reel the creator believes is tagged "
            "and never tell them — the same silent success as above."
        ),
        path=COMPOSER,
        old="      setNote(t(PRODUCT_TAGS_CLEARED_KEY));\n      setProductTagsCleared(true);\n",
        new="",
        suites=[COMPOSER_SUITE],
    ),
    dict(
        name="notice-is-only-a-note",
        control=(
            "The removal notice stands on its own element. This was the original "
            "implementation's real bug: the status panel that renders notes mounts "
            "only when there is an error, a recovered draft, a failed publish or "
            "queued media, so setNote alone wrote to a string nobody rendered."
        ),
        path=COMPOSER,
        old=(
            "      {productTagsCleared ? (\n"
            '        <View testID="home-composer-product-tags-cleared" accessibilityLiveRegion="polite" style={styles.productNotice}>\n'
            "          <Text style={styles.productNoticeText}>{t(PRODUCT_TAGS_CLEARED_KEY)}</Text>\n"
            "        </View>\n"
            "      ) : null}\n"
        ),
        new="",
        suites=[COMPOSER_SUITE],
    ),
    dict(
        name="notice-key-points-at-nothing",
        control=(
            "The removal notice resolves through the catalogue. A key that does not "
            "resolve is not an error here -- the engine humanises the last segment, "
            "so a mistyped namespace renders the plausible-looking 'Cleared' while "
            "validate-i18n still reports 11 locales at 100%, because it compares "
            "catalogues against en and never asks what the app looks up. Only the "
            "text assertion in the suite can tell those two apart, which is why "
            "this mutation exists: it changes nothing a human reviewer would notice "
            "and nothing either i18n gate inspects."
        ),
        path=COMPOSER,
        old='const PRODUCT_TAGS_CLEARED_KEY = "commerce:discovery.tagging.cleared";\n',
        new='const PRODUCT_TAGS_CLEARED_KEY = "commerce:discovery.tag.cleared";\n',
        suites=[COMPOSER_SUITE],
    ),
    dict(
        name="every-mode-claims-to-carry-tags",
        control=(
            "Only the feed-post path sends tags. Reels and statuses are different "
            "server contracts and createReel mirrors its own post onto the feed "
            "separately, so offering the picker there produces a tag that exists "
            "twice and is revoked once."
        ),
        path=COMPOSER,
        old='  return mode === "post" || mode === "poll";\n',
        new="  return true;\n",
        suites=[COMPOSER_SUITE],
    ),
    dict(
        name="recovered-draft-loses-its-tags",
        control=(
            "A recovered draft restores its tags, as it already did its music "
            "track. Without this the creator reopens the draft, sees their text, "
            "and publishes a post they believe is tagged."
        ),
        path=COMPOSER,
        old="        setProductListingIds(draft.productListingIds || []);\n",
        new="",
        suites=[COMPOSER_SUITE],
    ),
    dict(
        name="draft-ids-trusted-off-disk",
        control=(
            "Ids read back from storage are re-validated and deduped. This is JSON "
            "from disk: a NaN reaching the payload serialises to null and is "
            "refused server-side for the wrong reason."
        ),
        path=COMPOSER,
        old=(
            "    productListingIds: Array.from(\n"
            "      new Set((Array.isArray(raw.productListingIds) ? raw.productListingIds : []).map(Number).filter((id) => Number.isFinite(id) && id > 0))\n"
            "    ),\n"
        ),
        new="    productListingIds: (raw.productListingIds || []) as number[],\n",
        suites=[COMPOSER_SUITE],
    ),
]


def run_suites(suites, verbose: bool):
    """``(green, suite, tail)``. One jest process per file.

    Per-file for the same reason the Python matrices do it: a batched run's
    failures can belong to the batching, and here it also keeps the attribution
    honest when a mutation is only visible to one of the three suites.
    """
    for suite in suites:
        proc = subprocess.run(
            ["npx", "jest", suite, "--ci", "--silent"],
            cwd=APP, capture_output=True, text=True,
        )
        # Jest reports to stderr.
        output = f"{proc.stdout}\n{proc.stderr}"
        lines = [line for line in output.splitlines() if line.startswith("Tests:")]
        tail = lines[-1] if lines else f"(no summary, exit {proc.returncode})"
        if verbose:
            print(f"      {suite}: exit {proc.returncode} | {tail}")
        if proc.returncode != 0:
            if verbose:
                for line in output.splitlines():
                    if "✕" in line:
                        print(f"        {line.strip()}")
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
        path = APP / entry["path"]
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
        try:
            still_green, _, _ = run_suites(entry["suites"], args.verbose)
        finally:
            path.write_text(original)

        if still_green:
            print(f"  SURVIVED {entry['name']}")
            print(f"           {entry['control']}")
            print("           Breaking it changed no test result. Nothing observes this.")
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
