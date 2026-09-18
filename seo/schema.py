import json
import os

from services import app_links

SITE_URL = os.getenv("PUBLIC_SITE_URL", "https://pulsesoc.com").rstrip("/")
LOGO_URL = f"{SITE_URL}/static/brand/pulsesoc-logo-20260913.png"
SHARE_IMAGE_URL = f"{SITE_URL}/static/brand/pulsesoc-og-20260913.png"
SUPPORT_EMAIL = "support@pulsesoc.com"

# Read from Apple's own record of the listing on 2026-09-18
# (`https://itunes.apple.com/lookup?id=6777591572`) rather than from what this
# repo believes it shipped. Structured data is a set of claims made to a search
# engine; every one of these has to be checkable against a source outside the
# codebase, and the App Store listing is that source.
#
# `tests/test_app_schema.py` pins each value and says what it is a claim about,
# so a version bump that leaves this stale fails rather than quietly asserting
# the wrong version to Google.
APP_MINIMUM_IOS = "15.1"
APP_VERSION = "1.0.2"
APP_FIRST_RELEASED = "2026-07-01"
APP_CONTENT_RATING = "4+"


def organization_schema():
    return {
        "@type": "Organization",
        "@id": f"{SITE_URL}/#organization",
        "name": "PulseSoc",
        "legalName": "CoinPlotXAI Inc.",
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
        # `sameAs` is for profiles that identify this same entity elsewhere, and
        # the App Store listing is the strongest one available: Apple records
        # the seller as COINPLOTXAI INC, which independently corroborates the
        # `legalName` above rather than asking Google to take our word for it.
        "sameAs": [app_links.app_store_url(), "https://t.me/DocShieldX_bot"],
    }


def website_schema():
    return {
        "@type": "WebSite",
        "@id": f"{SITE_URL}/#website",
        "name": "PulseSoc",
        "url": SITE_URL + "/",
        "publisher": {"@id": f"{SITE_URL}/#organization"},
        "inLanguage": "en",
        "description": "PulseSoc is a social, creator, safety, market-intelligence, and premium community platform powered by CoinPlotXAI.",
        "potentialAction": {
            "@type": "SearchAction",
            "target": f"{SITE_URL}/search?q={{search_term_string}}",
            "query-input": "required name=search_term_string",
        },
    }


def mobile_app_schema():
    """The iPhone app, described the way Apple records it.

    What this replaces was wrong in every field that mattered: it declared
    `FinanceApplication` for a social network, `operatingSystem: "Telegram,
    Web, PWA"` for a product whose app is on iOS, and a $14.99 offer on an
    application that is free to download. It described the Telegram bot this
    company used to be.

    Two deliberate omissions:

    * No `aggregateRating`. The listing has two ratings, and Google's guidance
      is that a rating in structured data should come from reviews the site
      itself collects -- restating Apple's on our own domain is the kind of
      claim that is technically sourced and still misleading. A missing star
      rating costs nothing we currently qualify for.
    * No `downloadUrl`. `installUrl` is the field for "where a person installs
      this"; `downloadUrl` invites a direct binary, which we do not offer.

    The $14.99 in `product_schema` is a different claim about a different
    thing: the app is free, PulseSoc Premium is the paid subscription
    (`bot.PRO_PRICE_MONTHLY`). Both can be true at once, and were not before.
    """

    return {
        "@type": "MobileApplication",
        "@id": f"{SITE_URL}/#app",
        "name": "PulseSoc",
        "applicationCategory": "SocialNetworkingApplication",
        "operatingSystem": f"iOS {APP_MINIMUM_IOS} or later",
        "url": f"{SITE_URL}/app",
        "installUrl": app_links.app_store_url(),
        "softwareVersion": APP_VERSION,
        "datePublished": APP_FIRST_RELEASED,
        "contentRating": APP_CONTENT_RATING,
        "inLanguage": "en",
        "image": SHARE_IMAGE_URL,
        "publisher": {"@id": f"{SITE_URL}/#organization"},
        "description": "PulseSoc for iPhone: posts, reels, live video, direct messages, calls, creator profiles, and a marketplace, with reporting, blocking and moderation built in.",
        "offers": {
            "@type": "Offer",
            "price": "0",
            "priceCurrency": "USD",
            "availability": "https://schema.org/InStock",
        },
    }


def app_page_graph(page, trail=()):
    """The graph for the app pages, composed by hand rather than through
    `schema_graph`.

    `schema_graph` attaches a `Service` node to every page, with `serviceType`
    defaulting to "AI intelligence". On a page whose subject is a free iPhone
    app that is not a smaller claim than the rest, it is a different one, and
    the point of this pass is that each node corresponds to something real.

    `trail` is the breadcrumb between the home page and this one, so a feature
    page can say it sits under /app. Passing it explicitly rather than deriving
    it from the URL keeps the crumb honest when the two disagree.
    """

    graph = [
        organization_schema(),
        website_schema(),
        mobile_app_schema(),
        webpage_schema(page),
        breadcrumb_schema([
            ("Home", SITE_URL + "/"),
            *trail,
            (page["breadcrumb"], page["canonical"]),
        ]),
    ]
    if page.get("faqs"):
        graph.append(faq_schema(page["faqs"]))
    return json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False)


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
        mobile_app_schema(),
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
