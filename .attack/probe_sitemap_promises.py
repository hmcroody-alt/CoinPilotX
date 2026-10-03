"""Is every URL we *submit* to Google actually readable by an anonymous crawler?

A sitemap is not a description, it is a request: each `<loc>` asks Google to
spend crawl budget on that URL. Three ways that request can be a lie, and all
three are things Search Console reports as errors against the whole property:

  - the URL does not answer 200 anonymously  ("Submitted URL has crawl issue",
    or a 302 to /login, which is the one I expect here because memory says the
    social entity routes do exactly that);
  - the URL answers 200 and serves `noindex`  ("Submitted URL marked noindex");
  - the URL answers 200 and canonicalises somewhere else  ("Alternate page with
    proper canonical tag" -- the submitted URL is then pure waste).

This is the inverse of the gate I already landed. That gate walks the *route
table* and asks "does any page declining indexing get offered to search
engines". This walks the *offer* and asks "does every offer resolve". A path can
satisfy the first and fail this one, because the sitemaps are generated from
database rows, not from `app.url_map` -- the product, post, live and replay
sitemaps all interpolate ids the route walker never sees.

Anonymous on purpose: no session, no cookie. That is the only client whose
opinion matters here, and it is the client every other probe in this directory
has also used, so a 302 to a login wall shows up as a 302 rather than as a page.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(ROOT, ".attack", "scratch.db"))

import bot  # noqa: E402
from services import search_visibility  # noqa: E402

logging.disable(logging.CRITICAL)

RE_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
RE_ROBOTS = re.compile(r"""<meta[^>]+name=['"]robots['"][^>]*>""", re.I)
RE_CANON = re.compile(r"""<link[^>]+rel=['"]canonical['"][^>]*>""", re.I)
RE_CONTENT = re.compile(r"""content=['"]([^'"]*)['"]""", re.I)
RE_HREF = re.compile(r"""href=['"]([^'"]*)['"]""", re.I)

SITEMAP_ROUTES = ("/sitemap.xml", "/sitemap-pages.xml", "/sitemap-posts.xml",
                  "/sitemap-categories.xml", "/sitemap-products.xml",
                  "/sitemap-live.xml", "/sitemap-replays.xml")


def path_of(url):
    return re.sub(r"^https?://[^/]+", "", url) or "/"


def collect(client):
    """Every `<loc>` the property offers, credited to the sitemap offering it.

    Follows the index's children rather than trusting the hardcoded list, so a
    sitemap added later is still covered; the hardcoded list is only the seed.
    """

    seen_maps, queue, offers = set(), list(SITEMAP_ROUTES), {}
    while queue:
        route = queue.pop(0)
        if route in seen_maps:
            continue
        seen_maps.add(route)
        r = client.get(route)
        if r.status_code != 200:
            print(f"  !! sitemap {route} -> HTTP {r.status_code}")
            continue
        body = r.get_data(as_text=True)
        is_index = "<sitemapindex" in body.lower()
        for loc in RE_LOC.findall(body):
            p = path_of(loc)
            if is_index:
                queue.append(p)
            else:
                offers.setdefault(p, set()).add(route)
    return offers, seen_maps


def main():
    app = bot.webhook_app
    app.config["TESTING"] = True
    client = app.test_client()

    print("=" * 100)
    print("DOES EVERY SUBMITTED URL RESOLVE FOR AN ANONYMOUS CRAWLER?")
    print("=" * 100)

    offers, maps = collect(client)
    print(f"  sitemaps reachable: {len(maps)}   distinct URLs offered: {len(offers)}\n")

    unreadable, noindexed, mis_canonical = [], [], []
    by_status = Counter()

    for path in sorted(offers):
        src = ",".join(sorted(s.replace("/sitemap", "").replace(".xml", "") or "index"
                              for s in offers[path]))
        r = client.get(path, follow_redirects=False)
        by_status[r.status_code] += 1
        ctype = (r.headers.get("Content-Type") or "").lower()
        html = r.get_data(as_text=True) if "html" in ctype else ""

        tag = RE_ROBOTS.search(html)
        directive = RE_CONTENT.search(tag.group(0)).group(1) if tag else None
        serves_noindex = bool(directive and "noindex" in directive.lower())
        ctag = RE_CANON.search(html)
        canon = RE_HREF.search(ctag.group(0)).group(1) if ctag else None

        note = ""
        if r.status_code != 200:
            dest = r.headers.get("Location") or ""
            unreadable.append((path, r.status_code, dest, src))
            note = f"<<< UNREADABLE -> {dest or 'no Location'}"
        elif serves_noindex:
            noindexed.append((path, directive, src))
            note = f"<<< SUBMITTED BUT noindex ({directive})"
        elif canon and canon != search_visibility.CANONICAL_ORIGIN + path:
            mis_canonical.append((path, canon, src))
            note = f"<<< CANONICAL ELSEWHERE -> {canon}"
        elif canon is None and html:
            note = "(200, no canonical)"

        print(f"  {path:52s} {r.status_code}  [{src:22s}] {note}")

    print()
    print("=" * 100)
    print(f"  status distribution: {dict(by_status)}")
    print(f"  UNREADABLE ANONYMOUSLY : {len(unreadable)}")
    for path, code, dest, src in unreadable:
        print(f"      {path}  HTTP {code} -> {dest}   (offered by {src})")
    print(f"  SUBMITTED BUT noindex  : {len(noindexed)}")
    for path, directive, src in noindexed:
        print(f"      {path}  {directive}   (offered by {src})")
    print(f"  CANONICAL POINTS AWAY  : {len(mis_canonical)}")
    for path, canon, src in mis_canonical:
        print(f"      {path} -> {canon}   (offered by {src})")
    print("=" * 100)

    # Cross-check against the policy authority. A URL the policy itself would
    # refuse to submit, appearing in a sitemap, is a second and independent
    # fault: the generator did not ask.
    print()
    print("DOES THE GENERATOR CONSULT THE POLICY?")
    refused = [p for p in sorted(offers) if not search_visibility.sitemap_eligible(p)]
    print(f"  offered URLs that `search_visibility.sitemap_eligible` rejects: {len(refused)}")
    for p in refused[:20]:
        print(f"      {p}   classify={search_visibility.classify(p)}")


if __name__ == "__main__":
    main()
