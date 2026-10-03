"""Replicate the gate's setUpClass exactly: one client, sorted walk, target in place.

If /pulse/marketplace comes back noindex here but index when asked alone, the
directive depends on something the walk consumes -- and a gate that reads it
would be flaky. Find the trigger before landing the gate, because "mark it
flaky" is not available to me without proof of WHY.
"""
import logging, os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(ROOT, ".attack", "scratch.db"))
import bot
from services import search_visibility
logging.disable(logging.CRITICAL)
RE_R = re.compile(r"""<meta[^>]+name=['"]robots['"][^>]*>""", re.I)
RE_C = re.compile(r"""content=['"]([^'"]*)['"]""", re.I)

app = bot.webhook_app; app.config["TESTING"] = True
client = app.test_client()
rules = sorted({r.rule for r in app.url_map.iter_rules()
                if "GET" in (r.methods or ()) and not r.arguments})

WATCH = {"/pulse/marketplace", "/pulse/cart", "/pulse/help", "/help"}
seen = {}
order = []
for i, p in enumerate(rules):
    try:
        r = client.get(p, follow_redirects=False)
    except Exception as e:
        if p in WATCH:
            seen[p] = (i, "EXC", type(e).__name__, 0)
        continue
    if p in WATCH:
        tag = RE_R.search(r.get_data(as_text=True))
        d = RE_C.search(tag.group(0)).group(1) if tag else None
        seen[p] = (i, r.status_code, d, len(r.get_data()))
        order.append(p)

print("during the walk (index in walk, status, directive, body bytes):")
for p in sorted(WATCH):
    print(f"  {p:24s} {seen.get(p)}")

print("\nimmediately after, same client:")
for p in sorted(WATCH):
    r = client.get(p, follow_redirects=False)
    tag = RE_R.search(r.get_data(as_text=True))
    d = RE_C.search(tag.group(0)).group(1) if tag else None
    print(f"  {p:24s} ({r.status_code}, {d!r}, {len(r.get_data())})")
