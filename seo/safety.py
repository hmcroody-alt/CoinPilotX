"""The /safety page: the controls that exist, named only where code enforces them.

Why this is a module rather than a `seo/content.py` entry
--------------------------------------------------------
`/safety` was served by the `/<slug>` catch-all out of the `safety` dict in
`seo/content.py`, and it described a different product. Its title was "PulseSoc
Safety Center | Crypto Risk and Account Protection", its `answer` was
"CoinPlotXAI Inc. never holds user funds and never asks for private wallet
credentials", and its four `points` were "Never holds funds", "Public wallet
data only", "Stripe website billing" and "Educational AI intelligence only".

Every one of those is about a crypto tool. None of them is a safety control a
member of a social app can use. A person who reaches /safety is being harassed,
has been scammed, or is deciding whether to let someone they do not know message
them -- and the page named the payment processor instead. This follows the
extraction precedent recorded at `seo/content.py:211-216` and used for
`/features` and `/about`.

The sourcing rule this page is written under
--------------------------------------------
The owner's instruction is that there be no trust signals this product has not
earned. On a safety page that rule bites hardest, because safety copy is the
most rewarding place on a website to overclaim and the least likely to be
checked. So every control named below was read in the source first, and the test
beside this module pins the ones that are load-bearing.

What was verified, and where:

* **Blocking** -- `bot.py:115899` routes through
  `pulse_social_graph_service.block_user`, which writes both `blocked_users` and
  `comm_v2_blocks`. Both halves matter: the comment at `bot.py:115893` records
  that Messenger used to write only the first while `pulse_communications_v2`
  wrote only the second, so whether blocking hid your presence depended on which
  screen you did it from. `pulse_communications_v2/service.py:1191` and
  `notification_reconciliation.py:54` are the readers.
* **Reporting** -- `bot.py:105726` writes `pulse_reports`, and it reaches a queue
  a person actually works: `bot.py:105892` lists open reports and
  `bot.py:110885` dismisses or actions one. This is why the page can say a report
  is seen rather than only that it is filed.
* **Automated screening** -- `services/pulse_feed_engine.py:3152` runs
  `pulse_moderation_engine.moderate_text` over a post and stores the verdict.
  `blocked` is then filtered out of reads, not merely recorded: reels at
  `bot.py:52095`, comments at `bot.py:44916`, chat media at `bot.py:45329`,
  marketplace listings at `bot.py:43407`. `needs_review` stays visible and lands
  in the queue at `bot.py:105890`.
* **Comment and reaction controls** -- enforced server-side at `bot.py:95695`
  and `bot.py:95739`, which refuse a comment on a reel whose owner turned
  comments off. A control that were enforced only in the client would not be one.
* **Muting and hiding** -- `pulse_muted_users` at
  `services/pulse_settings_routes.py:186`, thread mute at
  `pulse_communications_v2/service.py:3513`, `/api/pulse/posts/<id>/hide` at
  `bot.py:97068`.
* **Devices and sessions** -- `bot.py:92344` lists trusted devices,
  `bot.py:92356` removes one, `bot.py:8801` signs out everywhere.
* **Deletion and export** -- `bot.py:9109` requires the account password;
  `services/pulse_settings_routes.py:1169` queues a data export.

Three things are deliberately not claimed
-----------------------------------------
Not omitted by oversight. Each was checked, found unenforced, and left out
rather than written up:

1. **Two-factor authentication.** `/api/account/2fa/enable` (`bot.py:92290`)
   sets `users.two_factor_enabled=1` and nothing more. The column has no reader
   in any authentication path -- its only consumers are a security *score*
   (`bot.py:92084`, `services/dashboard_account_command_center.py:1409`) and the
   settings card that offers the button (`bot.py:92181`). There is no TOTP
   secret, no enrolment and no second-factor challenge anywhere in the
   repository. A safety page claiming 2FA would be telling a member their
   account is protected by something that does not run.
2. **Message-request filtering.** `message_requests` is offered as
   everyone/followers/none (`bot.py:12760`) and its value is validated
   (`services/dashboard_account_command_center.py:40`), but no send path reads
   it. Blocking is what actually stops a message, so blocking is what this page
   describes.
3. **A minimum age.** `users.date_of_birth` has no writer -- the only
   `date_of_birth` writes in the tree target `admin_users` (`bot.py:20209`) and
   an employee record (`bot.py:29496`). There is nothing to state here that
   would be true.

The gaps themselves are reported to the owner rather than published. Saying "we
do not claim 2FA" is honest; printing "the 2FA switch in settings does nothing"
is an instruction to whoever is choosing a target, and the fix belongs in code
rather than in prose. The same reasoning kept the unenforced message-request
setting off the page instead of on it under a warning.

What is stated as a limit
-------------------------
Direct messages are not end-to-end encrypted, and `/about` already says so for
the reason recorded there: the App Store screenshots claim encryption this
product does not have, so a safety page going quiet about it would be the second
surface implying it. A member deciding what to put in a DM is exactly the reader
who needs that sentence, which is why it is repeated here rather than linked.
"""

from .schema import SUPPORT_EMAIL

CANONICAL_PATH = "/safety"


def page(canonical_url):
    """The render context for /safety.

    Takes `canonical_url` for the same reason `seo/about.py` does: the page is
    then subject to the same alias and host resolution as every other public
    page instead of hardcoding an origin into the document.
    """

    return {
        "canonical": canonical_url(CANONICAL_PATH),
        "breadcrumb": "Safety",
        # `commerce_policy_graph` attaches neither a `MobileApplication` node nor
        # the `Service` node whose `serviceType` defaults to "AI intelligence".
        # The app has its own node under `#app` on /app, and the AI service node
        # would be the retired crypto product talking on the one page that must
        # not overstate itself.
        "page_type": "WebPage",
        "title": "Safety on PulseSoc — the controls, and what they do not cover",
        "description": (
            "How to block, report, mute and hide on PulseSoc, what happens to a report, "
            "which account and device controls exist, and the limits stated plainly "
            "rather than left to be discovered."
        ),
        "h1": "Safety on PulseSoc",
        "lede": (
            "What you can do about another member, what happens after you do it, and the "
            "things this app does not protect you from. Only controls that are enforced by "
            "the service are listed here."
        ),
        "sections": [
            {
                "heading": "If someone is bothering you",
                "body": [
                    "<strong>Blocking</strong> is the control that actually stops contact. A "
                    "block takes effect across the whole product rather than on the screen you "
                    "set it from: the blocked member cannot message you, your presence is "
                    "hidden from them, and pending notifications from them are suppressed.",
                    "<strong>Muting</strong> is quieter and reversible. You can mute a member "
                    "without them knowing, mute a single conversation so it stops surfacing, "
                    "hide an individual post, or mark a reel as not interesting so less like it "
                    "reaches you.",
                    "<strong>Turning off replies.</strong> On your own reels you can switch off "
                    "comments, reactions, or both. The service refuses comments on a reel whose "
                    "owner has disabled them, so this is not only a change to what you see.",
                ],
            },
            {
                "heading": "Reporting, and what happens next",
                "body": [
                    "You can report a member, a post, a comment, a direct message, uploaded "
                    "media, a marketplace listing, a group post and a live stream. A report "
                    "opens a record in a review queue that PulseSoc staff work through, where it "
                    "can be actioned or dismissed.",
                    "Reporting is not the same as blocking and neither does the other's job. A "
                    "report asks us to look; a block stops the contact now. If someone is "
                    "actively harassing you, do both &mdash; the block takes effect immediately "
                    "and does not wait for the review.",
                    "Honest limits on what reporting is: it is reviewed by people during "
                    "working hours, not instantly, and we do not tell you the outcome for "
                    "another member's account. If what you are reporting is an emergency or "
                    "involves someone's immediate physical safety, contact your local emergency "
                    "services &mdash; a report to us is not a substitute for that.",
                ],
            },
            {
                "heading": "What is screened before it reaches anyone",
                "body": [
                    "Posts are checked automatically when published. A short list of prohibited "
                    "phrases &mdash; encouraging suicide, and doxxing &mdash; blocks a post "
                    "outright, and blocked content is filtered out of feeds, reels, comments, "
                    "message attachments and marketplace listings rather than merely flagged.",
                    "A second set of patterns, mostly the mechanics of a financial scam &mdash; "
                    "guaranteed returns, pressure to send funds, a request for a seed phrase or "
                    "a private key, a wallet-connect prompt &mdash; marks a post for human "
                    "review. Content marked that way stays visible while it waits, which is a "
                    "deliberate trade and worth knowing.",
                    "This is pattern matching, not judgement. It catches the obvious and the "
                    "formulaic and it will miss things said carefully, which is why reporting "
                    "matters and why we would rather describe the screening accurately than "
                    "call it more than it is.",
                ],
            },
            {
                "heading": "Your account and your devices",
                "body": [
                    "You can see the devices signed in to your account, remove any one of them, "
                    "and sign out everywhere at once. Signing out everywhere is the right first "
                    "move if you think someone else has your password; change the password "
                    "afterwards.",
                    "Sign-in activity, including failed attempts, is recorded against your "
                    "account so that a pattern is visible rather than invisible. You can "
                    "generate recovery codes and verify your email address and phone number.",
                    "If you are locked out or you think your account has been taken over, write "
                    f"to <a href=\"mailto:{SUPPORT_EMAIL}\">{SUPPORT_EMAIL}</a> from the email "
                    "address on the account where you can &mdash; it is the fastest way for us "
                    "to be sure we are helping the right person.",
                ],
            },
            {
                "heading": "Buying and selling safely",
                "body": [
                    "If an order arrives wrong or never arrives, open it from your orders and "
                    "raise a dispute; the <a href=\"/returns\">returns</a> and "
                    "<a href=\"/refund-policy\">refund</a> pages set out what happens, and an "
                    "order that never arrived is refunded rather than returned.",
                    "Keep the transaction in the app. The payment options shown on the listing "
                    "and at checkout are the authoritative ones for that item, and a seller who "
                    "moves you to another payment method, another app, a gift card or a bank "
                    "transfer has moved you outside everything on this page. Report that "
                    "listing.",
                    "What we do not do: PulseSoc does not verify a seller's identity or run "
                    "background checks on members. A seller account is not a credential. For "
                    "collection in person, meet somewhere public in daylight and take someone "
                    "with you where you can.",
                ],
            },
            {
                "heading": "Limits you should know about",
                "body": [
                    "Direct messages are <strong>not end-to-end encrypted</strong>. The "
                    "transport is TLS and messages are stored in a form the service can read. "
                    "Treat a DM as something the company can see, because it is &mdash; and "
                    "that is stated here rather than omitted because anything implying "
                    "otherwise would be a security claim this product cannot honour.",
                    "PulseSoc will never ask you for a crypto seed phrase, a private key or a "
                    "wallet password, and nothing in the app needs one. Anything that asks "
                    "&mdash; in a DM, in a comment, on a listing, or on a page that looks like "
                    "this one &mdash; is not us. The markets screens are educational context "
                    "only: no trade is ever executed for you, and nothing there is financial, "
                    "investment or tax advice.",
                    "We will never ask for your password. We ask for it only on the sign-in and "
                    "account screens in the app itself, never by email, never in a message, and "
                    "never over the phone.",
                ],
            },
            {
                "heading": "Leaving, and taking your data",
                "body": [
                    "You can request a copy of your data from your settings, and you can delete "
                    "your account. Deletion asks for your password first, because an account "
                    "deletion someone else can trigger is its own safety problem.",
                    "What each one covers, how long it takes and what is kept afterwards is set "
                    "out in the <a href=\"/privacy\">privacy policy</a> rather than summarised "
                    "here, so that there is one answer to that question instead of two.",
                ],
            },
            {
                "heading": "Reaching a person",
                "body": [
                    "<a href=\"/help\">Help</a> answers the common questions without anyone "
                    "having to get involved. <a href=\"/contact\">Contact</a> and "
                    f"<a href=\"mailto:{SUPPORT_EMAIL}\">{SUPPORT_EMAIL}</a> reach a person when "
                    "something has gone wrong.",
                    "For anything involving your account, an order or a payment, write from the "
                    "email address on the account where you can.",
                ],
            },
        ],
        "related": [
            {"path": "/community-rules", "label": "What is not allowed on PulseSoc"},
            {"path": "/privacy", "label": "Privacy policy"},
            {"path": "/terms", "label": "Terms of service"},
            {"path": "/help", "label": "Help"},
            {"path": "/contact", "label": "Contact"},
            {"path": "/crypto-safety", "label": "Spotting a crypto scam"},
        ],
    }
