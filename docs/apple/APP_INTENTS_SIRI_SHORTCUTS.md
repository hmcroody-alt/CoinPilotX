# App Intents / Siri / Shortcuts

Capability #3. Status: **NOT IMPLEMENTED.** No `AppIntent`, `INIntent`, SiriKit usage,
`.intentdefinition` file, or Siri entitlement anywhere in the repo.

The audit's constraint — read-only or compose-only intents, never a call intent — is right
and this document keeps it, with one addition it did not name (Finding 2). But the audit
treated the capability as mostly a scoping question. It is not. The dominant fact is
Finding 1, and it changes what an App Intent can *be* in this app.

Evidence: `AppIntents.framework/Modules/AppIntents.swiftmodule/arm64e-apple-ios.swiftinterface`
in the iOS 26.5 SDK (11,752 lines), plus this repo. Nothing has been run.

---

## Finding 1 — the default App Intent has no JavaScript

`AppIntent`'s declaration (`:552-571`) requires `func perform() async throws -> PerformResult`
and offers:

```swift
static var openAppWhenRun: Swift.Bool { get }
```

with a **default implementation** (`:583-585`), and that default is `false`. An App Intent
runs *without foregrounding the app*. That is the whole point of the design: Siri and
Shortcuts want to do the thing, not launch a UI.

For a React Native app this is decisive. `perform()` executes in a Swift context with no
Hermes runtime, no bridge, and no `pulseApi()`. Every piece of PulseSoc's business logic,
auth handling, token refresh, and error taxonomy lives in TypeScript. None of it is
reachable from `perform()`.

So there are exactly two shapes an App Intent can take here:

**(a) `openAppWhenRun = true`.** The intent foregrounds the app and hands off a destination.
Cheap, safe, and reuses code that already works. It is "a Siri-addressable deep link", which
sounds like a diminished version of the feature and is in fact the correct version of it for
a UI-centric social app.

**(b) A parallel native client.** `perform()` does real work: its own HTTP client, its own
auth, its own reading of the session token — which it cannot get from `expo-secure-store`
without a **keychain access group**, which does not exist yet (Wave 2,
`DEVICE_SECURITY.md`). Plus its own error handling and its own copy of whatever backend
contract it touches, permanently drifting from the TypeScript one.

There is no cheap middle. The `ForegroundContinuableIntent` escape hatch (Finding 4) narrows
the gap but does not close it.

**Recommendation: every PulseSoc intent is shape (a), and that is a deliberate ceiling, not
a first phase.** Shape (b) should require a written decision naming the specific intent and
why the duplication is worth it.

---

## Finding 2 — `AudioPlaybackIntent` is the protection-lock landmine, and it has a name

The audit forbade call intents: "Hey Siri, call X on PulseSoc" would drive the locked call
path from a new entry point. Correct. But the framework has a more direct hazard that the
audit did not name.

`:1204`:

```swift
@available(macOS 14.0, iOS 17.0, watchOS 10.0, tvOS 17.0, *)
public protocol AudioPlaybackIntent : AppIntents.SystemIntent {}
```

(plus its deprecated 16.0 predecessor `AudioStartingIntent` at `:1211`.)

Conforming an intent to `AudioPlaybackIntent` is how you tell the system "running this
begins audio playback" — which is what grants it the right to start audio from the
background or the lock screen. It is, precisely, a new audio-session entry point outside the
app's own ownership arbitration.

The obvious PulseSoc intent that would reach for it is "play PulseSoc radio". Under
`docs/realtime_audio_change_policy.md` that is exactly the forbidden shape: a screen-level
(here, intent-level) audio setup path that can take the session from a live call.

**So the rule to write down is: no type in this codebase conforms to `AudioPlaybackIntent`
or `AudioStartingIntent`.** That is a one-line grep a test can assert, which is worth
considerably more than a paragraph of guidance — and unlike "don't build a call intent", it
covers the case nobody was thinking about.

---

## Finding 3 — the default authentication policy lets an intent run on a locked phone

`:614-620`:

```swift
public enum IntentAuthenticationPolicy : Swift.Sendable {
  case alwaysAllowed
  case requiresAuthentication
  case requiresLocalDeviceAuthentication
}
```

`AppIntent` declares `static var authenticationPolicy` (`:563`) with a default implementation
at `:586-588`, and the default is `alwaysAllowed`.

For a social app this default is wrong for almost everything. An intent that surfaces
anything user-scoped — unread messages, saved items, notifications, a profile — running from
a locked device is a lock-screen data leak, and it happens by *omission*: the developer who
does not think about `authenticationPolicy` has chosen `alwaysAllowed`.

**Every PulseSoc intent must state its `authenticationPolicy` explicitly**, and anything
touching user-scoped data must be at least `requiresAuthentication`. This is the same
default-is-permissive shape as the route-auth gate already in the protection suite
(`test_new_routes_must_declare_their_auth` is default-deny for exactly this reason), and the
same remedy applies: make the declaration mandatory rather than trusting each author.

A related default, `:565` / `:599-601`: `isDiscoverable` (iOS 17+) defaults to **true**, so
every intent is offered in Shortcuts and Spotlight unless told otherwise. That is usually
what you want; it is not what you want for an intent that only exists as a building block.

---

## Finding 4 — `ForegroundContinuableIntent` is unavailable in extensions, which prices the audit's extension preference

The audit noted that "Shortcuts discoverability is much better from an extension." The SDK
attaches a cost to that choice.

`:1300` declares `ForegroundContinuableIntent`, the protocol that lets a background intent
ask to continue in the foreground mid-run (`requestToContinueInForeground`,
`needsToContinueInForegroundError`, `:1310-1313`). It is the natural middle ground for shape
(b) in Finding 1: try the work headless, escalate to the UI when you need a human.

Its availability annotations (`:1295-1299` and `:1305-1309`) mark it
`@available(iOSApplicationExtension, unavailable)`.

So an intent that lives in an extension target **cannot** continue into the foreground. The
choice is:

| Intent lives in | Discoverability | Can foreground mid-run |
|---|---|---|
| the app target | lower | yes |
| an extension target | better | **no** |

Note also that `ForegroundContinuableIntent` is itself deprecated at iOS 26.0 in favour of
`supportedModes: .foreground(.dynamic)` (`:1301-1305`), and `openAppWhenRun` carries the same
26.0 deprecation in favour of `supportedModes` (`:555-559`). New code written today against
`openAppWhenRun` is writing against a deprecated API on the newest SDK — which is fine and
supported, but it should be a decision rather than a surprise at the next Xcode bump.

---

## Finding 5 — the shipped routes make shape (a) nearly free, and there are two ways to wire it

`src/navigation/linking.ts:65` declares `prefixes: ["pulsesoc://", "https://pulsesoc.com"]`,
and the screen config already claims the three destinations the audit named as safe first
intents:

```
Search:  "pulse/search"   (:98)
Saved:   "pulse/saved"    (:99)
Profile: "pulse/profile"  (:107)
```

with a settings resolver on top (`settingsDeepLink`, `:24-32`) that turns every entry in the
settings registry into `pulsesoc://settings/<id>` without a per-screen declaration. These are
live, tested paths. An `openAppWhenRun = true` intent that lands on one of them is a small
amount of Swift over a large amount of already-working TypeScript.

The wiring question is how `perform()` hands the destination across. Two candidate routes,
and they are not equally attractive:

**Via `application(_:open:url:)`.** `AppDelegate.swift:53-59` is implemented and already
forwards to `RCTLinkingManager`:

```swift
return super.application(app, open: url, options: options) || RCTLinkingManager.application(app, open: url, options: options)
```

This is the path every existing `pulsesoc://` link takes, so it is the one with production
evidence behind it.

**Via `NSUserActivity`.** The tidier-looking route, and the one to avoid for now:
`application(_:continue:restorationHandler:)` is the broken entry point documented in
`HANDOFF.md` Finding 1 and `CORE_SPOTLIGHT.md` Finding 2 — it returns `true` for activities
nobody consumed. An intent routed this way would foreground the app and navigate nowhere,
and the failure would look like a broken intent rather than a broken delegate.

Neither has been tested here. Specifically, whether an `openAppWhenRun` intent can
successfully call `UIApplication.open` on its own scheme, and what the ordering guarantee is
between the foregrounding and the `perform()` body, are **unverified** — they are the first
things to check on device, and the answer determines which of the two routes above is real.

---

## Finding 6 — App Intents and Core Spotlight interlock in the framework itself

Worth recording because it affects sequencing. `IntentParameter`'s initialisers take Core
Spotlight indexing keys directly (`:1195-1201`):

```swift
convenience public init<Entity>(identifier:title:indexingKey: PartialKeyPath<CSSearchableItemAttributeSet>, ...)
convenience public init<Entity>(identifier:title:customIndexingKey: CSCustomAttributeKey, ...)
```

and `AppIntent` exposes `donate()` (`:604-609`) which feeds Siri suggestions from actual use.

The audit already observed that #3 and #4 "use the same object" and that doing them together
roughly halves the cost. The framework agrees more strongly than that note implies: an
`AppEntity` indexed through Core Spotlight is the same entity an intent takes as a parameter.
If both are going to be built, the entity model should be designed once, and
`DECISIONS_CORE_SPOTLIGHT_INDEXING_POLICY.md`'s allowlist is then also the intent-parameter
allowlist — which is a useful property, because it means the privacy analysis does not have
to be done twice.

---

## Floor

Resolved. App Intents needs iOS 16.0; the project floor is **16.1** since `c8f05637`. The
audit's note that "SiriKit's older `INIntent` works at 15.1 but is the legacy path" is now
moot — there is no reason to consider `Intents.framework` at all.

Two sub-features sit above the floor: `AudioPlaybackIntent` is 17.0 (irrelevant, it is
forbidden by Finding 2) and `isDiscoverable` is 17.0 (relevant — on 16.1 devices every intent
is discoverable and the property cannot be used to hide building blocks).

---

## Owed

| # | Item | Why |
|---|---|---|
| 1 | Decide and record: all intents are shape (a), `openAppWhenRun = true` | Finding 1; shape (b) needs the keychain access group first |
| 2 | A test asserting nothing conforms to `AudioPlaybackIntent` / `AudioStartingIntent` | Finding 2; cheap, and covers the case nobody anticipates |
| 3 | Make `authenticationPolicy` a mandatory explicit declaration per intent | Finding 3; the default is the wrong one |
| 4 | App target vs extension target decision, with Finding 4's table attached | affects everything downstream |
| 5 | Device check: can an `openAppWhenRun` intent open its own `pulsesoc://` URL, and in what order | Finding 5; decides the wiring |
| 6 | If #4 (Core Spotlight) is also built: design the `AppEntity` model once | Finding 6 |

---

## What was verified, and what was not

**Verified by reading, in this repo:** no `AppIntent`, `INIntent`, SiriKit,
`.intentdefinition` or Siri entitlement anywhere; `linking.ts:65` prefixes and the
`Search`/`Saved`/`Profile` path claims at `:98`, `:99`, `:107`; `settingsDeepLink` at
`:24-32`; `AppDelegate.swift:53-59` forwarding `open url:` to `RCTLinkingManager`;
`IPHONEOS_DEPLOYMENT_TARGET = 16.1`.

**Verified against the AppIntents Swift interface:** the `AppIntent` protocol requirements
and every default implementation cited, `IntentAuthenticationPolicy`'s three cases,
`AudioPlaybackIntent` / `AudioStartingIntent`, `ForegroundContinuableIntent` and its
extension-unavailability, the 26.0 `supportedModes` deprecations, `donate()`, and the
`CSSearchableItemAttributeSet` indexing-key initialisers — all at the line numbers given.

**Not verified.** Everything runtime. In particular the claim that `perform()` has no access
to the RN bridge is reasoning from how the framework and Hermes each work, not something
demonstrated by running an intent in this app; it is the single assumption the whole document
rests on and it should be the first thing an implementer proves or disproves. Finding 5's two
wiring routes are both untested. No intent has ever been donated, performed, or shown in
Shortcuts from this codebase.
