"""`/about` has to describe the product that exists and the company that bills you.

It did neither. The page was a single `Response(f"...")` in `bot.py` titled "About
CoinPlotXAI | AI Crypto Intelligence, Scam Protection and Arena Training", whose
`h1` offered "a safer AI command center for crypto learning, risk awareness, and
simulation" and whose seven sections were Mission, AI + Human Psychology, Arena
Training Ecosystem, Scam Protection, Privacy + Security, Continuous Innovation and
Educational Disclaimer. Its three buttons went to /signup, /arena-preview and
/scam-shield/scan.

Meanwhile the product is a social app with a marketplace that takes real card
payments, and `/about` is the page a buyer lands on when they want to know who they
are buying from. It did not mention that anything could be bought.

What this file pins, and why each one is here rather than left to a reading
---------------------------------------------------------------------------
Every assertion below is about a *failure mode*, not a wording preference. A test
that pinned the prose would have to be edited by anyone improving a sentence, and a
test everybody edits is a test nobody reads.

* **The crypto identity does not come back.** Not by a merge, not by someone
  restoring the old f-string, not by a well-meant mention of Arena. The page may say
  where the product came from — it does — but it cannot be *about* that.
* **Both company names appear.** Apple records the App Store seller as COINPLOTXAI
  INC. while every screen in the app says PulseSoc. A member who cannot reconcile
  those two names has a reasonable next step available to them and it is to dispute
  the charge. This is the page that has to answer it.
* **The money sentences track configuration.** The two sentences describing what a
  buyer pays and how they can pay are computed from `BUYER_SERVICE_FEE_CENTS` and
  `marketplace_card_payments_enabled()`. The test renders *both* flag states,
  because the defect this guards against is the one `seo/features.py` records: the
  Marketplace feature page asserted in five places that card payment was
  unavailable while production had it enabled against a live key, and the test that
  existed only ever rendered the CI default, so it held the false copy in place.
* **No rate is printed.** `PROPOSED_PLATFORM_FEE_BPS` is 500 and the owner's
  instruction is that no 5% or 10% figure be published. A number nobody has been
  charged is worse than no number.
* **It is a whole document with the shared head.** The old page had its own inline
  CSS, its own hardcoded canonical and no `robots` directive, which made it the
  fourth independent design for a public page on one domain.
* **It is readable signed out**, because the people it is written for are deciding
  whether to install and whether a charge is legitimate.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="about_page_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from seo import about as seo_about  # noqa: E402
from services import route_auth  # noqa: E402
from services import search_visibility as sv  # noqa: E402


#: Vocabulary from the product this page used to be about. `Arena` and `Scam Shield`
#: are named surfaces; `simulation` and `virtual dollars` are the claims that made
#: the old page describe a trainer rather than a marketplace.
RETIRED_IDENTITY = [
    "Arena",
    "Scam Shield",
    "Wallet Intel",
    "virtual dollar",
    "virtual portfolio",
    "simulation",
    "Start Free",
    "/arena-preview",
    "/scam-shield",
    "command center",
]


@pytest.fixture(scope="module")
def client():
    return bot.webhook_app.test_client()


def visible_text(html: str) -> str:
    """The words a reader actually gets, with the shared footer excluded.

    The footer links to `/terms` and `/privacy` from every page on the domain, so
    reading it back here would let the footer satisfy assertions that are about
    this page's own body.
    """

    body = html.split("<body", 1)[1].split("<footer", 1)[0]
    text = re.sub(r"<[^>]+>", " ", body)
    text = text.replace("&mdash;", "—").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).strip()


def test_about_is_served_as_a_whole_document_to_a_signed_out_visitor(client):
    response = client.get("/about")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "<!doctype html>" in html.lower()
    assert f'<link rel="canonical" href="{sv.CANONICAL_ORIGIN}/about">' in html
    assert 'name="robots"' in html
    assert "index" in re.search(r'name="robots" content="([^"]+)"', html).group(1)


def test_about_is_declared_public_with_a_reason():
    """The default-deny route-auth gate takes a declaration, not an inference.

    Saying it here as well says *which* answer is right for this path: an /about
    behind `@auth_required` would satisfy that gate and be useless to everyone it
    is written for.
    """

    declaration = route_auth.declaration_of(bot.webhook_app.view_functions["about_page"])
    assert declaration, "/about carries no route_auth declaration"
    assert declaration["kind"] == route_auth.AUTH_PUBLIC
    assert declaration["reason"].strip()


def test_about_does_not_describe_the_crypto_training_product(client):
    text = visible_text(client.get("/about").get_data(as_text=True))
    present = [term for term in RETIRED_IDENTITY if term.lower() in text.lower()]
    assert present == [], (
        "/about is describing the retired crypto training product again: "
        f"{present}. The page may say where PulseSoc came from, and it does, but "
        "it cannot be about that."
    )


def test_about_says_what_the_product_is(client):
    """A buyer should not have to infer that this is a social app with a shop."""

    text = visible_text(client.get("/about").get_data(as_text=True)).lower()
    for term in ("social", "marketplace", "iphone", "reels", "messages"):
        assert term in text, f"/about never mentions {term!r}"


def test_about_reconciles_the_two_company_names(client):
    """The one question only this page can answer.

    Apple records the seller as COINPLOTXAI INC.; every screen in the app says
    PulseSoc. A charge under a name you do not recognise is a good reason to be
    suspicious, and the answer has to be easier to find than a dispute form.
    """

    text = visible_text(client.get("/about").get_data(as_text=True))
    assert "PulseSoc" in text
    assert "CoinPlotXAI Inc." in text, "/about never names the publishing company"
    assert "COINPLOTXAI INC." in text, (
        "/about never shows the App Store seller name in the form Apple records it, "
        "which is the string a member is trying to match against their receipt"
    )
    assert "support@" in text, (
        "/about tells a member a charge may carry an unfamiliar name and gives them "
        "nowhere to ask about it"
    )


def test_about_never_publishes_a_platform_fee_rate(client):
    """`PROPOSED_PLATFORM_FEE_BPS` is 500 and no seller has been charged it."""

    text = visible_text(client.get("/about").get_data(as_text=True))
    for rate in ("5%", "10%", "5 percent", "10 percent"):
        assert rate not in text, f"/about publishes a platform fee rate: {rate}"


def test_about_does_not_name_the_payment_processor(client):
    """Who clears the card is an implementation detail and a phishing hook.

    A buyer cannot verify it and a scammer can borrow it, so the page describes
    what is charged rather than which vendor charges it.
    """

    text = visible_text(client.get("/about").get_data(as_text=True)).lower()
    assert "stripe" not in text


@pytest.mark.parametrize("card_enabled", [True, False])
def test_the_payment_sentences_agree_with_the_flags_either_way(client, monkeypatch, card_enabled):
    """Rendered under both settings, so neither state can be the premise.

    The predecessor of this test rendered only the CI default, which is how a page
    came to tell buyers for weeks that they could not pay by card while production
    was taking card payments on the whole catalogue.
    """

    monkeypatch.setenv("MARKETPLACE_CARD_PAYMENTS_ENABLED", "true" if card_enabled else "false")
    text = visible_text(client.get("/about").get_data(as_text=True))

    assert "Delivery is free to the buyer" in text, (
        "the buyer-cost sentence stopped stating the one fact Merchant Center and "
        "every buyer cares about"
    )
    if card_enabled:
        assert "by card in the app" in text
        assert "switched off" not in text
    else:
        assert "Card payment in the app is switched off" in text
        assert "payment is taken by card in the app" not in text

    # True in both branches: whatever the global flag says, the listing and the
    # checkout are what bind, because a seller also has to have finished payment
    # onboarding before a card can be taken for their item.
    assert "authoritative answer for that particular item" in text


def test_the_structured_data_joins_the_canonical_publisher_node(client):
    """The old page published an Organization node with no `@id`.

    A node with no `@id` joins nothing and corroborates nothing, and this is the
    one page whose subject *is* the publisher. Worse, its `description` described
    an educational crypto simulation platform — under the canonical `@id` that
    would not sit beside the WebSite's description, it would merge with it, and the
    contradiction would be resolved by crawl order.
    """

    html = client.get("/about").get_data(as_text=True)
    graph = json.loads(
        re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1)
    )["@graph"]
    by_type = {node["@type"]: node for node in graph}

    assert by_type["Organization"]["@id"] == f"{sv.CANONICAL_ORIGIN}/#organization"
    assert by_type["Organization"]["name"] == "PulseSoc"
    assert by_type["Organization"]["legalName"] == "CoinPlotXAI Inc."
    assert by_type["AboutPage"]["@id"] == f"{sv.CANONICAL_ORIGIN}/about#webpage"
    assert "MobileApplication" not in by_type, (
        "the app has its own node on /app and does not want a second declaration here"
    )
    assert "Service" not in by_type, (
        'the Service node defaults to serviceType "AI intelligence", which is the '
        "old product talking"
    )


def test_every_internal_link_on_the_page_resolves(client):
    """An About page is where someone goes when something has confused them."""

    html = client.get("/about").get_data(as_text=True)
    main = html.split("<body", 1)[1].split("<footer", 1)[0]
    broken = {}
    for path in sorted(set(re.findall(r'href="(/[^"#]*)"', main))):
        status = client.get(path).status_code
        if status not in (200, 301, 308):
            broken[path] = status
    assert broken == {}, f"/about links to paths that do not resolve: {broken}"


def test_the_page_states_the_claims_a_reader_cannot_get_elsewhere(client):
    """The absent-features section, for the reason the feature pages carry theirs.

    The end-to-end encryption sentence in particular is load-bearing: the App Store
    screenshots claim E2E encryption the product does not have, so a page that went
    quiet about it would be the second surface implying it.
    """

    text = visible_text(client.get("/about").get_data(as_text=True)).lower()
    assert "not end-to-end encrypted" in text
    assert "no group calling" in text or "there is no group calling" in text
    assert "hashtag" in text


def test_the_module_does_not_hardcode_its_own_canonical(client):
    """It takes `canonical_url`, so aliases and host resolution reach it too.

    The f-string wrote `https://pulsesoc.com/about` into the document directly,
    which is the one page on the domain that could not be moved or aliased.
    """

    page = seo_about.page(lambda path: f"https://example.test{path}")
    assert page["canonical"] == "https://example.test/about"
