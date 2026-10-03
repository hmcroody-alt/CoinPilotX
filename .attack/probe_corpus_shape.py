"""How big is the enumerated corpus, and which direction does each failure point?

Before promoting the url_map walk into a CI gate I need to know:
  - how many parameterless GET routes answer 200 text/html (the judgeable set)
  - how long the walk takes (a slow gate gets disabled)
  - for each disagreement, WHICH SIDE is wrong

The last one decides whether the gate is safe to land. A gate that says
"page must equal policy" would, on /forgot-password, demand the page start
advertising itself as indexable -- the page is right and the table is wrong.
A gate that tells you the wrong fix is worse than no gate at all.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(ROOT, ".attack", "scratch.db"))

import bot  # noqa: E402
from services import search_visibility  # noqa: E402

logging.disable(logging.CRITICAL)

RE_ROBOTS = re.compile(r"""<meta[^>]+name=['"]robots['"][^>]*>""", re.I)
RE_CONTENT = re.compile(r"""content=['"]([^'"]*)['"]""", re.I)


def served(html):
    tag = RE_ROBOTS.search(html or "")
    if not tag:
        return None
    m = RE_CONTENT.search(tag.group(0))
    return m.group(1).strip() if m else None


def toks(d):
    return None if d is None else frozenset(t.strip().lower() for t in d.split(",") if t.strip())


def indexable(t):
    return t is not None and "noindex" not in t


def main():
    app = bot.app
    app.config["TESTING"] = True
    client = app.test_client()

    paths = sorted({r.rule for r in app.url_map.iter_rules()
                    if "GET" in (r.methods or ()) and not r.arguments})

    t0 = time.time()
    judged, nonhtml, nonzero = [], 0, 0
    for p in paths:
        try:
            resp = client.get(p, follow_redirects=False)
        except Exception:
            nonzero += 1
            continue
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if resp.status_code != 200:
            nonzero += 1
            continue
        if "html" not in ctype:
            nonhtml += 1
            continue
        judged.append((p, resp.get_data(as_text=True), resp.headers.get("X-Robots-Tag")))
    elapsed = time.time() - t0

    print(f"parameterless GET rules      {len(paths)}")
    print(f"  not 200 (redirect/404/exc) {nonzero}")
    print(f"  200 but not HTML           {nonhtml}")
    print(f"  JUDGEABLE (200 + HTML)     {len(judged)}")
    print(f"walk time                    {elapsed:.1f}s")

    leak, policy_wrong, token_drift, silent_noindex, agree = [], [], [], [], 0
    for p, html, hdr in judged:
        pol = search_visibility.robots_meta(p)
        eff = served(html) or hdr
        pt, et = toks(pol), toks(eff)
        if et is None:
            # Absence means "index,follow" to a crawler. Only a problem when
            # the table wanted the page kept out.
            if not indexable(pt):
                silent_noindex.append((p, pol))
            else:
                agree += 1
            continue
        if pt == et:
            agree += 1
        elif indexable(et) and not indexable(pt):
            leak.append((p, pol, eff))
        elif indexable(pt) and not indexable(et):
            policy_wrong.append((p, pol, eff))
        else:
            token_drift.append((p, pol, eff))

    print()
    print(f"AGREE                                      {agree}")
    print(f"LEAK  (policy noindex, page invites)       {len(leak)}   <-- fix the PAGE")
    print(f"SILENT (policy noindex, page sends nothing){len(silent_noindex)}   <-- fix the PAGE")
    print(f"TABLE WRONG (policy index, page refuses)   {len(policy_wrong)}   <-- fix the TABLE")
    print(f"TOKEN DRIFT (same indexability)            {len(token_drift)}   <-- fix the PAGE")

    for label, rows in (("LEAK", leak), ("SILENT", silent_noindex),
                        ("TABLE WRONG", policy_wrong), ("TOKEN DRIFT", token_drift)):
        if not rows:
            continue
        print(f"\n--- {label} ---")
        for row in rows:
            if len(row) == 2:
                print(f"  {row[0]}  (policy: {row[1]})")
            else:
                print(f"  {row[0]}\n      policy {row[1]}\n      served {row[2]}")


if __name__ == "__main__":
    main()
