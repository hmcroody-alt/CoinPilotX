"""Can I mint an unbounded number of indexable marketplace pages?

This is the facet-explosion attack, and `/pulse/marketplace` is the only place
on the site with the two ingredients for it: a query parameter the canonical
policy treats as *content* (`_CONTENT_QUERY_PARAMS` names `category`), and a
`page` parameter owned by a second canonical authority
(`marketplace_storefront.RenderedPage.canonical_path` keeps a clamped `page`
where `search_visibility.canonical_url` drops it).

A content parameter is a promise that each distinct value is a distinct
document worth ranking. That promise is only safe if the value space is
*closed*. If an arbitrary string is accepted, answers 200, declares itself
indexable, and canonicalises to itself, then every junk string is a new
competing copy of the catalogue — and the crawler will find them, because
crawlers try things.

So, three questions per variant, the same three the URL-shape probe asked:

  - does it answer 200?
  - does it declare itself indexable?
  - what does its canonical say — itself, or the one true URL?

The dangerous answer is 200 + indexable + self-canonical on a value nobody
published. The safe answers are: 404, or noindex, or a canonical that points
back at the unfiltered index.

Pagination is the same question in a different parameter. `page=99999` on a
catalogue of 56 rows either clamps (and should canonicalise to the real last
page), or renders an empty grid (which is a thin page asking to be ranked).

Runs against `.attack/scratch.db`, which holds 56 listings across ~15 real
supplier categories. That matters: A12-06 established this page picks its
robots directive from the row count, so an empty database would make every
variant below look safely `noindex` for a reason that has nothing to do with
facets.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(ROOT, ".attack", "scratch.db"))

import bot  # noqa: E402
from services import search_visibility  # noqa: E402

logging.disable(logging.CRITICAL)

INDEX = "/pulse/marketplace"

RE_ROBOTS = re.compile(r"""<meta[^>]+name=['"]robots['"][^>]*>""", re.I)
RE_CANON = re.compile(r"""<link[^>]+rel=['"]canonical['"][^>]*>""", re.I)
RE_CONTENT = re.compile(r"""content=['"]([^'"]*)['"]""", re.I)
RE_HREF = re.compile(r"""href=['"]([^'"]*)['"]""", re.I)
#: `marketplace_seo.PRODUCT_PATH` -- /pulse/marketplace/{id}. Guessing
#: /pulse/product/ first made every row report zero products while the control
#: was still declaring itself indexable, which is the contradiction that caught
#: it. A row count is the whole severity argument here, so it has to be read
#: from the path the renderer actually writes.
RE_PRODUCT_LINK = re.compile(r"""href=['"](/pulse/marketplace/\d+)['"]""", re.I)
RE_NEXT_PREV = re.compile(r"""<link[^>]+rel=['"](next|prev)['"][^>]*>""", re.I)

#: Real department *slugs*, read from the hub's own category nav rather than
#: from the `category` column. The first run of this probe passed the raw label
#: ("Beauty") and every real facet came back `noindex` -- which looked like a
#: clean pass and was actually the probe measuring nothing: an unmatched label
#: filters to zero rows, and zero rows is its own noindex condition. The facets
#: the site really publishes are all indexable and self-canonical. A probe that
#: cannot tell "closed" from "I asked the wrong question" is worse than no
#: probe, so these come from `marketplace_seo.category_path`.
REAL_SLUGS = ("womens-clothing", "jewelry-watches", "mens-clothing", "home")

#: Values that must NOT mint an indexable page. `known_category` is checked
#: against the built taxonomy, so these are refused by policy rather than by
#: happening to match no rows.
JUNK_SLUGS = ("aaaaaaaa-spam-doorway-1", "aaaaaaaa-spam-doorway-2",
              "cheap-viagra-buy-now", "WOMENS-CLOTHING")


def read(client, url):
    r = client.get(url, follow_redirects=False)
    ctype = (r.headers.get("Content-Type") or "").lower()
    html = r.get_data(as_text=True) if "html" in ctype else ""
    tag = RE_ROBOTS.search(html)
    directive = RE_CONTENT.search(tag.group(0)).group(1) if tag else None
    ctag = RE_CANON.search(html)
    canon = RE_HREF.search(ctag.group(0)).group(1) if ctag else None
    return {
        "status": r.status_code,
        "directive": directive,
        "canonical": canon,
        "location": r.headers.get("Location"),
        "products": len(set(RE_PRODUCT_LINK.findall(html))),
        "relnext": RE_NEXT_PREV.findall(html),
        "bytes": len(html),
    }


def indexable(directive):
    """Absence reads as index,follow -- the same rule the robots gate applies."""

    return directive is None or "noindex" not in directive.lower()


def variants():
    yield "unfiltered (control)", INDEX
    for slug in REAL_SLUGS:
        yield f"real: {slug}", f"{INDEX}?category={slug}"
    for slug in JUNK_SLUGS:
        yield f"junk: {slug[:18]}", f"{INDEX}?category={slug}"
    yield "empty category", f"{INDEX}?category="
    yield "repeated category", f"{INDEX}?category={REAL_SLUGS[0]}&category=home"
    yield "page=1", f"{INDEX}?page=1"
    yield "page=2", f"{INDEX}?page=2"
    yield "page=99999", f"{INDEX}?page=99999"
    yield "page=0", f"{INDEX}?page=0"
    yield "page=-1", f"{INDEX}?page=-1"
    yield "page=abc", f"{INDEX}?page=abc"
    yield "page float", f"{INDEX}?page=2.5"
    yield "real cat + page=2", f"{INDEX}?category={REAL_SLUGS[0]}&page=2"
    yield "junk cat + page=7", f"{INDEX}?category=aaaaaaaa-spam&page=7"
    # Search and sort. A search-results URL is unbounded and user-generated, so
    # an indexable one is the textbook doorway; a sort permutation is the same
    # product set in another order and must not be a second document.
    yield "q= search", f"{INDEX}?q={quote('gold ring')}"
    yield "q= injected kw", f"{INDEX}?q={quote('buy cheap viagra online')}"
    yield "q= empty", f"{INDEX}?q="
    yield "sort=newest", f"{INDEX}?sort=newest"
    yield "sort junk", f"{INDEX}?sort=aaaa-spam"
    yield "real cat + sort", f"{INDEX}?category={REAL_SLUGS[0]}&sort=newest"
    yield "q + category", f"{INDEX}?q={quote('ring')}&category={REAL_SLUGS[1]}"


def main():
    app = bot.webhook_app
    app.config["TESTING"] = True
    client = app.test_client()

    policy_canonical = search_visibility.canonical_url(INDEX)
    print("=" * 96)
    print(f"FACET AND PAGINATION EXPLOSION -- {INDEX}")
    print(f"search_visibility.canonical_url({INDEX!r}) = {policy_canonical}")
    print("=" * 96)

    control = read(client, INDEX)
    print(f"  control: status={control['status']} products={control['products']} "
          f"directive={control['directive']!r}")
    print(f"           canonical={control['canonical']}")
    print()

    # A real department being indexable and self-canonical is the *intended*
    # design, documented at `marketplace_seo.CATEGORY_MIN_INDEXABLE_LISTINGS`:
    # all departments serve `index,follow` and the sitemap then submits only
    # those with three or more indexable listings. So "indexable + self
    # canonical" is only a doorway when nobody published the value.
    doorways = []
    for label, url in variants():
        got = read(client, url)
        # Self-canonical means the canonical names *this* URL, query and all.
        # A substring test is not good enough and produced two false positives:
        # `?category=a&category=b` canonicalising to `?category=a` is the
        # correct answer for a duplicate, and an `in` check calls it
        # self-canonical because the first segment is present. Full equality is
        # the only form that distinguishes "declares itself the document" from
        # "points at the one real document".
        self_canonical = got["canonical"] == search_visibility.CANONICAL_ORIGIN + url
        points_home = got["canonical"] == control["canonical"]
        legitimate = label.startswith("real") or label == "unfiltered (control)"

        verdict = ""
        if got["status"] == 200 and label != "unfiltered (control)":
            if not indexable(got["directive"]):
                verdict = "noindex - safe"
            elif points_home:
                verdict = "canonical -> hub - safe"
            elif self_canonical and legitimate:
                verdict = "indexable + self-canonical - INTENDED (real department)"
            elif self_canonical:
                verdict = "SELF-CANONICAL + INDEXABLE <<< DOORWAY"
                doorways.append((label, url, got))
            else:
                verdict = f"indexable, canonical={got['canonical']}"
        elif got["status"] != 200:
            verdict = f"-> {got['location']}" if got["location"] else "not 200"

        print(f"  {label:24s} {str(got['status']):4s} prod={got['products']:<3d} "
              f"idx={'Y' if indexable(got['directive']) else 'N'}  {verdict}")
        print(f"  {'':24s}      canonical={got['canonical']}")
        if got["relnext"]:
            print(f"  {'':24s}      rel={got['relnext']}")

    print()
    print("=" * 96)
    print(f"INDEXABLE SELF-CANONICAL DOORWAYS: {len(doorways)}")
    for label, url, got in doorways:
        print(f"  {url}\n      {label}: {got['products']} products, "
              f"{got['bytes']} bytes, directive {got['directive']!r}")
    print("=" * 96)

    # Reachability decides severity, exactly as it did for the double-slash
    # finding: a doorway nothing links to and no sitemap lists still has to be
    # guessed, and a doorway we publish ourselves is already being crawled.
    print()
    print("REACHABILITY -- do we publish facet or page URLs ourselves?")
    for src in ("/sitemap.xml", "/sitemap-marketplace.xml", "/robots.txt", INDEX):
        r = client.get(src)
        body = r.get_data(as_text=True) if r.status_code == 200 else ""
        cats = len(re.findall(r"[?&]category=", body))
        pages = len(re.findall(r"[?&]page=", body))
        print(f"  {src:32s} {r.status_code}  ?category= x{cats}  ?page= x{pages}")


if __name__ == "__main__":
    main()
