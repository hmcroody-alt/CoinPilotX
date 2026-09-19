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
> the `git ls-files` evidence behind each. ~~Nothing has been implemented yet.~~
>
> **Correction 2026-09-19:** the floor decision has *shipped*. `c8f05637` ("build(ios):
> raise the deployment floor from 15.1 to 16.1") set `IPHONEOS_DEPLOYMENT_TARGET = 16.1`
> project-wide and `"ios.deploymentTarget": "16.1"` in `Podfile.properties.json`. The *app*
> target moved; RN's `post_install` keeps the individual pods at 15.1, which
> `DECISIONS_DEPLOYMENT_TARGET_AND_EXTENSIONS.md:135-143` documents as deliberate. The
> section below and the "Minimum iOS" column of the summary table are left as
> written but must be read against a **16.1** floor, not 15.1. Every row marked `15.1 ✅` is
> still satisfied; the two rows that were *above* the floor no longer are.

### 1. The iOS deployment target is 15.1 — **now 16.1, see the correction above**

As found (before `c8f05637`):

```
$ grep -o "IPHONEOS_DEPLOYMENT_TARGET = [0-9.]*" mobile-native/ios/PulseSoc.xcodeproj/project.pbxproj | sort -u
IPHONEOS_DEPLOYMENT_TARGET = 15.1
```

One value, project-wide. Several requested capabilities had a minimum OS above it:

| Capability | Minimum iOS | Above the old 15.1 by | vs. the 16.1 floor today |
|---|---|---|---|
| Sign in with Apple | 13.0 | — (below it) | satisfied by a wide margin |
| App Intents | 16.0 | 0.9 | satisfied |
| Live Activities | 16.1 | 1.0 | satisfied, exactly at the floor |
| ActivityKit `ActivityContent` / `staleDate` | 16.2 | 1.1 | **still above the floor** |
| ActivityKit push updates | 17.2 | 2.1 | **still above the floor** |
| Control Center controls (`ControlWidget`) | 18.0 | 2.9 | **still above the floor** |

The 16.2 row is new: `LIVE_ACTIVITIES.md` Finding 6 argues that the 16.1-era ActivityKit API
has no stale-content handling, which matters for an activity that can outlive its updater.

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

> **Revised 2026-09-19 — the three-part prerequisite is down to one part, and the removed
> parts are the two that carried the risk.**
>
> Each capability was designed in detail after this paragraph was written, and each one
> dissolved a different piece of it:
>
> | Part | Status |
> |---|---|
> | **Target-generation strategy** | **Settled.** `ios/` is a committed bare workflow, so an extension target is ordinary Xcode work, not a config plugin that synthesizes targets (`DECISIONS_DEPLOYMENT_TARGET_AND_EXTENSIONS.md` Decision 2). `tests/protection/test_ios_native_target_inventory.py` already guards it. |
> | **Keychain access group** | **Not needed by anything currently recommended.** App Intents use a deep link (`APP_INTENTS_SIRI_SHORTCUTS.md` F1); the widget renders a snapshot the app writes (`WIDGETKIT.md` F2); the Share Extension stages bytes and never calls the API (`SHARE_EXTENSION.md` F3). Only #2 Live Activities has not been re-examined against the pattern. |
> | **App Group** | **Still real, still shared, still should be built once.** |
>
> The pattern all three arrived at independently is worth stating once here: **on every
> Apple surface that runs outside the app process, the app owns the network and the
> out-of-process surface owns only a file in the shared container.** That is why the App
> Group survives and the keychain access group does not.
>
> The consequence for planning is larger than it looks. `DECISIONS_KEYCHAIN_ACCESS_GROUP.md`
> argued for building the access group as its own release *before* the first extension,
> because its failure mode is a silent mass sign-out of every existing user. If no
> recommended capability needs it, that release — and that risk — comes off the plan
> entirely. See `SHARE_EXTENSION.md` Finding 4.

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

> **Amended 2026-09-19.** App Intents' adjacency is not only "could be asked to start a
> call." `AudioPlaybackIntent` is a framework protocol whose entire purpose is to grant an
> intent the right to begin audio from the background or lock screen; the intent that would
> use it is "play PulseSoc radio", not a call. The constraint is therefore a named
> conformance ban, not a judgement about subject matter — see §3 and
> `APP_INTENTS_SIRI_SHORTCUTS.md` Finding 2.

---

## Capability matrix

| # | Capability | Status | Min iOS | New target | Wave |
|---|---|---|---|---|---|
| 1 | App Attest + DeviceCheck | NOT IMPLEMENTED | 14.0 ✅ | No | 3 |
| 2 | Live Activities + Dynamic Island | NOT IMPLEMENTED | 16.1 ✅ (16.2 for the API worth using) | **Yes** | 4 |
| 3 | App Intents / Siri / Shortcuts | NOT IMPLEMENTED | 16.0 ✅ | No — app target, deliberately (Finding 4) | 4 |
| 4 | Core Spotlight | NOT IMPLEMENTED | 15.1 ✅ | No | 3 |
| 5 | BackgroundTasks | NOT IMPLEMENTED (unused `fetch` declaration removed 2026-09-19) | 16.1 ✅ (device-only to test) | No | 3 |
| 6 | Sign in with Apple | NOT IMPLEMENTED | **13.0** ✅ (the least constrained item here) | No | 2 |
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

Full treatment in **`APP_ATTEST_DEVICECHECK.md`**.

- **Status** — NOT IMPLEMENTED
- **Evidence** — repo-wide grep for `DCAppAttest|DCDevice|app-attest|appattest` across
  `*.swift *.ts *.tsx *.py *.entitlements` returns zero hits.
- **What exists** — a genuinely strong *software* device identity already. Mobile access
  tokens are HMAC-signed with a `device_hash` bound into the payload, and refresh tokens
  are opaque `psr_`-prefixed values hashed into `mobile_security_sessions`. An attacker
  replaying a token on another device fails the device binding.
- **What is missing** — hardware attestation. Nothing today proves the client is a genuine,
  unmodified PulseSoc build on real Apple hardware. `device_hash` is client-asserted.
- **Entitlement** — `com.apple.developer.devicecheck.appattest-environment`. Its
  `development`/`production` split is the APNs environment trap again and must be a build
  setting, not a literal — `PulseSoc.entitlements` already does this for `aps-environment`.
- **Minimum iOS** — 14.0. Below the current floor; no target change needed. But
  `DCAppAttestService.supported` is `false` on Mac/Catalyst/Apple silicon, and `generateKey`
  fails in app extensions regardless — so the floor is not the whole availability story.
- **New target** — no.
- **Backend dependency** — substantial and the real cost. A challenge endpoint, Apple's
  attestation-object verification (x5c chain to Apple's App Attest root, receipt parsing,
  counter tracking to reject replay), and a per-device key registry. Roughly the same
  machinery as the StoreKit JWS verifier in
  `services/business_os/entitlements/iap_apple.py:74-159`, which is a good template — that
  code already does x5c chain validation against an injected trust anchor.
  **Corrected 2026-09-19:** "same machinery" overstates it. The chain/trust-anchor half
  transfers; the envelope does not — a StoreKit payload is a JWS (`_split_jws` requires
  three dot-separated segments) while an App Attest attestation is CBOR, and
  `requirements.txt` has no CBOR library. New dependency, new parsing layer. Full treatment
  in **`APP_ATTEST_DEVICECHECK.md`**.
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
  **Resolved 2026-09-19 (`APP_ATTEST_DEVICECHECK.md`):** still deferred, but the question is
  now answerable instead of a judgement call. The device tier of the rate limiter keys on
  `device_fingerprint(user_agent, X-PulseSoc-Device-Id)` — a client-supplied header
  (`bot.py:3155-3158`, `pulse_security_core.py:107-111`, `:150`) — so rotating one header
  mints a fresh device bucket. Counting distinct `device_hash` per `ip_hash` per window
  measures exactly the behaviour attestation would block, costs nothing, and decides the
  deferral with evidence.

## 2. Live Activities + Dynamic Island

Full treatment in **`LIVE_ACTIVITIES.md`**, which disagrees with this entry on one point:
the first Live Activity should be a **media upload**, not a call. The call one is where
everybody's instinct goes, and it is the worst venue for shaking out a brand-new extension
target — `Activity.request` *throws*, and one of the things it throws for is "this device
already has too many activities", which must never be able to reach the call path.

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

- **Status** — NOT IMPLEMENTED. Detail in **`APP_INTENTS_SIRI_SHORTCUTS.md`**.
- **Evidence** — no `AppIntent`, `INIntent`, SiriKit, `.intentdefinition`, or Siri
  entitlement. No `NSUserActivityTypes` in Info.plist.
- **Minimum iOS** — 16.0 for App Intents (15.1 floor is below it). SiriKit's older
  `INIntent` works at 15.1 but is the legacy path and should not be chosen for new work.

> **Resolved 2026-09-19:** the floor question is moot. The project floor is **16.1** since
> `c8f05637`, above App Intents' 16.0, so `Intents.framework` need not be considered at all.
>
> **Two things this section did not say**, both from reading the AppIntents Swift interface
> (`APP_INTENTS_SIRI_SHORTCUTS.md` Findings 1–3):
>
> 1. `AppIntent.openAppWhenRun` **defaults to `false`**, so `perform()` runs without
>    foregrounding the app — in a Swift context with no Hermes runtime, no bridge, and no
>    `pulseApi()`. Every intent is therefore either a Siri-addressable deep link
>    (`openAppWhenRun = true`) or a parallel native client with its own auth, which needs a
>    keychain access group that does not exist yet. There is no cheap middle. This is a
>    larger constraint than "React Native ↔ Swift plumbing that does not exist yet" implies:
>    it bounds what an intent can *be*, not how much work it is.
> 2. `AppIntent.authenticationPolicy` **defaults to `alwaysAllowed`**, so an intent surfacing
>    user-scoped data runs on a locked phone unless someone remembers to say otherwise. Same
>    default-is-permissive shape as the route-auth gate; same remedy.
>
> The protection-lock bullet below is right but under-specified — see the correction attached
> to it.
- **New target** — not strictly (App Intents can live in the app target), but Shortcuts
  discoverability is much better from an extension.

  > **Priced 2026-09-19.** `ForegroundContinuableIntent` — the protocol that lets a
  > background intent escalate into the foreground mid-run — is
  > `@available(iOSApplicationExtension, unavailable)`. So the extension choice buys
  > discoverability and sells the only escape hatch out of the no-JavaScript box in Finding 1.
  > If every intent is `openAppWhenRun = true` the trade does not bite, which is one more
  > argument for that ceiling. `APP_INTENTS_SIRI_SHORTCUTS.md` Finding 4 has the table.
- **Backend dependency** — none beyond existing APIs.
- **Protection-lock interaction** — **constrain deliberately.** "Hey Siri, call X on
  PulseSoc" is the intent users will expect and it is the one that must not be built in
  Wave 1: it would drive the locked call path from a new entry point. Safe first intents
  are read-only or compose-only — open a profile, search, start a post draft.

  > **Sharpened 2026-09-19.** "No call intents" is a rule about intent *subject matter*, and
  > it depends on someone recognising a call intent as one. The framework has a hazard with
  > an actual name: conforming a type to `AudioPlaybackIntent` (or its deprecated predecessor
  > `AudioStartingIntent`) is how you tell the system "running this begins audio playback",
  > which grants it the right to start audio from the background or the lock screen — a new
  > audio-session entry point outside the app's ownership arbitration. The intent that would
  > reach for it is "play PulseSoc radio", which nobody would classify as a call intent.
  >
  > **The rule to assert in a test: no type in this codebase conforms to
  > `AudioPlaybackIntent` or `AudioStartingIntent`.** One grep, and it covers the case nobody
  > was thinking about. See `APP_INTENTS_SIRI_SHORTCUTS.md` Finding 2.
- **User value** — moderate. Real value needs React Native ↔ Swift plumbing that does not
  exist yet.
- **Effort** — Medium per intent, Large for the first one.
- **Risk if wrong** — an intent that hangs or fails silently is worse than no intent; Siri
  surfaces the failure to the user as the app's fault.
- **Note** — App Intents are Swift-native. There is no Expo module for this; it is real
  native work in a codebase whose only Swift files today are `AppDelegate.swift` and the
  `pulse-now-playing` module.

  > **Corrected 2026-09-19.** That last clause is false, and it is the sentence this document
  > leans on wherever it prices Swift work. There are **three** local Swift modules:
  > `pulse-now-playing` (1 file), `pulse-video-mixer` (1), and `pulse-apple-translation`
  > (8 implementation files plus a 6-file Swift test harness). The last is a fully worked
  > example of what a new native capability looks like here — a podspec holding the floor
  > below the feature's own requirement and weak-linking the framework, an
  > `@available`-gated implementation, a typed error enum whose raw values are the wire
  > contract with TypeScript, and tests that build as a plain host executable without an
  > Xcode test target. Every Swift effort estimate in this document should be read against
  > that, not against a codebase with one Swift file in it.
  > See `APPLE_NATIVE_ARCHITECTURE.md` and `SIGN_IN_WITH_APPLE.md` Finding 6.

## 4. Core Spotlight

- **Status** — NOT IMPLEMENTED. Policy in **`DECISIONS_CORE_SPOTLIGHT_INDEXING_POLICY.md`**;
  mechanism in **`CORE_SPOTLIGHT.md`**.
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
  deliverable, not the code. **Added 2026-09-19:** there is a second prerequisite this entry
  missed — the AppDelegate must switch on `userActivity.activityType` before any indexing
  code ships, because a Spotlight tap arrives at
  `application(_:continue:restorationHandler:)` and both existing handlers there ignore it
  while the delegate returns `true`. See `CORE_SPOTLIGHT.md` Finding 2. This entry also said
  "none" for protection-lock interaction, which is right for *audio* but understates the
  coupling: the AppDelegate fix is shared with Handoff, Live Activities and App Intents, and
  `AppDelegate.swift` is itself an unprotected file that the audio locks arguably should
  cover.

## 5. BackgroundTasks

- **Status** — NOT IMPLEMENTED (was PARTIALLY IMPLEMENTED by declaration only; see RESOLVED
  below). Full treatment in **`BACKGROUND_TASKS.md`**.
- **Evidence** — `mobile-native/ios/PulseSoc/Info.plist:68-73` declares `UIBackgroundModes`
  = `audio`, `voip`, `remote-notification` (`fetch` removed 2026-09-19). No
  `BGTaskSchedulerPermittedIdentifiers` key; no `BGAppRefreshTask`, `BGProcessingTask`,
  `expo-background-fetch`, or `expo-task-manager` anywhere.
- **What this means** — `fetch` is declared but **nothing implements it**. The app claims a
  background capability it does not use. That is not harmful, but it is the kind of
  discrepancy App Review occasionally asks about, and it is misleading to the next reader.
- **What is missing** — the modern `BGTaskScheduler` path entirely.
- **Minimum iOS** — satisfied at 16.1 (BGTaskScheduler is 13.0+). But **untestable on the
  simulator**: `BGTaskScheduler.h` names Simulator as a cause of
  `BGTaskSchedulerErrorCodeUnavailable`. Device-only by construction.
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
- **RESOLVED 2026-09-19** — dropped. `fetch` is gone from both `app.json` and
  `Info.plist`; `audio`, `voip` and `remote-notification` remain untouched. Status is now
  **NOT IMPLEMENTED** and honestly so, rather than PARTIALLY IMPLEMENTED by declaration
  only. It must not gate itself on a module-scope `AppState.currentState` — see "Risk if
  wrong" above.
- **CORRECTION 2026-09-19** — the sentence this note originally ended with ("it arrives
  with a `BGTaskSchedulerPermittedIdentifiers` entry and an actual task") was incomplete,
  and wrong in a way that produces an unreadable runtime error. `BGTaskScheduler` did not
  supersede the background modes. From `BGTask.h` in the iOS 26.5 SDK: a `BGAppRefreshTask`
  "requires setting the `fetch` `UIBackgroundModes` capability", and a `BGProcessingTask`
  requires `processing` — a mode this app has never declared. So implementing #5 means
  **re-adding a background mode as well as the identifiers key**, in both `app.json` and
  `Info.plist`. Omitting either yields `BGTaskSchedulerErrorCodeNotPermitted`, which cannot
  distinguish the two causes. See `BACKGROUND_TASKS.md` Finding 2.
- **FOUND WHILE AUDITING #5, and it is not about #5** — `UIBackgroundModes` is asserted by
  `tests/protection/test_realtime_audio_architecture.py:312`, but that test reads
  `app.json`. The committed `ios/PulseSoc/Info.plist` is what Xcode builds, and it is in no
  manifest category and no `dependency_watch` list. Removing `audio` from it leaves the
  protection suite green and the audio change gate clean while background call and radio
  audio die on device. Live exposure, **owed to an audio mission** — see
  `BACKGROUND_TASKS.md` Finding 1.

## 6. Sign in with Apple

- **Status** — NOT IMPLEMENTED. Decisions in **`DECISIONS_SIGN_IN_WITH_APPLE.md`** (server,
  schema, linking policy); client/framework detail in **`SIGN_IN_WITH_APPLE.md`**.
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

  > **Corrected 2026-09-19** (`SIGN_IN_WITH_APPLE.md` Finding 6):
  >
  > - **RS256, not ES256.** Fetched live, `https://appleid.apple.com/auth/keys` returns three
  >   RSA keys, all `alg: RS256`, with distinct `kid`s. The ES256 here is contamination from
  >   the IAP verifier (`iap_apple.py`), which genuinely is ES256 — the third time this
  >   mission has found one Apple verifier assumed to generalise to another. The three
  >   concurrent keys are also why `kid` selection is mandatory: there is no "Apple's public
  >   key" to pin.
  > - **A table, not a column.** Superseded by `DECISIONS_SIGN_IN_WITH_APPLE.md` Decision 2 —
  >   `user_external_identities`, because `users` is created twice and the second definition
  >   wins, so a column can be added to the wrong one and silently discarded.
  > - **The nonce needs a server-issued challenge or it is not a check.** Comparing a
  >   client-supplied nonce against a client-supplied token proves nothing; no such
  >   machinery exists in the repo (Finding 4).
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

Full treatment in **`MAPKIT.md`**.

> **Sharpened 2026-09-19 — the verdict stands; its reason changes.** `MAPKIT.md` re-derived
> this section and agrees with "do not implement," but the section as written above is the
> version most likely to be reversed by the next person who checks it, because *"there is no
> location data" is falsifiable by a grep and the grep returns hits.* Three corrections:
>
> **1. The lat/long is not merely IP-derived — it has never held a value.** `latitude` and
> `longitude` occur three times in the whole repo, at two locations: the `visitor_sessions`
> declarations (`bot.py:122504-122505`) and the column list of the single insert
> (`:15039`), which binds literal `NULL, NULL` (`:15040`). No `UPDATE` writes them and none
> of the six `visitor_sessions` selects reads them. "Don't display it" reads like a
> presentation problem with a presentation fix; there is nothing to present.
>
> **2. The blocker is not a schema change, it is a trusted source — and that was already
> looked for here and not found.** The section lists "a schema change, a geocoding pipeline,
> an address-entry UI, and a privacy decision." The fourth is not open. `bot.py:15058-15063`
> records the decision: geo came off request headers, "every visitor row's country/region/city
> was whatever the visitor typed," and the fix was that region and city "record nothing rather
> than recording a claim." The revisit condition above — *"if marketplace sellers gain
> structured addresses"* — describes exactly the steerable, self-asserted claim that fix
> removed, now with a map pin attached to make it look authoritative.
>
> **3. The schema does contain geocodable addresses, and they belong to staff.** Five
> `address` columns exist; three are **Bitcoin** addresses (`:116061`, `:116074`, `:120483`).
> The other two are `admin_users` (`:122347-122352`, full `address_line1/2` + city/state/zip/
> country beside `date_of_birth` and emergency contact) and `employees` (`:123809`). They are
> structured where every other address in the product is free text, which makes them **the
> only rows a geocoder would succeed on** — so an engineer told "build the map, find the data"
> lands on them by following the evidence. `MAPKIT.md` Finding 4 turns that into a named
> prohibition, because an absence of data is not a control.
>
> `MAPKIT.md` Finding 6 also names what *would* be appropriate — a country-level marketplace
> filter using the one field with a trusted source — so that "do not implement" does not get
> re-litigated as "but users want local sellers."

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

- **Status** — NOT IMPLEMENTED. Detail in **`WIDGETKIT.md`**.
- **Evidence** — no `WidgetKit`, no `TimelineProvider`, no widget target, no App Group.
- **Minimum iOS** — satisfied at 15.1 for home-screen widgets (lock-screen widgets need
  16.0; interactive widgets need 17.0).

  > **Updated 2026-09-19.** At the 16.1 floor the parenthetical is moot: **lock-screen
  > accessory widgets are available.** That turns a deferred question into a live one — an
  > accessory widget renders user-scoped content on a locked device continuously, which is
  > the third appearance of the property Core Spotlight and App Intents each raised. The
  > allowlist in `DECISIONS_CORE_SPOTLIGHT_INDEXING_POLICY.md` should govern widget content
  > too, and applied as written it denies the unread-count widget. `WIDGETKIT.md` Finding 3.
  >
  > Configurable widgets on the modern path (`AppIntentConfiguration`) are 17.0 and therefore
  > **above** the floor; the legacy `IntentConfiguration` route is built on
  > `Intents.framework`, which this mission has ruled out. The first widget must be static.

- **New target** — **yes**, plus App Group, plus the keychain-access-group problem in
  §"Two structural facts" — a widget showing personalised content must read the session.

  > **Disputed 2026-09-19** (`WIDGETKIT.md` Finding 2). The last clause is avoidable, and
  > avoiding it is the better design. A widget extension has no RN bridge and no
  > `pulseApi()`, so a fetching widget is a parallel native HTTP client with duplicated auth —
  > the shape App Intents rejected, except that a widget has no `openAppWhenRun` fallback.
  > Instead: **the app writes a rendered summary to the App Group container and calls
  > `reloadTimelines`; the widget makes no network calls at all.** The App Group is still
  > required; the keychain access group is not, and WidgetKit stops waiting on it.
  >
  > The cost is that the widget is only as fresh as the last app launch — which is a
  > structural argument for the Progress widget over the unread count, agreeing with the
  > "user value" bullet below for a different reason. Apple's own fix for the staleness is
  > `WidgetPushHandler`, at **iOS 26.0**.
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

  > **Two specific shapes of that risk, 2026-09-19.**
  >
  > `TimelineProvider` has **no error channel** — `getTimeline` returns a `Timeline`, not a
  > `Result`, and the iOS 17 `async` successor is not `async throws` either. A widget cannot
  > decline to render, so a failed fetch becomes whatever the default entry says. `unread: 0`
  > on a failure tells the user nobody has written to them. The `Entry` type must carry an
  > explicit state.
  >
  > On sign-out, deleting the App Group snapshot is **not sufficient** — the system keeps
  > showing the last timeline it was handed. It takes a delete *and* a
  > `reloadAllTimelines()`, both inside `clearUserScopedMediaState()`. The App Group container
  > is a third storage tier that neither `accountScopedKeys` nor `clearAllMediaCaches` can
  > see. `WIDGETKIT.md` Findings 1 and 4.

## 13. Share Extensions

Full treatment in **`SHARE_EXTENSION.md`**.

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

> **Corrected 2026-09-19 — "plus keychain access" is wrong, and the memory limit is the
> second reason, not the first.**
>
> Two changes, both from `SHARE_EXTENSION.md`:
>
> **1. No keychain access group.** The line above assumes the extension reads the session.
> It does not need to, because it makes no network calls (see 2). It stages bytes into the
> App Group container and completes; the app uploads. The only state it needs is a
> signed-in boolean, which is not a credential and belongs in App Group `UserDefaults`.
> This is the same conclusion `WIDGETKIT.md` Finding 2 reached for widgets, and it retires
> the keychain half of the "three-part prerequisite" for a third of the four capabilities
> that were said to need it — see the note in §0.
>
> **2. The ~120 MB figure is not the constraint that decides the design, and this session
> could not verify it from any first-party source.** The constraint that does decide it is
> stated in the SDK. `NSExtensionContext.h:18` says post-completion work runs "as a
> background-priority task" and that the `expired` flag will be YES "if the system decides
> to prematurely terminate" it. Uploading from an extension is therefore lifecycle-unsafe
> *at any payload size*, not just at video size. The distinction matters because a team
> told the obstacle is memory will reasonably conclude that a 200 KB JPEG may be uploaded
> inline — and will ship a path that silently loses items on a busy device.
>
> Related and load-bearing: `NSItemProvider.h:119` — the shared file "will be deleted when
> the completion handler returns." The copy into the container is not an optimisation; it
> is the only correct use of the API.
>
> The **Effort — Large** rating stands, but the largest single unknown is not engineering:
> `SHARE_EXTENSION.md` Finding 7 shows the product shape turns on whether
> `NSExtensionContext.openURL:` works from a share extension, which the header permits and
> Apple's prose has historically restricted. That must be settled on device before the
> target is created.

## 14. Action Button + Control Center

- **Status** — NOT IMPLEMENTED *for Action Button and Control Center.* Note, added
  2026-09-19: the app **does** already ship a full lock-screen / Control Centre / CarPlay
  transport control set via `modules/pulse-now-playing`
  (`MPNowPlayingInfoCenter` + `MPRemoteCommandCenter`), which this entry did not mention.
  See `SYSTEM_CONTROLS.md` — it covers both halves.
- **Evidence** — no `ControlWidget`, no App Intents (which both of these are built on).
- **Minimum iOS** — Control Center controls require **18.0**, two majors above the 16.1
  floor. The Action Button (iPhone 15 Pro+) is configured by the *user* and routes through
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
  measured native sessions below iOS 18. Applying it is four pbxproj lines and nothing
  else — a non-clean prebuild was tested and preserves them, so no `app.json` change and
  no declaration are needed. See `DECISIONS_DEPLOYMENT_TARGET_AND_EXTENSIONS.md`.
- ~~Resolve the unused `UIBackgroundModes: fetch` declaration (#5).~~ **DONE 2026-09-19.**
  Removed from `app.json` and `Info.plist`; nothing implemented background fetch and a
  declared-but-unimplemented mode is a Guideline 2.5.4 exposure. Declared and batteried —
  see the background-mode addendum in `reports/realtime_audio_change_declaration.md`.
- ~~Optionally add `webcredentials:pulsesoc.com` to the associated domains (#7).~~
  **DROPPED 2026-09-19.** Nothing plans passkeys. Adding an entitlement no code uses is
  the same anti-pattern as the `fetch` mode removed one line above; it should arrive with
  the feature that needs it, not in advance of one.

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
- BackgroundTasks (#5) — Wave 1 removed the unused declaration rather than implementing.
  Only revisit if a concrete refresh need appears; push already covers most of it.
- App Attest report-only mode (#1), if the abuse case justifies it.

**Wave 4 — extension-dependent and OS-gated.**
- WidgetKit (#12) — start with Progress/referral status.
- App Intents (#3) — read-only intents only; **no call intents**, and **no conformance to
  `AudioPlaybackIntent` / `AudioStartingIntent`**. All intents `openAppWhenRun = true` with
  an explicit `authenticationPolicy` (`APP_INTENTS_SIRI_SHORTCUTS.md`).

  > **Note 2026-09-19:** #3 is in this wave for sequencing reasons, not extension ones. With
  > the app-target decision it needs neither the App Group nor a second App ID, so it is the
  > one item here that Wave 2 does not gate. It could move earlier if the device check in its
  > Owed table (can an `openAppWhenRun` intent open its own `pulsesoc://` URL?) comes back
  > clean.

- Live Activities (#2) — only if the target moves to 16.1; call state read-only.
- Handoff sending half (#10), as a by-product of Spotlight.

**Wave 5 — deferred or not recommended.**
- Share Extensions (#13).
- Control Center / Action Button (#14) — blocked on #3 and on an 18.0 floor.
- MapKit (#9) — **not recommended**; not because the data is missing but because the usable
  source was deliberately emptied (`bot.py:15058-15063`) and the only geocodable addresses
  left are staff home addresses. `MAPKIT.md`.

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
