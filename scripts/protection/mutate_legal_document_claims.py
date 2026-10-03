#!/usr/bin/env python3
"""Puts each false legal claim back and checks the claim matrix notices.

The previous Terms and Privacy Policy described wallets, trading signals and a
Telegram companion. Nobody edited them into being wrong -- they were written for
a product that was then replaced, and no test read them, so they stayed published
for months saying things that had become false.

The replacement documents are only worth more than those if the assertions
guarding them actually fail. Each entry below rewrites one truthful sentence into
the plausible false version of itself -- the sentence a well-meaning rewrite would
produce -- and demands that
``tests/web_surface/test_legal_documents_describe_the_real_product.py`` goes red.

Two entries are worth reading even if the rest are routine:

* ``owner-marker-leaks-as-html-comment`` changes ``{#`` to ``<!--``. Jinja strips
  the first server-side; the second ships an internal counsel TODO to a public
  legal page. The two are one character apart in the editor and look identical in
  a diff skimmed quickly, which is exactly why it is here.
* ``fixed-retention-window`` reintroduces a 90-day promise. ``/privacy-center``
  already publishes 90/180/730-day windows with nothing enforcing them, so this
  is not a hypothetical mistake -- it is the mistake already live elsewhere in
  the product, aimed at the document that would make it authoritative.

Run: python3 scripts/protection/mutate_legal_document_claims.py [--verbose]
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from mutation_harness import run_matrix  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]

CLAIM_MATRIX = "tests/web_surface/test_legal_documents_describe_the_real_product.py"
TERMS = "templates/terms.html"
PRIVACY = "templates/privacy.html"


MUTATIONS = [
    dict(
        name="seller-commission-becomes-10-percent",
        control="Sellers are told the commission rate they actually pay, which is 0%.",
        path=TERMS,
        old="PulseSoc currently charges sellers <strong>no commission</strong> — the rate in force today is 0%.",
        new="PulseSoc charges sellers a 10% commission on every sale.",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="buyer-pays-shipping",
        control="Buyers are charged no separate shipping amount at checkout.",
        path=TERMS,
        old="Buyers are not currently charged a separate shipping amount at checkout.",
        new="Shipping costs are calculated at checkout and added to your total.",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="tax-calculated-at-checkout",
        control="No sales tax is calculated or collected at checkout.",
        path=TERMS,
        old="PulseSoc does not currently calculate or collect sales tax at checkout",
        new="Tax is calculated at checkout",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="terms-claim-messages-are-e2e-encrypted",
        control="Direct messages are not end-to-end encrypted and the Terms say so.",
        path=TERMS,
        old="Direct messages and group chats on PulseSoc are <strong>not</strong> end-to-end encrypted.",
        new="Direct messages and group chats on PulseSoc are end-to-end encrypted.",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="privacy-claims-messages-are-e2e-encrypted",
        control="The Privacy Policy states plainly that message content is readable by our systems.",
        path=PRIVACY,
        old="Direct messages and group chats are <strong>not end-to-end encrypted</strong>.",
        new="Direct messages and group chats are end-to-end encrypted.",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="terms-offer-two-factor-authentication",
        control="No second factor has shipped, so the password is named as the only one.",
        path=TERMS,
        old="PulseSoc does not currently offer two-factor authentication, and you should not assume a second factor is protecting your account.",
        new="PulseSoc offers two-factor authentication to protect your account.",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="age-is-verified",
        control="Age is self-confirmed at signup; no date of birth is collected and nothing checks it.",
        path=TERMS,
        old="PulseSoc does not ask for your date of birth and does not verify your age.",
        new="PulseSoc verifies your age when you sign up.",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="seller-identity-is-verified",
        control="PulseSoc does not verify seller identity; only the card processor onboards sellers.",
        path=TERMS,
        old="PulseSoc does not independently verify a seller's identity",
        new="PulseSoc verifies the identity of every seller",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="worldwide-shipping",
        control="Marketplace shipping is limited to the configured country set, which is US only.",
        path=TERMS,
        old="PulseSoc does not currently offer worldwide shipping, and no PulseSoc order is currently fulfilled by a third-party supplier network.",
        new="PulseSoc offers worldwide shipping on every order.",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="deletion-completes-on-a-timeline",
        control="Deletion runs during the request, so no future date may be promised for it.",
        path=PRIVACY,
        old="the deletion runs immediately — it is not queued and there is no waiting period.",
        new="your account will be deleted within 30 days.",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="deletion-reverts-to-a-manual-review-queue",
        control=(
            "The pessimistic lie. /account/delete completes synchronously, so describing "
            "it as a request awaiting review understates what the member can do -- which "
            "an earlier draft of this Policy actually did."
        ),
        path=PRIVACY,
        old="You can delete your account yourself at <a href=\"/account/delete\">Account → Delete Account</a>. It asks for your password, and when you confirm, the deletion runs immediately — it is not queued and there is no waiting period.",
        new="You can request deletion of your account. A deletion request is recorded and reviewed by us, and we do not promise a completion date.",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="self-service-export-is-advertised",
        control="No export route exists in the url_map; nothing emails a member their data.",
        path=PRIVACY,
        old="There is <strong>no self-service data download on PulseSoc today</strong>.",
        new="You can download a copy of your data from your settings, and your export will be emailed to you within 7 days.",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="fixed-retention-window",
        control="No retention job exists, so no fixed period may be published as if one did.",
        path=PRIVACY,
        old="We have deliberately not published a single fixed retention period for every category, because a number published here has to be one the system actually enforces.",
        new="We keep your content for 90 days after you delete it.",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="third-party-analytics-are-active",
        control="Analytics are first-party; no third-party analytics or advertising tag is served.",
        path=PRIVACY,
        old="PulseSoc's website does <strong>not</strong> load Google Analytics, Google Ads conversion tracking, PostHog, or any other third-party analytics or advertising tag",
        new="PulseSoc uses Google Analytics and PostHog to understand how the site is used",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="member-facing-telegram-integration",
        control="No Telegram surface is offered to members; the bot token lives on a separate internal service.",
        path=PRIVACY,
        old="<tr><td>CoinGecko</td>",
        new="<tr><td>Telegram</td><td>Companion bot</td><td>Your messages to the bot</td></tr>\n            <tr><td>CoinGecko</td>",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="home-page-drops-its-policy-footer",
        control="The home page is the most visited public surface and must link both documents.",
        path="templates/index.html",
        old='    <nav class="footer-legal" aria-label="Policies">\n      <a href="/terms">Terms</a>\n      <a href="/privacy">Privacy</a>',
        new='    <nav class="footer-legal" aria-label="Policies">\n      <a href="/about">About</a>',
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="trust-page-shell-drops-its-policy-footer",
        control="One footer serves /community-rules, /privacy-center, /trust-center and the policy pages.",
        path="bot.py",
        old="""<a href="/terms">Terms</a><a href="/privacy">Privacy</a><a href="/community-rules">Community Rules</a>""",
        new="""<a href="/community-rules">Community Rules</a>""",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="legacy-money-pages-drop-their-policy-links",
        control="/legal/refunds, /legal/payments and /legal/seller-terms are still published and were dead ends.",
        path="bot.py",
        old="""<a href='/terms'>Terms</a> &middot; <a href='/privacy'>Privacy</a> &middot; <a href='/refund-policy'>Refund Policy</a>""",
        new="""<a href='/refund-policy'>Refund Policy</a>""",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="owner-marker-leaks-as-html-comment",
        control="Counsel markers are Jinja comments, stripped server-side, never shipped to the member.",
        path=PRIVACY,
        old="{# OWNER / COUNSEL DECISION REQUIRED (D4)",
        new="<!-- OWNER / COUNSEL DECISION REQUIRED (D4)",
        suites=[CLAIM_MATRIX],
    ),
]


if __name__ == "__main__":
    sys.exit(run_matrix(ROOT, MUTATIONS))
