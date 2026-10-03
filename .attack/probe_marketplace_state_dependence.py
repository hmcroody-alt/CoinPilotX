"""Why does /pulse/marketplace serve two different robots directives?

Direct GET  -> index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1
Inside walk -> noindex,follow

Two hypotheses, and they have different owners and different severities:
  (A) the test client's cookie jar picked up a session, so the page renders a
      signed-in variant. Then the directive depends on WHO asks.
  (B) server-side global state changed during the walk. Then it depends on
      WHEN you ask.

Distinguish by replaying the walk and then asking again with a FRESH client
(new cookie jar) as well as the dirty one.
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
TARGET = "/pulse/marketplace"

app = bot.webhook_app; app.config["TESTING"] = True

def directive(client):
    r = client.get(TARGET, follow_redirects=False)
    tag = RE_R.search(r.get_data(as_text=True))
    d = RE_C.search(tag.group(0)).group(1) if tag else None
    return r.status_code, d, len(r.get_data())

print(f"policy says: {search_visibility.robots_meta(TARGET)}\n")

c1 = app.test_client()
print(f"1. fresh client, first request      -> {directive(c1)}")

rules = sorted({r.rule for r in app.url_map.iter_rules()
                if "GET" in (r.methods or ()) and not r.arguments})
walked = 0
for p in rules:
    if p == TARGET:
        continue
    try:
        c1.get(p, follow_redirects=False)
        walked += 1
    except Exception:
        pass
print(f"   (walked {walked} other routes on c1)")

print(f"2. SAME client, after the walk      -> {directive(c1)}")
print(f"   cookies on that client: {[k.key for k in c1._cookies.values()] if hasattr(c1,'_cookies') else 'n/a'}")

c2 = app.test_client()
print(f"3. FRESH client, after the walk     -> {directive(c2)}")

print()
if directive(c2)[1] == directive(c1)[1]:
    print("=> SERVER-SIDE state: a fresh cookie jar sees the same thing.")
else:
    print("=> CLIENT/SESSION state: a fresh cookie jar sees the original directive.")
