# Invite sharing — what the link is, and why it is not something else

Written 2026-09-19. Exists because "let's give invites their own
`/open/invite/<token>` universal link" is a proposal that sounds obviously right
and would make the product worse. The evidence is below so the question can be
settled by reading rather than by re-deriving it.

## The link

Invites go out as `https://pulsesoc.com/r/<referral_code>`.

- Built server-side: `bot.py:48504` (`/api/progress/invite`, routed at
  `bot.py:23711`), returned to the app as `ProgressInvite.referral_link`.
- Served by `bot.py:15591`, `GET /r/<referral_code>`.
- The code is the user's own `users.referral_code`, generated once at signup
  (`bot.py:5759`, `bot.py:6621`). **One member has exactly one code, forever.**
  There is no per-invite token and there should not be one.

## What `/r/` actually does

Reading the handler matters, because it is doing more than a redirect:

1. Writes a `referral_events` row — code, referrer, session id, landing page,
   referer, `ip_hash`, timestamp.
2. **If the visitor is on iOS** and the code resolves to a real user, writes a
   deferred-attribution row keyed on `ip_hash` + a device-family hash, redeemable
   for 48 hours by `POST /api/mobile/referral/claim`
   (`bot.py:15572-15573`) after the recipient signs up.
3. iOS visitors are redirected **straight to the App Store listing** — no landing
   page, no interstitial, no extra tap. Invalid codes still go to the store, just
   without an attribution row.
4. Everyone else goes to `/?ref=<code>&utm_source=referral&...`.

Verified in production: `/r/TESTCODE` → `HTTP 302`.

## Why not a Universal Link

Because a Universal Link solves the wrong half of the problem.

The person receiving an invite **does not have the app**. That is what makes it an
invite. A Universal Link only does anything for someone who already installed the
app; for everyone else iOS hands the URL to Safari, and you are back to needing
exactly the App Store redirect that `/r/` already performs — except now with an
extra hop through a web page.

`/r/` is the app-first path. It goes to the App Store *and* preserves attribution
across the install, which is the one genuinely hard part of invite plumbing and
which a Universal Link cannot do at all.

Measured against the repo as it stands:

| Proposed path | Production | In AASA? | Route in `linking.ts`? |
|---|---|---|---|
| `/open/invite/<token>` | **404** | no | no |
| `/pulse/invite/<token>` | **404** | no | no |
| `/r/<code>` | **302** ✅ | no, deliberately | n/a — App Store hop |

`/open/*` routes do exist (`bot.py:56346-56347`) but are **deliberately not
universal links**: they render an interstitial offering an App Store link and a
`pulsesoc://` button. `services/app_links.py` explains the constraint that forces
this — iOS only hands the app paths that the **shipped binary's** AASA claimed, so
newly claiming a path does nothing for already-installed users until they update.
Building `/open/invite/<token>` would therefore have shipped a 404 behind a
token system, replacing something that works.

## The one improvement that is actually available

Adding `/r/*` to `APPLE_LINK_COMPONENTS` in `services/native_app_links.py` would
let *already-installed* recipients open the app directly instead of bouncing
through Safari to the App Store.

It is not free and was not done here:

- It only helps users on a build shipped **after** the claim, per the constraint
  above.
- It interacts with deferred attribution. Today the iOS branch of `/r/` writes the
  claim row *because the request reaches the server*. A universal link that opens
  the app never makes that request, so attribution for installed users would have
  to move into the app's link handler.
- `linking.ts` would need a route, and the app would need to decide what an
  invite even navigates to for someone already signed in.

Worth doing deliberately, as its own piece of work, with the attribution change
designed rather than discovered.

## What changed in this piece of work

Only the words around the link.

`ProgressCenterScreen`'s share action used to be `Share.share({ message: link })` —
a bare URL, arriving in someone's inbox with nothing to explain it. It now sends a
sentence with the link on its own final line, built by
`mobile-native/src/sharing/inviteMessage.ts`.

- Five approved tones (`default`, `casual`, `network`, `discovery`, `short`),
  fixed and centralized. Nothing is generated or randomised, so the same inputs
  always produce the same message. Only `default` is wired to the button today;
  the rest exist so a tone picker does not require touching the share path.
- Personalisation uses the **public username only**, appended as its own trailing
  line. A missing or unusable handle drops that line instead of rendering a
  sentence with a hole in it. `inviteHandle` is deny-by-default — a display name,
  an email address or anything with a space is discarded.
- All seven strings are in the `progress:invite.message.*` catalog across all 11
  locales. Missing key families are a hard failure in
  `scripts/validate-i18n.mjs:212`, so English-only was never an option.
- **Copy Link is unchanged and still copies the bare URL.** The two actions are
  deliberately different: a copied link is about to be pasted somewhere the member
  is already writing their own words, and a canned message would fight what they
  are typing.
- The share payload sets `message` and `subject` but **never a standalone `url`**.
  On iOS those become two activity items and a target may take one and drop the
  other, which is exactly how the explaining sentence goes missing.

Tests: `mobile-native/src/sharing/__tests__/inviteMessage.test.ts`, which resolves
against the **real shipped English catalog** rather than a stub, so a renamed or
emptied key fails the suite.

## Analytics — deliberately not added

The brief asked for privacy-safe invite analytics. The valuable half already
exists server-side: `/r/` writes a `referral_events` row on every link open, and
`record_referral_signup` logs a `referral_signup` product event on conversion.

The client half was skipped on purpose. `mobile-native` has no app-wide analytics
pipeline — only `src/payments/premiumAnalytics.ts`, a seam with no transport
attached. Adding share/copy events would have produced instrumentation that
reports to nothing, plus a second seam to reconcile later. If a real transport
lands, invite events should attach to *that*, not to a parallel system built here.

## Locks respected

No audio, livestream, call, PushKit, CallKit, Marketplace payment or StoreKit
path was read or modified. The change is one screen callback, one new module, one
test file, and catalog additions.
