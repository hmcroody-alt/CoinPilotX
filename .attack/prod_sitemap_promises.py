"""Invariants 3 and 9, measured against production instead of a dev copy.

I published "206/206 offered URLs answer 200, 0 submitted-but-noindex" from the
local database. Production disagrees about the corpus: it offers **148** URLs,
not 206, and its catalogue is 196 published listings against the copy's 16. A
number measured on an unrepresentative corpus is not wrong so much as it is
about a different property, and the listing-110 mistake came from exactly that
gap -- so the two invariants that matter most get re-measured here against the
real thing.

    invariant 3  no sitemap <loc> resolves to a noindex 200
    invariant 9  every <loc> answers 200 to an anonymous crawler, no redirect

Anonymous GETs, no cookie, no session -- the only client whose opinion counts.
Children are followed from `/sitemap.xml` rather than hardcoded, because a gate
that cannot see the whole set it certifies is A12-09 and I am not repeating it.

The detectors need controls or a clean sweep means nothing, so two paths known
to fail each one are fetched alongside: `/login` serves `noindex` (the noindex
detector fires) and a stock-retired product id answers 404 (the unreachable
detector fires). Neither is offered.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

BASE = "https://pulsesoc.com"
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
RE_ROBOTS = re.compile(
    r"""<meta[^>]+name=['"]robots['"][^>]*content=['"]([^'"]*)['"]""", re.I)
RE_CANON = re.compile(r"""<link[^>]+rel=['"]canonical['"][^>]*href=['"]([^'"]+)['"]""", re.I)

#: Known to serve noindex, and known to 404. Not offered; they exist to prove
#: the detectors below can fire at all.
CONTROL_NOINDEX = "/login"
CONTROL_GONE = "/pulse/marketplace/16"


def fetch(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; Agent12-readonly-probe)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.geturl(), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, url, ""
    except Exception as exc:
        return 0, url, f"({type(exc).__name__})"


def locs(xml_text):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    return [el.text.strip() for el in root.findall(".//s:loc", NS) if el is not None and el.text]


def directive(body):
    match = RE_ROBOTS.search(body)
    return match.group(1).strip().lower() if match else None


def main():
    status, _final, body = fetch(f"{BASE}/sitemap.xml")
    children = locs(body)
    print("=" * 96)
    print("INVARIANTS 3 AND 9, AGAINST PRODUCTION")
    print("=" * 96)
    print(f"  /sitemap.xml HTTP {status}, {len(children)} children (followed, not hardcoded)")

    offered = []
    for child in children:
        cstatus, _f, cbody = fetch(child)
        child_locs = locs(cbody)
        offered.extend(child_locs)
        print(f"      {child.rsplit('/', 1)[-1]:28s} HTTP {cstatus}  locs={len(child_locs)}")

    offered = sorted(set(offered))
    if not offered:
        print("  REFUSING TO REPORT: zero URLs offered. A clean sweep over an empty")
        print("  corpus is the failure mode this probe exists to avoid.")
        return

    print("\n  controls:")
    for path, expect in ((CONTROL_NOINDEX, "noindex"), (CONTROL_GONE, "404")):
        code, _f, cbody = fetch(BASE + path)
        print(f"      {path:28s} HTTP {code}  robots={directive(cbody)}   (expect {expect})")

    print(f"\n  fetching {len(offered)} offered URLs anonymously...")
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(fetch, offered))

    codes = {}
    noindexed = []
    redirected = []
    canonical_away = []
    for url, (code, final, text) in zip(offered, results):
        codes[code] = codes.get(code, 0) + 1
        if code != 200:
            continue
        if final.rstrip("/") != url.rstrip("/"):
            redirected.append((url, final))
        d = directive(text)
        if d and "noindex" in d:
            noindexed.append((url, d))
        canon = RE_CANON.search(text)
        if canon and canon.group(1).rstrip("/") != url.rstrip("/"):
            canonical_away.append((url, canon.group(1)))

    print(f"\n  status distribution      {dict(sorted(codes.items()))}")
    print(f"  INVARIANT 9  non-200     {sum(n for c, n in codes.items() if c != 200)}")
    print(f"  INVARIANT 9  redirected  {len(redirected)}")
    print(f"  INVARIANT 3  noindex 200 {len(noindexed)}")
    print(f"  canonical points away    {len(canonical_away)}")
    for url, got in (noindexed + redirected + canonical_away)[:20]:
        print(f"      {url}  ->  {got}")

    print()
    print("=" * 96)
    print(f"  Production offers {len(offered)} URLs. My earlier 206 was the dev copy and")
    print("  describes a catalogue production does not have.")


if __name__ == "__main__":
    main()
