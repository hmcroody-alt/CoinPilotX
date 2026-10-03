"""What does production actually serve for the listings Agent 1 named?

Anonymous GETs against pulsesoc.com. No session, no cookie, no writes -- the
only client whose opinion matters for an indexability claim.

This exists because my own dev-DB reading was wrong about listing 110. I
reported it "unverifiable as stated -- answers 404", and production says
`status=published`: the local copy holds 16 publishable rows against
production's 196, so the 404 was my corpus, not the property. Agent 1 measured
something real and I called it unverifiable from a partial database. The lesson
is narrow and worth stating: *a claim about a row cannot be refuted by a
database that does not contain the row.*

So: 110 against 50, 52 and 77 -- the three I did confirm locally -- plus 35 and
36, which are `index,follow` locally and are the control. If every production
page came back `noindex` the probe would be measuring a site-wide directive
rather than a per-listing decision; if every one came back `index` the detector
is not firing at all.
"""

from __future__ import annotations

import re
import urllib.request

BASE = "https://pulsesoc.com"
IDS = (50, 52, 77, 110, 35, 36)

RE_ROBOTS = re.compile(
    r"""<meta[^>]+name=['"]robots['"][^>]*content=['"]([^'"]*)['"]""", re.I)
RE_CANON = re.compile(r"""<link[^>]+rel=['"]canonical['"][^>]*href=['"]([^'"]+)['"]""", re.I)


def fetch(path):
    req = urllib.request.Request(
        BASE + path,
        headers={"User-Agent": "Mozilla/5.0 (compatible; Agent12-readonly-probe)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.status, resp.geturl(), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, BASE + path, ""


def main():
    print("=" * 96)
    print("WHAT PRODUCTION SERVES FOR THE LISTINGS AGENT 1 NAMED")
    print("=" * 96)

    seen = {}
    for listing_id in IDS:
        path = f"/pulse/marketplace/{listing_id}"
        status, final, body = fetch(path)
        robots = RE_ROBOTS.search(body)
        canon = RE_CANON.search(body)
        directive = robots.group(1).strip().lower() if robots else "(NO DIRECTIVE)"
        seen[listing_id] = (status, directive)
        print(f"  {path}")
        print(f"      HTTP {status}" + ("" if final.endswith(path) else f"  -> {final}"))
        print(f"      robots    {directive}")
        print(f"      canonical {canon.group(1) if canon else '(NO CANONICAL)'}")

    print()
    print("=" * 96)
    directives = {d for _s, d in seen.values()}
    noindexed = sorted(i for i, (_s, d) in seen.items() if "noindex" in d)
    print(f"  distinct directives seen: {sorted(directives)}")
    print(f"  noindex: {noindexed}")
    if len(directives) < 2:
        print("  REFUSING TO CONCLUDE: one directive across every listing means this is")
        print("  reading a site-wide value, not a per-listing decision.")
        return
    print("  A per-listing split is what makes the eligibility engine's verdict")
    print("  observable from outside. 110 belongs in that split or Agent 1's claim")
    print("  is wrong on the wire rather than wrong about the row.")


if __name__ == "__main__":
    main()
