"""Does every URL we submit actually serve as an indexable, self-canonical page?

A sitemap entry is a recommendation. Submitting a URL that answers 404, that
redirects, that says `noindex`, or that names a *different* page as its
canonical is a self-contradiction -- we are asking Google to crawl a page we
have already told it to ignore or to fold into another one.

`tests/protection/test_sitemap_entries_are_indexable.py` already pins this
statically, against `search_visibility.classify()`. That is the right place for
the policy, and it cannot see the thing this script is for: the policy agreeing
with itself does not prove the *deployed* page agrees with the policy. The
template emits its own robots meta, the route may 302 before the template runs,
and a listing can go held between the sitemap being built and the page being
fetched. So this reads the wire.

Read-only. It issues GETs against whatever origin it is pointed at and writes
nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

GOOGLEBOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"

# Single-quoted attributes are live in this codebase -- bot.py writes the post
# page's canonical inside a single-quoted f-string. A pattern that only accepts
# double quotes reports a missing canonical on a page that has one, which is a
# mistake already made against this exact surface.
_CANONICAL_RE = re.compile(
    r"""<link\s[^>]*rel=['"]canonical['"][^>]*href=['"]([^'"]+)['"]""", re.IGNORECASE
)
_CANONICAL_REV_RE = re.compile(
    r"""<link\s[^>]*href=['"]([^'"]+)['"][^>]*rel=['"]canonical['"]""", re.IGNORECASE
)
_ROBOTS_RE = re.compile(
    r"""<meta\s[^>]*name=['"]robots['"][^>]*content=['"]([^'"]*)['"]""", re.IGNORECASE
)
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)


def fetch(url, timeout=25):
    """GET a URL without following redirects. Returns (status, headers, body)."""

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": GOOGLEBOT_UA})
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        return exc.code, dict(exc.headers or {}), body
    except Exception as exc:  # noqa: BLE001 - a transport failure is a result
        return 0, {"x-transport-error": str(exc)}, ""


def inspect(url):
    status, headers, body = fetch(url)
    canonical = None
    match = _CANONICAL_RE.search(body) or _CANONICAL_REV_RE.search(body)
    if match:
        canonical = match.group(1).strip()
    robots_meta = None
    match = _ROBOTS_RE.search(body)
    if match:
        robots_meta = match.group(1).strip()

    # An X-Robots-Tag header and a meta robots that disagree is the
    # contradictory-signal pair that makes indexability undefined.
    x_robots = None
    for name, value in headers.items():
        if name.lower() == "x-robots-tag":
            x_robots = value.strip()
            break

    faults = []
    if status == 0:
        faults.append(f"TRANSPORT:{headers.get('x-transport-error', '?')}")
    elif status != 200:
        faults.append(f"STATUS:{status}")
        location = headers.get("Location") or headers.get("location")
        if location:
            faults.append(f"REDIRECTS_TO:{location}")

    if status == 200:
        if robots_meta is None and x_robots is None:
            faults.append("NO_ROBOTS_DIRECTIVE")
        for directive in (robots_meta, x_robots):
            if directive and "noindex" in directive.lower():
                faults.append(f"NOINDEX:{directive}")
        if robots_meta and x_robots and ("noindex" in robots_meta.lower()) != ("noindex" in x_robots.lower()):
            faults.append(f"CONTRADICTION:meta={robots_meta};header={x_robots}")
        if canonical is None:
            faults.append("NO_CANONICAL")
        elif canonical.rstrip("/") != url.rstrip("/"):
            faults.append(f"CANONICAL_MISMATCH:{canonical}")

    return {
        "url": url,
        "status": status,
        "robots_meta": robots_meta,
        "x_robots_tag": x_robots,
        "canonical": canonical,
        "faults": faults,
    }


def sitemap_urls(origin, name):
    status, _headers, body = fetch(f"{origin}/{name}")
    if status != 200:
        return [], f"{name} -> HTTP {status}"
    return _LOC_RE.findall(body), None


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", default="https://pulsesoc.com")
    parser.add_argument(
        "--sitemaps",
        default="sitemap-products.xml,sitemap-categories.xml,sitemap-posts.xml",
        help="Comma-separated child sitemap filenames.",
    )
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args(argv)

    report = {"origin": args.origin, "sitemaps": {}, "total_faults": 0}
    exit_code = 0

    for name in [s.strip() for s in args.sitemaps.split(",") if s.strip()]:
        urls, error = sitemap_urls(args.origin, name)
        if error:
            report["sitemaps"][name] = {"error": error}
            print(f"!! {error}")
            exit_code = 1
            continue

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(inspect, urls))

        faulty = [r for r in results if r["faults"]]
        report["sitemaps"][name] = {
            "submitted": len(urls),
            "clean": len(results) - len(faulty),
            "faulty": faulty,
        }
        report["total_faults"] += len(faulty)

        print(f"\n=== {name}: {len(urls)} submitted, {len(faulty)} faulty ===")
        for r in faulty:
            print(f"  {r['url']}")
            for fault in r["faults"]:
                print(f"      {fault}")
        if faulty:
            exit_code = 1

    if args.as_json:
        print(json.dumps(report, indent=2))
    print(f"\nTOTAL FAULTY: {report['total_faults']}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
