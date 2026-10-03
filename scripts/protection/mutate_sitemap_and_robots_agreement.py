#!/usr/bin/env python3
"""Prove the sitemap/robots agreement tests can fail.

This change has the property that makes a test suite vacuously green: every
defect it fixes leaves a perfectly healthy-looking system behind. Before the
fix, `/sitemap-live.xml` was well-formed XML, served 200, validated, and every
URL in it was a login redirect. `robots.txt` was generated, derived, internally
consistent, and forbade the crawl of two pages the same module submits. Nothing
crashed. Nothing 500'd. No structural assertion anywhere could see it.

So the guards are all assertions about *agreement between two channels*, and an
assertion about agreement is exactly the kind that can be satisfied by making
both channels wrong in the same direction. Each mutation below is a plausible
wrong version of this change, paired with the one test that must notice.

    python3 scripts/protection/mutate_sitemap_and_robots_agreement.py

Mutates a `copytree` of the checkout inside a `TemporaryDirectory`. Real source
is edited, in a sandbox -- not a reimplementation of the policy inside the test,
which would only prove the copy agrees with itself.

TWO MUTATIONS HERE ARE THE SHIPPED BUGS
---------------------------------------
`the robots emitter goes back to bare prefixes` and `/momentum returns to the
live sitemap` are not hypothetical wrong versions. They are the exact state of
production on 2026-10-03, re-applied. If either survived, the corresponding
test would be decoration.

WHAT THE FIRST RUN FOUND
------------------------
8 killed, 7 survived -- including `the robots emitter goes back to bare
prefixes`. The guard written for the defect could not see the defect. Three
distinct causes, all worth keeping on the record because all three produce a
green suite that proves nothing:

  1. The test read the policy function, not the served file. Every robots
     assertion called `search_visibility.robots_disallow_patterns()` directly,
     so rewiring `seo_engine.robots_txt()` back to the bare-prefix emitter
     changed what ships and nothing the test looked at. A test named
     `..._by_our_own_robots_txt` never fetched robots.txt. Fixed by parsing
     `GET /robots.txt`; see `_served_disallows`. The one robots test that did
     read the emitter is the one that killed its mutation, which is what
     identified the cause.
  2. The corpus was derived from the table under test. "Every path classified
     `noindex,nofollow` is blocked" selects its corpus from `_RULES`, so
     deleting a rule removes the path from the iteration and the test passes
     with one fewer thing to check. All four rule-removal mutations survived on
     this. Fixed by `MUST_NOT_BE_CRAWLABLE`, a named list with per-path
     production evidence, which is why those four now point at it.
     `test_the_sitemap_index_lists_every_child...` had the same shape -- it
     compared the index against `SITEMAP_CHILDREN` while the index is generated
     from `SITEMAP_CHILDREN` -- and now reads the url_map instead.
  3. The assertion did not cover the claim. Flipping `/arena` to
     `noindex,nofollow` left every existing assertion true, because the
     segment-exact patterns protect the public siblings either way, while doing
     the exact thing the policy forbids: `Disallow` on a surface public pages
     link into, which also puts the `noindex` behind the block. Fixed by
     asserting the arena stays crawlable.

ON CI ENFORCEMENT
-----------------
Not run by CI -- `grep -rn mutate_ .github/` returns nothing, and that is true
of all three harnesses in this directory. Stated rather than left to be
discovered, because a harness nobody runs is worth less than it looks.

The durable half is enforced, but not through the manifest. I assumed it would
be and was wrong, which is worth writing down because the wrong answer is the
intuitive one. `config/ci_test_manifest.json` has 780 `run` entries and not one
of them is under `tests/protection/` -- that whole directory is deliberately in
neither list, because `scripts/protection/run_protection_suite.py` globs
`tests/protection/test_*.py` and runs each file as its own subprocess, and
`tests/protection/test_every_test_file_is_run_by_ci.py::test_the_indirect_runner_is_followed`
computes that set from the workflows rather than from a second hand-maintained
list. So `tests/protection/test_sitemap_entries_are_indexable.py` is enforced
by existing in that directory; adding it to the manifest would have run it
twice and, worse, implied the directory needs registering.

Two consequences for anyone adding a guard here:

  * The runner invokes `python3 <file>`, not pytest, so the file needs its
    `unittest.main()` guard. Without one it exits 0 having collected nothing --
    and the runner treats a silent pass as a failure precisely because that is
    how the LiveKit publish-grant job stayed green while measuring zero.
  * The job that calls it is the `backend` job in
    `.github/workflows/realtime-audio.yml`, which has no `if:` and no `paths:`
    filter, so it runs on every pull request regardless of whether anything
    audio-related moved. The filename is misleading; the coverage is real.

This script is the one-time evidence that the guards bite; that runner is what
keeps them running.
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
    "tests/protection/test_sitemap_entries_are_indexable.py",
    "tests/test_sitemap_integrity.py",
    "tests/test_search_visibility.py",
    "tests/test_registry_landing_pages.py",
)

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
    killed_by: str
    suites: tuple[str, ...] = field(default=SUITES)


MUTATIONS = (
    # --- the shipped bugs, re-applied ---------------------------------------
    Mutation(
        name="the robots emitter goes back to bare prefixes (the shipped bug)",
        path="services/seo_engine.py",
        anchor="search_visibility.robots_disallow_patterns()",
        replacement="search_visibility.robots_disallow_prefixes()",
        killed_by="test_no_sitemapped_url_is_blocked_by_our_own_robots_txt",
    ),
    Mutation(
        name="/momentum returns to the live sitemap (the shipped bug)",
        path="bot.py",
        anchor='paths = ["/arena/live", "/arena/roast-battle", "/arena/momentum", "/arena/leaderboard"]',
        replacement='paths = ["/arena/live", "/arena/roast-battle", "/arena/momentum", "/arena/leaderboard", "/momentum"]',
        killed_by="test_every_sitemapped_url_answers_200",
    ),
    Mutation(
        name="the arena stops being classified, so its login redirects re-enter the sitemap",
        path="services/search_visibility.py",
        anchor='    ("/arena", NOINDEX_FOLLOW, "authenticated arena surface behind a redirect"),',
        replacement="",
        killed_by="test_every_sitemapped_url_answers_200",
    ),

    # --- the pattern expansion, one form at a time --------------------------
    # Each form carries its own weight. Dropping any one of the three is a
    # different wrong answer, so each is mutated separately rather than trusting
    # one test to stand in for all three.
    Mutation(
        name="the $ form is dropped, so a bare private path becomes crawlable",
        path="services/search_visibility.py",
        anchor='        patterns += [f"{bare}$", f"{bare}?", f"{bare}/"]',
        replacement='        patterns += [f"{bare}?", f"{bare}/"]',
        killed_by="test_every_noindex_nofollow_path_is_actually_blocked",
    ),
    Mutation(
        name="the ? form is dropped, so /chat?asset=ETH escapes its Disallow",
        path="services/search_visibility.py",
        anchor='        patterns += [f"{bare}$", f"{bare}?", f"{bare}/"]',
        replacement='        patterns += [f"{bare}$", f"{bare}/"]',
        killed_by="test_a_query_string_does_not_escape_a_disallow",
    ),
    Mutation(
        name="the / form is dropped, so every child of a private surface is crawlable",
        path="services/search_visibility.py",
        anchor='        patterns += [f"{bare}$", f"{bare}?", f"{bare}/"]',
        replacement='        patterns += [f"{bare}$", f"{bare}?"]',
        killed_by="test_every_noindex_nofollow_path_is_actually_blocked",
    ),
    Mutation(
        name="$ is emitted but the checker reads it as a prefix, restoring the overreach",
        path="services/search_visibility.py",
        anchor="        if pattern.endswith(\"$\"):\n            if p == pattern[:-1]:\n                return pattern",
        replacement="        if pattern.endswith(\"$\"):\n            if p.startswith(pattern[:-1]):\n                return pattern",
        killed_by="test_a_disallow_does_not_catch_a_sibling_path",
    ),

    # --- the five rules the overreach had been standing in for --------------
    # Tightening the patterns without these would have *published* each of
    # these paths. `/webhooks/` is the one that was a live hole:
    # `GET /webhooks/stripe` answers 200 with no robots meta of its own.
    Mutation(
        name="/webhooks/ is unnamed again, publishing a Stripe webhook endpoint",
        path="services/search_visibility.py",
        anchor='    ("/webhooks/", NOINDEX_NOFOLLOW, "machine callback"),',
        replacement="",
        killed_by="test_the_paths_the_overreach_was_covering_are_blocked_on_their_own",
    ),
    Mutation(
        name="/admin-dashboard is unnamed again",
        path="services/search_visibility.py",
        anchor='    ("/admin-dashboard", NOINDEX_NOFOLLOW, "administrative surface"),',
        replacement="",
        killed_by="test_the_paths_the_overreach_was_covering_are_blocked_on_their_own",
    ),
    Mutation(
        name="/pulse/messages-v2 is unnamed again, feeding the login-wall trap",
        path="services/search_visibility.py",
        anchor='    ("/pulse/messages-v2", NOINDEX_NOFOLLOW, "private messaging"),',
        replacement="",
        killed_by="test_the_paths_the_overreach_was_covering_are_blocked_on_their_own",
    ),
    Mutation(
        name="/verify-email is unnamed again",
        path="services/search_visibility.py",
        anchor='    ("/verify-email", NOINDEX_NOFOLLOW, "one-time verification URL"),',
        replacement="",
        killed_by="test_the_paths_the_overreach_was_covering_are_blocked_on_their_own",
    ),

    # --- the pre-existing rule the new emitter must not have broken ---------
    Mutation(
        name="noindex,follow surfaces start getting Disallowed too",
        path="services/search_visibility.py",
        anchor="        if directive != NOINDEX_NOFOLLOW:\n            continue",
        replacement="        if directive == INDEX_DIRECTIVE:\n            continue",
        killed_by="test_robots_txt_still_only_disallows_noindex_nofollow_surfaces",
    ),
    Mutation(
        name="/arena is disallowed outright, taking its public siblings with it",
        path="services/search_visibility.py",
        anchor='    ("/arena", NOINDEX_FOLLOW, "authenticated arena surface behind a redirect"),',
        replacement='    ("/arena", NOINDEX_NOFOLLOW, "authenticated arena surface behind a redirect"),',
        killed_by="test_the_arena_subtree_is_not_indexable_but_its_public_siblings_are",
    ),

    # --- the sitemap index itself -------------------------------------------
    Mutation(
        name="a child sitemap is routed but no longer listed in the index",
        path="bot.py",
        anchor='SITEMAP_CHILDREN = ("/sitemap-pages.xml", "/sitemap-posts.xml", "/sitemap-categories.xml", "/sitemap-products.xml", "/sitemap-live.xml", "/sitemap-replays.xml")',
        replacement='SITEMAP_CHILDREN = ("/sitemap-pages.xml", "/sitemap-posts.xml", "/sitemap-categories.xml", "/sitemap-products.xml", "/sitemap-live.xml")',
        killed_by="test_the_sitemap_index_lists_every_child_and_each_one_renders",
    ),

    # --- and the gate the whole thing rests on ------------------------------
    Mutation(
        name="sitemap_xml stops gating entries on eligibility",
        path="services/seo_engine.py",
        anchor="if not search_visibility.sitemap_eligible(path)",
        replacement="if False",
        killed_by="test_every_sitemapped_url_answers_200",
    ),

    # --- the /pulse default-deny rule ---------------------------------------
    # The first of these is the most expensive single-character change in the
    # module, so it is mutated first and deliberately named for what it costs.
    Mutation(
        name="the /pulse rule flips to nofollow, Disallowing the entire commerce graph",
        path="services/search_visibility.py",
        anchor='    ("/pulse", NOINDEX_FOLLOW, "authenticated social application"),',
        replacement='    ("/pulse", NOINDEX_NOFOLLOW, "authenticated social application"),',
        killed_by="test_the_pulse_rule_does_not_take_the_commerce_graph_with_it",
    ),
    Mutation(
        name="the /pulse rule is removed, so 115 private app surfaces claim indexability again",
        path="services/search_visibility.py",
        anchor='    ("/pulse", NOINDEX_FOLLOW, "authenticated social application"),',
        replacement="",
        killed_by="test_the_pulse_rule_does_not_take_the_commerce_graph_with_it",
    ),
    Mutation(
        name="the marketplace carve-out is deleted, so /pulse swallows the product pages",
        path="services/search_visibility.py",
        anchor='    ("/pulse/marketplace", INDEX_DIRECTIVE, "public product collection and product pages"),',
        replacement="",
        killed_by="test_the_pulse_rule_does_not_take_the_commerce_graph_with_it",
    ),
    Mutation(
        name="a carve-out is ordered after the broad rule, where first-match-wins ignores it",
        path="services/search_visibility.py",
        anchor='    ("/pulse/post", INDEX_DIRECTIVE, "public post permalink"),\n    ("/pulse/help", INDEX_DIRECTIVE, "public help centre"),',
        replacement='    ("/pulse/help", INDEX_DIRECTIVE, "public help centre"),',
        killed_by="test_the_pulse_rule_does_not_take_the_commerce_graph_with_it",
    ),
    Mutation(
        name="classify goes back to hardcoding sitemap_eligible=False on every rule",
        path="services/search_visibility.py",
        anchor="            return _d(directive, directive.startswith(\"index\"), reason)",
        replacement="            return _d(directive, False, reason)",
        killed_by="test_the_pulse_rule_does_not_take_the_commerce_graph_with_it",
    ),

    # --- the third channel: what the page actually sends --------------------
    # The sitemap and robots channels are read from the policy module by every
    # other mutation here. This one goes after the meta tag, which no amount of
    # sitemap enumeration can see -- and which was wrong in production.
    Mutation(
        name="seo_page.html hardcodes index,follow again (the shipped bug)",
        path="templates/seo_page.html",
        anchor="""<meta name="robots" content="{{ robots or 'index, follow, max-image-preview:large, max-snippet:-1' }}" />""",
        replacement="""<meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1" />""",
        killed_by="test_the_meta_robots_a_page_sends_is_the_one_the_table_declares",
    ),
    Mutation(
        name="render_seo_landing stops passing the policy directive to the template",
        path="bot.py",
        anchor='        robots=search_visibility.robots_meta(urlparse(page["canonical"]).path),\n',
        replacement="",
        killed_by="test_the_meta_robots_a_page_sends_is_the_one_the_table_declares",
    ),

    # --- the response code an unknown slug gets -----------------------------
    # A different failure mode to everything above. The policy table is not
    # consulted at all here: these are 404s versus 302s over an unbounded URL
    # space, and the only artefact of getting it wrong is crawl budget spent on
    # URLs that do not exist. Nothing that reads a sitemap, `robots.txt` or a
    # meta tag can see it, which is how six handlers kept doing it.
    Mutation(
        name="unknown registry slugs go back to redirecting at the home page (the shipped bug)",
        path="bot.py",
        anchor='''def _registry_page_or_404(page, include_article=False):
    if not page:
        return Response("Not found", status=404)''',
        replacement='''def _registry_page_or_404(page, include_article=False):
    if not page:
        return redirect("/")''',
        killed_by="test_an_unknown_slug_is_a_404_and_not_a_redirect_to_the_home_page",
    ),
    # The mutation above would also be caught by a lazy `status_code != 200`.
    # This one is the reason the guard asserts `== 404` instead: a 301 is a
    # *correct* answer in general, and wrong here specifically, because there is
    # no successor for a symbol we do not cover. A test that accepted any 3xx
    # would let the redirect-to-home behaviour back in through the front door.
    Mutation(
        name="an unknown slug 301s somewhere plausible instead of 404ing",
        path="bot.py",
        anchor='        return Response("Not found", status=404)\n    return render_seo_landing(page, include_article=include_article)',
        replacement='        return redirect("/markets", code=301)\n    return render_seo_landing(page, include_article=include_article)',
        killed_by="test_an_unknown_slug_is_a_404_and_not_a_redirect_to_the_home_page",
    ),
)


def run(sandbox: Path, suite: str, selector: str | None = None) -> tuple[int, str]:
    cmd = [sys.executable, "-m", "pytest", suite, "-q", "--no-header",
           "-p", "no:cacheprovider", "-p", "no:warnings"]
    if selector:
        cmd += ["-k", selector]
    proc = subprocess.run(cmd, cwd=sandbox, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def verdicts(sandbox: Path, mutation: Mutation) -> tuple[list[tuple[str, int]], str]:
    """Run only the named guard, and insist it actually ran.

    pytest exits 5 when `-k` selected nothing, which in a loop that only checks
    for a non-zero code reads as a kill. A selector matching no test would
    otherwise "prove" every mutation attached to it.
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

    with tempfile.TemporaryDirectory(prefix="psx-mutate-sitemap-") as tmp:
        sandbox = Path(tmp) / "repo"
        print("copying the checkout...", flush=True)
        shutil.copytree(REPO, sandbox, ignore=IGNORE, symlinks=True)

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
              f"{len(broken)} harness failures, of {len(MUTATIONS)}")
        for mutation in survived:
            print(f"  SURVIVED  {mutation.name}")
        for mutation, why in broken:
            print(f"  BROKEN    {mutation.name}: {why}")
        return 0 if not survived and not broken else 1


if __name__ == "__main__":
    raise SystemExit(main())
