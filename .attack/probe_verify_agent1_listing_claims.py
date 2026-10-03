"""Re-measure Agent 1's claim that listings 50 / 52 / 110 render `noindex`.

Agent 1 asserted it with no captured wire evidence. Its claim about
`/pulse/cart` was asserted the same way and turned out to be true but
*understated* (A12-01: the page is stricter than policy, which Agent 1 did not
notice), so an unproven claim from that report is worth measuring rather than
either trusting or dismissing.

Checks three things per listing, because a product page's indexability has
three authorities that must agree and only the first is what Agent 1 looked at:

  - the robots directive the page actually serves;
  - `marketplace_seo.eligibility(listing).indexable`, the record-level verdict;
  - whether `/sitemap-products.xml` offers the URL.

A row that serves `noindex` while the sitemap offers it is the A12-06 defect
class at row level. A row that serves `index` while `eligibility` says
otherwise is the inverse and worse. Printing all three next to each other is
the only way to tell "Agent 1 was right" from "Agent 1 was right about the
directive and there is a second disagreement underneath it".

Walks the whole publishable set rather than only the three named listings. A
claim about three rows is not interesting; whether the three are *the* three is.
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

logging.disable(logging.CRITICAL)

RE_ROBOTS = re.compile(r"""<meta[^>]+name=['"]robots['"][^>]*>""", re.I)
RE_CONTENT = re.compile(r"""content=['"]([^'"]*)['"]""", re.I)

CLAIMED_NOINDEX = (50, 52, 110)


def main():
    app = bot.webhook_app
    app.config["TESTING"] = True
    client = app.test_client()

    publishable = {int(l["id"]): l for _row, l in bot.marketplace_public_listings(500)}
    offered = set(re.findall(r"/pulse/marketplace/(\d+)",
                             client.get("/sitemap-products.xml").get_data(as_text=True)))

    print("=" * 94)
    print("AGENT 1's CLAIM: listings 50 / 52 / 110 render noindex")
    print("=" * 94)
    print(f"{'lid':>5s} {'http':>4s} {'served':16s} {'in_sitemap':10s} "
          f"{'eligible':9s} reason")

    disagreements = []
    confirmed = []
    for lid in sorted(set(publishable) | set(CLAIMED_NOINDEX)):
        resp = client.get(f"/pulse/marketplace/{lid}")
        html = resp.get_data(as_text=True)
        tag = RE_ROBOTS.search(html)
        directive = RE_CONTENT.search(tag.group(0)).group(1) if tag else None
        serves_noindex = bool(directive and "noindex" in directive.lower())
        served = "noindex" if serves_noindex else ("index" if directive else "NONE")

        listing = publishable.get(lid)
        verdict = marketplace_seo.eligibility(listing) if listing else None
        in_sitemap = str(lid) in offered

        # The two cross-authority faults worth failing over.
        if verdict is not None:
            if serves_noindex and in_sitemap:
                disagreements.append((lid, "serves noindex but the sitemap offers it"))
            if (not serves_noindex) and resp.status_code == 200 and not verdict.indexable:
                disagreements.append((lid, "serves index but eligibility says not indexable"))
        if lid in CLAIMED_NOINDEX:
            confirmed.append((lid, resp.status_code, served))

        print(f"{lid:5d} {resp.status_code:4d} {served:16s} {str(in_sitemap):10s} "
              f"{(verdict.indexable if verdict else '-')!s:9s} "
              f"{verdict.reason if verdict else 'not publishable (no row)'}")

    print()
    print("CLAIM VERDICT")
    for lid, status, served in confirmed:
        if status != 200:
            note = f"HTTP {status} — not a 200-with-noindex; claim unverifiable here"
        elif served == "noindex":
            note = "CONFIRMED"
        else:
            note = f"REFUTED — serves {served}"
        print(f"  listing {lid:<5d} {note}")

    # Agent 1 named three. If the condition catches others, the report was a
    # sample and not a census -- which changes what a fix has to cover.
    others = sorted(lid for lid, l in publishable.items()
                    if not marketplace_seo.eligibility(l).indexable
                    and lid not in CLAIMED_NOINDEX)
    print(f"\n  same shape, NOT named by Agent 1: {others or 'none'}")
    print(f"\nCROSS-AUTHORITY DISAGREEMENTS: {len(disagreements)}")
    for lid, why in disagreements:
        print(f"  listing {lid}: {why}")


if __name__ == "__main__":
    main()
