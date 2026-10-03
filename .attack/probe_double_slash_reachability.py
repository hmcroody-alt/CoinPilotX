"""Is //pulse/marketplace reachable, or is the broken 301 a curiosity?

A 301 -> 404 only costs crawl budget if a crawler can find the source URL.
Crawlers find double-slash paths when a template concatenates a base that
already ends in "/" with a path that starts with "/". So look for emitted
double slashes in the sitemaps and in the pages a crawler actually reads.
"""
import logging, os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(ROOT, ".attack", "scratch.db"))
import bot
logging.disable(logging.CRITICAL)
app = bot.webhook_app; app.config["TESTING"] = True
c = app.test_client()

# host-relative double slash in an href/src/loc, i.e. a path like //foo
RE_BAD = re.compile(r"""(?:href|src)=['"](//[A-Za-z0-9_\-][^'"]*)['"]""")
RE_BADLOC = re.compile(r"""<loc>\s*(https?://[^/]+//[^<\s]*)\s*</loc>""", re.I)
RE_BADABS = re.compile(r"""https?://pulsesoc\.com//[^'"\s<]*""")

targets = ["/sitemap.xml", "/sitemap-pages.xml", "/sitemap-products.xml",
           "/sitemap-posts.xml", "/robots.txt", "/", "/help", "/pulse/marketplace",
           "/terms", "/privacy"]
total = 0
for t in targets:
    r = c.get(t, follow_redirects=False)
    if r.status_code != 200:
        print(f"  {t:26s} {r.status_code} (skipped)")
        continue
    body = r.get_data(as_text=True)
    hits = RE_BAD.findall(body) + RE_BADLOC.findall(body) + RE_BADABS.findall(body)
    # protocol-relative CDN refs like //fonts.googleapis.com are legitimate and
    # are NOT host-relative paths into our own app; drop anything with a dot in
    # the first segment.
    hits = [h for h in hits if "." not in h.split("/")[2].split("?")[0]] if hits else []
    total += len(hits)
    print(f"  {t:26s} {r.status_code}  double-slash self-paths: {len(hits)}"
          + (f"  {hits[:5]}" if hits else ""))
print(f"\nTOTAL emitted double-slash self-paths: {total}")
