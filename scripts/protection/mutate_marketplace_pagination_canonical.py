#!/usr/bin/env python3
"""Prove the pagination-canonical tests can fail.

Everything the pagination change adds is a *policy*: which page a canonical
names, which parameters it carries, which page number is believed, and whether a
URL that was `noindex` stays that way. A policy guard is the archetypal
vacuously-green test -- break the policy and the route still answers 200, still
renders a grid, still emits a well-formed `<link rel="canonical">`, and still
passes every structural check in the storefront suite. Only an assertion about
the *value* notices, and an assertion about a value is exactly the kind that can
be written to match whatever the code already does.

So each mutation below is a plausible wrong version of this change, paired with
the single test that must notice it. A mutation nothing notices is reported as
SURVIVED and is a finding about the tests, not about the mutation.

    python3 scripts/protection/mutate_marketplace_pagination_canonical.py

Mutates a `copytree` of the checkout inside a `TemporaryDirectory`. Real source
is edited, in a sandbox -- not a reimplementation of the policy inside the test,
which would only ever prove that the copy agrees with itself.

ON CI ENFORCEMENT
-----------------
This script is *not* run by CI, and neither is its predecessor
`mutate_marketplace_category_sitemap.py` -- `grep -rn mutate_ .github/` returns
nothing. That is stated rather than quietly left true, because a harness nobody
runs is worth less than it looks.

What makes that acceptable here is that the durable half *is* enforced:
`tests/test_marketplace_pagination_canonical.py` is registered in
`config/ci_test_manifest.json`, and `.github/workflows/backend-tests.yml` runs
every manifest entry in its own process across eight shards -- so each guard
named in `killed_by` below executes on every pull request. This script is the
one-time evidence that those guards bite; the manifest is what keeps them
running. The reverse arrangement -- an unenforced harness guarding unenforced
tests -- is the one to refuse.
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
    "tests/test_marketplace_pagination_canonical.py",
    "tests/test_marketplace_storefront.py",
    "tests/test_search_visibility.py",
)

#: Copied rather than symlinked, so `parents[2]` inside the sandbox resolves to
#: the sandbox rather than reaching back into the real checkout and mutating it.
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
    # --- the defect this change exists to fix -------------------------------
    Mutation(
        name="the canonical goes back to never naming the page (the original bug)",
        path="services/marketplace_storefront.py",
        anchor='                "page": canonical_page,\n',
        replacement='                "page": 1,\n',
        killed_by="test_page_two_is_its_own_canonical_and_not_page_one",
    ),
    Mutation(
        name="the canonical believes the requested page instead of the served one",
        path="services/marketplace_storefront.py",
        anchor="        page.page if (not filters.q and filters.sort == mw.DEFAULT_SORT) else 1\n",
        replacement="        filters.page if (not filters.q and filters.sort == mw.DEFAULT_SORT) else 1\n",
        killed_by="test_an_out_of_range_page_canonicalises_to_the_real_last_page",
    ),
    Mutation(
        name="the canonical carries the page into sorted views too",
        path="services/marketplace_storefront.py",
        anchor="        page.page if (not filters.q and filters.sort == mw.DEFAULT_SORT) else 1\n",
        replacement="        page.page if True else 1\n",
        killed_by="test_a_sorted_view_is_not_a_distinct_document",
    ),
    Mutation(
        name="the canonical drops the category and keeps only the page",
        path="services/marketplace_storefront.py",
        anchor='                "category": filters.category if known_category else "",\n',
        replacement='                "category": "",\n',
        killed_by="test_a_category_page_two_does_not_collapse_into_anything_else",
    ),

    # --- indexability must not follow the canonical -------------------------
    Mutation(
        name="indexability is clamped too, so a clamped page 2 becomes indexable",
        path="services/marketplace_storefront.py",
        anchor="        and filters.page == 1\n",
        replacement="        and page.page == 1\n",
        killed_by="test_a_clamped_page_two_is_still_not_indexable",
    ),
    Mutation(
        name="every page becomes indexable now that each has its own canonical",
        path="services/marketplace_storefront.py",
        anchor="        and filters.page == 1\n",
        replacement="        and True\n",
        killed_by="test_page_one_is_indexable_and_page_two_is_not",
    ),

    # --- normalisation ------------------------------------------------------
    Mutation(
        name="a page value below 1 is trusted instead of normalised",
        path="services/marketplace_storefront.py",
        anchor="            page=max(1, page),\n",
        replacement="            page=page,\n",
        killed_by="test_an_unusable_page_value_becomes_page_one",
    ),
    Mutation(
        name="page=1 is spelled out instead of folding to the clean parent",
        path="services/marketplace_web.py",
        anchor='and not (k == "page" and v == "1")',
        replacement="and True",
        killed_by="test_page_one_canonicalises_to_the_clean_parent",
    ),
    Mutation(
        name="canonical parameters stop having one deterministic order",
        path="services/marketplace_web.py",
        anchor="urlencode(sorted(pairs))",
        replacement="urlencode(list(reversed(pairs)))",
        killed_by="test_canonical_parameters_are_in_a_fixed_order",
    ),
    Mutation(
        name="paginate stops clamping, so an overshoot renders an empty page",
        path="services/marketplace_web.py",
        anchor="    current = min(max(1, int(page or 1)), pages)\n",
        replacement="    current = max(1, int(page or 1))\n",
        killed_by="test_an_out_of_range_page_is_never_empty",
    ),

    # --- the sequence has to stay crawlable ---------------------------------
    Mutation(
        name="the next-page control becomes a span instead of an anchor",
        path="services/marketplace_storefront.py",
        anchor='f\'<a href="{esc(filters.url(page=page.page + 1))}" rel="next">Next</a>\'',
        replacement="'<span rel=\"next\">Next</span>'",
        killed_by="test_the_pager_links_to_the_next_page_as_a_real_anchor",
    ),
    Mutation(
        name="the pager navigates from the requested page, so an overshoot strands",
        path="services/marketplace_storefront.py",
        anchor='f\'<a href="{esc(filters.url(page=page.page - 1))}" rel="prev">Previous</a>\'',
        replacement='f\'<a href="{esc(filters.url(page=filters.page - 1))}" rel="prev">Previous</a>\'',
        killed_by="test_the_pager_uses_the_clamped_page_so_an_overshoot_still_navigates",
    ),

    # --- and none of it may reach a sitemap ---------------------------------
    Mutation(
        name="the sitemap gate stops looking at the query string",
        path="services/search_visibility.py",
        anchor='    if "?" in (path or ""):\n',
        replacement="    if False:\n",
        killed_by="test_a_paginated_path_is_not_sitemap_eligible",
    ),
)


def run(sandbox: Path, suite: str, selector: str | None = None) -> tuple[int, str]:
    cmd = [sys.executable, "-m", "pytest", suite, "-q", "--no-header",
           "-p", "no:cacheprovider"]
    if selector:
        cmd += ["-k", selector]
    proc = subprocess.run(cmd, cwd=sandbox, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def verdicts(sandbox: Path, mutation: Mutation) -> tuple[list[tuple[str, int]], str]:
    """Run only the named guard, and insist it actually ran.

    pytest exits 5 when `-k` selected nothing, which in a loop that only checks
    for a non-zero code reads as a kill. A selector that matches no test would
    otherwise "prove" every mutation it is attached to.
    """
    results: list[tuple[str, int]] = []
    for suite in mutation.suites:
        code, out = run(sandbox, suite, mutation.killed_by)
        if code == 5:
            continue
        if "error" in out.lower() and " passed" not in out and " failed" not in out:
            return results, f"{suite} errored rather than reporting: {out.strip()[-300:]}"
        results.append((suite, code))
    if not results:
        return results, f"selector {mutation.killed_by!r} matched no test in any suite"
    return results, ""


def locate(sandbox: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for name in dict.fromkeys(m.killed_by for m in MUTATIONS):
        nodes: list[str] = []
        for suite in SUITES:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", suite, "-q", "--no-header",
                 "--collect-only", "-p", "no:cacheprovider", "-k", name],
                cwd=sandbox, capture_output=True, text=True,
            )
            if proc.returncode != 0:
                continue
            nodes += [l.strip() for l in proc.stdout.splitlines() if "::" in l]
        found[name] = nodes
    return found


def main() -> int:
    assert (REPO / "bot.py").is_file(), f"{REPO} is not a checkout"
    print(f"repo      {REPO}")
    print(f"python    {sys.executable}\n")

    with tempfile.TemporaryDirectory(prefix="psx-mutate-pagination-") as tmp:
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
        ambiguous = {n: f for n, f in functions.items() if len(f) != 1}
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
                print(f"BROKEN    {mutation.name}\n"
                      f"          anchor matched {hits}x in {mutation.path}")
                continue

            (sandbox / mutation.path).write_text(
                source.replace(mutation.anchor, mutation.replacement))
            try:
                results, fault = verdicts(sandbox, mutation)
                if fault:
                    broken.append((mutation, fault))
                    print(f"BROKEN    {mutation.name}\n          {fault}")
                    continue
                failing = [s for s, code in results if code == 1]
                if failing:
                    killed.append(mutation)
                    print(f"killed    {mutation.name}\n"
                          f"          by {mutation.killed_by} ({Path(failing[0]).name})")
                else:
                    survived.append(mutation)
                    print(f"SURVIVED  {mutation.name}\n"
                          f"          {mutation.killed_by} still passes")
            finally:
                (sandbox / mutation.path).write_text(source)

        print(f"\n{len(killed)} killed, {len(survived)} survived, "
              f"{len(broken)} harness failures of {len(MUTATIONS)} mutations")
        for mutation in survived:
            print(f"  SURVIVED {mutation.name} -- {mutation.killed_by} guards nothing")
        for mutation, why in broken:
            print(f"  BROKEN   {mutation.name} -- {why}")
        return 0 if not survived and not broken else 1


if __name__ == "__main__":
    raise SystemExit(main())
