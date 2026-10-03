"""Do the two canonical builders agree, and does a category page keep its own URL?

Invariant #7 in the matrix. There are two places that construct a canonical:

    services/search_visibility.py:560      canonical_url(path)  -- page-blind
    services/marketplace_storefront.py     render_discovery()   -- page-aware

The first strips any query param outside `_CONTENT_QUERY_PARAMS`, which for
`/pulse/marketplace` is `("category",)` -- so it drops `page` and keeps
`category`. The second computes a clamped `canonical_page` and spells it back
into the canonical. Read statically they disagree for `?page=2`.

A first attempt to measure this was inconclusive and would have been published
as "they agree": the scratch DB had 16 publishable listings against a PAGE_SIZE
of 24, so page 2 did not exist and the storefront clamped `canonical_page` to 1.
A page-less canonical proved nothing. This probe therefore asserts the corpus
spans more than one page BEFORE believing any agreement, which is the same
non-empty-corpus discipline the gates use.

`category` is the second axis and the more interesting one. A whitelisted
content param means `/pulse/marketplace?category=home` is supposed to be its own
indexable document. If the page's own canonical drops it, every category
landing page folds onto the bare collection and the whole facet strategy is
silently void -- while `search_visibility` keeps saying they are distinct.
`bogus-not-a-category` is the control: an unknown facet SHOULD collapse, so if
the known and unknown categories behave identically the probe is measuring
nothing.
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
from services import marketplace_web as mw  # noqa: E402
from services import search_visibility  # noqa: E402

logging.disable(logging.CRITICAL)

RE_CANON = re.compile(r"""<link[^>]+rel=['"]canonical['"][^>]*>""", re.I)
RE_HREF = re.compile(r"""href=['"]([^'"]+)['"]""", re.I)

CASES = (
    "/pulse/marketplace",
    "/pulse/marketplace?page=2",
    "/pulse/marketplace?page=999",
    "/pulse/marketplace?category=home",
    "/pulse/marketplace?page=2&category=home",
    "/pulse/marketplace?category=bogus-not-a-category",
)


def main():
    app = bot.webhook_app
    app.config["TESTING"] = True
    client = app.test_client()

    publishable = len(list(bot.marketplace_public_listings(500)))
    pages = (publishable + mw.PAGE_SIZE - 1) // mw.PAGE_SIZE
    print("=" * 96)
    print("DO THE TWO CANONICAL BUILDERS AGREE?")
    print("=" * 96)
    print(f"  publishable listings {publishable}   PAGE_SIZE {mw.PAGE_SIZE}   "
          f"=> {pages} page(s)")
    if pages < 2:
        print("  REFUSING TO REPORT: with one page, `?page=2` clamps to 1 and a "
              "page-less canonical\n  proves nothing. Seed past the boundary first.")
        return
    print()

    disagreements = []
    for query in CASES:
        resp = client.get(query)
        match = RE_CANON.search(resp.get_data(as_text=True))
        served = RE_HREF.search(match.group(0)).group(1) if match else None
        policy = search_visibility.canonical_url(query)
        agree = served == policy
        if not agree:
            disagreements.append((query, served, policy))
        print(f"  {query}")
        print(f"      page serves       {served}")
        print(f"      search_visibility {policy}")
        print(f"      {'agree' if agree else '<<< DISAGREE'}   "
              f"sitemap_eligible={search_visibility.sitemap_eligible(query)}")

    print()
    print("=" * 96)
    print(f"  DISAGREEMENTS: {len(disagreements)}")
    for query, served, policy in disagreements:
        print(f"      {query}\n          page   {served}\n          policy {policy}")
    print("=" * 96)
    print("  Control: `bogus-not-a-category` SHOULD collapse to the bare collection.")
    print("  If `category=home` collapses the same way, category pages have no URL")
    print("  of their own and the facet strategy is void. If only the bogus one")
    print("  collapses, the taxonomy allowlist is working as designed.")


if __name__ == "__main__":
    main()
