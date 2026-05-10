import json

SITE_URL = "https://coinpilotx.app"
LOGO_URL = f"{SITE_URL}/static/Coinpilot%20Logo/NewLogo.png"
SHARE_IMAGE_URL = f"{SITE_URL}/static/assets/coinpilotxai-share-card.svg"
SUPPORT_EMAIL = "support@coinpilotx.app"


def organization_schema():
    return {
        "@type": "Organization",
        "@id": f"{SITE_URL}/#organization",
        "name": "CoinPilotXAI Inc.",
        "legalName": "CoinPilotXAI Inc.",
        "url": SITE_URL + "/",
        "logo": LOGO_URL,
        "email": SUPPORT_EMAIL,
        "contactPoint": [{
            "@type": "ContactPoint",
            "contactType": "customer support",
            "email": SUPPORT_EMAIL,
            "url": f"{SITE_URL}/support",
            "availableLanguage": "en",
        }],
        "sameAs": ["https://t.me/DocShieldX_bot"],
    }


def website_schema():
    return {
        "@type": "WebSite",
        "@id": f"{SITE_URL}/#website",
        "name": "CoinPilotX",
        "url": SITE_URL + "/",
        "publisher": {"@id": f"{SITE_URL}/#organization"},
        "inLanguage": "en",
        "description": "CoinPilotX is an AI crypto intelligence, scam awareness, wallet risk, sports edge, and Telegram-first market education platform powered by CoinPilotXAI Inc.",
    }


def software_schema():
    return {
        "@type": "SoftwareApplication",
        "@id": f"{SITE_URL}/#software",
        "name": "CoinPilotX",
        "applicationCategory": "FinanceApplication",
        "operatingSystem": "Telegram, Web, PWA",
        "url": SITE_URL + "/",
        "image": SHARE_IMAGE_URL,
        "publisher": {"@id": f"{SITE_URL}/#organization"},
        "description": "AI crypto intelligence, wallet scanning education, scam protection, whale alerts, Sports Edge, portfolio context, and Telegram-first market analysis.",
        "offers": {
            "@type": "Offer",
            "price": "14.99",
            "priceCurrency": "USD",
            "availability": "https://schema.org/OnlineOnly",
        },
    }


def faq_schema(faqs):
    return {
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": item["question"],
                "acceptedAnswer": {"@type": "Answer", "text": item["answer"]},
            }
            for item in faqs
        ],
    }


def breadcrumb_schema(items):
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": index,
                "name": name,
                "item": url,
            }
            for index, (name, url) in enumerate(items, start=1)
        ],
    }


def webpage_schema(page):
    return {
        "@type": "WebPage",
        "@id": page["canonical"] + "#webpage",
        "url": page["canonical"],
        "name": page["title"],
        "description": page["description"],
        "isPartOf": {"@id": f"{SITE_URL}/#website"},
        "publisher": {"@id": f"{SITE_URL}/#organization"},
        "image": page.get("image") or SHARE_IMAGE_URL,
        "inLanguage": "en",
    }


def product_schema():
    return {
        "@type": "Product",
        "@id": f"{SITE_URL}/#product",
        "name": "CoinPilotX Pro",
        "brand": {"@id": f"{SITE_URL}/#organization"},
        "description": "CoinPilotX Pro adds deeper AI reasoning, whale intelligence, wallet risk context, Sports Edge intelligence, portfolio scenario analysis, and advanced scam protection.",
        "image": SHARE_IMAGE_URL,
        "offers": {
            "@type": "Offer",
            "price": "14.99",
            "priceCurrency": "USD",
            "url": f"{SITE_URL}/#pricing",
            "availability": "https://schema.org/InStock",
        },
    }


def article_schema(page):
    return {
        "@type": "Article",
        "headline": page["title"],
        "description": page["description"],
        "image": page.get("image") or SHARE_IMAGE_URL,
        "author": {"@id": f"{SITE_URL}/#organization"},
        "publisher": {"@id": f"{SITE_URL}/#organization"},
        "dateModified": page.get("updated", "2026-05-10"),
        "datePublished": page.get("published", "2026-05-10"),
        "mainEntityOfPage": page["canonical"],
    }


def schema_graph(page, include_product=False, include_article=False):
    graph = [
        organization_schema(),
        website_schema(),
        software_schema(),
        webpage_schema(page),
        breadcrumb_schema([
            ("Home", SITE_URL + "/"),
            (page.get("breadcrumb") or page["h1"], page["canonical"]),
        ]),
    ]
    if page.get("faqs"):
        graph.append(faq_schema(page["faqs"]))
    if include_product:
        graph.append(product_schema())
    if include_article:
        graph.append(article_schema(page))
    return json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False)

