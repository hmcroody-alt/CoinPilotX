"""The four buyer-facing commerce policy pages Google Merchant Center requires.

`/returns`, `/refund-policy`, `/shipping` and `/contact`. Merchant Center will
not approve a Shopping account whose landing pages do not reach a return policy,
a refund policy, shipping information and a way to contact the seller, so these
four pages are a prerequisite for `/feeds/merchant-center.xml` doing anything at
all. That is why they live next to the feed's mission rather than in the legal
pile.

EVERY SENTENCE HERE IS A COMMITMENT, SO EVERY SENTENCE IS SOURCED
----------------------------------------------------------------
This is the hard part and it is the reason this module is prose-heavy. A returns
page is not marketing copy: it is the document a buyer, a card issuer and Google
will all hold this platform to. Writing "we offer hassle-free 30-day returns with
prepaid labels" would make these pages *pass* Merchant Center review and would be
a lie, which is a worse outcome than the 404 they replace -- the 404 blocks an
account, the lie invites chargebacks the platform would lose.

So each claim below traces to something that exists:

* The 14-day window, the four policy types and the exclusion list come from
  `docs/marketplace_returns_refunds.md`, which is the policy of record.
* "Disclosed at checkout before authorization" comes from
  `docs/marketplace_buyer_protection.md` and `docs/marketplace_rules.md`, and is
  enforced in code -- an order cannot enter payment until delivery method,
  shipping and tax have been validated.
* Refunds being server-authoritative and reconciled exactly once is
  `docs/marketplace_returns_refunds.md` again, implemented by the admin refund
  route and the dispute resolution route under `/admin/business-os/marketplace`.
* The *mechanism* a buyer uses is deliberately described as "contact support or
  open a dispute on the order", because that is what exists: there is a
  buyer-callable dispute endpoint and an admin-issued refund path, and there is
  no self-serve return-request flow, no `returned` order state and no prepaid
  label generation. A page promising a button that is not there is the same
  misrepresentation failure the feed module is built to avoid, arriving through
  a different door.

WHAT IS NOT CLAIMED, AND WHY EACH ABSENCE IS DELIBERATE
-------------------------------------------------------
* **No delivery timeframes.** `marketplace_listings.estimated_delivery` is a
  free-text column and 12 of the 15 publishable rows leave it empty. A
  site-wide "ships in 3-5 business days" would be invented, and inventing a
  shipping promise is exactly what `merchant_center_feed` refuses to do when it
  omits `g:shipping`. The two refusals have to agree or the page and the feed
  contradict each other.
* **No shipping rates.** Cost is per listing and per delivery method, resolved
  at checkout. There is no site-wide rate card to publish because there is no
  column that would hold one.
* **No free-returns claim.** Buyer-remorse return shipping is buyer-paid unless
  an individual seller offers otherwise, which is a per-seller fact this page
  cannot assert on their behalf.
* **No phone number.** `support@pulsesoc.com` is the channel that exists and is
  monitored. Merchant Center accepts email as a contact method; a phone number
  nobody answers is worse than none.

WHY FOUR PAGES AND NOT ONE
--------------------------
Merchant Center checks for the *policies* and would accept a combined page, and
a single page would be less work. Four, because Google's own guidance is that
each policy be findable on its own, and because a buyer looking for "how do I
send this back" should not have to read about contacting support to find it.

The risk of four pages is the one `seo/features.py` documents at length for the
feature pages: this site already carries 108 templated pages measuring 99.2%
identical, and they are `noindex` for it. Four policy pages sharing one template
could rebuild that problem in miniature. They do not, because the four subjects
genuinely differ -- a return window, a money-movement guarantee, a delivery
model and a contact channel share a house style and no sentences.
`tests/test_commerce_policy_pages.py` measures the rendered similarity and fails
if they converge, the same guard `tests/test_feature_pages.py` applies next door.

THE LEGAL NAME IS NOT THE BRAND
-------------------------------
`name` is PulseSoc, `legalName` is CoinPlotXAI Inc., both are true, and
`tests/test_site_identity.py` pins the split -- Apple records the App Store
seller as COINPLOTXAI INC., so a policy page that says "CoinPlotXAI Inc." where
it states who is liable is correct and must not be "fixed" to the brand.
"""

from __future__ import annotations

#: Where buyers are told to write. The one channel that exists and is monitored;
#: see the module docstring on why no phone number is published.
SUPPORT_EMAIL = "support@pulsesoc.com"

#: The company that signs things, as distinct from the brand. See
#: `tests/test_site_identity.py`.
LEGAL_NAME = "CoinPlotXAI Inc."

#: The return-request window for eligible physical goods, from
#: `docs/marketplace_returns_refunds.md`. A number Merchant Center reads and a
#: buyer relies on, so it is named once and referenced rather than retyped.
RETURN_WINDOW_DAYS = 14

POLICIES = (
    {
        "slug": "returns",
        "breadcrumb": "Returns",
        "h1": "Returns",
        "title": "Returns policy | PulseSoc Marketplace",
        "description": (
            f"Eligible physical goods bought on the PulseSoc Marketplace can be returned "
            f"within {RETURN_WINDOW_DAYS} days of delivery. How to start a return, what is "
            "excluded, and who pays return shipping."
        ),
        "lede": (
            f"Most physical items can be sent back within {RETURN_WINDOW_DAYS} days of "
            "delivery. Some categories cannot be, and the listing tells you which before "
            "you buy."
        ),
        "sections": [
            {
                "heading": f"The {RETURN_WINDOW_DAYS}-day window",
                "body": [
                    f"Eligible physical goods carry a {RETURN_WINDOW_DAYS}-day "
                    "return-request window, counted from the day the order is delivered "
                    "rather than the day it was placed.",
                    "Each listing carries one of four policy types: <strong>returns "
                    "accepted</strong>, <strong>final sale</strong>, <strong>defect "
                    "only</strong>, or a <strong>category policy</strong> that applies "
                    "versioned rules for what the item is. The type that applies to a "
                    "particular item is shown on its listing, and it is shown before "
                    "checkout rather than after.",
                ],
            },
            {
                "heading": "How to start one",
                "body": [
                    f"Email <a href=\"mailto:{SUPPORT_EMAIL}\">{SUPPORT_EMAIL}</a> with your "
                    "order number, or open a dispute on the order itself. Both routes reach "
                    "the same review.",
                    "There is deliberately no self-serve returns button and no automatically "
                    "generated shipping label. Returns are reviewed rather than granted on "
                    "request, because whether an item is a seller fault or a change of mind "
                    "determines who pays to ship it back, and that is a judgement rather "
                    "than a checkbox. We would rather say so than publish a button that "
                    "does not exist.",
                ],
            },
            {
                "heading": "Who pays return shipping",
                "body": [
                    "If the item arrived <strong>wrong, damaged, counterfeit or materially "
                    "different from its description</strong>, that is a seller fault and the "
                    "return is not at your cost.",
                    "If you simply changed your mind, return shipping is paid by you, unless "
                    "the individual seller offers free returns. Sellers set that themselves, "
                    "so it is a per-listing fact rather than a platform-wide promise.",
                ],
            },
            {
                "heading": "What cannot be returned",
                "body": [
                    "Personalised items, perishable goods, hygiene-sensitive items that have "
                    "been opened, digital products, and goods whose return is restricted by "
                    "law may all be excluded. Exclusions are applied by versioned category "
                    "rules, not decided case by case after the fact.",
                    "An excluded item still has the protections that do not depend on a "
                    "return: if it never arrived, or arrived broken, or was not what the "
                    "listing said, that is a dispute and not a return.",
                ],
            },
        ],
        "related": ("refund-policy", "shipping", "contact"),
    },
    {
        "slug": "refund-policy",
        "breadcrumb": "Refunds",
        "h1": "Refund policy",
        "title": "Refund policy | PulseSoc Marketplace",
        "description": (
            "How PulseSoc Marketplace refunds are calculated, where the money goes, and how "
            "long it takes. Refunds return to the original payment method and are reconciled "
            "exactly once."
        ),
        "lede": (
            "A refund goes back to the card or account that paid, for the amount that was "
            "taken, once."
        ),
        "sections": [
            {
                "heading": "Where the money goes",
                "body": [
                    "Refunds are issued to the original payment method. We cannot redirect a "
                    "refund to a different card, to a balance, or to someone else, because "
                    "the refund is processed by the payment provider against the original "
                    "charge rather than sent as a new payment.",
                    "How quickly it appears is then the bank's business rather than ours. "
                    "Card refunds typically post within a few business days of being issued, "
                    "and we have no way to accelerate that.",
                ],
            },
            {
                "heading": "What is included in the amount",
                "body": [
                    "A refund reconciles the buyer's funds, tax, shipping, the seller's "
                    "ledger, the reversal of the platform fee, the state held by the payment "
                    "provider, and the state of the order &mdash; and it does all of that "
                    "exactly once.",
                    "That last clause is the substance. Every refund is authoritative on the "
                    "server, so a retried request, a double-clicked button or a provider "
                    "webhook arriving twice cannot produce two refunds for one order. Partial "
                    "refunds are possible where only part of an order is affected.",
                ],
            },
            {
                "heading": "When a refund is the answer and when a dispute is",
                "body": [
                    "An agreed return, a cancelled order, an item that never shipped, or a "
                    "seller who cannot fulfil leads to a refund.",
                    "A disagreement about any of those is a dispute. Disputes are recorded "
                    "against the order and resolved by review, and the outcome can be a full "
                    "refund, a partial one, or none. Both the platform's record and the "
                    "payment provider's record are kept, so a resolved dispute is traceable "
                    "afterwards by both sides.",
                ],
            },
            {
                "heading": "What we do not promise",
                "body": [
                    "No guaranteed outcome. Refunds can be limited by how much of a digital "
                    "product was used, whether an item was delivered, abuse prevention, the "
                    "payment provider's own rules, and applicable law.",
                    f"{LEGAL_NAME} is not a bank, and does not hold your money. Card handling "
                    "and payouts are performed by third-party payment providers; the platform "
                    "keeps an internal ledger so the arithmetic is auditable, but the actual "
                    "movement of funds depends on the provider confirming it.",
                ],
            },
        ],
        "related": ("returns", "shipping", "contact"),
    },
    {
        "slug": "shipping",
        "breadcrumb": "Shipping",
        "h1": "Shipping and delivery",
        "title": "Shipping and delivery | PulseSoc Marketplace",
        "description": (
            "Delivery on the PulseSoc Marketplace is free to the buyer. What you authorise "
            "at checkout is the item price and nothing else: no shipping line, and no fee "
            "added by PulseSoc."
        ),
        "lede": (
            "Shipping is free to you. Not discounted, not free over a threshold &mdash; there "
            "is no shipping line on a PulseSoc order, and the total you see is the total "
            "before you authorise payment."
        ),
        "sections": [
            {
                "heading": "Shipping is free to the buyer",
                "body": [
                    "Every checkout path on this platform builds the amount you authorise "
                    "from the item price, the quantity, and any discount the seller applied. "
                    "The shipping component of that sum is zero. There is no threshold to "
                    "clear, no express tier to decline, and no per-seller rate card.",
                    "This is a property of how orders are priced rather than a promotion. "
                    "Where the platform shows a delivery estimate at all, the price beside it "
                    "is the literal word <strong>FREE</strong> rather than a formatted zero, "
                    "specifically so that no surface can render it as a charge of $0.00 next "
                    "to a total and invite the question of when it stops being zero.",
                    "The guarantee that survives any future change is the one about "
                    "disclosure, not the one about the number: an order cannot enter payment "
                    "until the server has validated the current price, currency, stock, "
                    "fulfilment method and final total. A cost that is not in the total you "
                    "were shown is not a cost you have agreed to.",
                ],
            },
            {
                "heading": "What the platform pays, and why you are told",
                "body": [
                    "Free to you is not free. Someone pays a courier, and on the "
                    "supplier-fulfilled part of the catalogue that is the platform. The "
                    "internal cost is tracked separately from anything a buyer or a seller "
                    "sees, which is deliberate: the two numbers answer different questions "
                    "and a single figure serving both is how a buyer ends up reading a "
                    "freight cost as their own.",
                    "It is stated here because a buyer who does not know who pays cannot "
                    "tell the difference between free delivery and delivery that has not "
                    "been quoted yet. Those are different situations and only one of them "
                    "ends in an unexpected charge.",
                ],
            },
            {
                "heading": "What you do pay",
                "body": [
                    "The item price multiplied by the quantity, less any discount the seller "
                    "has set. PulseSoc adds no commission, service charge or handling fee of "
                    "its own to the buyer&rsquo;s total &mdash; the amount the card is "
                    "authorised for is the merchandise total.",
                    "Where a payment provider or a jurisdiction requires tax to be collected "
                    "on an order, it is computed and shown at checkout as its own line "
                    "before authorisation. It is never folded into the item price.",
                ],
            },
            {
                "heading": "Delivery methods differ by listing",
                "body": [
                    "A listing is fulfilled as a physical shipment, as a digital delivery, or "
                    "by local pickup, and which one applies is set by the seller on the "
                    "listing. A digital item has nothing to ship, so the question of delivery "
                    "cost does not arise for it at all.",
                    "Where a listing is collected in person, the precise pickup details are "
                    "revealed at the appropriate stage of a paid order rather than published "
                    "on the listing, which protects the address of whoever is handing the "
                    "item over.",
                ],
            },
            {
                "heading": "Where an order can be delivered",
                "body": [
                    "PulseSoc does not deliver everywhere. Checkout will only accept a "
                    "delivery address in the countries the marketplace currently serves, and "
                    "the address step is the authoritative list &mdash; it is the same list "
                    "the payment provider enforces, so an address accepted there cannot be "
                    "refused at payment.",
                    "The list is short today and this page does not reproduce it, because a "
                    "country list copied onto a policy page is a country list that goes out "
                    "of date without anyone noticing. If your country is not offered at the "
                    "address step, the order cannot be placed rather than placed and then "
                    "cancelled.",
                ],
            },
            {
                "heading": "Why there are no delivery estimates on this page",
                "body": [
                    "Because we would have to invent them. Sellers can state an estimated "
                    "delivery time on an individual listing, and most currently do not. A "
                    "site-wide \"ships within 3&ndash;5 days\" would therefore be a number "
                    "with nothing behind it, and a delivery promise that nothing enforces is "
                    "the kind of claim that turns into a chargeback.",
                    "Where a seller has given an estimate, it appears on the listing and at "
                    "checkout. Where none is shown, none has been made. If an order has not "
                    "arrived and you have no estimate to measure it against, treat that as a "
                    f"reason to write to <a href=\"mailto:{SUPPORT_EMAIL}\">{SUPPORT_EMAIL}</a> "
                    "rather than as a term you agreed to.",
                ],
            },
            {
                "heading": "If it does not arrive",
                "body": [
                    "An order that never arrives is not a returns question, because there is "
                    "nothing to send back. Open a dispute on the order or email support, and "
                    "the outcome is a refund rather than a return.",
                    "Tracking, where the delivery method supports it, is attached to the "
                    "order rather than emailed separately, so the order is always the "
                    "authoritative place to look.",
                ],
            },
        ],
        "related": ("returns", "refund-policy", "contact"),
    },
    {
        "slug": "contact",
        "breadcrumb": "Contact",
        "h1": "Contact us",
        # The only one of the four whose subject schema.org names directly. The
        # other three are `WebPage`, because there is no `ReturnPolicyPage` type
        # and `MerchantReturnPolicy` describes a policy attached to an offer,
        # not a page a person reads.
        "page_type": "ContactPage",
        "title": "Contact PulseSoc | Support, marketplace orders and security",
        "description": (
            f"Reach PulseSoc at {SUPPORT_EMAIL} for help with an account, a marketplace "
            "order, a return or a refund. Security reports have their own address."
        ),
        "lede": (
            "One address for support, a separate one for security reports, and the order "
            "number is the thing that makes a marketplace question answerable."
        ),
        "sections": [
            {
                "heading": "Support",
                "body": [
                    f"<a href=\"mailto:{SUPPORT_EMAIL}\">{SUPPORT_EMAIL}</a> reaches the "
                    "people who can look at an account, an order, a return or a refund.",
                    "For anything about a marketplace purchase, include the order number. "
                    "Orders are the record everything else is attached to &mdash; payment, "
                    "delivery method, tracking, disputes and refunds all hang off the order "
                    "&mdash; so with the number a question can be answered in one reply, and "
                    "without it the first reply has to ask for it.",
                ],
            },
            {
                "heading": "Security reports",
                "body": [
                    "If you have found a vulnerability, write to "
                    "<a href=\"mailto:security@pulsesoc.com\">security@pulsesoc.com</a> "
                    "rather than to support, and please do not include working exploit steps "
                    "in a first email to a shared inbox.",
                    "Reports about a user, a post or a listing are not security reports: "
                    "those are handled by reporting the item itself in the app, which "
                    "attaches the report to the thing being reported.",
                ],
            },
            {
                "heading": "Who you are writing to",
                "body": [
                    f"PulseSoc is operated by {LEGAL_NAME}. PulseSoc is the brand and "
                    f"{LEGAL_NAME} is the company; both names are correct and you will see "
                    "each of them in different places, including on the App Store.",
                    "The marketplace is a platform: for a question about a specific item, "
                    "its condition or its delivery, the seller is the party who knows, and "
                    "support can put the question to them. For anything about payment, "
                    "refunds, policy or account access, write to support directly.",
                ],
            },
            {
                "heading": "Before you write",
                "body": [
                    "Returns, refunds and delivery each have a page that probably answers the "
                    "question faster than an email round trip: <a href=\"/returns\">returns</a>, "
                    "<a href=\"/refund-policy\">refunds</a> and "
                    "<a href=\"/shipping\">shipping</a>.",
                    "We do not publish a phone number. The email addresses above are the "
                    "channels that are actually monitored, and a number that rings in an "
                    "empty room would be worse than not listing one.",
                ],
            },
        ],
        "related": ("returns", "refund-policy", "shipping"),
    },
)

BY_SLUG = {policy["slug"]: policy for policy in POLICIES}


def canonical_path(slug):
    """``/returns``, not ``/policies/returns``.

    Top-level on purpose. These are the URLs a person guesses, the URLs Merchant
    Center's reviewers look for, and the URLs a footer link is shortest for.
    """

    return f"/{slug}"


def all_paths():
    """Every policy path, for the sitemap and for the tests that walk them."""

    return tuple(canonical_path(policy["slug"]) for policy in POLICIES)


def page(slug, canonical_url):
    """The render context for one policy page, or None if the slug is not ours.

    Returning None rather than raising, matching `seo.features.detail_page`, so
    the route can 404 an unknown slug instead of serving a soft 404 at 200.
    """

    policy = BY_SLUG.get(slug)
    if not policy:
        return None
    return {
        "canonical": canonical_url(canonical_path(slug)),
        "breadcrumb": policy["breadcrumb"],
        "title": policy["title"],
        "description": policy["description"],
        "h1": policy["h1"],
        "lede": policy["lede"],
        "sections": policy["sections"],
        "page_type": policy.get("page_type", "WebPage"),
        "related": [
            {
                "path": canonical_path(other),
                "breadcrumb": BY_SLUG[other]["breadcrumb"],
                "h1": BY_SLUG[other]["h1"],
            }
            for other in policy["related"]
        ],
    }
