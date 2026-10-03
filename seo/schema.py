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


def serialise_graph(payload):
    """The one place a graph becomes the string a template writes out.

    Every caller's output lands in a raw-text ``script`` element through
    ``|safe``, and inside one of those ``<`` is the only character that can end
    the block early: ``</script`` plus any whitespace closes it, and the ``>``
    needed to finish an injected tag can come from the markup that follows. So
    ``<`` is escaped here rather than at each call site, which makes the property
    structural -- a graph that later carries a value someone else supplies cannot
    be the one serialiser that forgot.

    ``\\u003c`` is ordinary JSON decoding to the same character, so a consumer
    reads the identical string. ``ensure_ascii=False`` is kept: these graphs
    restate text that is also visible on the page, and escaping its typographic
    punctuation would leave the structured copy subtly different from the visible
    one.
    """

    return json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")


def organization_schema():
    """The one Organization node this domain publishes.

    `@id` is what makes it one node. Google consolidates nodes that share an
    `@id`, so two pages describing `#organization` differently do not produce
    two entities -- they produce one entity with contradictory properties, and
    the contradiction is resolved by whichever page was crawled last.

    That was the state before this became the single source. `/terms` and
    `/privacy` each declared this exact `@id` with `name: "CoinPlotXAI Inc."`,
    while `/app`, `/features/*` and `/pricing` declared it with `name:
    "PulseSoc"`. Separately, `/` and `/about` published Organization nodes with
    no `@id` at all, which join nothing and corroborate nothing.

    `name` is the brand a person searches for. `legalName` is the company that
    signs things. Both are true, they are different fields, and the split is
    corroborated outside this repo: Apple records the App Store seller as
    COINPLOTXAI INC.
    """

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

    The `price: "0"` is a claim about the download and is checkable against
    Apple's listing. It says nothing about what a subscription costs, and no node
    in this module says that any more -- see the note where `product_schema` used
    to be for why the site publishes no subscription price at all.
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
    return serialise_graph({"@context": "https://schema.org", "@graph": graph})


def commerce_policy_graph(page):
    """The graph for `/returns`, `/refund-policy`, `/shipping` and `/contact`.

    A third hand-composed graph rather than a reuse, and the omission is the
    reason. `app_page_graph` attaches `mobile_app_schema()` to every page it
    builds, which is right for a page whose subject is the iPhone app and wrong
    here: a returns policy is not a claim about an app, and a `MobileApplication`
    node on it invites Google to read the page as app marketing. `schema_graph`
    would additionally attach a `Service` node with `serviceType` defaulting to
    "AI intelligence", which on a shipping page is simply false.

    What is left is what is true: who publishes the page (Organization), which
    site it belongs to (WebSite), what the page is (WebPage), and where it sits
    (BreadcrumbList). Merchant Center's reviewers read the rendered page rather
    than the JSON-LD, so nothing here is load-bearing for approval -- which is
    precisely why it must not overclaim to buy something.

    `/contact` gets one extra node, a `ContactPage` type on the WebPage itself
    rather than a separate entity, because that is the one page of the four
    whose subject schema.org has a specific type for.
    """

    webpage = webpage_schema(page)
    if page.get("page_type"):
        webpage["@type"] = page["page_type"]
    return serialise_graph({
        "@context": "https://schema.org",
        "@graph": [
            organization_schema(),
            website_schema(),
            webpage,
            breadcrumb_schema([
                ("Home", SITE_URL + "/"),
                (page["breadcrumb"], page["canonical"]),
            ]),
        ],
    })


def about_page_graph(page):
    """The graph for `/about`.

    `AboutPage` rather than a bare `WebPage` because schema.org has a type for
    exactly this and Google uses it to understand which page of a site speaks
    for the entity. The Organization node is the same canonical `#organization`
    every other page publishes, so this page describes that entity rather than
    introducing a second one -- which is what the hand-rolled node here used to
    do, under a name no other page used.

    Four nodes, and the omissions are deliberate. No `mobile_app_schema()`: the
    page links the App Store and says the app is free, but its subject is the
    platform, and a `MobileApplication` node would invite Google to read a brand
    page as app marketing. No `Service` node, which `schema_graph` would attach
    with `serviceType` defaulting to "AI intelligence" -- the single claim this
    rewrite exists to stop making. No `aggregateRating`, `review`, `founder`,
    `numberOfEmployees` or `award`: there is nothing real behind any of them, and
    an About page is where fabricated social proof is most tempting to add.
    """

    webpage = webpage_schema(page)
    webpage["@type"] = "AboutPage"
    return serialise_graph({
        "@context": "https://schema.org",
        "@graph": [
            organization_schema(),
            website_schema(),
            webpage,
            breadcrumb_schema([
                ("Home", SITE_URL + "/"),
                (page["breadcrumb"], page["canonical"]),
            ]),
        ],
    })


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


# There is no `product_schema()` here any more, and the gap is the point.
#
# It published one `Product` -- "PulseSoc Premium", `$14.99`, `InStock`, with
# `offers.url` pointing at `/#pricing` -- onto `/portfolio-intelligence`,
# `/ai-market-analysis` and `/telegram-crypto-bot`. Three facts about that node,
# each of which is enough on its own:
#
# * None of those three pages, nor `/pricing`, prints a price or that product's
#   name anywhere a reader can see it. The number went to Google and nowhere
#   else, so no page view could ever have contradicted it.
# * `$14.99` is not Premium's price. It is `crypto_intelligence_pro`'s monthly
#   plan (1499) in `services/business_os/entitlements/schema.py`; Premium's plan
#   there is 999, and the charge the Premium checkout takes is
#   `PULSE_PREMIUM_PRICE_CENTS`, defaulting to 1900. The node carried the social
#   product's name and benefits over the crypto product's price, on pages whose
#   subject is the crypto product.
# * `/#pricing` is an anchor no page on this domain defines.
#
# Which of 999, 1499 and 1900 is the public price is a question for whoever sets
# prices, and a structured-data layer that picks one is inventing a fact rather
# than projecting one. So the node is removed rather than corrected. Nothing is
# lost from the graph: `service_schema` already describes what each of these
# pages offers, named from the page's own `h1`, and `tests/test_app_schema.py`
# holds the floor for putting a price back -- a reader of the page has to be able
# to see the same number.


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


def schema_graph(page, include_article=False):
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
    if include_article or page.get("og_type") == "article":
        graph.append(article_schema(page))
    related = related_item_list_schema(page)
    if related:
        graph.append(related)
    return serialise_graph({"@context": "https://schema.org", "@graph": graph})
