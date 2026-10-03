"""The published Terms and Privacy Policy may only claim what PulseSoc does.

Both documents were rewritten (2026-10) because the previous versions described a
crypto product -- wallets, trading signals, a Telegram companion, Sports Edge --
that the member reading them could not find. Replacing that fiction with a
*newer* fiction is the failure mode this file exists to prevent, so the
assertions below are deliberately of two kinds:

* **Banned claims.** Statements that would be false today, each one a sentence a
  plausible rewrite would reach for: a commission rate, buyer-paid shipping,
  end-to-end encrypted DMs, two-factor authentication, a fixed retention window,
  verified seller identities, worldwide delivery, an age check. These are
  asserted absent from the rendered page.

* **Truth-linked claims.** Where the document states a fact the product controls,
  the expectation is read from the code that controls it rather than restated
  here. ``platform_fee_bps()`` is the live commission rate, so the Terms must say
  "no commission" exactly while that function returns 0 -- and the day an owner
  opens the three fee gates, this file goes red and names the sentence to change.
  A literal ``0`` pinned here would instead stay green through the one event it
  needed to catch.

The second kind is the point. A banned-phrase list alone cannot tell that a true
document has become false because production changed underneath it, and that is
how both of the previous documents got to be wrong without anybody editing them.

Run: python3 -m pytest tests/web_surface/test_legal_documents_describe_the_real_product.py
"""

from __future__ import annotations

import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="legal_documents_product_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import content_translation  # noqa: E402
from services.business_os.marketplace import policy as marketplace_policy  # noqa: E402


TERMS = "/terms"
PRIVACY = "/privacy"


def _visible(html: str) -> str:
    """What the member actually reads.

    Script and style bodies are stripped first: a banned phrase matched inside a
    JSON-LD blob or a CSS selector would be a false positive, and -- more to the
    point -- a true claim found only in a ``<script>`` is not a published
    statement to anybody.
    """

    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


#: Words that turn a mention into a denial. Checked over the text leading up to a
#: match, which is how these documents are actually written: the sentence names
#: the thing PulseSoc does not have ("does not currently offer two-factor
#: authentication") rather than avoiding the word. A plain term ban would forbid
#: exactly the disclosure the mission requires.
_NEGATORS = re.compile(r"\b(?:not|no|never|without|cannot|lacks|nor)\b", re.I)


def _unnegated(text: str, pattern: str, window: int = 140) -> list[str]:
    """Mentions of ``pattern`` that are *not* denied by nearby wording.

    The window looks backwards from the match and stops at the previous sentence
    boundary, so a denial in the preceding sentence cannot launder an affirmative
    claim in this one.
    """

    offenders = []
    for match in re.finditer(pattern, text, re.I):
        start = max(0, match.start() - window)
        lead = text[start:match.start()]
        lead = lead.rsplit(". ", 1)[-1]
        if not _NEGATORS.search(lead):
            offenders.append(text[max(0, match.start() - 60):match.end() + 40])
    return offenders


@pytest.fixture(scope="module")
def client():
    return bot.webhook_app.test_client()


@pytest.fixture(scope="module")
def pages(client):
    rendered = {}
    for path in (TERMS, PRIVACY):
        response = client.get(path)
        assert response.status_code == 200, f"{path} answered {response.status_code}"
        raw = response.get_data(as_text=True)
        rendered[path] = {"raw": raw, "visible": _visible(raw)}
    return rendered


# ---------------------------------------------------------------------------
# Banned claims: sentences that would be false if either document said them
# ---------------------------------------------------------------------------

#: (claim, pattern, why it is false today). Patterns are deliberately narrow --
#: they match the shape of the false *statement*, not the topic, so a document is
#: free to discuss commission or encryption truthfully.
BANNED = [
    (
        "a non-zero commission rate",
        r"\b(?:[1-9]\d*(?:\.\d+)?)\s*%\s*(?:platform\s+)?(?:commission|fee)\b",
        "platform_fee_bps() returns 0; no commission is charged on any method.",
    ),
    (
        "buyer-paid shipping",
        r"\b(?:shipping|delivery)\s+(?:costs?|charges?|fees?)\s+(?:are|is|will be)\s+(?:added|calculated|charged)",
        "Buyers are charged no separate shipping amount at checkout.",
    ),
    (
        "tax calculated at checkout",
        r"\btax(?:es)?\s+(?:is|are|will be)\s+(?:calculated|collected|added)\s+at\s+checkout",
        "No sales tax is calculated or collected at checkout.",
    ),
    (
        "end-to-end encrypted messaging",
        r"\b(?:are|is)\s+end-to-end\s+encrypted\b",
        "Direct messages and group chats are not end-to-end encrypted.",
    ),
    (
        "verified seller identity",
        r"\b(?:we|PulseSoc)\s+verif\w*\s+(?:the\s+)?(?:identity|identities)\s+of\s+(?:sellers|every seller)",
        "PulseSoc does not verify seller identity; its payment processor does its own onboarding.",
    ),
    (
        "age verification",
        r"\b(?:we|PulseSoc)\s+verif\w*\s+(?:your|a member's|the member's|every member's)\s+age\b",
        "No date of birth is collected and no age check exists; age is self-confirmed.",
    ),
    (
        "worldwide shipping",
        r"\b(?:ship|ships|shipping|deliver|delivers|delivery)\s+(?:to\s+)?(?:worldwide|globally|anywhere in the world|to every country)\b",
        "Marketplace shipping is restricted to the configured country set.",
    ),
    (
        "automatic account deletion on a timeline",
        r"\b(?:account|data)\s+(?:will be|is)\s+(?:automatically\s+)?(?:deleted|erased|purged)\s+within\s+\d+\s+days?\b",
        "Deletion is recorded and reviewed by hand; no worker completes it and no date is promised.",
    ),
    (
        "automatic data export delivery",
        r"\bexport\s+(?:will be|is)\s+(?:automatically\s+)?(?:emailed|sent|delivered)\s+(?:to you\s+)?within\b",
        "No export worker exists; exports are not delivered automatically.",
    ),
    (
        "a member-facing Telegram integration",
        r"\bTelegram\b",
        "No Telegram surface is offered to members; the bot token lives on a separate internal service.",
    ),
    (
        "third-party analytics or ad tracking being active",
        r"\b(?:we|PulseSoc)\s+use[s]?\s+(?:Google Analytics|Google Ads|PostHog)\b",
        "No third-party analytics or advertising tag is served; analytics are first-party.",
    ),
]


@pytest.mark.parametrize("path", [TERMS, PRIVACY])
@pytest.mark.parametrize("claim,pattern,why", BANNED, ids=[c[0] for c in BANNED])
def test_neither_document_makes_a_claim_the_product_cannot_support(pages, path, claim, pattern, why):
    found = re.findall(pattern, pages[path]["visible"], re.I)
    assert not found, (
        f"{path} appears to claim {claim}: {found[:3]}. {why} "
        "Either the product changed and this document is now behind it, or the "
        "sentence is wrong -- do not resolve this by loosening the pattern."
    )


# ---------------------------------------------------------------------------
# Fixed retention windows: a number here has to be one something enforces
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [TERMS, PRIVACY])
def test_no_fixed_retention_window_is_published(pages, path):
    """`/privacy-center` already publishes 90/180/730-day windows with no purge
    job behind them. Repeating that mistake in the Policy itself would make it the
    authoritative version of a promise nothing keeps, so retention is described by
    purpose instead. This fails if a duration reappears next to retention
    language -- and the fix is a retention job, not a sentence."""

    pattern = r"(?:retain|retained|retention|keep|kept|stored?)[^.]{0,120}?\b(\d+)\s*(?:days?|months?|years?)\b"
    found = re.findall(pattern, pages[path]["visible"], re.I)
    assert not found, (
        f"{path} publishes a fixed retention period ({found[:3]}). No retention "
        "worker enforces one. Describe retention by purpose, or ship the job first."
    )


# ---------------------------------------------------------------------------
# Truth-linked claims: expectation read from the code that decides it
# ---------------------------------------------------------------------------


def test_the_commission_the_terms_state_is_the_commission_the_code_charges(pages):
    """Reads `platform_fee_bps()` rather than pinning 0.

    The rate is env-gated behind three owner attestations. On the day those open,
    the Terms' "no commission" sentence becomes false with no code change to the
    document -- exactly the way the old documents rotted. This is the assertion
    that notices.
    """

    visible = pages[TERMS]["visible"]
    bps = marketplace_policy.platform_fee_bps()
    says_free = re.search(r"\bno commission\b", visible, re.I)
    if bps == 0:
        assert says_free, (
            "platform_fee_bps() is 0 but the Terms do not say sellers pay no "
            "commission. Sellers are entitled to read the rate they are charged."
        )
        assert re.search(r"\b0\s*%", visible), "the Terms state no commission without naming the 0% rate"
    else:
        assert not says_free, (
            f"platform_fee_bps() now returns {bps} ({bps / 100:g}%) but the Terms "
            "still tell sellers they pay no commission. Update the Terms and bump "
            "legal_acceptance.DOCUMENTS so sellers re-accept the rate."
        )


@pytest.mark.parametrize("path", [TERMS, PRIVACY])
def test_every_mention_of_a_second_factor_is_a_denial(pages, path):
    """No TOTP has shipped, so 2FA may only ever appear as something absent.

    Asserted as "every mention is negated" rather than "the words never appear",
    because the mission requires the opposite of silence here: a member deciding
    what to trust the account with has to be told the password is the only factor.
    An affirmative sentence is the defect; the denial is the deliverable.
    """

    offenders = _unnegated(pages[path]["visible"], r"\b(?:two-factor|two-step|2FA)\b")
    assert not offenders, (
        f"{path} mentions a second factor without denying it: {offenders}. "
        "No TOTP or other second factor has shipped."
    )


def test_the_shipping_scope_is_not_wider_than_the_configured_countries(pages):
    """A wider-than-reality delivery claim, checked against the country allowlist.

    ``MARKETPLACE_SHIPPING_COUNTRIES`` is unset in production, so the effective
    list is the conservative ``US`` default. While it holds one country, an
    un-denied mention of international delivery is a false claim.
    """

    configured = [
        value.strip().upper()
        for value in os.getenv("MARKETPLACE_SHIPPING_COUNTRIES", "US").split(",")
        if value.strip()
    ]
    assert configured, "no shipping countries configured; the default is US"
    visible = pages[TERMS]["visible"]
    if len(configured) == 1:
        offenders = _unnegated(
            visible, r"\b(?:international|worldwide|global)\s+(?:shipping|delivery)\b"
        )
        assert not offenders, (
            f"the Terms claim delivery wider than the configured {configured}: {offenders}"
        )
        assert re.search(r"limited to the United States", visible, re.I), (
            "shipping is restricted to US only, but the Terms do not say where a "
            "buyer can actually have an order delivered"
        )


def test_the_privacy_policy_discloses_translation_of_the_content_types_it_covers(pages):
    """Chat is in ``ALLOWED_CONTENT_TYPES``, so the member has to be told.

    Translation sends text to Google Cloud Translation. A Policy that lists every
    translatable surface *except* conversations would be accurate about the
    feature and silent about the only part a member would object to, so the
    disclosure is asserted against the allowlist itself: adding a content type
    there without naming it here fails.
    """

    visible = pages[PRIVACY]["visible"]
    assert "chat" in content_translation.ALLOWED_CONTENT_TYPES, (
        "chat left ALLOWED_CONTENT_TYPES; re-check what the Policy should still claim"
    )
    assert re.search(r"chat (?:messages|text)", visible, re.I), (
        "the Privacy Policy does not disclose that chat content can be sent for "
        "translation, but 'chat' is in content_translation.ALLOWED_CONTENT_TYPES"
    )
    assert re.search(r"Google Cloud Translation", visible, re.I), (
        "the Privacy Policy names no translation processor"
    )


def test_the_no_third_party_analytics_claim_matches_what_the_page_serves(pages):
    """Self-checking: the page is the evidence for its own claim.

    The Policy states that no third-party analytics or advertising tag loads. Both
    templates carry conditional Google tag blocks, so if an environment ever sets
    the measurement id, the claim and the page contradict each other on the same
    response -- which is the cheapest possible place to catch it.

    Only script bodies and fetched URLs are examined. The Policy names these
    vendors in prose in order to deny them, so scanning the whole response would
    make the disclosure itself the failure.
    """

    for path in (TERMS, PRIVACY):
        raw = pages[path]["raw"]
        loaded = " ".join(
            re.findall(r"<script\b.*?(?:</script>|/?>)", raw, re.S | re.I)
            + re.findall(r"(?:src|href)\s*=\s*[\"']([^\"']+)[\"']", raw, re.I)
        ).lower()
        for tag in ("googletagmanager.com", "google-analytics.com", "posthog", "connect.facebook.net"):
            assert tag not in loaded, (
                f"{path} loads {tag}, and the Privacy Policy states that no "
                "third-party analytics or advertising tag is served. Either remove "
                "the tag or stop claiming there is none."
            )


def test_the_privacy_policy_is_explicit_that_messages_are_not_end_to_end_encrypted(pages):
    """The absence of a false claim is not the same as a true one.

    App Store screenshots have claimed encryption this product does not have, so
    saying nothing would let a member keep an impression the platform created.
    """

    assert re.search(r"not\b[^.]{0,40}end-to-end encrypted", pages[PRIVACY]["visible"], re.I), (
        "the Privacy Policy does not state that messages are not end-to-end encrypted"
    )


def test_the_policy_describes_self_service_deletion_while_the_route_offers_it(pages):
    """Pessimistic fiction is still fiction.

    An earlier draft of this Policy said deletion was recorded for manual review
    and promised no completion date. That was written from the Privacy Center's
    unfinished control, and it was false: `/account/delete` takes a password and
    runs `permanently_delete_account` synchronously. Understating what a member
    can do is the same defect as overstating it, so this reads the url_map rather
    than trusting either document.
    """

    paths = {rule.rule for rule in bot.webhook_app.url_map.iter_rules()}
    visible = pages[PRIVACY]["visible"]

    if "/account/delete" not in paths:
        assert "/account/delete" not in pages[PRIVACY]["raw"], (
            "the Privacy Policy sends members to /account/delete, which no longer exists"
        )
        return

    assert "/account/delete" in pages[PRIVACY]["raw"], (
        "/account/delete deletes an account on the spot, but the Privacy Policy "
        "does not tell the member where it is"
    )
    stalling = _unnegated(visible, r"deletion\s+(?:request|is\s+recorded|is\s+reviewed)")
    assert not stalling, (
        "the Privacy Policy describes deletion as a request awaiting review, but "
        f"/account/delete completes it during the request: {stalling}"
    )
    assert re.search(r"\b(?:immediately|runs immediately|straight away)\b", visible, re.I), (
        "deletion happens during the request and the Policy should say so plainly"
    )


def test_both_documents_publish_the_version_they_are_accepted_under(pages):
    """Guards the pin in tests/test_legal_acceptance.py from the other side.

    That test asserts the constant matches the page. This one asserts the page
    says a date at all -- a document that stopped publishing one would make the
    constant unfalsifiable rather than wrong.
    """

    for path in (TERMS, PRIVACY):
        assert re.search(r"Last updated:\s*\w+\s+\d{4}", pages[path]["visible"]), (
            f"{path} publishes no 'Last updated' date, so no stored acceptance "
            "version can be checked against it"
        )


#: The surfaces a member passes through before or while committing to something:
#: the public entry point, both authentication forms, the cart, the storefront,
#: and the policy pages themselves. A document nobody can find is not published in
#: any sense that matters, and before this list existed the home page -- the single
#: most visited page on the site -- linked neither one.
REACHABILITY_SURFACES = [
    "/",
    "/login",
    "/signup",
    "/pulse/cart",
    "/pulse/marketplace",
    "/community-rules",
    "/refund-policy",
    "/privacy-center",
    "/legal/refunds",
]


@pytest.mark.parametrize("path", REACHABILITY_SURFACES)
def test_a_member_can_reach_both_documents_from_the_surfaces_that_bind_them(client, path):
    """Signed out, because that is who has to read them.

    Checked against the rendered response rather than the template, since several
    of these inherit their footer from a shared shell -- asserting against a
    template file would have passed for the cart while the page it produces was
    fine, and failed for pages whose links come from a helper.
    """

    response = client.get(path)
    assert response.status_code == 200, f"{path} answered {response.status_code} to a signed-out visitor"
    body = response.get_data(as_text=True)
    for document in ("/terms", "/privacy"):
        assert f'href="{document}"' in body or f"href='{document}'" in body, (
            f"{path} offers no link to {document}. A member agreeing to these "
            "documents has to be able to open them from where they agree."
        )


def test_no_owner_decision_marker_reaches_the_member(pages):
    """Two deferrals are marked for counsel in the source as Jinja comments.

    Jinja strips those server-side; an HTML comment would have shipped an
    internal TODO to a public legal page. This asserts the stripping actually
    happened, because the two are one character apart in the editor.
    """

    for path in (TERMS, PRIVACY):
        raw = pages[path]["raw"]
        assert "OWNER / COUNSEL DECISION REQUIRED" not in raw, (
            f"{path} ships an internal owner-decision marker to the client; it "
            "must be a Jinja comment ({# ... #}), not an HTML comment"
        )
        assert "DECISION REQUIRED" not in raw, f"{path} ships an internal decision marker"
