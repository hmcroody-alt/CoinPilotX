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
        name="terms-drop-the-placebo-toggle-warning",
        control=(
            "Denying 2FA is not enough while a screen says 'Enabled'. A member who used "
            "the toggle would read the denial as stale rather than the toggle as fake."
        ),
        path=TERMS,
        old="<strong>That setting does not currently add a second factor.</strong>",
        new="That setting is part of our ongoing security work.",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="privacy-drops-the-placebo-toggle-warning",
        control="Same warning, in the document that members are pointed to about security.",
        path=PRIVACY,
        old="that setting does not currently add a second factor: nothing asks you for a code, and the recovery codes it issues are not currently checked",
        new="that setting reflects our layered account protection",
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
        name="privacy-center-presented-as-a-working-control",
        control=(
            "The exact sentence an earlier draft carried. Three of the Privacy "
            "Center's four boxes save a value nothing reads, so pointing members "
            "there to change visibility bundles a real path with a placebo one."
        ),
        path=PRIVACY,
        old="<p>You can edit your profile, and change your privacy, notification and visibility settings, at any time in your settings.",
        new="<p>You can edit your profile, change your privacy, notification and visibility settings, and withdraw marketing consent at any time in your settings or through <a href=\"/privacy-center\">Privacy Center</a>.",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="public-profile-box-left-looking-like-a-privacy-control",
        control=(
            "Unticking 'Public profile visible' looks exactly like making a "
            "profile private and does nothing. Dropping this one clause is the "
            "whole defect: everything else on the page can stay honest."
        ),
        path=PRIVACY,
        old="In particular, <strong>unticking \"Public profile visible\" on that page does not make your profile private.</strong> ",
        new="",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="website-deletion-speaks-for-the-whole-product",
        control=(
            "The quietest §64 failure, and it was mine. Deleting on the website is "
            "immediate; deleting in the shipped iOS app queues a row nothing ever "
            "processes. Dropping this paragraph restores a sentence that was true "
            "of the route it was written from and false for every app member."
        ),
        path=PRIVACY,
        old="<p><strong>Deleting from the PulseSoc mobile app is not the same thing, and right now it is worse.</strong> The app's delete-account screen does not perform the deletion described above. It records a request, tells you the account is scheduled for deletion about 30 days later, and cancels that request if you sign back in before then. The cancellation works. The deletion does not: <strong>nothing currently carries out a scheduled deletion</strong>, so a request made in the app can sit indefinitely and your account is not removed. We are not going to describe that as a 30-day deletion, because it is not one. Until the app is fixed, use <a href=\"/account/delete\">Account → Delete Account</a> on this website if you want your account actually deleted, or email us and we will do it by hand.</p>",
        new="",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="app-deletion-restated-as-a-real-30-day-deletion",
        control=(
            "Repeating the app's own schedule as though a job honoured it. The "
            "cancel half is wired to sign-in and works; nothing completes a "
            "scheduled deletion, so 30 days is a number with no process behind it."
        ),
        path=PRIVACY,
        old="The cancellation works. The deletion does not: <strong>nothing currently carries out a scheduled deletion</strong>, so a request made in the app can sit indefinitely and your account is not removed. We are not going to describe that as a 30-day deletion, because it is not one.",
        new="Your account is then deleted at the end of that 30-day period.",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="policy-repeats-the-apps-unkept-export-email",
        control=(
            "Reported speech drifting into the Policy's own voice. The first draft "
            "of this sentence said 'we will email a download link', which the claim "
            "matrix caught: a member skimming it cannot tell a quotation from a "
            "promise, and nothing sends the email either way."
        ),
        path=PRIVACY,
        old="produces a message saying a download link is on its way to you. <strong>No link is ever sent.</strong>",
        new="means we will email a download link to you when it is ready.",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="ip-hash-listed-as-a-security-safeguard",
        control=(
            "The real sentence this restores. It sat in the Security section beside "
            "password hashing and TLS. Every word true; the paragraph still told a "
            "member the hash protected them, and bot.py's own comment calls it "
            "'pseudonymisation that does not pseudonymise'."
        ),
        path=PRIVACY,
        old="Traffic to PulseSoc is encrypted in transit.</p>\n        <p>Three things we will not overstate:",
        new="Traffic to PulseSoc is encrypted in transit. IP addresses in analytics records are stored as hashes.</p>\n        <p>Three things we will not overstate:",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="ip-hash-disclosure-dropped",
        control=(
            "Removing the bullet leaves the member with a hashed IP and no warning "
            "that the hash is reversible -- the omission half of the same fiction."
        ),
        path=PRIVACY,
        old="is not anonymisation, and we are not going to list it as a safeguard.",
        new="protects your address.",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="all-usage-events-claimed-accountless",
        control=(
            "The real sentence this restores. Drafted from the page-view writer, which "
            "hard-codes user_id NULL, and generalised over log_product_event, which "
            "writes the signed-in user_id into the same table."
        ),
        path=PRIVACY,
        old="Two kinds of usage event are recorded, and the difference matters.",
        new="These website events are recorded without being linked to an account.",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="section-10-hands-privacy-center-authority-over-visibility",
        control=(
            "The real sentence this restores. Section 13 said three of the four boxes "
            "do nothing; section 10, four hundred lines earlier, said the 'visibility "
            "controls in Privacy Center ... determine the rest'. The document "
            "contradicted itself, and the first version of the claim test matched only "
            "the change/manage/update phrasing, so it passed."
        ),
        path=PRIVACY,
        old="The settings that actually decide this are the privacy and visibility settings in your account settings. The visibility tick-boxes on <a href=\"/privacy-center\">Privacy Center</a> are not what controls it, for the reason set out in section 13.",
        new="The visibility controls in <a href=\"/privacy-center\">Privacy Center</a> and your settings determine the rest.",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="marketing-opt-out-points-at-an-unsubscribe-link",
        control=(
            "The consent rows at signup say 'I can unsubscribe anytime', and an "
            "earlier draft repeated that as the mechanism. No route serves an email "
            "unsubscribe, nothing here sends a marketing campaign, and no inbound-SMS "
            "handler reads STOP. The working control is the opt-in tick-box."
        ),
        path=PRIVACY,
        old="<p><strong>Marketing email and SMS are opt-in.</strong>",
        new=(
            "<p>You can unsubscribe from marketing email using the link in the "
            "message, and stop marketing SMS by replying STOP where supported. "
            "<strong>Marketing email and SMS are opt-in.</strong>"
        ),
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="marketing-control-left-unnamed",
        control=(
            "Renaming the heading away from the opt-in wording leaves the Policy with "
            "no plain statement of the one marketing control that works, which is the "
            "pessimistic half of the same fiction."
        ),
        path=PRIVACY,
        old="<strong>Marketing email and SMS are opt-in.</strong>",
        new="<strong>Marketing preferences.</strong>",
        suites=[CLAIM_MATRIX],
    ),
    dict(
        name="self-service-export-is-advertised",
        control="No export route exists in the url_map; nothing emails a member their data.",
        path=PRIVACY,
        old="There is <strong>no working self-service data download on PulseSoc today</strong>.",
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
