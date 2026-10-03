"""The /about page: who publishes this, and what the product actually is.

Why this is a module rather than the f-string it replaces
---------------------------------------------------------
`/about` was a single `Response(f"...")` in `bot.py` -- a complete HTML
document with its own inline CSS, which made it the fourth independent design
for a public page on this domain. It carried no `robots` directive, hardcoded
its own canonical rather than asking `services.search_visibility`, and
published an `Organization` node with no `@id`, so the one page whose subject
*is* the publisher corroborated nothing about the publisher. `seo/schema.py`
describes that defect from the other side: "`/` and `/about` published
Organization nodes with no `@id` at all, which join nothing and corroborate
nothing."

Why the copy changed completely
-------------------------------
The old page was not stale at the edges, it was about a different product. Its
title was "About CoinPlotXAI | AI Crypto Intelligence, Scam Protection and
Arena Training" and its `h1` read "A safer AI command center for crypto
learning, risk awareness, and simulation." Its seven sections were Mission, AI
+ Human Psychology, Arena Training Ecosystem, Scam Protection, Privacy +
Security, Continuous Innovation and Educational Disclaimer, and its three
buttons went to /signup, /arena-preview and /scam-shield/scan.

Meanwhile the product is a social app with a marketplace that takes real card
payments. The page a person lands on when they want to know who they are
buying from described an educational crypto simulator and did not mention that
anything could be bought at all.

The two names
-------------
The most useful thing this page can do is explain why there are two names,
because that confusion is not a branding nicety -- it is the thing a buyer
hits when they look at their App Store receipt. Apple records the seller as
COINPLOTXAI INC. while every screen in the app says PulseSoc. A person who
cannot reconcile those two names has a reasonable next step available to them,
and it is to dispute the charge.

So `name` and `legalName` are both stated, in prose, on the page a person
reaches by searching for either one. `seo.schema.organization_schema` already
makes the same split in structured data for the same reason.

What is deliberately absent
---------------------------
No team size, no founding date, no headquarters, no user count, no award, no
security certification and no uptime claim. Not one of those can be read out of
this repository, and an About page is the single most tempting place on a site
to write something warm that nobody can check. The owner's instruction is that
there be no trust signals this product has not earned, which rules out the
invented ones first.

Two sentences are computed, not typed
------------------------------------
The two sentences in "Buying and selling" that describe money -- what a buyer is
charged and how they can pay -- are built from `BUYER_SERVICE_FEE_CENTS` and
`marketplace_card_payments_enabled()`, the same values checkout reads. Every
other sentence on this page is a structural fact about the product and is typed.
`seo/features.py` records why the distinction is worth the indirection: the
Marketplace feature page told buyers in five places that card payment was
unavailable while production had it enabled against a live key. Prose does not
fail a build, so a claim that lives in a flag has to be read from the flag.

The "What PulseSoc does not do" section is here for the same reason the feature
pages carry theirs: those sentences are the ones a reader cannot get anywhere
else, and the end-to-end encryption one in particular is a security claim this
repo cannot honour. `seo/features.py` sources each of them.
"""

from services.business_os.marketplace.policy import BUYER_SERVICE_FEE_CENTS
from services.marketplace_payment_pause import marketplace_card_payments_enabled

from .schema import SUPPORT_EMAIL

CANONICAL_PATH = "/about"


def _buyer_total_sentence():
    """What a buyer is charged, read from the constant checkout adds."""
    if BUYER_SERVICE_FEE_CENTS == 0:
        return (
            "A buyer pays the item price and nothing else. "
            "<a href=\"/shipping\">Delivery is free to the buyer</a> &mdash; there is no "
            "shipping line on a PulseSoc order &mdash; and PulseSoc adds no service charge or "
            "handling fee to the total you authorise."
        )
    return (
        "<a href=\"/shipping\">Delivery is free to the buyer</a> &mdash; there is no shipping "
        "line on a PulseSoc order. PulseSoc does add a service fee, and the total you authorise "
        "at checkout is itemised before you confirm it."
    )


def _payment_methods_sentence():
    """How a buyer pays, read from the flag checkout reads.

    Not typed as prose, for the reason `seo/features.py` records at length: the
    Marketplace feature page asserted in five places that card payment was
    unavailable while production had it enabled against a live key, and nothing
    made anyone edit the sentence because a sentence is not a build failure.
    This page says less than that one does, but it must not say a different
    thing.
    """
    if marketplace_card_payments_enabled():
        return (
            "Depending on the listing, payment is taken by card in the app or settled in cash on "
            "local pickup. Taking card payment also requires that seller to have finished payment "
            "onboarding, so the payment options shown on the listing and at checkout are the "
            "authoritative answer for that particular item."
        )
    return (
        "Payment is settled in cash on local pickup. Card payment in the app is switched off at "
        "present, so the payment options shown on the listing and at checkout are the "
        "authoritative answer for that particular item."
    )


def page(canonical_url):
    """The render context for /about.

    Takes `canonical_url` rather than building the URL, so that this page is
    subject to the same alias and host resolution as every other public page
    instead of hardcoding `https://pulsesoc.com/about` the way the f-string did.
    """

    return {
        "canonical": canonical_url(CANONICAL_PATH),
        "breadcrumb": "About",
        # Renders through `seo.schema.commerce_policy_graph`, which is the one
        # graph builder that attaches neither a `MobileApplication` node nor a
        # `Service` node with `serviceType: "AI intelligence"`. The app has its
        # own node under `#app` on /app and does not want a second declaration
        # here, and the AI service node would be the old product talking.
        # `AboutPage` is the schema.org type for this page's subject, the same
        # reason /contact passes `ContactPage`.
        "page_type": "AboutPage",
        "title": "About PulseSoc — the social app, and the company behind it",
        "description": (
            "PulseSoc is a social app for iPhone with a feed, reels, live video, messages, "
            "calls and a marketplace. It is published by CoinPlotXAI Inc., which is the name "
            "Apple records as the seller."
        ),
        "h1": "About PulseSoc",
        "lede": (
            "A social app for iPhone, with a marketplace attached to it. This page is the "
            "plain account of what it does, who publishes it, and the things it does not do."
        ),
        "sections": [
            {
                "heading": "What PulseSoc is",
                "body": [
                    "PulseSoc is a social network that runs as an iPhone app. You post to a "
                    "feed, publish short vertical video to reels, go live, send direct "
                    "messages, make voice and video calls, join topic groups and live audio "
                    "rooms, and keep a profile that collects what you have made.",
                    "Attached to that is a marketplace, where a member can list something for "
                    "sale and another member can buy it. The social half and the commerce half "
                    "are the same account and the same app rather than two products sharing a "
                    "name.",
                    "It is free to download and runs on iOS 15.1 or later. There is a Premium "
                    "subscription, billed through your Apple ID, and nothing described above "
                    "requires it.",
                ],
            },
            {
                "heading": "Who publishes it, and why you may have seen another name",
                "body": [
                    "PulseSoc is the product. <strong>CoinPlotXAI Inc.</strong> is the company "
                    "that publishes it, and is the entity named in the "
                    "<a href=\"/terms\">terms of service</a> and the "
                    "<a href=\"/privacy\">privacy policy</a>.",
                    "Apple records the App Store seller as COINPLOTXAI INC., so that is the "
                    "name you may see against a download or a Premium subscription even though "
                    "every screen in the app says PulseSoc. The two names are one company. It "
                    "is written down here because a name you do not recognise next to a charge "
                    "is a good reason to be suspicious, and the answer should be easier to find "
                    "than a dispute form.",
                    "If you are looking at something you cannot place, write to "
                    f"<a href=\"mailto:{SUPPORT_EMAIL}\">{SUPPORT_EMAIL}</a> before disputing "
                    "it and a person will tell you what it was.",
                ],
            },
            {
                "heading": "Where it came from",
                "body": [
                    "This product began as a crypto intelligence tool, which is where the "
                    "company name comes from. That part was not thrown away: the app still "
                    "carries a markets screen, and the website still publishes "
                    "<a href=\"/ai-market-analysis\">market analysis</a>, "
                    "<a href=\"/crypto-safety\">scam and safety tooling</a> and "
                    "<a href=\"/portfolio-intelligence\">portfolio tracking</a>.",
                    "It is a section of a social app now rather than the whole of the product. "
                    "Anything it says about markets is educational context: it is not financial, "
                    "investment or tax advice, no trade is ever executed for you, and PulseSoc "
                    "never asks for a seed phrase, a private key or a wallet password.",
                ],
            },
            {
                "heading": "Buying and selling",
                "body": [
                    _buyer_total_sentence(),
                    _payment_methods_sentence(),
                    "If something arrives wrong or does not arrive, the "
                    "<a href=\"/returns\">returns</a> and "
                    "<a href=\"/refund-policy\">refund</a> pages set out what happens, and an "
                    "order that never arrived is refunded rather than returned.",
                ],
            },
            {
                "heading": "What PulseSoc does not do",
                "body": [
                    "Direct messages are <strong>not end-to-end encrypted</strong>. The "
                    "transport is TLS and the messages are stored in a form the service can "
                    "read. Anything implying otherwise would be a security claim this product "
                    "cannot honour, so it is stated here rather than omitted.",
                    "Calls are between two people; there is no group calling. Screen sharing is "
                    "not implemented on calls or on live video. There are no hashtags and no "
                    "@-mentions &mdash; nothing in the posting pipeline parses either one.",
                    "These are the things people otherwise discover after installing, and "
                    "discovering them then is worse for everyone than reading them now.",
                ],
            },
            {
                "heading": "Reaching a person",
                "body": [
                    "<a href=\"/help\">Help</a> answers questions without anyone having to get "
                    "involved. <a href=\"/contact\">Contact</a> and "
                    f"<a href=\"mailto:{SUPPORT_EMAIL}\">{SUPPORT_EMAIL}</a> reach a person when "
                    "something has actually gone wrong.",
                    "For an account, an order or a payment, write from the email address on the "
                    "account where you can &mdash; it is the fastest way to be sure we are "
                    "talking about the right one.",
                ],
            },
        ],
        "related": [
            {"path": "/app", "label": "PulseSoc for iPhone"},
            {"path": "/features", "label": "What the app does, one page each"},
            {"path": "/help", "label": "Help"},
            {"path": "/terms", "label": "Terms of service"},
            {"path": "/privacy", "label": "Privacy policy"},
            {"path": "/contact", "label": "Contact"},
        ],
    }
