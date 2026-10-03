import logging, os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(ROOT, ".attack", "scratch.db"))
import bot
from services import search_visibility
logging.disable(logging.CRITICAL)
RE_R = re.compile(r"""<meta[^>]+name=['"]robots['"][^>]*>""", re.I)
app = bot.webhook_app; app.config["TESTING"] = True
c = app.test_client()
for p in ["/education","/education/optimism","/education/scam-alerts","/education/toncoin-scenarios",
          "/legal/payments","/legal/refunds","/legal/seller-terms","/predictions/crypto","/quote",
          "/roast-battle-preview","/sports-edge","/pulse/marketplace"]:
    r = c.get(p, follow_redirects=False)
    tag = RE_R.search(r.get_data(as_text=True))
    print(f"{p:34s} {r.status_code}  meta={(tag.group(0) if tag else 'NONE')!r:34s} xrt={r.headers.get('X-Robots-Tag')!r} policy={search_visibility.robots_meta(p)}")
