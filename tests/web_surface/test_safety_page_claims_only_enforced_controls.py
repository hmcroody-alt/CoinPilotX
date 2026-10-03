"""`/safety` may name a control only where the service enforces it.

The page used to be a `seo/content.py` entry titled "PulseSoc Safety Center |
Crypto Risk and Account Protection", whose `answer` was "CoinPlotXAI Inc. never
holds user funds and never asks for private wallet credentials" and whose four
`points` were "Never holds funds", "Public wallet data only", "Stripe website
billing" and "Educational AI intelligence only". A person reaches /safety
because someone is harassing them, because they think their account is
compromised, or because a listing smells wrong. None of those four sentences is
a thing they can do.

Why this file is mostly about what the page must NOT say
-------------------------------------------------------
Safety copy is the most rewarding place on a website to overclaim and the least
likely to be checked, and the pressure runs one way: every control in the
settings screen looks like something worth advertising. Two of them are not, and
the reason is not visible from the settings screen.

* **Two-factor authentication.** `/api/account/2fa/enable` sets
  `users.two_factor_enabled=1` and that column has no reader in any
  authentication path -- only a security *score* and the settings card that
  offers the button. There is no TOTP secret, no enrolment, no challenge. A page
  claiming 2FA tells a member their account is protected by something that never
  runs, which is worse than saying nothing: it is the sentence that stops them
  picking a better password.
* **Message-request filtering.** `message_requests` is offered as
  everyone/followers/none and its value is validated on the way in, but no send
  path reads it. Blocking is what actually stops a message.

`test_the_page_may_claim_2fa_the_day_2fa_exists` is the counterpart, and it is
written to fail when the defect is fixed. If anyone implements a real second
factor, that test goes red and tells them /safety is now allowed to say so --
rather than leaving the page permanently silent about a control that by then
works. The same trick is used by `PENDING_PUBLICATION` in
`test_app_canonical_legal_urls.py`: the list empties because the file goes red.

What is positively pinned
-------------------------
Only the controls read in the source: blocking (`bot.py:115899` via
`pulse_social_graph_service.block_user`, which writes both block tables),
reporting into a queue a person works (`bot.py:105726` writing `pulse_reports`,
`bot.py:105892` listing them, `bot.py:110885` actioning one), automated
screening whose `blocked` verdict is filtered out of reads rather than merely
recorded (`services/pulse_feed_engine.py:3152`, enforced at `bot.py:52095`,
`44916`, `45329`, `43407`), comment controls refused server-side
(`bot.py:95695`), device and session revocation (`bot.py:92344`, `92356`,
`8801`), and deletion behind a password (`bot.py:9109`).
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="safety_page_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from seo import content as seo_content  # noqa: E402
from seo import safety as seo_safety  # noqa: E402
from services import route_auth  # noqa: E402
from services import search_visibility as sv  # noqa: E402


#: Vocabulary from the product this page used to be about.
RETIRED_IDENTITY = [
    "Arena",
    "Scam Shield",
    "Wallet Intel",
    "Safety Center",
    "Never holds funds",
    "Public wallet data only",
    "Educational AI intelligence",
    "CoinPlotXAI",
]

#: Phrasings that would amount to claiming a second authentication factor. Each
#: is checked case-insensitively against the page's visible words.
TWO_FACTOR_CLAIMS = [
    "two-factor",
    "two factor",
    "2fa",
    "authenticator",
    "one-time code",
    "second factor",
]


@pytest.fixture(scope="module")
def client():
    return bot.webhook_app.test_client()


@pytest.fixture(scope="module")
def text(client):
    """The words a reader actually gets, with the shared footer excluded.

    The footer links to /terms and /privacy from every page on the domain, so
    reading it back here would let the footer satisfy assertions about this
    page's own body.
    """

    html = client.get("/safety").get_data(as_text=True)
    body = html.split("<body", 1)[1].split("<footer", 1)[0]
    stripped = re.sub(r"<[^>]+>", " ", body)
    stripped = stripped.replace("&mdash;", "—").replace("&amp;", "&")
    return re.sub(r"\s+", " ", stripped).strip()


def test_safety_is_served_as_a_whole_document_to_a_signed_out_visitor(client):
    """A member locked out of their account is one of the readers it is for."""

    response = client.get("/safety")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "<!doctype html>" in html.lower()
    assert f'<link rel="canonical" href="{sv.CANONICAL_ORIGIN}/safety">' in html
    assert "index" in re.search(r'name="robots" content="([^"]+)"', html).group(1)


def test_safety_is_declared_public_with_a_reason():
    declaration = route_auth.declaration_of(bot.webhook_app.view_functions["safety_page"])
    assert declaration, "/safety carries no route_auth declaration"
    assert declaration["kind"] == route_auth.AUTH_PUBLIC
    assert declaration["reason"].strip()


def test_safety_has_its_own_route_rather_than_the_slug_catch_all():
    """A static rule, so it cannot fall back to a `seo/content.py` entry.

    The entry is gone, so without this route /safety would 404 through
    `seo_topic_page`.
    """

    rules = {str(r.rule) for r in bot.webhook_app.url_map.iter_rules()}
    assert "/safety" in rules
    assert "safety" not in seo_content.SEO_PAGES, (
        "the crypto-era /safety entry is back in SEO_PAGES; the page has a module now"
    )


def test_safety_stays_in_the_sitemap_after_the_extraction():
    """Removing the SEO_PAGES entry must not silently drop the URL.

    `all_public_paths()` built /safety from `SEO_PAGES` before the extraction. A
    rewrite that de-indexed a live page would be worse than the copy it replaced.
    """

    assert "/safety" in seo_content.all_public_paths()


def test_safety_does_not_describe_the_crypto_product(text):
    present = [term for term in RETIRED_IDENTITY if term.lower() in text.lower()]
    assert present == [], f"/safety is describing the retired crypto product again: {present}"


def test_safety_names_the_controls_that_actually_exist(text):
    """Each of these is enforced in the source; see the module docstring."""

    lowered = text.lower()
    for control in ("block", "report", "mute", "hide", "comment"):
        assert control in lowered, f"/safety never mentions {control!r}"


def test_safety_does_not_claim_two_factor_authentication(text):
    """The claim that would be false, and the easiest one to add by accident.

    `users.two_factor_enabled` is written by `/api/account/2fa/enable` and read
    by nothing that authenticates anybody. Anyone auditing the settings screen
    for things to put on this page will find a 2FA switch and be wrong.
    """

    lowered = text.lower()
    claimed = [phrase for phrase in TWO_FACTOR_CLAIMS if phrase in lowered]
    assert claimed == [], (
        f"/safety claims a second authentication factor: {claimed}. "
        "`two_factor_enabled` has no reader in any authentication path -- there is no "
        "TOTP secret, no enrolment and no challenge. See "
        "test_the_page_may_claim_2fa_the_day_2fa_exists."
    )


def test_the_page_may_claim_2fa_the_day_2fa_exists():
    """Written to fail when the defect is fixed, so the page can be updated.

    Without this, /safety would stay permanently silent about a control that by
    then works, and the test above would be enforcing a stale fact. If this goes
    red, a second factor now exists: say so on /safety and delete this test.
    """

    roots = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), name)
        for name in ("bot.py", "services", "pulse_communications_v2")
    ]
    mechanism = re.compile(r"\btotp\b|\bsecond_factor\b|\btwo_factor_required\b", re.I)
    found = []
    for root in roots:
        if os.path.isfile(root):
            files = [root]
        else:
            files = [
                os.path.join(parent, name)
                for parent, _dirs, names in os.walk(root)
                for name in names
                if name.endswith(".py")
            ]
        for path in files:
            with open(path, encoding="utf-8", errors="replace") as handle:
                for number, line in enumerate(handle, 1):
                    if mechanism.search(line) and "private_office" not in path:
                        found.append(f"{os.path.relpath(path)}:{number}")

    assert not found, (
        "a second-factor mechanism now appears in the source:\n  "
        + "\n  ".join(found)
        + "\n\nIf authentication really does challenge for it, /safety is allowed to say so. "
        "Update seo/safety.py, drop the 2FA entry from its docstring, and delete this test "
        "together with test_safety_does_not_claim_two_factor_authentication."
    )


def test_safety_does_not_claim_the_message_request_setting_filters_anything(text):
    """`message_requests` is stored and validated, and no send path reads it.

    The page describes blocking instead, which is what actually stops a message.
    """

    lowered = text.lower()
    for phrase in ("message request", "who can message you", "only followers can message"):
        assert phrase not in lowered, (
            f"/safety presents message-request filtering as a working control ({phrase!r}), "
            "but no send path reads `message_requests`"
        )


def test_safety_states_the_encryption_limit(text):
    """The App Store screenshots claim E2E encryption the product lacks.

    A member deciding what to put in a DM is exactly the reader who needs this,
    so /safety repeats it rather than linking to /about for it.
    """

    assert "not end-to-end encrypted" in text.lower()


def test_safety_tells_a_member_we_never_ask_for_credentials(text):
    """The sentence that makes a phishing attempt recognisable."""

    lowered = text.lower()
    assert "seed phrase" in lowered
    assert "never ask" in lowered


def test_safety_does_not_promise_instant_or_guaranteed_moderation(text):
    """Overclaiming here is what makes a member rely on us instead of the police.

    The queue is worked by people, so the page must not imply otherwise, and it
    has to point somewhere real when the situation is an emergency.
    """

    lowered = text.lower()
    # Deliberately not the bare word "guarantee": the page describes "guaranteed
    # returns" as one of the phrases that marks a post for review, which is the
    # page doing its job. Only a promise *we* are making is an overclaim, so
    # these match the subject rather than the word.
    for overclaim in (
        "24/7",
        "around the clock",
        "immediately reviewed",
        "reviewed immediately",
        "instantly removed",
        "we guarantee",
        "guaranteed removal",
        "guaranteed response",
    ):
        assert overclaim not in lowered, f"/safety overclaims moderation: {overclaim!r}"
    assert "emergency services" in lowered, (
        "/safety never tells a member in danger to contact emergency services"
    )


def test_safety_never_publishes_a_platform_fee_rate(text):
    """`PROPOSED_PLATFORM_FEE_BPS` is 500 and no seller has been charged it."""

    for rate in ("5%", "10%", "5 percent", "10 percent"):
        assert rate not in text, f"/safety publishes a platform fee rate: {rate}"


def test_safety_does_not_name_the_payment_processor(text):
    """A buyer cannot verify it and a scammer can borrow it."""

    assert "stripe" not in text.lower()


def test_the_structured_data_joins_the_canonical_publisher_node(client):
    html = client.get("/safety").get_data(as_text=True)
    graph = json.loads(
        re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1)
    )["@graph"]
    by_type = {node["@type"]: node for node in graph}

    assert by_type["Organization"]["@id"] == f"{sv.CANONICAL_ORIGIN}/#organization"
    assert by_type["Organization"]["name"] == "PulseSoc"
    assert by_type["Organization"]["legalName"] == "CoinPlotXAI Inc."
    assert "Service" not in by_type, (
        'the Service node defaults to serviceType "AI intelligence", which is the old '
        "product talking on the one page that must not overstate itself"
    )


def test_every_internal_link_on_the_page_resolves(client):
    """Safety is the worst page on the domain to have a dead link on."""

    html = client.get("/safety").get_data(as_text=True)
    main = html.split("<body", 1)[1].split("<footer", 1)[0]
    broken = {}
    for path in sorted(set(re.findall(r'href="(/[^"#]*)"', main))):
        status = client.get(path).status_code
        if status not in (200, 301, 308):
            broken[path] = status
    assert broken == {}, f"/safety links to paths that do not resolve: {broken}"


def test_the_module_does_not_hardcode_its_own_canonical():
    page = seo_safety.page(lambda path: f"https://example.test{path}")
    assert page["canonical"] == "https://example.test/safety"
