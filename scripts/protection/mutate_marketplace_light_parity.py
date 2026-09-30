#!/usr/bin/env python3
"""Prove every assertion in `tests/test_marketplace_light_parity.py` can fail.

Why this exists
===============
That suite is the only thing standing between the web storefront's palette and
a silent drift away from the native app's. It is 7 tests over a transcribed
`--store-*` block, and a transcription gate has a specific way of dying: it
keeps passing while checking less. Two of its own docstrings describe exactly
that happening to the older native-parity gates, whose literal-only regex drops
a key rather than failing on it.

A green suite is therefore not evidence. This harness is the evidence: it
breaks the stylesheet (and, for one case, the theme parser) in eight ways that
the suite is supposed to notice, and fails if any of them slips through.

Two rules it follows
====================
*Never writes repo source.* The stylesheet is copied into a temporary
directory, mutated there, and the test module's `CSS_PATH` is repointed at the
copy. There is no restore step to get wrong -- if this process is killed
mid-run, the checkout is untouched, which is not true of a write-mutate-restore
harness like `cart_variant_mutation_matrix.py`.

*A mutation that does not apply is not a mutation that passed.* Every CSS
mutation states the text it expects to find and how many times; a count of zero
or an unexpected count is reported DRIFTED and fails the run rather than
quietly testing nothing.

Coverage is default-deny: `TARGETED` below must name every `test_*` in the
parity module. Adding an eighth test without a mutation for it fails here.

Usage
=====
    python3 scripts/protection/mutate_marketplace_light_parity.py [-v]

Exit 0 when every mutation was caught by its intended test, 1 when one
survived or drifted, 2 when the unmutated baseline is not green (in which case
the harness can prove nothing and says so instead of guessing).
"""

import argparse
import importlib.util
import pathlib
import re
import sys
import tempfile
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[2]
PARITY = ROOT / "tests" / "test_marketplace_light_parity.py"
CSS = ROOT / "static" / "css" / "pulse_marketplace.css"


# --------------------------------------------------------------------------- #
# Loading and running the suite in-process
# --------------------------------------------------------------------------- #
# The parity tests are plain functions reading module-level path constants, so
# they can be called directly and pointed at a mutated copy. That is cheaper
# than a pytest subprocess per mutation and -- more importantly -- it means the
# mutation lives in a tempdir instead of in the file pytest would collect.

def fresh():
    """A newly executed copy of the parity module, with its own path constants."""
    spec = importlib.util.spec_from_file_location("_parity_under_test", PARITY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_names(module):
    return tuple(sorted(n for n in dir(module) if n.startswith("test_")))


def run_one(module, name):
    """None if the test passed, else a one-line reason."""
    try:
        getattr(module, name)()
        return None
    except AssertionError as exc:
        return (str(exc).replace("\n", " ")[:160] or "<assertion, empty message>")
    except Exception:
        return "NON-ASSERTION ERROR: " + traceback.format_exc(limit=1).replace("\n", " ")[:160]


def run_all(module):
    return {name: run_one(module, name) for name in test_names(module)}


# --------------------------------------------------------------------------- #
# The mutations
# --------------------------------------------------------------------------- #
# `find` is asserted to occur exactly `count` times before anything is
# replaced. The stock-line mutation is the reason `count` is explicit rather
# than assumed: `color: var(--store-status-success);` appears five times in the
# stylesheet, so a bare string replace would mutate the PDP stock line, the
# cart link and two `[data-mkt-added]` states as well, and the harness would be
# reporting on a blast radius it never described.

MUTATIONS = (
    dict(
        name="transcribed_page_grey_drifts",
        why="One transcribed token moves by a single channel step -- the shape "
            "of a hand-edit or a bad merge, invisible to the eye.",
        find="--store-bg-page: #eaeded;",
        count=1,
        replace="--store-bg-page: #eaebec;",
        expect="test_transcribed_tokens_equal_the_native_theme",
    ),
    dict(
        name="derived_wash_invents_a_hue",
        why="A web-only derived token keeps its alpha but stops taking the "
            "app's hue. Derived tokens are allowed to exist; inventing a "
            "colour is what they are not allowed to do.",
        find="--store-wash: rgba(15, 17, 17, 0.08);",
        count=1,
        replace="--store-wash: rgba(0, 40, 120, 0.08);",
        expect="test_derived_tokens_take_their_hue_from_the_app",
    ),
    dict(
        name="derived_wash_alpha_moves",
        why="Same token, opposite half of the claim: right hue, wrong alpha. "
            "Pairs with the mutation above so neither half of that test can "
            "be doing all the work.",
        find="--store-wash: rgba(15, 17, 17, 0.08);",
        count=1,
        replace="--store-wash: rgba(15, 17, 17, 0.5);",
        expect="test_derived_tokens_take_their_hue_from_the_app",
    ),
    dict(
        name="unclassified_token_added",
        why="A 39th `--store-*` token appears with no classification. This is "
            "the mutation that proves the gate checks the whole block rather "
            "than the subset someone remembered to list.",
        find="--store-bg-card: #ffffff;",
        count=1,
        replace="--store-bg-card: #ffffff;\n  --store-bg-rogue: #ff00ff;",
        expect="test_every_store_token_is_classified",
    ),
    dict(
        name="palette_flips_back_to_dark",
        why="The whole storefront returns to dark. Note that transcription "
            "equality would NOT catch this if the native theme flipped too -- "
            "polarity needs its own assertion, and this proves it has one.",
        find="--store-bg-page: #eaeded;",
        count=1,
        replace="--store-bg-page: #0b0b0c;",
        also=(("--store-bg-card: #ffffff;", 1, "--store-bg-card: #141518;"),),
        expect="test_the_storefront_palette_is_light_not_dark",
    ),
    dict(
        # The bug that actually shipped, restored verbatim.
        #
        # Anchored on the selector, not on the declarations. The obvious anchor
        # -- `font-weight: 700;` plus the colour -- matched twice, because the
        # PDP's `.mkt-stock` is deliberately the same two declarations as the
        # card's. The count check above caught that and reported DRIFTED rather
        # than silently mutating two rules and crediting the result to one.
        name="stock_line_reverts_to_the_stroke_mint",
        why="`.mkt-card-stock` goes back to `var(--mkt-accent)` -- the real "
            "regression, which measured 2.25:1 on a white card while all 38 "
            "tokens still matched the app exactly. No value check can see it.",
        find=".mkt-card-stock {\n"
             "  margin: var(--spacing-xs, 8px) 0 0;\n"
             "  font-size: var(--font-size-xs, 12px);\n"
             "  font-weight: 700;\n"
             "  color: var(--store-status-success);",
        count=1,
        replace=".mkt-card-stock {\n"
                "  margin: var(--spacing-xs, 8px) 0 0;\n"
                "  font-size: var(--font-size-xs, 12px);\n"
                "  font-weight: 700;\n"
                "  color: var(--mkt-accent);",
        expect="test_a_stroke_token_is_never_used_as_a_text_colour",
    ),
    dict(
        # `color` is not the only property that paints glyphs, so the rule lists
        # `-webkit-text-fill-color` as well -- and the stylesheet has no such
        # declaration, which means that half of the tuple is asserted by
        # nothing. An unexercised branch in a guard is indistinguishable from a
        # typo in it. This mutation is the only thing that tells them apart.
        name="stroke_mint_painted_via_text_fill_color",
        why="The same misuse smuggled in through `-webkit-text-fill-color`, "
            "which overrides `color` where it is supported. Proves the second "
            "property in the rule's tuple is live and not a dead string.",
        find="  color: var(--store-status-success);\n}\n\n.mkt-card-stock.is-low",
        count=1,
        replace="  color: var(--store-status-success);\n"
                "  -webkit-text-fill-color: var(--store-accent-on-light);\n"
                "}\n\n.mkt-card-stock.is-low",
        expect="test_a_stroke_token_is_never_used_as_a_text_colour",
    ),
    dict(
        name="remedy_green_drifts_pale",
        why="The token the rule above tells authors to use instead drifts to a "
            "mint as unreadable as the one it replaces. Without this, the ban "
            "could keep passing while pointing everybody at 2.2:1.",
        find="--store-status-success: #067d62;",
        count=1,
        replace="--store-status-success: #3ddca8;",
        expect="test_the_remedy_token_that_rule_points_at_really_is_readable",
    ),
)

#: The one mutation that is not a text edit. Failure mode 1 in the parity
#: module's own header is a parser that shrinks, so the thing to break is the
#: parser, not the data: `_load_theme` is wrapped to drop exactly the keys whose
#: values are references rather than literals -- which is what a literal-only
#: regex reader does, and what the older native gates really do.
PARSER_MUTATION = "parser_regresses_to_literals_only"
PARSER_EXPECT = "test_the_theme_parser_resolves_references_rather_than_dropping_them"

#: Default-deny coverage. Every test in the parity module must appear here.
TARGETED = frozenset([m["expect"] for m in MUTATIONS] + [PARSER_EXPECT])


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

def apply_edits(source, edits, label):
    """Apply (find, count, replace) triples, or return (None, reason)."""
    out = source
    for find, count, replace in edits:
        seen = out.count(find)
        if seen != count:
            return None, ("anchor %r occurs %d time(s), expected %d -- the "
                          "stylesheet moved under this mutation"
                          % (find[:60], seen, count))
        out = out.replace(find, replace)
    if out == source:
        return None, "applied cleanly but changed nothing"
    return out, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every test that caught each mutation, not "
                             "just whether the intended one did")
    args = parser.parse_args()

    print("repo     %s" % ROOT)
    print("suite    %s" % PARITY.relative_to(ROOT))
    print("mutating %s (into a tempdir; source is never written)\n"
          % CSS.relative_to(ROOT))

    baseline_module = fresh()
    discovered = test_names(baseline_module)

    # --- coverage gate ----------------------------------------------------- #
    missing = sorted(set(discovered) - TARGETED)
    stale = sorted(TARGETED - set(discovered))
    if missing or stale:
        if missing:
            print("NO MUTATION for %d parity test(s):" % len(missing))
            for name in missing:
                print("   %s" % name)
            print("\nEvery assertion in that suite has to be shown capable of "
                  "failing. Add a mutation, or the test is decoration.")
        if stale:
            print("MUTATION TARGETS a test that no longer exists:")
            for name in stale:
                print("   %s" % name)
        return 2

    # --- baseline ---------------------------------------------------------- #
    baseline = run_all(baseline_module)
    print("=== baseline, unmutated (%d tests, all must pass)" % len(discovered))
    for name, err in baseline.items():
        print("   %-70s %s" % (name[:70], "PASS" if err is None else "FAIL"))
        if err is not None:
            print("      %s" % err)
    if any(err is not None for err in baseline.values()):
        print("\nABORT: the suite is not green before any mutation, so nothing "
              "this harness reports would mean anything. Fix the suite first.")
        return 2

    source = CSS.read_text(encoding="utf-8")
    verdicts = []

    with tempfile.TemporaryDirectory(prefix="mkt-parity-mutants-") as tmp:
        for spec in MUTATIONS:
            edits = [(spec["find"], spec["count"], spec["replace"])]
            edits.extend(spec.get("also", ()))
            mutated, reason = apply_edits(source, edits, spec["name"])
            if mutated is None:
                print("\n=== %s -> DRIFTED" % spec["name"])
                print("   %s" % reason)
                verdicts.append((spec["name"], "DRIFTED"))
                continue

            path = pathlib.Path(tmp) / ("%s.css" % spec["name"])
            path.write_text(mutated, encoding="utf-8")

            module = fresh()
            module.CSS_PATH = str(path)
            results = run_all(module)
            caught = [n for n, err in results.items() if err is not None]

            if spec["expect"] in caught:
                verdict = "KILLED"
            elif caught:
                # Something noticed, but not the test whose job it was. That is
                # a coverage hole wearing a green mask: the intended assertion
                # is untested and only survives behind a neighbour.
                verdict = "KILLED BY THE WRONG TEST"
            else:
                verdict = "SURVIVED"

            print("\n=== %s -> %s" % (spec["name"], verdict))
            print("   %s" % spec["why"])
            if verdict == "KILLED" and not args.verbose:
                print("   caught by %s" % spec["expect"])
                print("      %s" % results[spec["expect"]])
            elif caught:
                print("   expected %s" % spec["expect"])
                for name in caught:
                    print("   caught by %s" % name)
                    print("      %s" % results[name])
            else:
                print("   expected %s -- NOTHING FAILED" % spec["expect"])
            verdicts.append((spec["name"], verdict))

    # --- the parser mutation ---------------------------------------------- #
    module = fresh()
    real_load = module._load_theme

    def literals_only():
        resolved = real_load()
        return {key: value for key, value in resolved.items()
                if key not in module.REFERENCE_VALUED}

    module._load_theme = literals_only
    results = run_all(module)
    caught = [n for n, err in results.items() if err is not None]
    verdict = ("KILLED" if PARSER_EXPECT in caught
               else "KILLED BY THE WRONG TEST" if caught else "SURVIVED")
    print("\n=== %s -> %s" % (PARSER_MUTATION, verdict))
    print("   The theme reader drops reference-valued keys instead of failing "
          "on them -- `accent.brand`, `badge.featuredText` and the whole `cta` "
          "object, i.e. both halves of the primary CTA.")
    if caught:
        for name in caught:
            if name == PARSER_EXPECT or args.verbose:
                print("   caught by %s" % name)
                print("      %s" % results[name])
    else:
        print("   expected %s -- NOTHING FAILED" % PARSER_EXPECT)
    verdicts.append((PARSER_MUTATION, verdict))

    # --- summary ----------------------------------------------------------- #
    killed = [n for n, v in verdicts if v == "KILLED"]
    survived = [(n, v) for n, v in verdicts if v != "KILLED"]
    print("\n%s" % ("-" * 70))
    print("%d/%d mutations killed by the test that was supposed to kill them"
          % (len(killed), len(verdicts)))
    for name, verdict in survived:
        print("   %-46s %s" % (name, verdict))
    if survived:
        print("\nA surviving mutation means the assertion it targets is not "
              "load-bearing. The palette could drift that way in production "
              "and the suite would stay green.")
    return 0 if not survived else 1


if __name__ == "__main__":
    sys.exit(main())
