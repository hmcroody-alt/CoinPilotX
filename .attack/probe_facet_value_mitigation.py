"""Is the unbounded `?category=` value space actually mitigated at the page?

`_CONTENT_QUERY_PARAMS` whitelists a parameter *name* and never looks at the
*value*, so `search_visibility` answers `sitemap_eligible=True` for every
arbitrary `?category=` string -- `spam-casino-viagra`, a 200-character slug,
`../../etc/passwd` -- and echoes each one into a distinct canonical. On its own
that is an infinite set of self-declared-indexable URLs.

I was ready to publish that as a finding. `canonical_url`'s own docstring got
there first, and names the mitigation:

    "it has no catalogue access, so it cannot tell a real department from an
    invented `?category=` value and will hand back a canonical for either. The
    page is what resolves that -- an unknown slug renders `noindex,follow` and
    canonicalises to the bare hub, so nothing a crawler reaches is affected.
    The callers that *submit* URLs read the live taxonomy first."

So the question is not "is the policy layer value-blind" -- it is, by design and
in writing. The question is whether the three things that sentence promises are
true, because the whole facet strategy rests on them:

    1. an unknown slug serves `noindex` from the live route
    2. an unknown slug canonicalises to the bare hub
    3. the submitters derive slugs from the live taxonomy, never from input

(3) is settled by reading `marketplace_seo.category_entries`: it builds the
slug set from `build_taxonomy([...])` over the public catalogue and submits only
`taxonomy` members, so no arbitrary string has a path into a sitemap. (2) is
measured and held in `probe_canonical_page_and_category.py`. (1) is the one
nobody has measured, and it is the load-bearing one: if an unknown slug serves
`index,follow`, then a single inbound link to
`?category=<whatever>` mints an indexable thin page, the policy layer agrees
it is eligible, and the documented mitigation is fiction.

THE CONTROL IS A KNOWN-GOOD SLUG. `home` must serve `index,follow` with its own
canonical. If the known and unknown slugs behave identically in either
direction this probe is measuring nothing -- either the page noindexes every
department (and the facet strategy is void) or it indexes every string (and the
mitigation is absent).
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
from services import marketplace_seo  # noqa: E402
from services import marketplace_web as mw  # noqa: E402
from services import search_visibility  # noqa: E402

logging.disable(logging.CRITICAL)

RE_ROBOTS = re.compile(
    r"""<meta[^>]+name=['"]robots['"][^>]*content=['"]([^'"]*)['"]""", re.I)
RE_CANON = re.compile(r"""<link[^>]+rel=['"]canonical['"][^>]*href=['"]([^'"]+)['"]""", re.I)

#: The control. Must exist in the catalogue or the probe cannot distinguish
#: "unknown slugs are noindexed" from "every slug is noindexed".
KNOWN = "home"

HOSTILE = (
    "bogus-not-a-category",
    "spam-casino-viagra",
    "x" * 200,
    "1",
    "../../etc/passwd",
    "a b",
    "<script>alert(1)</script>",
)


def main():
    app = bot.webhook_app
    app.config["TESTING"] = True
    client = app.test_client()

    pairs = list(bot.marketplace_public_listings(500))
    taxonomy = mw.build_taxonomy([listing.get("category") for _row, listing in pairs])
    slugs = {node.slug for node in taxonomy}

    print("=" * 96)
    print("IS THE UNBOUNDED ?category= VALUE SPACE MITIGATED AT THE PAGE?")
    print("=" * 96)
    print(f"  public listings {len(pairs)}   live taxonomy slugs {sorted(slugs)}")
    if KNOWN not in slugs:
        print(f"  REFUSING TO REPORT: control slug '{KNOWN}' is not in the live taxonomy, so a")
        print("  `noindex` on an invented slug would prove nothing -- every slug would be one.")
        return
    print()

    def read(slug):
        path = marketplace_seo.category_path(slug)
        body = client.get(path).get_data(as_text=True)
        robots = RE_ROBOTS.search(body)
        canon = RE_CANON.search(body)
        return (path,
                robots.group(1).strip().lower() if robots else "(NO DIRECTIVE)",
                canon.group(1) if canon else "(NO CANONICAL)")

    bare = search_visibility.CANONICAL_ORIGIN + "/pulse/marketplace"

    path, robots, canon = read(KNOWN)
    print(f"  CONTROL  category={KNOWN}")
    print(f"      serves    robots={robots}")
    print(f"      canonical {canon}")
    control_ok = "noindex" not in robots and canon != bare
    print(f"      {'own indexable document -- control is live' if control_ok else '<<< CONTROL FAILED'}")
    print()

    unmitigated = []
    for slug in HOSTILE:
        path, robots, canon = read(slug)
        eligible = search_visibility.sitemap_eligible(path)
        policy = search_visibility.canonical_url(path)
        collapses = canon == bare
        noindexed = "noindex" in robots
        label = ("mitigated" if (noindexed and collapses)
                 else "PARTIAL" if (noindexed or collapses) else "<<< UNMITIGATED")
        if not (noindexed and collapses):
            unmitigated.append((slug, robots, canon))
        shown = slug if len(slug) <= 40 else slug[:37] + "..."
        print(f"  category={shown}")
        print(f"      page robots      {robots}")
        print(f"      page canonical   {canon}")
        print(f"      policy canonical {policy}")
        print(f"      policy sitemap_eligible={eligible}   {label}")

    print()
    print("=" * 96)
    print(f"  policy layer calls every one of these eligible: "
          f"{all(search_visibility.sitemap_eligible(marketplace_seo.category_path(s)) for s in HOSTILE)}")
    print(f"  page layer leaves unmitigated: {len(unmitigated)} of {len(HOSTILE)}")
    for slug, robots, canon in unmitigated:
        print(f"      {slug[:60]}  robots={robots}  canonical={canon}")
    print()
    print("  A mitigated row is `noindex` AND a collapse to the bare hub -- either alone")
    print("  leaves a crawlable thin page or a self-declared duplicate. If the CONTROL")
    print("  also noindexes, the facet strategy is void and that is the finding instead.")


if __name__ == "__main__":
    main()
