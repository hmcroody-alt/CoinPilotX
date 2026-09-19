# PulseSoc — Apple Native Capability Audit

Stage 1 map. Written 2026-09-19 against `origin/main` = `3e9618da`.

This document exists to be read *before* anyone implements an Apple capability. Every
status below is backed by a file path and a line number, or by a command whose output is
quoted. Where a claim could not be verified it is marked UNVERIFIED rather than guessed.

**This work targets a future build.** `CFBundleVersion` 28 / `CFBundleShortVersionString`
1.0.2 is under App Store review. Nothing in this audit changes a shipping file.

---

## The two structural facts that govern everything below

Read these first. Between them they decide the status of six of the fourteen capabilities,
and they do it before any product judgement gets made.

> **Both were resolved on 2026-09-19.** They are left stated as found, because the
> reasoning below is what the decisions answer. The answers — raise the floor to **16.1**,
> and add extension targets to the **committed** Xcode project rather than generating them —
> are in `DECISIONS_DEPLOYMENT_TARGET_AND_EXTENSIONS.md`, with the production numbers and
> the `git ls-files` evidence behind each. Nothing has been implemented yet.

### 1. The iOS deployment target is 15.1

```
$ grep -o "IPHONEOS_DEPLOYMENT_TARGET = [0-9.]*" mobile-native/ios/PulseSoc.xcodeproj/project.pbxproj | sort -u
IPHONEOS_DEPLOYMENT_TARGET = 15.1
```

One value, project-wide. Several requested capabilities have a minimum OS above it:

| Capability | Minimum iOS | Above 15.1 by |
|---|---|---|
| App Intents | 16.0 | 0.9 |
| Live Activities | 16.1 | 1.0 |
| ActivityKit push updates | 17.2 | 2.1 |
| Control Center controls (`ControlWidget`) | 18.0 | 2.9 |

Raising the target is a product decision with a user cost, not a build setting — it drops
every device that cannot run the new floor. It is also not a prerequisite for *all* of
them: a capability can be compiled in with `@available` guards and simply not appear on
older OSes. Which of those two routes applies is a per-capability decision and is recorded
in each entry below. What is **not** acceptable is discovering this at build time.

### 2. There is exactly one native target, and no extensions

```
$ grep -c "isa = PBXNativeTarget" mobile-native/ios/PulseSoc.xcodeproj/project.pbxproj
1
```

WidgetKit, Live Activities, Share Extensions and Control Center controls are **all** app
extensions. Every one of them requires:

- a new target in the Xcode project,
- an **App Group** entitlement shared between app and extension (the only supported way to
  hand data across the process boundary),
- a second provisioning profile and App ID in the Apple Developer portal,
- and a decision about how the extension reads the session, because
  `mobile-native/src/session/sessionStore.ts` pins the keychain to a service string
  (`com.pulsesoc.app.session`) with **no** `keychain-access-groups` entitlement — so an
  extension cannot read the user's token today.

That last point is the real cost and it is easy to miss. Four capabilities in this document
are gated behind the same three-part prerequisite — App Group, keychain access group, and a
target-generation strategy that survives `expo prebuild` — and that prerequisite should be
built **once**, deliberately, as its own piece of work. See Wave 2.

The current entitlements file is two keys, total:

`mobile-native/ios/PulseSoc/PulseSoc.entitlements`
```xml
aps-environment                            = $(PULSESOC_APS_ENVIRONMENT)
com.apple.developer.associated-domains     = ["applinks:pulsesoc.com"]
```

Absent: `com.apple.developer.applesignin`, `com.apple.security.application-groups`,
`com.apple.developer.devicecheck.appattest-environment`, `keychain-access-groups`,
`com.apple.developer.siri`, iCloud, `com.apple.developer.usernotifications.*`.

---

## Protection locks — what this audit must not touch

The brief names these and the audit confirms they are real, load-bearing, and working.
**Read-only integration with their state is permitted. Modification is not.**

| Locked surface | Where it lives | Why it is locked |
|---|---|---|
| PushKit + CallKit | `mobile-native/ios/PulseSoc/AppDelegate.swift:4-5, 34-47, 89-196` | Reports incoming calls to CallKit *before the JS bridge exists*. A VoIP push that is not reported synchronously terminates the app. |
| VoIP push backend | `services/pulsesoc_voip_push.py` | UUIDv5 call-id derivation; payload table; APNS environment selection. |
| Realtime audio | `config/realtime-audio-protected-paths.json` | CI gate. `AVAudioSession` category setup at screen level is forbidden regardless of justification. |
| Livestream / calls / radio | per the manifest | A second microphone track or LiveKit/Agora publication path is forbidden. |

Note for accuracy: the RTC vendor is **Agora** (`mobile-native/package.json:72`,
`react-native-agora@4.6.2`), not LiveKit. `CLAUDE.md` says LiveKit and is stale on this
point. The protection policy applies to the audio session either way.

Two capabilities below have a genuine adjacency to the locked surfaces — Live Activities
(wants call state) and App Intents (could be asked to start a call). Both are marked and
both are constrained to *observing* state, never driving it.

---

## Capability matrix

| # | Capability | Status | Min iOS | New target | Wave |
|---|---|---|---|---|---|
| 1 | App Attest + DeviceCheck | NOT IMPLEMENTED | 14.0 | No | 3 |
| 2 | Live Activities + Dynamic Island | NOT IMPLEMENTED | 16.1 | **Yes** | 4 |
| 3 | App Intents / Siri / Shortcuts | NOT IMPLEMENTED | 16.0 | No | 4 |
| 4 | Core Spotlight | NOT IMPLEMENTED | 15.1 ✅ | No | 3 |
| 5 | BackgroundTasks | PARTIALLY IMPLEMENTED | 15.1 ✅ | No | 3 |
| 6 | Sign in with Apple | NOT IMPLEMENTED | 15.1 ✅ | No | 2 |
| 7 | Universal Links + Associated Domains | **FULLY IMPLEMENTED** | — | No | — |
| 8 | StoreKit 2 | **FULLY IMPLEMENTED** | — | No | — |
| 9 | MapKit | NOT APPROPRIATE (no data) | 15.1 ✅ | No | 5 |
| 10 | Handoff | FOUNDATION EXISTS | 15.1 ✅ | No | 4 |
| 11 | Keychain + Secure Enclave + LocalAuthentication | PARTIALLY IMPLEMENTED | 15.1 ✅ | No | 3 |
| 12 | WidgetKit | NOT IMPLEMENTED | 15.1 ✅ | **Yes** | 4 |
| 13 | Share Extensions | NOT IMPLEMENTED | 15.1 ✅ | **Yes** | 5 |
| 14 | Action Button + Control Center | NOT IMPLEMENTED | 18.0 (CC) | **Yes** | 5 |

Two of fourteen are already done and done well. That is the most important line in this
document, because the brief is framed as an expansion and the honest finding is that the
two highest-risk, highest-value Apple integrations — the payment path and the link path —
are finished, tested, and should be left alone.

---

## 1. App Attest + DeviceCheck

- **Status** — NOT IMPLEMENTED
- **Evidence** — repo-wide grep for `DCAppAttest|DCDevice|app-attest|appattest` across
  `*.swift *.ts *.tsx *.py *.entitlements` returns zero hits.
- **What exists** — a genuinely strong *software* device identity already. Mobile access
  tokens are HMAC-signed with a `device_hash` bound into the payload, and refresh tokens
  are opaque `psr_`-prefixed values hashed into `mobile_security_sessions`. An attacker
  replaying a token on another device fails the device binding.
- **What is missing** — hardware attestation. Nothing today proves the client is a genuine,
  unmodified PulseSoc build on real Apple hardware. `device_hash` is client-asserted.
- **Entitlement** — `com.apple.developer.devicecheck.appattest-environment`.
- **Minimum iOS** — 14.0. Below the current floor; no target change needed.
- **New target** — no.
- **Backend dependency** — substantial and the real cost. A challenge endpoint, Apple's
  attestation-object verification (x5c chain to Apple's App Attest root, receipt parsing,
  counter tracking to reject replay), and a per-device key registry. Roughly the same
  machinery as the StoreKit JWS verifier in
  `services/business_os/entitlements/iap_apple.py:74-159`, which is a good template — that
  code already does x5c chain validation against an injected trust anchor.
- **Protection-lock interaction** — none.
- **User value** — invisible to users. Value is anti-abuse: it raises the cost of scripted
  signup, referral farming, and ad-credit fraud.
- **Effort** — Large. Backend-dominant.
- **Risk if wrong** — **High, and asymmetric.** Attestation that fails closed on a false
  negative locks real users out of the app with no recovery path. Must ship behind a flag
  in *report-only* mode first, measured, and only then enforced.
- **Prerequisites** — decide what it gates. Attesting everything is wrong; attesting
  signup, referral claim, and IAP is defensible.
- **Open question** — does this earn its cost at 39 users / 11 MAU? Documented for
  completeness; recommended **deferred** until abuse is observed rather than anticipated.

## 2. Live Activities + Dynamic Island

- **Status** — NOT IMPLEMENTED
- **Evidence** — no `ActivityKit`, `ActivityAttributes`, `Activity<`, or
  `NSSupportsLiveActivities` anywhere under `mobile-native/`.
- **What exists** — nothing. The Info.plist key is absent.
- **Entitlement** — none as such, but `NSSupportsLiveActivities` in Info.plist, plus an App
  Group to share state with the widget extension.
- **Minimum iOS** — **16.1**, above the 15.1 floor. Push-updated activities need 17.2.
- **New target** — **yes** (widget extension).
- **Backend dependency** — for locally-updated activities, none. For push-updated ones, a
  second APNs topic (`<bundle>.push-type.liveactivity`) and token registration — and note
  that `apns-topic` is currently deployment-wide, which is a known trap recorded in memory.
- **Protection-lock interaction** — **direct, and this is the danger.** The obvious first
  Live Activity is an ongoing call. Call state lives behind the PushKit/CallKit lock. A
  Live Activity may **read** call state; it must never start, end, mute, or route a call,
  and it must not touch `AVAudioSession`. If a call Live Activity is built, its state must
  come from an existing published observable, never from a new CallKit delegate.
- **User value** — high *if* there is a genuinely ongoing, glanceable activity. Calls and
  livestreams qualify. Feed and chat do not.
- **Effort** — Large (first extension in the project pays the whole Wave 2 tax).
- **Risk if wrong** — a Live Activity that never ends is a persistent, un-dismissable
  annoyance on the user's lock screen. Dismissal policy must be designed before any code.
- **Prerequisites** — Wave 2 extension foundation; deployment-target decision.

## 3. App Intents / Siri / Shortcuts

- **Status** — NOT IMPLEMENTED
- **Evidence** — no `AppIntent`, `INIntent`, SiriKit, `.intentdefinition`, or Siri
  entitlement. No `NSUserActivityTypes` in Info.plist.
- **Minimum iOS** — 16.0 for App Intents (15.1 floor is below it). SiriKit's older
  `INIntent` works at 15.1 but is the legacy path and should not be chosen for new work.
- **New target** — not strictly (App Intents can live in the app target), but Shortcuts
  discoverability is much better from an extension.
- **Backend dependency** — none beyond existing APIs.
- **Protection-lock interaction** — **constrain deliberately.** "Hey Siri, call X on
  PulseSoc" is the intent users will expect and it is the one that must not be built in
  Wave 1: it would drive the locked call path from a new entry point. Safe first intents
  are read-only or compose-only — open a profile, search, start a post draft.
- **User value** — moderate. Real value needs React Native ↔ Swift plumbing that does not
  exist yet.
- **Effort** — Medium per intent, Large for the first one.
- **Risk if wrong** — an intent that hangs or fails silently is worse than no intent; Siri
  surfaces the failure to the user as the app's fault.
- **Note** — App Intents are Swift-native. There is no Expo module for this; it is real
  native work in a codebase whose only Swift files today are `AppDelegate.swift` and the
  `pulse-now-playing` module.

## 4. Core Spotlight

- **Status** — NOT IMPLEMENTED
- **Evidence** — no `CSSearchableItem`, `CoreSpotlight`, or
  `NSUserActivity.isEligibleForSearch`.
- **Minimum iOS** — satisfied at 15.1.
- **New target** — no.
- **Backend dependency** — none. Indexing is local, from data the app already has.
- **Protection-lock interaction** — none.
- **User value** — moderate and *cheap*, which is a rare combination here. Indexing
  conversations, saved items and visited profiles makes PulseSoc content findable from the
  home screen.
- **Effort** — Small to Medium. Genuinely the best value-per-unit-effort in this document.
- **Risk if wrong** — **privacy, and it is the sharp edge.** Spotlight indexes are
  device-global. Indexing a private conversation, a blocked user, or Private Office content
  leaks it into system search outside the app's own gates. Indexing must be
  deny-by-default, must respect `users.profile_visibility` and `blocked_users`, and must be
  **fully purged on logout** — `CSSearchableIndex.deleteAllSearchableItems`.
- **Prerequisites** — an explicit written policy on what is indexable. That policy is the
  deliverable, not the code.

## 5. BackgroundTasks

- **Status** — PARTIALLY IMPLEMENTED
- **Evidence** — `mobile-native/ios/PulseSoc/Info.plist:68-74` declares `UIBackgroundModes`
  = `audio`, `voip`, `fetch`, `remote-notification`. No `BGTaskSchedulerPermittedIdentifiers`
  key; no `BGAppRefreshTask`, `BGProcessingTask`, `expo-background-fetch`, or
  `expo-task-manager` anywhere.
- **What this means** — `fetch` is declared but **nothing implements it**. The app claims a
  background capability it does not use. That is not harmful, but it is the kind of
  discrepancy App Review occasionally asks about, and it is misleading to the next reader.
- **What is missing** — the modern `BGTaskScheduler` path entirely.
- **Minimum iOS** — satisfied at 15.1 (BGTaskScheduler is 13.0+).
- **New target** — no.
- **Protection-lock interaction** — `audio` and `voip` modes are load-bearing for the
  locked surfaces. **Do not edit the `UIBackgroundModes` array** except to add/remove
  `fetch`, and even that should be a considered change.
- **User value** — low-to-moderate. Most of what background refresh would buy is already
  delivered by push.
- **Effort** — Small.
- **Risk if wrong** — battery. Also: a module-scope `AppState.currentState` check is
  always `"inactive"` in this codebase (recorded in memory) — background work gated on it
  silently never runs, and jest cannot catch it.
- **Recommendation** — either implement one narrow refresh task or **drop the unused
  `fetch` declaration**. Leaving a claimed-but-unused capability is the worst of the three.

## 6. Sign in with Apple

- **Status** — NOT IMPLEMENTED
- **Evidence** — no `expo-apple-authentication` in `package.json`; no
  `com.apple.developer.applesignin` entitlement; `usesAppleSignIn` absent from `app.json`;
  no Apple JWT verification route in `bot.py`; the PulseSoc users schema
  (`bot.py:115548-115627`) has `password_hash` and Telegram columns but **no `apple_sub`**
  or any external-identity column. `LoginScreen.tsx` offers email/password + biometric only.
- **Is it required by App Review?** — **No, not currently.** Guideline 4.8 requires an
  equivalent login option only when an app offers *third-party or social* login. PulseSoc
  offers neither — there is no Google, Facebook, or phone auth path. This is worth stating
  plainly because "Apple will reject us without it" is a common and, here, incorrect
  assumption. **It becomes mandatory the moment any social login is added.**
- **Entitlement** — `com.apple.developer.applesignin`.
- **Minimum iOS** — satisfied.
- **Backend dependency** — moderate: verify Apple's identity token (JWT, ES256, keys from
  `https://appleid.apple.com/auth/keys`, validate `iss`/`aud`/`exp`/`nonce`), a new
  `apple_sub` column with a unique index, and an account-linking policy for an email that
  already exists. Apple's private-relay addresses must be handled — they are real,
  deliverable addresses but they are per-app and must not be treated as a stable human
  identity.
- **Protection-lock interaction** — none, but it touches the auth path, which has its own
  hazards: route-auth declaration gate, and login rate limits that leak across tests.
- **User value** — high for conversion. One-tap signup with no password measurably lifts
  signup completion, and at 39 users the signup funnel is where value is.
- **Effort** — Medium.
- **Risk if wrong** — account takeover if the token is verified incorrectly, or duplicate
  accounts if linking is sloppy. Never trust the client-supplied email; trust only `sub`.
- **Recommendation** — **highest-value item in this document for the current stage of the
  product.** Wave 2.

## 7. Universal Links + Associated Domains — FULLY IMPLEMENTED

- **Status** — FULLY IMPLEMENTED. Leave it alone.
- **Evidence** — three layers, coordinated, and tested:
  - AASA served at `bot.py:128713` from
    `services/native_app_links.py:78-100`; live response verified HTTP 200, 1703 bytes,
    appID `87ZC69AGSR.com.pulsesoc.app`.
  - Path claims in `services/native_app_links.py:44-71`, with a comment block explaining
    that **component order is load-bearing** — `exclude` entries must sit *above* the
    pattern they carve out of, or they are dead configuration that reads as if it worked.
    `/pulse/app` and `/pulse/app/*` are correctly excluded above `/pulse/*`.
  - App-side parsing in `mobile-native/src/navigation/linking.ts:64-92`.
  - Parity locked by `tests/web_parity/test_aasa_claims.py`; live health check in
    `scripts/web_rebuild/aasa_health.py`.
- **Claimed paths** — `/pulse`, `/pulse/*` (minus `/pulse/app*`), `/search*`, `/dashboard`,
  `/dashboard/*`, `/account/*`, `/settings/*`, `/notifications`, `/saved`, `/education/*`.
- **Mismatches found between the three layers** — **none.**
- **What is genuinely absent** — `webcredentials` (would enable password-autofill
  association and is a prerequisite for a future passkey story) and `activitycontinuation`.
  Both are additive and low-risk. `webcredentials` is the more useful of the two.
- **Correction to a widely-held belief in this repo** — `/open/<destination>` routes
  (`bot.py:56346-56347`) exist but are **deliberately not universal links**. They render an
  interstitial with an App Store link and a `pulsesoc://` button. `services/app_links.py`
  documents why: iOS only hands the app paths the *shipped binary's* AASA claimed, so a
  newly-claimed path does nothing for installed users until they update. Anyone proposing
  a new `/open/...` link as an app-opening URL has misread this.

## 8. StoreKit 2 — FULLY IMPLEMENTED

- **Status** — FULLY IMPLEMENTED, with server-side cryptographic verification.
- **Evidence** —
  - Client: `mobile-native/src/payments/appleIapAdCredits.ts` and
    `appleIapPremium.ts`, via `expo-iap@^4.3.1`. SKUs `com.pulsesoc.adcredits.*`,
    `com.pulsesoc.premium.monthly`, `com.pulsesoc.premium.annual`.
  - Verification: `services/business_os/entitlements/iap_apple.py:74-159` — JWS ES256
    signature check plus full x5c certificate-chain validation against a trust anchor from
    `APPLE_ROOT_CA_CERTS`. **The server does not trust the client.**
  - Notifications v2 webhook: `services/business_os/entitlements/iap_api.py:85-109`,
    returns 503 when trust anchors are unconfigured rather than silently accepting.
  - Entitlement authority: `business_os_ent_grants`
    (`services/business_os/entitlements/schema.py:227-249`) with `source` ∈ {stripe,
    apple_app_store, google_play, admin} and a full audit trail.
  - Owner-only grant/revoke: `bot.py:23408-23465`, CSRF-protected, reason required.
- **Assessment** — this is the strongest Apple integration in the codebase and is better
  than most production implementations. It does the one thing that actually matters
  (server-side JWS + chain verification) rather than the thing that is easy (trusting a
  client boolean).
- **Open items, both minor** — product IDs live in mobile config and are not reconciled
  against App Store Connect from the backend, so a typo surfaces as an empty product list
  at runtime; and there is a legacy dual-mode path (`premium_glow_manual_grant`,
  `lifetime_premium` columns) sitting beside the canonical grants table.
- **Recommendation** — **no changes.** Do not "modernise" this.

## 9. MapKit

- **Status** — **NOT APPROPRIATE — there is no location data to draw.**
- **Evidence** — no `react-native-maps`, no `MapKit`, no `expo-location`. `bot.py` sets
  `geolocation=()` in `Permissions-Policy`. `marketplace_sellers`
  (`bot.py:117682-117705`) and `marketplace_merchant_applications`
  (`bot.py:117707-117734`) store `country` and `state_region` as **free text with no
  latitude/longitude columns**. The only lat/long in the schema is in a request-logging
  table, which is IP geolocation and must never be shown to a user as a business location.
- **What this means** — MapKit is not blocked by an entitlement or an OS version. It is
  blocked by the absence of the data a map would display. Adding a map is therefore not an
  iOS task at all; it is a schema change, a geocoding pipeline, an address-entry UI, and a
  privacy decision about publishing seller locations.
- **Recommendation** — **do not implement.** Revisit only if marketplace sellers gain
  structured addresses and a product reason to be found by proximity. Listing this as an
  iOS capability to build would be a category error.

## 10. Handoff

- **Status** — FOUNDATION EXISTS
- **Evidence** — `AppDelegate.swift:61-69` implements
  `application(_:continue:restorationHandler:)` and delegates to
  `RCTLinkingManager`. This is the correct and complete *receiving* half.
- **What is missing** — the *sending* half. Nothing creates an `NSUserActivity`, sets
  `isEligibleForHandoff`, or declares `NSUserActivityTypes` in Info.plist. So the app can
  receive a continuation but never advertises one.
- **Naming trap worth recording** — the codebase contains `shareComposerHandoff.ts`,
  `previewHandoff.ts`, `createComposerHandoff.ts`. These are **internal state persistence
  between screens** and have nothing to do with Apple Handoff. A grep for "handoff" will
  mislead you.
- **Minimum iOS / target / backend** — satisfied / no / none.
- **Protection-lock interaction** — none.
- **User value** — low. Handoff pays off across iPhone↔Mac↔iPad; PulseSoc has no Mac or
  iPad-optimised client, so there is nowhere to hand off *to*. The web client is the only
  other surface and Handoff-to-Safari is weak.
- **Effort** — Small (the receiving half is done).
- **Recommendation** — low priority. Implement `NSUserActivity` **only** as a by-product of
  Core Spotlight (#4), which uses the same object. Doing both together roughly halves the
  combined cost; doing Handoff alone buys little.

## 11. Keychain + Secure Enclave + LocalAuthentication

- **Status** — PARTIALLY IMPLEMENTED — and the implemented part is genuinely good.
- **Evidence, what exists** —
  - `mobile-native/src/session/sessionStore.ts` pins
    `keychainAccessible: AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY` — correct: not
    `ALWAYS`, and `THIS_DEVICE_ONLY` blocks the credential from migrating via encrypted
    backup to another device.
  - A separate biometric keychain service with `requireAuthentication: true`, which puts
    the item behind a Secure Enclave-backed access control.
  - `expo-local-authentication@~17.0.8`; `NSFaceIDUsageDescription` present in Info.plist.
  - AsyncStorage holds only non-sensitive cached metadata plus an enrollment marker — no
    tokens.
  - Private Office two-tier lock: passcode plus a server-minted opaque grant token held
    **in memory only**, sent as `X-Office-Grant`/`X-Office-Device`, gated server-side at
    `services/private_office_routes.py:302-317`.
- **What is missing** — direct Secure Enclave use: no `SecKeyCreateRandomKey` with
  `kSecAttrTokenIDSecureEnclave`, so there is no device-resident private key and therefore
  no request signing and no hardware-backed proof of possession. Also absent: certificate
  pinning, and a `keychain-access-groups` entitlement (which any future extension needs).
- **Correction to the brief's premise** — the brief implies this area needs building. It
  largely does not. The security posture here is stronger than most RN apps: proper
  accessibility classes, biometric gating, in-memory-only high-privilege grants, and
  device-bound HMAC tokens. The honest gap list is short: **Secure Enclave key + request
  signing, and cert pinning.**
- **Effort** — Medium (Enclave key + signing), Small (keychain access group).
- **Risk if wrong** — high. A bug in keychain accessibility or an over-eager biometric gate
  locks users out of their own accounts. This area has the worst failure mode in the
  document: it breaks *existing* working auth.
- **Recommendation** — do not touch the working parts. Add the keychain access group as
  part of the Wave 2 extension foundation because it is needed there anyway.

## 12. WidgetKit

- **Status** — NOT IMPLEMENTED
- **Evidence** — no `WidgetKit`, no `TimelineProvider`, no widget target, no App Group.
- **Minimum iOS** — satisfied at 15.1 for home-screen widgets (lock-screen widgets need
  16.0; interactive widgets need 17.0).
- **New target** — **yes**, plus App Group, plus the keychain-access-group problem in
  §"Two structural facts" — a widget showing personalised content must read the session.
- **Backend dependency** — a small, cheap, cacheable summary endpoint. A widget must not
  call a heavy feed endpoint on a timeline refresh.
- **Protection-lock interaction** — none, provided the widget never touches audio.
- **User value** — moderate. Honestly assessed: widgets pay off for apps with a
  glanceable number that changes. PulseSoc's candidates are unread count and Progress/
  referral status. The Progress widget is the more compelling of the two and is unusually
  well-suited — it is a slowly-changing number the user is motivated to watch.
- **Effort** — Large for the first widget (pays the extension tax), Small for subsequent.
- **Risk if wrong** — a widget stuck showing stale or logged-out state is visible on the
  home screen indefinitely. Timeline refresh budget is enforced by iOS and is stingier than
  most implementations assume.

## 13. Share Extensions

- **Status** — NOT IMPLEMENTED
- **Evidence** — no `NSExtension` targets; one native target total.
- **Minimum iOS** — satisfied.
- **New target** — **yes**, plus App Group (to stage shared payloads) and keychain access.
- **Backend dependency** — reuses existing upload/compose APIs.
- **Protection-lock interaction** — none *if* the extension never handles audio or video
  capture. A share extension that tried to record would collide with the locked surfaces;
  it must only accept already-existing items.
- **User value** — moderate-to-high for a social app — "share to PulseSoc" from Safari or
  Photos is a real content-acquisition channel.
- **Effort** — Large. Share extensions have a hard memory limit (~120 MB), which makes
  large video payloads a genuine engineering problem, not a detail. The correct pattern is
  to copy the item into the App Group container and let the main app do the work.
- **Risk if wrong** — extension crashes appear to the user as the *system* share sheet
  failing, which reflects badly and is hard to diagnose from crash logs.

## 14. Action Button + Control Center

- **Status** — NOT IMPLEMENTED
- **Evidence** — no `ControlWidget`, no App Intents (which both of these are built on).
- **Minimum iOS** — Control Center controls require **18.0**, well above the 15.1 floor.
  The Action Button (iPhone 15 Pro+) is configured by the *user* and routes through
  Shortcuts, so it needs App Intents (#3) rather than anything Action Button-specific.
- **New target** — yes for Control Center (widget extension).
- **Dependency** — **hard-blocked on #3 App Intents.** There is no Action Button API; you
  expose an App Intent and the user assigns it. Building "Action Button support" as a
  discrete feature is not a thing that exists.
- **User value** — low for this app at this stage.
- **Recommendation** — deprioritise. Revisit only after App Intents ships and only if the
  deployment floor moves to 18.0, which is a large exclusion today.

---

## Wave sequencing

Ordered by dependency first, value second. Feature flags default **OFF** throughout.

**Wave 1 — decisions and no-cost corrections.** No new capability.
- ~~Decide the deployment-target question.~~ **DECIDED 2026-09-19: raise to 16.1.** Zero
  measured native sessions below iOS 18. Applying it is still Wave 1 work: four pbxproj
  lines plus `expo-build-properties` in `app.json`, which trips the `dependency_watch`
  gate and needs a declaration. See `DECISIONS_DEPLOYMENT_TARGET_AND_EXTENSIONS.md`.
- Resolve the unused `UIBackgroundModes: fetch` declaration (#5).
- Optionally add `webcredentials:pulsesoc.com` to the associated domains (#7) — additive,
  low-risk, and a prerequisite for any future passkey work.

**Wave 2 — the two foundations everything else waits on.**
- **Sign in with Apple** (#6). Highest value, no target work, no lock interaction.
- **Extension foundation**: one App Group, `keychain-access-groups`, a second App ID, and a
  target-generation strategy. ~~Prebuild-safe generation was the open unknown.~~
  **DECIDED 2026-09-19: the Xcode project is committed, so extensions are committed
  targets.** That removes the hard part and replaces it with one obligation — a protection
  test pinning the `PBXNativeTarget` count and names, so a stray `expo prebuild` cannot
  delete every extension at once. Ship the foundation with a trivial placeholder widget to
  prove the pipeline end to end. Four later capabilities depend on this and it should be
  built once, on purpose, rather than four times accidentally.

**Wave 3 — cheap, self-contained wins.**
- Core Spotlight (#4) — best value-per-effort, gated on a written indexing policy.
- Keychain access group + Secure Enclave signing (#11).
- BackgroundTasks (#5), if Wave 1 decided to implement rather than remove.
- App Attest report-only mode (#1), if the abuse case justifies it.

**Wave 4 — extension-dependent and OS-gated.**
- WidgetKit (#12) — start with Progress/referral status.
- App Intents (#3) — read-only intents only; **no call intents**.
- Live Activities (#2) — only if the target moves to 16.1; call state read-only.
- Handoff sending half (#10), as a by-product of Spotlight.

**Wave 5 — deferred or not recommended.**
- Share Extensions (#13).
- Control Center / Action Button (#14) — blocked on #3 and on an 18.0 floor.
- MapKit (#9) — **not recommended**; blocked on data that does not exist.

---

## What this audit does *not* claim

Stated explicitly, per the brief's no-false-claims requirement.

- Nothing in this document has been implemented. This is a map.
- Statuses come from static inspection of `3e9618da` plus live probes of
  `https://pulsesoc.com`. No capability was tested on a device, because none of the
  unimplemented ones exist to test.
- The physical device is available and is the required model — `P3r7or` is an
  iPhone 16 Pro (`iPhone17,1`), paired and reachable — so device verification is not
  blocked for future stages.
- Effort sizes are relative judgements, not estimates in hours.
- The App Store Connect state of the product IDs in #8 is **UNVERIFIED** from this session;
  the code references them but nothing here confirms they exist and are approved in ASC.
- The claim that Sign in with Apple is not currently required by Guideline 4.8 rests on
  PulseSoc having no third-party login path today, which was verified in the code. It is a
  reading of Apple's published guideline, not a ruling from App Review.
