"""Agent 12: capture verbatim evidence for findings A12-01..A12-04.

Read-only. Runs against a scratch DB copy so probe GETs cannot write to the
dev database (serving a page writes visitor_logs).
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
RE_ATTR = re.compile(r"""(content|href)=['"]([^'"]*)['"]""", re.I)


def attr(tag):
    if not tag:
        return None
    m = RE_ATTR.search(tag.group(0))
    return m.group(2).strip() if m else None


def toks(d):
    return None if d is None else frozenset(t.strip().lower() for t in d.split(",") if t.strip())


def fetch(client, path):
    resp = client.get(path, follow_redirects=False)
    ctype = (resp.headers.get("Content-Type") or "").lower()
    html = resp.get_data(as_text=True) if (resp.status_code == 200 and "html" in ctype) else ""
    return resp.status_code, ctype, attr(RE_ROBOTS.search(html)), attr(RE_CANON.search(html))


def main():
    app = bot.app
    client = app.test_client()

    print("=" * 72)
    print("A12-01  /pulse/cart: policy vs served robots directive")
    print("=" * 72)
    for p in ("/pulse/cart",):
        st, ct, robots, canon = fetch(client, p)
        print(f"  path            {p}")
        print(f"  status          {st}")
        print(f"  policy          {search_visibility.robots_meta(p)}")
        print(f"  served          {robots}")
        print(f"  agrees          {toks(robots) == toks(search_visibility.robots_meta(p))}")

    print()
    print("=" * 72)
    print("A12-02  fallthrough declares non-public pages indexable + sitemap-eligible")
    print("=" * 72)
    suspects = ["/forgot-password", "/forgot-username", "/offline", "/reset-pwa",
                "/scam-shield/scan"]
    prefixes = search_visibility.robots_disallow_prefixes()
    for p in suspects:
        st, ct, robots, canon = fetch(client, p)
        matched = [x for x in prefixes if p.startswith(x)]
        print(f"  {p}")
        print(f"      policy_directive  {search_visibility.robots_meta(p)}")
        print(f"      is_indexable      {search_visibility.is_indexable(p)}")
        print(f"      sitemap_eligible  {search_visibility.sitemap_eligible(p)}")
        print(f"      robots_blocked    {search_visibility.robots_blocked(p)}")
        print(f"      disallow_match    {matched or 'NONE'}")
        print(f"      served_directive  {robots}   (page self-defends)")

    print()
    print("=" * 72)
    print("A12-03  directive-literal drift: pages that drop preview directives")
    print("=" * 72)
    full = search_visibility.INDEX_DIRECTIVE
    print(f"  canonical INDEX_DIRECTIVE = {full}")
    print()
    drift = ["/help", "/privacy", "/terms", "/support", "/pulse/help", "/pulse/support",
             "/advertising-policy", "/arena-preview", "/community-rules",
             "/creator-monetization-policy", "/enterprise", "/privacy-center", "/pro",
             "/trust-center"]
    for p in drift:
        st, ct, robots, canon = fetch(client, p)
        pol = search_visibility.robots_meta(p)
        missing = sorted(toks(pol) - toks(robots)) if robots else ["<no directive>"]
        print(f"  {p:36s} status={st} dropped={missing}")

    print()
    print("=" * 72)
    print("A12-04  sitemap-eligible paths whose canonical points elsewhere")
    print("=" * 72)
    print(f"  _CANONICAL_ALIASES = {search_visibility._CANONICAL_ALIASES}")
    print()
    for p in ("/support", "/pulse/support", "/pulse/help", "/help"):
        st, ct, robots, canon = fetch(client, p)
        pol = search_visibility.canonical_url(p)
        print(f"  {p}")
        print(f"      status            {st}")
        print(f"      served_canonical  {canon}")
        print(f"      policy_canonical  {pol}")
        print(f"      sitemap_eligible  {search_visibility.sitemap_eligible(p)}")
        print(f"      AGREES            {canon == pol}")


if __name__ == "__main__":
    main()
