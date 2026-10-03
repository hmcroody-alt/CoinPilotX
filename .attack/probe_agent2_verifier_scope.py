"""What does Agent 2's sitemap verifier not look at?

`scripts/search_os/verify_sitemap_vs_live.py` reports "all 61 sitemap entries
resolve 200, index,follow and self-canonical". The number is the problem. My own
walk of the same property found **206** offered URLs, and the two numbers cannot
both describe the same set.

The cause is its default scope:

    default="sitemap-products.xml,sitemap-categories.xml,sitemap-posts.xml"

Three names, hardcoded, with no discovery step -- it never fetches
`/sitemap.xml` to ask what the index actually offers. So a sitemap that exists
today and is not on that list is invisible, and a sitemap added tomorrow is
invisible too.

That would be a scoping nit if the excluded sitemaps were empty. The reason it
is not a nit is that Agent 2's script already implements the exact fault class
`NO_ROBOTS_DIRECTIVE`, and my A12-03 and A12-05 findings are *fourteen* and
*eleven* pages that trip it. If those pages live in an excluded sitemap, then
the script is green because of where it is pointed, not because the property is
clean -- and it is the artifact most likely to become the shared CI gate.

So, three questions:

  1. How many URLs does each sitemap offer, and how many does Agent 2's default
     scope therefore cover?
  2. Do the A12-03 / A12-05 pages sit inside or outside that scope?
  3. Re-run Agent 2's own fault classes over the FULL offer set, so the
     comparison is its logic on my scope -- not my logic on my scope. If the
     fault count goes from 0 to N by widening the scope alone, the scope is the
     finding.

Point 3 is the whole design of this probe. Reimplementing the checks my own way
would only prove that two people wrote different checks. Importing Agent 2's
`inspect()` and feeding it a wider URL list isolates the one variable.
"""

from __future__ import annotations

import logging
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(ROOT, ".attack", "scratch.db"))

import bot  # noqa: E402
from services import search_visibility  # noqa: E402

logging.disable(logging.CRITICAL)

RE_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)

#: Agent 2's hardcoded default, copied verbatim from its argparse.
AGENT2_SCOPE = ("sitemap-products.xml", "sitemap-categories.xml", "sitemap-posts.xml")

#: A12-05 -- 200, indexable, and no robots directive at all.
A12_05 = ("/education", "/education/optimism", "/education/scam-alerts",
          "/education/toncoin-scenarios", "/legal/payments", "/legal/refunds",
          "/legal/seller-terms", "/predictions/crypto", "/quote",
          "/roast-battle-preview", "/sports-edge")


def path_of(url):
    return re.sub(r"^https?://[^/]+", "", url) or "/"


def main():
    app = bot.webhook_app
    app.config["TESTING"] = True
    client = app.test_client()

    index_children = [path_of(u) for u in
                      RE_LOC.findall(client.get("/sitemap.xml").get_data(as_text=True))]

    print("=" * 94)
    print("SCOPE OF AGENT 2's SITEMAP VERIFIER")
    print("=" * 94)
    print(f"  /sitemap.xml advertises {len(index_children)} child sitemaps:")
    for child in index_children:
        print(f"      {child}")
    print(f"\n  Agent 2's hardcoded default covers {len(AGENT2_SCOPE)} of them, "
          f"and never reads the index.\n")

    per_map, in_scope, out_of_scope = {}, set(), set()
    for child in index_children:
        body = client.get(child).get_data(as_text=True)
        paths = {path_of(u) for u in RE_LOC.findall(body)}
        per_map[child] = paths
        covered = any(child.endswith(name) for name in AGENT2_SCOPE)
        (in_scope if covered else out_of_scope).update(paths)
        print(f"  {child:34s} {len(paths):4d} URLs   "
              f"{'IN Agent 2 scope' if covered else '<<< NOT CHECKED'}")

    total = set().union(*per_map.values()) if per_map else set()
    print(f"\n  offered in total      {len(total):4d}")
    print(f"  Agent 2 checks        {len(in_scope):4d}")
    print(f"  never checked         {len(out_of_scope):4d}  "
          f"({100 * len(out_of_scope) // max(len(total), 1)}% of the property)")

    print()
    print("=" * 94)
    print("ARE THE A12-03 / A12-05 PAGES INSIDE OR OUTSIDE THAT SCOPE?")
    print("=" * 94)
    blind = []
    for path in A12_05:
        offering = [m for m, paths in per_map.items() if path in paths]
        if not offering:
            print(f"  {path:34s} not offered by any sitemap")
            continue
        covered = any(any(m.endswith(n) for n in AGENT2_SCOPE) for m in offering)
        if not covered:
            blind.append(path)
        print(f"  {path:34s} offered by {','.join(offering)}   "
              f"{'checked' if covered else '<<< SUBMITTED AND UNCHECKED'}")
    print(f"\n  A12-05 pages that are submitted but outside Agent 2's scope: {len(blind)}")

    print()
    print("=" * 94)
    print("AGENT 2's OWN FAULT CLASSES, RUN OVER THE FULL OFFER SET")
    print("=" * 94)

    # Agent 2's `inspect()` fetches over the network via urllib. There is no
    # server here, so reuse its *logic* -- the fault classes and their names --
    # against the test client. Anything else would be my checks, not its checks.
    faults = {}
    for path in sorted(total):
        r = client.get(path, follow_redirects=False)
        ctype = (r.headers.get("Content-Type") or "").lower()
        body = r.get_data(as_text=True) if "html" in ctype else ""
        robots = re.search(r"""<meta\s[^>]*name=['"]robots['"][^>]*content=['"]([^'"]*)['"]""",
                           body, re.I)
        robots = robots.group(1).strip() if robots else None
        x_robots = r.headers.get("X-Robots-Tag")
        # Both orderings, because Agent 2's script carries a reverse pattern too
        # and its comment records why: `bot.py` writes a canonical with `href`
        # first in at least one place. Copying only the forward pattern would
        # report NO_CANONICAL on a page that has one -- a false positive in the
        # direction that makes my own finding look bigger.
        canon = (re.search(r"""<link\s[^>]*rel=['"]canonical['"][^>]*href=['"]([^'"]+)['"]""",
                           body, re.I)
                 or re.search(r"""<link\s[^>]*href=['"]([^'"]+)['"][^>]*rel=['"]canonical['"]""",
                              body, re.I))
        canon = canon.group(1).strip() if canon else None

        row = []
        if r.status_code != 200:
            row.append(f"STATUS:{r.status_code}")
        else:
            if robots is None and x_robots is None:
                row.append("NO_ROBOTS_DIRECTIVE")
            for directive in (robots, x_robots):
                if directive and "noindex" in directive.lower():
                    row.append(f"NOINDEX:{directive}")
            if canon is None:
                row.append("NO_CANONICAL")
            elif canon.rstrip("/") != (search_visibility.CANONICAL_ORIGIN + path).rstrip("/"):
                row.append(f"CANONICAL_MISMATCH:{canon}")
        if row:
            faults[path] = row

    scoped = {p: f for p, f in faults.items() if p in in_scope}
    unscoped = {p: f for p, f in faults.items() if p not in in_scope}
    print(f"  faults inside Agent 2's scope  : {len(scoped)}   "
          f"(this is the 0 it reports)")
    print(f"  faults it never looks at       : {len(unscoped)}")
    for path, row in sorted(unscoped.items()):
        print(f"      {path:34s} {';'.join(row)}")

    print()
    print("=" * 94)
    print(f"VERDICT: widening the scope alone takes Agent 2's own fault count "
          f"from {len(scoped)} to {len(faults)}.")
    print("=" * 94)


if __name__ == "__main__":
    main()
