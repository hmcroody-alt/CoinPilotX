import json
import os

SITE_URL = os.getenv("PUBLIC_SITE_URL", "https://pulsesoc.com").rstrip("/")
LOGO_URL = f"{SITE_URL}/static/brand/pulsesoc-logo-20260606.png"
SHARE_IMAGE_URL = LOGO_URL
SUPPORT_EMAIL = "support@pulsesoc.com"


def organization_schema():
    return {
        "@type": "Organization",
        "@id": f"{SITE_URL}/#organization",
        "name": "PulseSoc",
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
        "name": "PulseSoc",
        "url": SITE_URL + "/",
        "publisher": {"@id": f"{SITE_URL}/#organization"},
        "inLanguage": "en",
        "description": "PulseSoc is a social, creator, safety, market-intelligence, and premium community platform powered by CoinPilotXAI.",
        "potentialAction": {
            "@type": "SearchAction",
            "target": f"{SITE_URL}/search?q={{search_term_string}}",
            "query-input": "required name=search_term_string",
        },
    }


def software_schema():
    return {
        "@type": "SoftwareApplication",
        "@id": f"{SITE_URL}/#software",
        "name": "PulseSoc",
        "applicationCategory": "FinanceApplication",
        "operatingSystem": "Telegram, Web, PWA",
        "url": SITE_URL + "/",
        "image": SHARE_IMAGE_URL,
        "publisher": {"@id": f"{SITE_URL}/#organization"},
        "description": "Creator, video, live, messaging, scam-safety, portfolio context, market intelligence, and premium community features.",
        "offers": {
            "@type": "Offer",
            "price": "14.99",
            "priceCurrency": "USD",
            "availability": "https://schema.org/OnlineOnly",
        },
    }


def service_schema(page):
    return {
        "@type": "Service",
        "@id": page["canonical"] + "#service",
        "name": page["h1"],
        "provider": {"@id": f"{SITE_URL}/#organization"},
        "areaServed": "Worldwide",
        "serviceType": page.get("eyebrow", "AI intelligence"),
        "description": page["description"],
        "url": page["canonical"],
        "termsOfService": f"{SITE_URL}/terms",
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
    schema = {
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
    if page.get("keywords"):
        schema["keywords"] = page["keywords"]
    if page.get("dateModified") or page.get("updated"):
        schema["dateModified"] = page.get("dateModified") or page.get("updated")
    return schema


def product_schema():
    return {
        "@type": "Product",
        "@id": f"{SITE_URL}/#product",
        "name": "PulseSoc Premium",
        "brand": {"@id": f"{SITE_URL}/#organization"},
        "description": "PulseSoc Premium adds prestige identity, creator enhancements, advanced safety and intelligence features, and premium community tools.",
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
    schema = {
        "@type": "Article",
        "@id": page["canonical"] + "#article",
        "headline": page["title"],
        "description": page["description"],
        "image": page.get("image") or SHARE_IMAGE_URL,
        "author": {"@id": f"{SITE_URL}/#organization"},
        "publisher": {"@id": f"{SITE_URL}/#organization"},
        "dateModified": page.get("updated", "2026-05-10"),
        "datePublished": page.get("published", "2026-05-10"),
        "mainEntityOfPage": {"@id": page["canonical"] + "#webpage"},
        "articleSection": page.get("eyebrow", "AI intelligence"),
        "inLanguage": "en",
    }
    if page.get("keywords"):
        schema["keywords"] = page["keywords"]
    if page.get("answer"):
        schema["abstract"] = page["answer"]
    return schema


def related_item_list_schema(page):
    related = page.get("related") or []
    if not related:
        return None
    return {
        "@type": "ItemList",
        "@id": page["canonical"] + "#related",
        "name": f"Related PulseSoc intelligence pages for {page['h1']}",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": index,
                "url": SITE_URL + path,
                "name": path.strip("/").replace("-", " ").replace("/", " ").title(),
            }
            for index, path in enumerate(related, start=1)
        ],
    }


def schema_graph(page, include_product=False, include_article=False):
    graph = [
        organization_schema(),
        website_schema(),
        software_schema(),
        webpage_schema(page),
        service_schema(page),
        breadcrumb_schema([
            ("Home", SITE_URL + "/"),
            (page.get("breadcrumb") or page["h1"], page["canonical"]),
        ]),
    ]
    if page.get("faqs"):
        graph.append(faq_schema(page["faqs"]))
    if include_product:
        graph.append(product_schema())
    if include_article or page.get("og_type") == "article":
        graph.append(article_schema(page))
    related = related_item_list_schema(page)
    if related:
        graph.append(related)
    return json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False)
