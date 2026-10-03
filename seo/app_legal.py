"""The two legal documents the shipped iPhone app links to and the web never had.

`/legal/cookies` and `/legal/licenses`. Both are named, as absolute URLs, inside
`mobile-native/src/screens/settings/legalContent.ts` -- the Cookie & Tracking
Notice and the Open-source licences screens each carry a `canonicalUrl`, and the
screen renders an "Open full document" handoff to it. Both URLs returned 404 in
production. The app was telling members that the legally operative version of a
document lived at an address with nothing behind it.

The three sibling URLs the app names the same way -- `/legal/terms`,
`/legal/privacy`, `/legal/guidelines` -- are 301s to pages that already existed,
and `bot.py` explains at length why a redirect rather than a second copy: "the
operative text has exactly one home: a second copy would be a second document to
keep current, and the one that drifted would still be the one a user was shown."

There is no page for a cookie notice or a licences list to redirect to, so these
two have to be written. That reintroduces exactly the drift the redirects avoid,
and `tests/protection/test_app_legal_parity.py` is the answer to it: every
paragraph the iPhone binary ships must either appear verbatim in the document
below or be listed in `APP_TEXT_DIVERGENCES` with the reason it does not. Edit
the app text and the test fails until this page is brought along.

WHY THIS PAGE IS THE CANONICAL ONE AND THE APP SCREEN IS A SUMMARY
------------------------------------------------------------------
The app's own text already says so, and the direction matters. A cookie notice
written only for the app would omit the website's cookies; a notice written only
for the website would omit the on-device storage that replaces them. One
document covering both surfaces is the only version that is complete for a
person who uses both, which is the ordinary case.

WHERE THE SHIPPED APP TEXT IS WRONG, AND WHY IT IS CORRECTED HERE
-----------------------------------------------------------------
Three paragraphs in the app's cookie notice describe mechanisms that are not in
the binary. They are not republished. Each is recorded in
`APP_TEXT_DIVERGENCES` with what the code actually does, which is also the
work list for the next iOS build:

* "A rotating installation identifier lets us group crash reports and
  performance samples" -- `mobile-native/src/api/installationId.ts` is the only
  installation identifier in the app. Its own docstring calls it "the stable
  per-install device id every push registration on this device files under", it
  is deliberately stable rather than rotating because the VoIP suppression path
  joins two registrations on it, and it is never sent with a crash report
  because no crash reporter ships: there is no Sentry, Crashlytics, Bugsnag or
  Datadog dependency in `mobile-native/package.json`.
* "PulseSoc only uses your device advertising identifier if you have granted app
  tracking permission" -- the app has no App Tracking Transparency integration
  at all. No `expo-tracking-transparency`, no `requestTrackingPermissions`, no
  `NSUserTrackingUsageDescription` in `Info.plist`, and no read of
  `advertisingIdentifier` anywhere in `mobile-native/`. A conditional disclosure
  of a collection that cannot happen still reads as a disclosure that it does,
  and over-disclosure is as wrong on a privacy page as under-disclosure.
* "Turn off personalised ads, analytics, and crash reporting from Settings >
  Data and privacy" -- the screen is titled "Data & personalization", and the
  three switches write `user_settings` (`services/pulse_settings_routes.py`)
  while the ad engine reads `privacy_preferences.personalized_ads_opt_out`
  (`services/pulse_ads_service.user_personalized_ads_opt_out`). Nothing joins
  them. The outcome is privacy-safe rather than dangerous -- a missing
  `privacy_preferences` row reads as opted *out* -- but a page may not describe
  a switch as the control when it is not wired to the thing it names.

EVERY COOKIE NAMED BELOW WAS COUNTED, NOT ASSUMED
-------------------------------------------------
The backend has exactly two `set_cookie` call sites -- `bot.py`'s
`set_persistent_session_cookie` and the cart blueprint's
`_attach_guest_cart_cookie` -- plus Flask's own signed session cookie. That is
the whole first-party set, and it is why this page can say the website sets no
analytics or advertising cookies of its own without hedging.
`tests/protection/test_app_legal_parity.py` counts the call sites again on every
run, so a third one cannot be added without this page noticing.
"""

from __future__ import annotations

#: Mirrors the `effectiveDate` on both documents in `legalContent.ts`. One
#: constant because the parity test compares it to the app's, and a document
#: whose web and in-app effective dates disagree is two documents.
EFFECTIVE_DATE = "1 March 2026"

#: The company that signs things, as distinct from the brand. Same split
#: `seo/commerce_policies.py` documents and `tests/test_site_identity.py` pins.
LEGAL_NAME = "CoinPlotXAI Inc."

SUPPORT_EMAIL = "support@pulsesoc.com"

#: The significant third-party packages the iPhone app ships. Name, version,
#: licence, purpose -- the same four fields and the same order as
#: `OPEN_SOURCE_DEPENDENCIES` in `legalContent.ts`, because the parity test
#: compares the two element by element. The versions are the *resolved* ones
#: from `mobile-native/package-lock.json`, not the ranges in `package.json`: a
#: licences page naming "^0.21.0" when 0.21.2 is what shipped is naming the
#: wrong artefact.
OPEN_SOURCE_DEPENDENCIES = (
    ("React", "19.1.0", "MIT", "UI runtime and component model."),
    ("React Native", "0.81.5", "MIT", "Draws the app's interface on your phone."),
    ("Expo", "54.0.36", "MIT", "Device features, build tooling, and app updates."),
    ("React Navigation", "6.1.18", "MIT", "Stack and tab navigation."),
    ("@react-native-async-storage/async-storage", "2.2.0", "MIT", "On-device cache and preference snapshots."),
    ("react-native-gesture-handler", "2.28.0", "MIT", "Recognises taps, swipes, and other gestures."),
    ("react-native-screens", "4.16.0", "MIT", "Screen containers used when you move between screens."),
    ("react-native-safe-area-context", "5.6.2", "MIT", "Safe-area insets across notches and home bars."),
    ("react-native-svg", "15.12.1", "MIT", "Vector drawing."),
    ("react-native-qrcode-svg", "6.3.21", "MIT", "Profile and share QR codes."),
    ("@expo/vector-icons", "15.1.1", "MIT", "Ionicons and related icon sets."),
    ("expo-av", "16.0.8", "MIT", "Audio and video playback."),
    ("expo-camera", "17.0.10", "MIT", "Camera capture for posts, reels, and stories."),
    ("expo-notifications", "0.32.17", "MIT", "Push notification delivery and handling."),
    ("expo-secure-store", "15.0.8", "MIT", "Keychain/Keystore storage for session credentials."),
    ("@egjs/hammerjs", "2.0.17", "MIT", "Gesture primitives used by gesture-handler on web."),
    ("nullthrows", "1.1.1", "MIT", "Null-assertion helper used by the React Native toolchain."),
    ("@babel/runtime", "7.29.7", "MIT", "Shared helpers emitted by the compiler."),
    ("react-native-web", "0.21.2", "MIT", "Web target used for QA and previews."),
)

#: Verbatim from `legalContent.ts`. Quoted here rather than paraphrased so the
#: parity test can compare them as strings -- a paraphrased licence notice is a
#: different licence notice.
MIT_NOTICE = (
    "Permission is hereby granted, free of charge, to any person obtaining a copy of this "
    "software and associated documentation files (the “Software”), to deal in the "
    "Software without restriction, including without limitation the rights to use, copy, "
    "modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, "
    "subject to the inclusion of the above copyright notice and this permission notice in "
    "all copies or substantial portions of the Software. The Software is provided “as "
    "is”, without warranty of any kind."
)

APACHE_NOTICE = (
    "Licensed under the Apache License, Version 2.0. You may obtain a copy of the licence "
    "at apache.org/licenses/LICENSE-2.0. Unless required by applicable law or agreed to in "
    "writing, software distributed under the licence is distributed on an “as is” "
    "basis, without warranties or conditions of any kind, either express or implied."
)

_DEPENDENCY_LINES = tuple(
    f"{name} {version} — {licence}" for name, version, licence, _purpose in OPEN_SOURCE_DEPENDENCIES
)

#: Paragraphs the iPhone binary ships that this page deliberately does not
#: carry, each with the reason. The parity test allows an app paragraph to be
#: absent here only if it is listed, so this tuple is the complete set of places
#: the two surfaces say different things -- and the queue for the next build.
APP_TEXT_DIVERGENCES = (
    {
        "app_text": (
            "A rotating installation identifier lets us group crash reports and performance "
            "samples from the same device without identifying you personally."
        ),
        "reason": (
            "The app's only installation identifier is stable, not rotating, and exists to "
            "register the device for push and CallKit. No crash-reporting or analytics SDK "
            "ships in the binary, so no crash report carries it."
        ),
        "evidence": "mobile-native/src/api/installationId.ts; mobile-native/package.json",
    },
    {
        "app_text": (
            "PulseSoc only uses your device advertising identifier if you have granted app "
            "tracking permission at the operating-system level and left personalised ads "
            "enabled in Settings › Data and privacy."
        ),
        "reason": (
            "There is no App Tracking Transparency integration and no read of the device "
            "advertising identifier anywhere in the app. The condition can never be met, so "
            "stating it discloses a collection that does not occur."
        ),
        "evidence": "no expo-tracking-transparency dependency; no NSUserTrackingUsageDescription",
    },
    {
        "app_text": (
            "With either of those off, you still see ads, but they are selected from coarse, "
            "non-personal signals such as the language your app is set to."
        ),
        "reason": (
            "Describes the fallback of an advertising-identifier mechanism that does not "
            "exist. Ad selection never had an identifier to fall back from."
        ),
        "evidence": "services/pulse_ads_service.py",
    },
    {
        "app_text": (
            "Analytics sharing and crash reporting can each be turned off in Settings › "
            "Data and privacy. Turning them off does not affect any feature of the app."
        ),
        "reason": (
            "True but hollow, and hollow in the misleading direction. The two switches "
            "exist, but no analytics or crash-reporting SDK ships in the app, so there is "
            "nothing for them to turn off and nothing was being shared to begin with. The "
            "screen is also titled “Data & personalization”."
        ),
        "evidence": "mobile-native/package.json; src/screens/settings/DataPrivacySettingsScreen.tsx",
    },
    {
        "app_text": (
            "Revoke tracking permission entirely in your device's own privacy settings. "
            "PulseSoc honours that immediately."
        ),
        "reason": (
            "There is no tracking permission to revoke. The app never requests App "
            "Tracking Transparency authorisation, so no PulseSoc entry appears under the "
            "device's tracking settings and the instruction cannot be followed."
        ),
        "evidence": "no expo-tracking-transparency dependency; no NSUserTrackingUsageDescription",
    },
    {
        "app_text": (
            "Turn off personalised ads, analytics, and crash reporting from Settings › "
            "Data and privacy."
        ),
        "reason": (
            "The screen is titled “Data & personalization”, and its three switches "
            "write user_settings while the ad engine reads "
            "privacy_preferences.personalized_ads_opt_out. Nothing joins the two."
        ),
        "evidence": "services/pulse_settings_routes.py; services/pulse_ads_service.py",
    },
)

COOKIES = {
    "slug": "cookies",
    "breadcrumb": "Cookie notice",
    "h1": "Cookie and tracking notice",
    "title": "Cookie and tracking notice | PulseSoc",
    "description": (
        "Every cookie the PulseSoc website sets, what it is for, how long it lasts, and the "
        "on-device storage the iPhone app uses in place of cookies."
    ),
    "lede": (
        "PulseSoc sets three cookies and all three are strictly necessary. This page names "
        "each one, and covers the on-device storage the iPhone app uses instead."
    ),
    "sections": [
        {
            "heading": "What this notice covers",
            "body": [
                "This is the canonical version of PulseSoc's cookie and tracking notice: the "
                "one the iPhone app's Cookie &amp; Tracking Notice screen links out to, and the "
                "one that governs if the two ever disagree. The app screen is a summary of "
                "the sections below that apply on a phone.",
                "Two surfaces, one document. The website uses cookies; the app uses the "
                "platform keychain and a local cache instead. Describing only one of them "
                "would leave the notice incomplete for anyone who uses both, which is most "
                "people.",
            ],
        },
        {
            "heading": "Cookies the website sets",
            "body": [
                "<strong>session</strong> &mdash; a signed cookie holding your signed-in "
                "identity and the handful of short-lived values a page needs between "
                "requests, such as a form token. <code>HttpOnly</code>, "
                "<code>SameSite=Lax</code>, and <code>Secure</code> on any deployed "
                "environment. It is re-issued on each request and lasts ten years if you do "
                "not sign out.",
                "<strong>pulse_refresh_session</strong> &mdash; the refresh credential that "
                "keeps you signed in across visits without re-entering your password. "
                "<code>HttpOnly</code>, <code>SameSite=Lax</code>, <code>Secure</code>, and a "
                "ten-year lifetime. Deleted the moment you sign out.",
                "<strong>psoc_guest_cart</strong> &mdash; set only if you add something to a "
                "Marketplace cart without an account. It is a key to the cart row and nothing "
                "else: no name, no email, no card. <code>HttpOnly</code>, "
                "<code>SameSite=Lax</code>, <code>Secure</code>, thirty days.",
                "That is the entire list. All three are strictly necessary in the sense the "
                "word is normally used &mdash; without them you cannot stay signed in or keep "
                "a cart &mdash; which is why PulseSoc does not show a cookie banner asking you "
                "to accept them.",
            ],
        },
        {
            "heading": "What the website does not set",
            "body": [
                "No analytics cookie, no advertising cookie, no cross-site tracking cookie of "
                "our own. There are precisely three places in the server that set a cookie and "
                "they are the three above.",
                "Two of the marketing pages are built to carry Google&rsquo;s measurement tag "
                "if a measurement ID is configured for the deployment. Where it is, Google "
                "sets its own cookies under its own policies, and we do not read them. No "
                "page that serves a policy, a help article or a product listing loads that "
                "tag at all.",
                "We do not sell or share cookie data, because there is none of the kind that "
                "is normally sold.",
            ],
        },
        {
            "heading": "The same storage inside the iPhone app",
            "body": [
                "The sections that follow are the ones the app screen shows, and they describe "
                "on-device storage rather than cookies.",
            ],
        },
        {
            "heading": "How this applies in the app",
            "body": [
                "The PulseSoc mobile app does not use browser cookies for its own screens. It "
                "uses the equivalent on-device storage: a session credential held in the "
                "platform keychain or keystore, and an application cache held in local app "
                "storage.",
                "Cookies in the traditional sense only appear when you follow a link out of "
                "PulseSoc into your browser — for example the “Open full "
                "document” action on this screen. Those cookies are governed by the site "
                "you land on.",
            ],
        },
        {
            "heading": "Strictly necessary storage",
            "body": [
                "Session credential — keeps you signed in between launches and lets the "
                "app refresh your session without asking for your password. Removed when you "
                "sign out.",
                "Preference snapshot — a local copy of your settings so Settings renders "
                "instantly and survives being offline.",
                "Content cache — recently viewed feed items, avatars, and media, so "
                "scrolling back does not re-download everything. You can clear this from "
                "Settings › Storage.",
            ],
        },
        {
            "heading": "Identifiers, analytics and crash reporting in the app",
            "body": [
                "The app stores one installation identifier. It is stable for the life of the "
                "install, it exists so that the device can be registered for push "
                "notifications and incoming calls, and it is the only thing those two "
                "registrations are joined on.",
                "It is not an analytics identifier and it is not sent with crash reports, "
                "because the app ships no analytics SDK and no crash-reporting SDK. Nothing in "
                "the binary reports a stack trace, a performance sample or a screen view to "
                "us or to a third party.",
                "The app never reads your device advertising identifier. There is no App "
                "Tracking Transparency integration, so the system tracking prompt is never "
                "shown &mdash; because there is nothing to ask permission for.",
            ],
        },
        {
            "heading": "Your choices",
            "body": [
                "Clear the content cache from Settings › Storage.",
                "Sign out to delete the session credential on the device, and the "
                "<code>session</code> and <code>pulse_refresh_session</code> cookies in a "
                "browser.",
                "Block or clear cookies in your browser&rsquo;s own settings. The three "
                "cookies above are strictly necessary, so blocking them means you will not be "
                "able to stay signed in or keep a cart &mdash; the rest of the site still "
                "reads.",
                "Settings › Data &amp; personalization in the app carries switches for "
                "personalised ads, usage analytics and crash reports. The analytics and "
                "crash-report switches govern systems the app does not currently contain, so "
                "there is nothing for them to turn off. The preference the advertising engine "
                "reads is the one set in the web <a href=\"/privacy-center\">privacy "
                "centre</a>, and an account with no preference recorded there is treated as "
                "opted out of personalised ads.",
            ],
        },
        {
            "heading": "Who to ask",
            "body": [
                f"{LEGAL_NAME} is the controller for the storage described here. Questions, "
                f"corrections and requests go to <a href=\"mailto:{SUPPORT_EMAIL}\">"
                f"{SUPPORT_EMAIL}</a>.",
                f"This notice is effective {EFFECTIVE_DATE}. The version the iPhone app shows "
                "names this page as the operative one, so this is the copy to quote.",
            ],
        },
    ],
    "related": ("licenses",),
}

LICENSES = {
    "slug": "licenses",
    "breadcrumb": "Open-source licences",
    "h1": "Open-source licences",
    "title": "Open-source licences | PulseSoc",
    "description": (
        "The third-party open-source software the PulseSoc iPhone app ships, the version of "
        "each, and the licence terms it is distributed under."
    ),
    "lede": (
        "PulseSoc is built on open-source software maintained by people who owe us nothing. "
        "These are the packages inside the app and the licences they come under."
    ),
    "sections": [
        {
            "heading": "Acknowledgements",
            "body": [
                "PulseSoc is built on open-source software maintained by people who owe us "
                "nothing. The packages below are shipped inside this app; their copyright "
                "remains with their respective authors.",
                *_DEPENDENCY_LINES,
            ],
        },
        {
            "heading": "MIT License",
            "body": [
                "Applies to the packages listed above as MIT.",
                MIT_NOTICE,
            ],
        },
        {
            "heading": "Apache License 2.0",
            "body": [
                "Applies to the packages listed above as Apache-2.0.",
                APACHE_NOTICE,
            ],
        },
        {
            "heading": "Full licence texts",
            "body": [
                "The complete, unabridged licence text for every dependency — including "
                "transitive dependencies not listed here — is published alongside each "
                "release. Open the full document to read it.",
                "The versions above are the resolved versions from the app&rsquo;s lockfile "
                "rather than the ranges in its manifest, so each names the artefact that was "
                "actually built into the release you are running.",
            ],
        },
        {
            "heading": "Corrections",
            "body": [
                f"If your package is listed under the wrong licence, or is missing, write to "
                f"<a href=\"mailto:{SUPPORT_EMAIL}\">{SUPPORT_EMAIL}</a> and it will be "
                f"corrected. {LEGAL_NAME} publishes this list as an acknowledgement, not as a "
                f"claim over anyone else&rsquo;s work.",
            ],
        },
    ],
    "related": ("cookies",),
}

DOCUMENTS = (COOKIES, LICENSES)

BY_SLUG = {document["slug"]: document for document in DOCUMENTS}


def canonical_path(slug):
    """``/legal/cookies``, because that is the URL the shipped binary names.

    Not negotiable the way `seo.commerce_policies.canonical_path` is: these two
    paths are already in an App Store build, so the page has to come to the URL
    rather than the other way round.
    """

    return f"/legal/{slug}"


def all_paths():
    """Every path in this family, for the sitemap and for the tests."""

    return tuple(canonical_path(document["slug"]) for document in DOCUMENTS)


def page(slug, canonical_url):
    """The render context for one document, or None if the slug is not ours.

    Returning None rather than raising, matching `seo.commerce_policies.page`, so
    the route can 404 an unknown slug instead of serving a soft 404 at 200.
    """

    document = BY_SLUG.get(slug)
    if not document:
        return None
    return {
        "canonical": canonical_url(canonical_path(slug)),
        "breadcrumb": document["breadcrumb"],
        "title": document["title"],
        "description": document["description"],
        "h1": document["h1"],
        "lede": document["lede"],
        "effective_date": EFFECTIVE_DATE,
        "sections": document["sections"],
        "related": [
            {
                "path": canonical_path(other),
                "breadcrumb": BY_SLUG[other]["breadcrumb"],
                "h1": BY_SLUG[other]["h1"],
            }
            for other in document["related"]
        ],
    }
