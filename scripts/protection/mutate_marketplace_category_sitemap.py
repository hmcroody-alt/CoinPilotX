#!/usr/bin/env python3
"""Prove the category-sitemap tests can fail.

A test that has only ever been observed passing is indistinguishable from a
test that cannot fail. Everything this change adds is a *policy* -- a threshold,
a population to count, a URL spelling, a date -- and a policy guard is exactly
the kind of test that goes vacuously green: break the policy, and a sitemap
route still returns valid XML, still 200s, and still passes every structural
check in `tests/test_sitemap_integrity.py`.

So each mutation below is a plausible wrong version of this change, paired with
the test that must notice. A mutation nothing notices is reported as SURVIVED
and is a finding about the tests, not about the mutation.

    python3 scripts/protection/mutate_marketplace_category_sitemap.py

Mutates a `copytree` of the checkout inside a `TemporaryDirectory`, and has no
restore step on purpose. A harness that mutates real source and restores
afterwards is one exception away from leaving a mutation live, and `git checkout
<file>` as a restore destroys uncommitted work in the same file.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

SUITES = (
    "tests/test_search_visibility.py",
    "tests/test_marketplace_public_pages.py",
    "tests/test_sitemap_integrity.py",
)

#: Copied rather than symlinked, so `parents[2]` inside the sandbox resolves to
#: the sandbox. Skipping what cannot affect a Python test result, because a
#: 360MB copy per mutation is the difference between a harness someone runs and
#: one they do not.
IGNORE = shutil.ignore_patterns(
    ".git", "mobile-native", "mobile", "node_modules", ".venv", "venv",
    "*.db", "*.sqlite3", "*.jsbundle", ".fuse_hidden*", "ios", "android",
)


@dataclass(frozen=True)
class Mutation:
    name: str
    path: str
    anchor: str
    replacement: str
    #: The test that must fail. Named rather than "some test failed", because a
    #: mutation killed by an unrelated collection error proves nothing.
    killed_by: str
    suites: tuple[str, ...] = field(default=SUITES)


MUTATIONS = (
    # --- the canonical ------------------------------------------------------
    Mutation(
        name="canonical_url goes back to stripping the whole query string",
        path="services/search_visibility.py",
        anchor="    allowed = _CONTENT_QUERY_PARAMS.get(resolved)\n"
               "    if not allowed or \"?\" not in (path or \"\"):",
        replacement="    allowed = None\n"
                    "    if not allowed or \"?\" not in (path or \"\"):",
        killed_by="test_canonical_url_keeps_a_parameter_that_selects_which_content_is_shown",
    ),
    Mutation(
        name="the content-parameter exception becomes 'keep the query string'",
        path="services/search_visibility.py",
        anchor='_CONTENT_QUERY_PARAMS = {\n    "/pulse/marketplace": ("category",),\n}',
        replacement='_CONTENT_QUERY_PARAMS = {\n'
                    '    "/pulse/marketplace": ("category", "sort", "page", "utm_source"),\n'
                    '    "/pulse/reels": ("category",),\n}',
        killed_by="test_keeping_the_category_parameter_did_not_become_keep_the_query_string",
    ),

    # --- the threshold ------------------------------------------------------
    Mutation(
        name="the quality threshold is dropped (every department is submitted)",
        path="services/marketplace_seo.py",
        anchor="CATEGORY_MIN_INDEXABLE_LISTINGS = 3",
        replacement="CATEGORY_MIN_INDEXABLE_LISTINGS = 1",
        killed_by="test_a_department_below_the_threshold_is_not_submitted",
    ),
    Mutation(
        name="the threshold is raised past the catalogue (nothing is submitted)",
        path="services/marketplace_seo.py",
        anchor="CATEGORY_MIN_INDEXABLE_LISTINGS = 3",
        replacement="CATEGORY_MIN_INDEXABLE_LISTINGS = 99",
        killed_by="test_a_department_at_the_threshold_is_submitted",
    ),
    Mutation(
        name="the threshold counts public listings instead of indexable ones",
        path="services/marketplace_seo.py",
        anchor="    indexable = [\n"
               "        (row, listing) for row, listing in rows if eligibility(listing).indexable\n"
               "    ]",
        replacement="    indexable = list(rows)",
        killed_by="test_the_threshold_counts_indexable_products_not_public_ones",
    ),

    # --- the URL spelling ---------------------------------------------------
    Mutation(
        name="the taxonomy is built from the eligible subset, so the slug can flip",
        path="services/marketplace_seo.py",
        anchor="    taxonomy = marketplace_web.build_taxonomy([\n"
               "        listing.get(\"category\") for _row, listing in rows\n"
               "    ])",
        replacement="    taxonomy = marketplace_web.build_taxonomy([\n"
                    "        listing.get(\"category\") for _row, listing in rows\n"
                    "        if eligibility(listing).indexable\n"
                    "    ])",
        killed_by="test_the_submitted_slug_is_the_spelling_the_grid_answers_on",
    ),
    Mutation(
        name="depth-2 sections are submitted alongside their department",
        path="services/marketplace_seo.py",
        anchor="    entries = []\n    for node in taxonomy:",
        replacement="    entries = []\n"
                    "    taxonomy = [n for node in taxonomy for n in (node,) + tuple(node.children)]\n"
                    "    for node in taxonomy:",
        killed_by="test_only_departments_are_submitted_never_their_sections",
    ),

    # --- lastmod ------------------------------------------------------------
    Mutation(
        name="lastmod is stamped with the crawl date",
        path="services/marketplace_seo.py",
        anchor='        entries.append((path, max([s for s in stamps if s], default="")))',
        replacement="        from datetime import date\n"
                    "        entries.append((path, date.today().isoformat()))",
        killed_by="test_lastmod_is_the_newest_eligible_product_in_the_department",
    ),
    Mutation(
        name="lastmod is dated by products the department is not asking to rank",
        path="services/marketplace_seo.py",
        anchor="        stamps = [\n"
               "            str(row.get(\"updated_at\") or row.get(\"created_at\") or \"\") for row in members\n"
               "        ]",
        replacement="        stamps = [\n"
                    "            str(row.get(\"updated_at\") or row.get(\"created_at\") or \"\")\n"
                    "            for row, listing in rows\n"
                    "            if marketplace_web.category_matches(listing.get(\"category\"), node.slug)\n"
                    "        ]",
        killed_by="test_an_ineligible_product_does_not_date_the_department",
    ),

    # --- the wiring ---------------------------------------------------------
    Mutation(
        name="the new child sitemap is missing from the edge-cache tuple",
        path="bot.py",
        anchor='"/sitemap-posts.xml", "/sitemap-categories.xml", "/sitemap-products.xml", "/sitemap-live.xml", "/sitemap-replays.xml", merchant_center_feed.FEED_PATH',
        replacement='"/sitemap-posts.xml", "/sitemap-products.xml", "/sitemap-live.xml", "/sitemap-replays.xml", merchant_center_feed.FEED_PATH',
        killed_by="test_every_sitemap_is_cacheable_at_the_edge",
    ),
    Mutation(
        name="the new child sitemap is not named by the sitemap index",
        path="bot.py",
        anchor='SITEMAP_CHILDREN = ("/sitemap-pages.xml", "/sitemap-posts.xml", "/sitemap-categories.xml",',
        replacement='SITEMAP_CHILDREN = ("/sitemap-pages.xml", "/sitemap-posts.xml",',
        killed_by="test_sitemap_xml_is_an_index_pointing_at_every_child",
    ),
)


def run(sandbox: Path, suite: str, selector: str = "") -> tuple[int, str]:
    """Run `suite`, narrowed to `selector` by name.

    Narrowed with ``-k`` rather than a ``file::name`` nodeid, because most of
    these guards are ``unittest.TestCase`` *methods*: the nodeid of a method is
    ``file::Class::name``, so a ``file::name`` selector addresses nothing and
    pytest exits 4. The first version of this harness did exactly that and
    called seven guards uncollectable. ``-k`` does not need to know the class,
    and still exits 5 when the name matches nothing -- which is the signal
    :func:`verdicts` uses to decide the test lives in another file.
    """

    argv = [sys.executable, "-m", "pytest", suite, "-q", "--no-header", "-p", "no:randomly"]
    if selector:
        argv += ["-k", selector]
    proc = subprocess.run(argv, cwd=sandbox, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def verdicts(sandbox: Path, mutation: Mutation) -> tuple[list[tuple[str, int]], str]:
    """Run `mutation.killed_by` wherever it lives, and classify each outcome.

    Dispatched on pytest's exit code rather than on whether it was non-zero. A
    selector naming a test that lives in another file exits 4 (usage error) or 5
    (nothing collected) -- both non-zero, and counting either as a kill reports a
    guard that never ran as a guard that worked. Two of these mutations did
    exactly that on the first pass of this harness.

    Returns ``(verdicts, fault)`` where a non-empty ``fault`` means the harness
    could not get a test result at all, which is not the same as SURVIVED.
    """

    results: list[tuple[str, int]] = []
    for suite in mutation.suites:
        code, _out = run(sandbox, suite, mutation.killed_by)
        if code in (4, 5):
            continue  # the named test is not in this file
        if code not in (0, 1):
            return results, f"{suite} exited {code}, not a test result"
        results.append((suite, code))
    if not results:
        return results, f"{mutation.killed_by} was collected nowhere"
    return results, ""


def locate(sandbox: Path) -> dict[str, list[str]]:
    """Map each `killed_by` to the node ids it actually selects.

    Run before any mutation, and every name must resolve to exactly one test
    *function*. Two things this catches that the mutation loop cannot: a
    `killed_by` typo, which would otherwise read as "collected nowhere" and look
    like a harness bug rather than a wrong name; and the looseness of ``-k``,
    which matches by substring, so a short selector could be satisfied by a
    *different* test failing. Attribution is the whole value of this harness --
    "some test went red" is not evidence that the named guard works.

    One function, not one node id: ``test_every_sitemap_is_cacheable_at_the_edge``
    is parametrised over all seven sitemap routes, so it legitimately collects
    seven ids. Those are one guard asked seven questions, and the mutation it
    answers for breaks exactly the ``[/sitemap-categories.xml]`` one. Requiring
    a single id would have rejected a correct selector; requiring a single
    function still rejects a selector that spans two different guards.
    """

    found: dict[str, list[str]] = {}
    for name in dict.fromkeys(m.killed_by for m in MUTATIONS):
        nodes: list[str] = []
        for suite in SUITES:
            code, out = run_collect(sandbox, suite, name)
            if code != 0:
                continue
            nodes += [line.strip() for line in out.splitlines() if "::" in line]
        found[name] = nodes
    return found


def run_collect(sandbox: Path, suite: str, selector: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", suite, "-q", "--no-header",
         "-p", "no:randomly", "--collect-only", "-k", selector],
        cwd=sandbox, capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    assert (REPO / "bot.py").is_file(), f"{REPO} is not a checkout"
    print(f"repo      {REPO}")
    print(f"python    {sys.executable}\n")

    with tempfile.TemporaryDirectory(prefix="psx-mutate-") as tmp:
        sandbox = Path(tmp) / "repo"
        print("copying the checkout...", flush=True)
        shutil.copytree(REPO, sandbox, ignore=IGNORE, symlinks=True)

        # A mutant "killed" by an already-red suite proves nothing, so the
        # baseline is established before any mutation is believed.
        print("baseline:", flush=True)
        for suite in SUITES:
            code, out = run(sandbox, suite)
            tail = out.strip().splitlines()[-1] if out.strip() else "(no output)"
            print(f"  {'PASS' if code == 0 else 'FAIL'}  {suite}  {tail}")
            if code != 0:
                print("\nbaseline is not green; every result below would be meaningless.")
                return 2

        print("\nselectors:", flush=True)
        located = locate(sandbox)
        functions = {
            name: {node.split("[", 1)[0] for node in nodes}
            for name, nodes in located.items()
        }
        ambiguous = {name: fns for name, fns in functions.items() if len(fns) != 1}
        for name, nodes in located.items():
            params = f" ({len(nodes)} parametrisations)" if len(nodes) > 1 else ""
            print(f"  {len(functions[name])} fn{params}  {name}")
        if ambiguous:
            print("\na selector that names zero or several guards cannot attribute a kill:")
            for name, fns in ambiguous.items():
                print(f"  {name} -> {len(fns)} test functions")
                for fn in sorted(fns):
                    print(f"      {fn}")
            return 2

        pristine = {m.path: (sandbox / m.path).read_text() for m in MUTATIONS}
        killed, survived, broken = [], [], []

        print()
        for mutation in MUTATIONS:
            source = pristine[mutation.path]
            hits = source.count(mutation.anchor)
            if hits != 1:
                # A mutation that does not apply is not a mutation that passed.
                broken.append((mutation, f"anchor matched {hits}x"))
                print(f"BROKEN    {mutation.name}\n          anchor matched {hits}x "
                      f"in {mutation.path}")
                continue

            # Written through `write_text`, which updates mtime -- a `cp -p`
            # restore preserves it and leaves the previous `__pycache__` valid,
            # so the mutation stays live into the next run.
            (sandbox / mutation.path).write_text(
                source.replace(mutation.anchor, mutation.replacement))
            try:
                results, fault = verdicts(sandbox, mutation)
                if fault:
                    broken.append((mutation, fault))
                    print(f"BROKEN    {mutation.name}\n          {fault}")
                    continue
                failing = [suite for suite, code in results if code == 1]
                if failing:
                    killed.append(mutation)
                    print(f"killed    {mutation.name}\n"
                          f"          by {mutation.killed_by} "
                          f"({Path(failing[0]).name})")
                else:
                    survived.append(mutation)
                    print(f"SURVIVED  {mutation.name}\n"
                          f"          {mutation.killed_by} still passes")
            finally:
                (sandbox / mutation.path).write_text(source)

        print(f"\n{len(killed)} killed, {len(survived)} survived, {len(broken)} harness failures"
              f" of {len(MUTATIONS)} mutations")
        for mutation in survived:
            print(f"  SURVIVED {mutation.name} -- {mutation.killed_by} guards nothing")
        for mutation, why in broken:
            print(f"  BROKEN   {mutation.name} -- {why}")
        return 0 if not survived and not broken else 1


if __name__ == "__main__":
    raise SystemExit(main())
