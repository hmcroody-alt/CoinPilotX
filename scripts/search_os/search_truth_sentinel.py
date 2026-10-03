"""Does every product page we submit tell one consistent story about itself?

A single live PDP states its price in seven places: `<meta name="description">`,
`og:description`, `twitter:description`, the JSON-LD `WebPage.description`
prose, the JSON-LD `Offer.price`, the `data-mkt-variants` bootstrap attribute,
and the visible `data-mkt-price` pill. Each is produced by a different code
path, so each can drift alone, and nothing in this repository compared any of
them to any other before `services/search_truth.py`.

The drift is silent by construction. The page still renders, the feed still
validates, the unit tests still pass -- the only readers who notice are a buyer
at checkout and a Merchant Center reviewer filing a misrepresentation finding.
`tests/test_search_truth.py` pins the comparison logic, and it cannot see what
this script is for: a module agreeing with itself does not prove the *deployed*
page agrees with it.

SCOPE, AND WHAT IT DELIBERATELY LEAVES ALONE
--------------------------------------------
This asks one question -- do a page's own claims agree with each other, with
path policy, and with its identity. It does not re-ask Agent 2's question
(status, redirect chain, `X-Robots-Tag`-vs-meta contradiction, canonical
self-reference per sitemap URL); `scripts/search_os/verify_sitemap_vs_live.py`
owns that and the two are meant to be run together.

By default it runs without a database, which is deliberate rather than a
limitation: `import bot` opens a connection and executes `init_db()` at module
scope, so a sentinel that needed the ORM could not safely be pointed at
production at all. The strongest finding available -- a page quoting two
different prices in two of its own tags -- needs no row. `--with-db` adds the
row-level comparisons (price_label vs variant price vs wire, availability,
record-level indexability) and is for a local or CI run against a local
database, never against production.

Read-only. It issues GETs against whatever origin it is pointed at, writes
nothing, and sends no PII.

    python3 scripts/search_os/search_truth_sentinel.py --origin https://pulsesoc.com
    python3 scripts/search_os/search_truth_sentinel.py --listing 163 --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import search_truth  # noqa: E402

GOOGLEBOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
PRODUCT_SITEMAP = "/sitemap-products.xml"

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
_PRODUCT_URL_RE = re.compile(r"/pulse/marketplace/(\d+)\s*$")


def fetch(url, timeout=25):
    """GET without following redirects. Returns ``(status, body)``.

    A transport failure returns status 0 and an empty body, which the caller
    turns into UNKNOWN rather than into a fault -- a page we could not read is a
    statement about the fetch, not about the page.
    """

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": GOOGLEBOT_UA})
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception as exc:  # noqa: BLE001 - a transport failure is a result
        return 0, f"<!-- transport: {exc} -->"


def sitemap_listing_ids(origin):
    """Listing ids currently submitted for indexing, or ``None`` if unreadable.

    ``None`` and ``set()`` are different answers and the caller must keep them
    apart: an empty sitemap is a finding, an unreachable sitemap is an unknown.
    """

    status, body = fetch(origin.rstrip("/") + PRODUCT_SITEMAP)
    if status != 200 or not body:
        return None
    ids = set()
    for loc in _LOC_RE.findall(body):
        match = _PRODUCT_URL_RE.search(loc.strip())
        if match:
            ids.add(int(match.group(1)))
    return ids


def _load_rows(listing_ids):
    """Rows for ``listing_ids`` via the one loader the sitemap and feed share.

    Imported lazily and only under ``--with-db``, because importing ``bot``
    connects and runs ``init_db()``.
    """

    import bot  # noqa: PLC0415 - see docstring

    wanted = set(listing_ids)
    return {
        int(row.get("id")): row
        for row in bot.marketplace_public_listings()
        if int(row.get("id") or 0) in wanted
    }


def run(origin, listing_ids=None, *, with_db=False, workers=8):
    sitemap_ids = sitemap_listing_ids(origin)
    targets = sorted(listing_ids) if listing_ids else sorted(sitemap_ids or ())

    rows = _load_rows(targets) if with_db else {}

    def one(listing_id):
        status, body = fetch(f"{origin.rstrip('/')}/pulse/marketplace/{listing_id}")
        page = body if status == 200 and body else None
        if with_db and listing_id in rows:
            verdict = search_truth.compare_listing(rows[listing_id], page, sitemap_ids=sitemap_ids)
        else:
            verdict = search_truth.compare_page(listing_id, page)
            if with_db:
                verdict.skipped["ROW_COMPARISONS"] = "listing not returned by marketplace_public_listings"
        if page is None:
            verdict.skipped["PAGE_FETCH"] = f"HTTP {status}"
        return verdict

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, targets)), sitemap_ids


def _as_dict(verdict):
    return {
        "listing_id": verdict.listing_id,
        "worst": verdict.worst,
        "faults": [{"code": f.code, "severity": f.severity, "detail": f.detail} for f in verdict.faults],
        "skipped": verdict.skipped,
        "evidence": {
            name: {"value": str(obs.value) if obs.known else None, "source": obs.source, "detail": obs.detail}
            for name, obs in sorted(verdict.evidence.items())
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--origin", default="https://pulsesoc.com")
    parser.add_argument("--listing", type=int, action="append", dest="listings")
    parser.add_argument(
        "--with-db",
        action="store_true",
        help="add row-level comparisons; imports bot and runs init_db(). Local/CI only.",
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--fail-on",
        choices=("P0", "P1", "P2", "never"),
        default="P0",
        help="exit non-zero at or above this severity (default P0)",
    )
    args = parser.parse_args(argv)

    verdicts, sitemap_ids = run(
        args.origin, args.listings, with_db=args.with_db, workers=args.workers
    )

    if args.json:
        print(
            json.dumps(
                {
                    "origin": args.origin,
                    "sitemap_product_ids": None if sitemap_ids is None else len(sitemap_ids),
                    "with_db": args.with_db,
                    "pages": [_as_dict(v) for v in verdicts],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        faulted = [v for v in verdicts if v.faults]
        print(f"origin           {args.origin}")
        print(
            "sitemap products "
            + ("UNREADABLE" if sitemap_ids is None else str(len(sitemap_ids)))
        )
        print(f"pages compared   {len(verdicts)}")
        print(f"pages faulted    {len(faulted)}")
        for verdict in faulted:
            print(f"\n  listing {verdict.listing_id}")
            for fault in verdict.faults:
                print(f"    {fault.severity} {fault.code}: {fault.detail}")
        # Printed unconditionally and alongside the fault count, never instead
        # of it. "0 faults" on its own reads as "verified"; it means "no
        # contradiction among the surfaces we could read", and this is the half
        # of that sentence that says which surfaces those were not.
        unknown = {}
        for verdict in verdicts:
            for code in verdict.skipped:
                unknown[code] = unknown.get(code, 0) + 1
        print("\nnot checked (UNKNOWN, not clean):")
        if not unknown:
            print("  -- every comparison was possible on every page")
        for code, count in sorted(unknown.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {count:4d}  {code}")

    if args.fail_on == "never":
        return 0
    threshold = ("P0", "P1", "P2").index(args.fail_on)
    worst = [v.worst for v in verdicts if v.worst]
    return 1 if any(("P0", "P1", "P2").index(w) <= threshold for w in worst) else 0


if __name__ == "__main__":
    raise SystemExit(main())
