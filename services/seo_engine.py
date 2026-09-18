"""Advanced SEO metadata and sitemap helpers."""

from __future__ import annotations

from datetime import datetime
from xml.sax.saxutils import escape


BASE_URL = "https://pulsesoc.com"

PUBLIC_LEARN_PATHS = [
    "/learn/crypto-scams",
    "/learn/crypto-trading-simulator",
    "/learn/how-to-detect-phishing",
    "/learn/market-psychology",
    "/learn/crypto-risk-management",
    "/learn/roast-battle-rules",
    "/learn/arena-ranking-system",
]

ADS_LANDING_PATHS = [
    "/live-roast-battle",
    "/crypto-scam-scanner",
    "/alpha-arena",
    "/crypto-training-simulator",
]


def page_meta(path="/"):
    path = path or "/"
    title_map = {
        "/": "PulseSoc | AI Crypto Command Center and Alpha Arena",
        "/arena/play": "Alpha Arena | Simulated Crypto Training Battles",
        "/arena/roast-battle": "Roast Battle Arena | Live Social Competition",
        "/scam-shield": "Scam Shield | AI Crypto Scam Detection",
        "/live-roast-battle": "Live Roast Battle | PulseSoc",
        "/crypto-scam-scanner": "Crypto Scam Scanner | PulseSoc Scam Shield",
        "/alpha-arena": "Alpha Arena | Crypto Training Simulator",
        "/crypto-training-simulator": "Crypto Training Simulator | PulseSoc",
    }
    title = title_map.get(path, "PulseSoc | AI Crypto Intelligence")
    description = "PulseSoc combines AI crypto intelligence, Alpha Arena simulated training, Scam Shield education, alerts, and live social gameplay."
    if "roast" in path:
        description = "Enter Roast Battle Arena, a moderated live social stage with call signs, crowd heat, virtual-dollar scoring, and replayable highlights."
    if "scam" in path:
        description = "Learn how to detect phishing, wallet drainers, fake giveaways, impersonation, and crypto scam patterns with PulseSoc."
    return {
        "title": title,
        "description": description,
        "canonical": BASE_URL + path,
        "robots": "index,follow",
        "og_type": "website",
        # Was /static/img/og-coinpilotxai.png, which 404'd -- the file predates
        # the PulseSoc rename and was never carried over, so every share of a
        # /learn/ page rendered with no preview image at all.
        "og_image": BASE_URL + "/static/brand/pulsesoc-og-20260913.png",
    }


def sitemap_xml(entries, changefreq=None):
    """Render a `<urlset>`, dropping anything we would not want indexed.

    Accepts either bare paths or `(path, lastmod)` pairs, because most callers
    have no modification date to offer and inventing one is the thing this
    function exists to stop.

    Three deliberate omissions
    -------------------------
    **Ineligible URLs.** Every entry passes `search_visibility.sitemap_eligible`.
    The gate lives here rather than in each caller so that a new sitemap route
    cannot forget it -- the old `/sitemap.xml` listed `/signup`, which ships
    `noindex`, for exactly that reason.

    **`lastmod` we do not know.** The previous implementation stamped today's
    date on all 354 URLs, every day. A field that always says "today" carries no
    information and Google discounts it once it stops correlating with real
    change. An absent `lastmod` is honest; a fabricated one spends credibility
    we then cannot use on the pages that genuinely did change.

    **`changefreq` and `priority`.** Both are ignored by Google, and ours were
    invented constants. The parameter is kept so existing callers still work.
    """

    from services import search_visibility

    body = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    seen = set()
    for entry in entries or []:
        path, lastmod = entry if isinstance(entry, (tuple, list)) else (entry, None)
        if not path or path in seen:
            continue
        if not search_visibility.sitemap_eligible(path):
            continue
        seen.add(path)
        body.append("  <url>")
        body.append(f"    <loc>{escape(search_visibility.canonical_url(path))}</loc>")
        if lastmod:
            body.append(f"    <lastmod>{escape(str(lastmod)[:10])}</lastmod>")
        body.append("  </url>")
    body.append("</urlset>")
    return "\n".join(body)


def sitemap_index_xml(children):
    """A `<sitemapindex>` pointing at the child sitemaps.

    Splitting by content type is not a size optimisation at our scale -- it is
    so Search Console reports coverage per section. "38 of 41 marketing pages
    indexed, 12 of 180 posts indexed" is actionable; one number over a mixed bag
    is not.
    """

    body = ['<?xml version="1.0" encoding="UTF-8"?>', '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for child in children:
        body.append("  <sitemap>")
        body.append(f"    <loc>{escape(BASE_URL + child)}</loc>")
        body.append("  </sitemap>")
    body.append("</sitemapindex>")
    return "\n".join(body)


def robots_txt():
    """Generated from the indexability policy, not maintained by hand.

    The hand-maintained list this replaces disallowed five prefixes and omitted
    a dozen others, while the page templates emitted their own `noindex` for a
    different set again. Deriving both from `search_visibility._RULES` is the
    only way the two stay in agreement.

    `Crawl-delay: 2` is gone. Google ignores it; Bing honours it, which means we
    were asking Bing to take two seconds between requests and then wondering why
    coverage there was thin. Nothing about this host needs rate limiting.

    robots.txt is not a privacy mechanism. Nothing here is the only thing
    standing between the public and private data -- the routes still
    authenticate.
    """

    from services import search_visibility

    lines = ["User-agent: *", "Allow: /"]
    lines += [f"Disallow: {prefix}" for prefix in search_visibility.robots_disallow_prefixes()]
    lines += [
        "",
        f"Sitemap: {BASE_URL}/sitemap.xml",
        "",
    ]
    return "\n".join(lines)


def json_ld(path="/"):
    meta = page_meta(path)
    return {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "PulseSoc",
        "applicationCategory": "FinanceApplication",
        "operatingSystem": "Web, iOS PWA, Android PWA",
        "url": meta["canonical"],
        "description": meta["description"],
    }
