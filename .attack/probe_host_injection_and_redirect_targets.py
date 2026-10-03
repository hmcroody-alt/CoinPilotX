"""Two attacks the URL-shape probe surfaced or left open.

(1) HOST HEADER CANONICAL INJECTION. If the canonical is built from the
    request's Host, I can make pulsesoc.com declare that the canonical version
    of its own page lives on my domain. That is the highest-severity SEO attack
    there is -- it hands ranking to the attacker. Memory says there is no
    ProxyFix anywhere and that Railway's edge rejects unknown Host, but "the
    edge would stop it" is a deployment detail, not an application invariant,
    and the edge is not in front of the test client.

(2) REDIRECT TARGETS. The slash-merge redirects do not go where I expected:
        //help               301 -> https://pulsesoc.com/
        //pulse/marketplace  301 -> https://pulsesoc.com/marketplace
    A 301 that lands on a 404 burns crawl budget and loses whatever equity the
    source URL had. Resolve each target and see.
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

RE_CANON = re.compile(r"""<link[^>]+rel=['"]canonical['"][^>]*>""", re.I)
RE_HREF = re.compile(r"""href=['"]([^'"]*)['"]""", re.I)
RE_OG = re.compile(r"""<meta[^>]+property=['"]og:url['"][^>]*>""", re.I)
RE_CONTENT = re.compile(r"""content=['"]([^'"]*)['"]""", re.I)

EVIL = "evil-seo-attacker.example"


def main():
    app = bot.webhook_app
    app.config["TESTING"] = True
    client = app.test_client()

    print("=" * 76)
    print("(1) HOST HEADER CANONICAL INJECTION")
    print("=" * 76)
    print(f"policy canonical for /help: {search_visibility.canonical_url('/help')}\n")

    headers_matrix = [
        ("baseline", {}),
        ("Host", {"Host": EVIL}),
        ("X-Forwarded-Host", {"X-Forwarded-Host": EVIL}),
        ("X-Forwarded-Proto", {"X-Forwarded-Proto": "http"}),
        ("X-Original-Host", {"X-Original-Host": EVIL}),
        ("X-Host", {"X-Host": EVIL}),
        ("Forwarded", {"Forwarded": f"host={EVIL};proto=http"}),
        ("Host + XFH", {"Host": EVIL, "X-Forwarded-Host": EVIL}),
    ]

    poisoned = []
    for label, headers in headers_matrix:
        for path in ("/help", "/pulse/marketplace"):
            r = client.get(path, headers=headers, follow_redirects=False)
            html = r.get_data(as_text=True)
            ctag = RE_CANON.search(html)
            canon = RE_HREF.search(ctag.group(0)).group(1) if ctag else None
            otag = RE_OG.search(html)
            og = RE_CONTENT.search(otag.group(0)).group(1) if otag else None
            bad = (canon and EVIL in canon) or (og and EVIL in og)
            if bad:
                poisoned.append((label, path, canon, og))
            print(f"  {label:20s} {path:22s} {r.status_code} "
                  f"canonical={canon}  og:url={og}{'   <<< POISONED' if bad else ''}")
    print(f"\n  POISONED CANONICALS: {len(poisoned)}")

    print()
    print("=" * 76)
    print("(2) REDIRECT TARGETS -- does the 301 land on a real page?")
    print("=" * 76)
    for src in ("//help", "//pulse/marketplace", "//terms", "//privacy",
                "//enterprise", "//pulse/cart"):
        r = client.get(src, follow_redirects=False)
        loc = r.headers.get("Location")
        print(f"  {src:24s} {r.status_code} -> {loc}")
        if not loc:
            continue
        target_path = re.sub(r"^https?://[^/]+", "", loc) or "/"
        r2 = client.get(target_path, follow_redirects=False)
        verdict = ("DEAD END - 301 into an error" if r2.status_code >= 400
                   else ("another redirect" if 300 <= r2.status_code < 400 else "ok"))
        print(f"  {'':24s}    target {target_path} -> {r2.status_code}  [{verdict}]")
        if 300 <= r2.status_code < 400:
            print(f"  {'':24s}    then -> {r2.headers.get('Location')}")


if __name__ == "__main__":
    main()
