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
        "/": "Pulse | AI Crypto Command Center and Alpha Arena",
        "/arena/play": "Alpha Arena | Simulated Crypto Training Battles",
        "/arena/roast-battle": "Roast Battle Arena | Live Social Competition",
        "/scam-shield": "Scam Shield | AI Crypto Scam Detection",
        "/live-roast-battle": "Live Roast Battle | Pulse",
        "/crypto-scam-scanner": "Crypto Scam Scanner | Pulse Scam Shield",
        "/alpha-arena": "Alpha Arena | Crypto Training Simulator",
        "/crypto-training-simulator": "Crypto Training Simulator | Pulse",
    }
    title = title_map.get(path, "Pulse | AI Crypto Intelligence")
    description = "Pulse combines AI crypto intelligence, Alpha Arena simulated training, Scam Shield education, alerts, and live social gameplay."
    if "roast" in path:
        description = "Enter Roast Battle Arena, a moderated live social stage with call signs, crowd heat, virtual-dollar scoring, and replayable highlights."
    if "scam" in path:
        description = "Learn how to detect phishing, wallet drainers, fake giveaways, impersonation, and crypto scam patterns with Pulse."
    return {
        "title": title,
        "description": description,
        "canonical": BASE_URL + path,
        "robots": "index,follow",
        "og_type": "website",
        "og_image": BASE_URL + "/static/img/og-coinpilotxai.png",
    }


def sitemap_xml(paths, changefreq="weekly"):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    body = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path in paths:
        if not path:
            continue
        body.append("  <url>")
        body.append(f"    <loc>{escape(BASE_URL + path)}</loc>")
        body.append(f"    <lastmod>{today}</lastmod>")
        body.append(f"    <changefreq>{changefreq}</changefreq>")
        body.append("    <priority>0.72</priority>")
        body.append("  </url>")
    body.append("</urlset>")
    return "\n".join(body)


def robots_txt():
    return "\n".join([
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /api/",
        "Disallow: /dashboard",
        "Disallow: /account",
        "Disallow: /chat",
        "Crawl-delay: 2",
        f"Sitemap: {BASE_URL}/sitemap.xml",
        f"Sitemap: {BASE_URL}/sitemap-pages.xml",
        f"Sitemap: {BASE_URL}/sitemap-live.xml",
        f"Sitemap: {BASE_URL}/sitemap-replays.xml",
        "",
    ])


def json_ld(path="/"):
    meta = page_meta(path)
    return {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "Pulse",
        "applicationCategory": "FinanceApplication",
        "operatingSystem": "Web, iOS PWA, Android PWA",
        "url": meta["canonical"],
        "description": meta["description"],
    }
