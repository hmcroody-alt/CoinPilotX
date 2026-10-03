"""Can I mint extra indexable copies of a page by reshaping its URL?

Classic duplicate-content / doorway attack surface. For each variant of a known
indexable path I want to know three things:

  - does it answer 200 (rather than redirect or 404)?
  - does it declare itself indexable?
  - does its canonical point back at the ONE true URL?

A 200 + indexable + self-canonical variant is a second copy of the page
competing with the original. A 200 + indexable + no canonical is worse.

Memory says one of the two canonical builders preserves case deliberately, so
casing is the variant I expect to be live. Werkzeug's `merge_slashes` and
`strict_slashes` govern the slash variants.
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

RE_ROBOTS = re.compile(r"""<meta[^>]+name=['"]robots['"][^>]*>""", re.I)
RE_CANON = re.compile(r"""<link[^>]+rel=['"]canonical['"][^>]*>""", re.I)
RE_CONTENT = re.compile(r"""content=['"]([^'"]*)['"]""", re.I)
RE_HREF = re.compile(r"""href=['"]([^'"]*)['"]""", re.I)

#: Indexable, self-canonical, anonymously-readable documents. The things worth
#: duplicating.
BASES = ["/help", "/pulse/marketplace", "/terms", "/privacy", "/enterprise"]


def variants(path):
    yield "exact", path
    yield "trailing slash", path + "/"
    yield "double slash", "/" + path
    yield "upper", path.upper()
    yield "title", "".join(c.upper() if i == 1 else c for i, c in enumerate(path))
    yield "dot segment", path + "/."
    yield "parent traversal", path + "/../" + path.lstrip("/")
    yield "query junk", path + "?utm_source=x&fbclid=y"
    yield "unknown param", path + "?sortby=price&ref=spam"
    yield "index suffix", path + "/index.html"
    yield "semicolon", path + ";jsessionid=1"
    yield "encoded slash", path.replace("/", "%2F", 1) if path.count("/") > 1 else path + "%2F"


def read(client, url):
    try:
        r = client.get(url, follow_redirects=False)
    except Exception as exc:
        return None, f"EXC:{type(exc).__name__}", None, None
    html = r.get_data(as_text=True) if "html" in (r.headers.get("Content-Type") or "").lower() else ""
    tag = RE_ROBOTS.search(html)
    directive = RE_CONTENT.search(tag.group(0)).group(1) if tag else None
    ctag = RE_CANON.search(html)
    canon = RE_HREF.search(ctag.group(0)).group(1) if ctag else None
    return r.status_code, directive, canon, r.headers.get("Location")


def indexable(d):
    return d is None or "noindex" not in d.lower()


def main():
    app = bot.webhook_app
    app.config["TESTING"] = True
    client = app.test_client()

    findings = []
    for base in BASES:
        true_canon = search_visibility.canonical_url(base)
        print("=" * 76)
        print(f"{base}   one true canonical: {true_canon}")
        print("=" * 76)
        for label, url in variants(base):
            status, directive, canon, loc = read(client, url)
            flag = ""
            if status == 200 and label != "exact":
                if canon is None:
                    flag = "  <<< DUPLICATE, NO CANONICAL"
                elif canon != true_canon:
                    flag = f"  <<< DUPLICATE, CANONICAL -> {canon}"
                elif not indexable(directive):
                    flag = "  (200 but noindex)"
                else:
                    flag = "  (200, canonical points home - OK)"
                if flag.startswith("  <<<") and indexable(directive):
                    findings.append((base, label, url, status, directive, canon))
            print(f"  {label:18s} {url:46s} {str(status):4s} "
                  f"idx={'Y' if indexable(directive) else 'N'} canon={canon}{flag}")
            if loc:
                print(f"  {'':18s} {'':46s} -> {loc}")
        print()

    print("=" * 76)
    print(f"INDEXABLE DUPLICATES WITH A WRONG/MISSING CANONICAL: {len(findings)}")
    print("=" * 76)
    for base, label, url, status, directive, canon in findings:
        print(f"  {url}\n      variant {label} of {base}\n      status {status} "
              f"directive {directive!r} canonical {canon!r}")


if __name__ == "__main__":
    main()
