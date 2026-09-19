# How Apple-native code is organised here

Written 2026-09-19. The brief asks for "a unified native Apple integration layer
instead of random one-off bridges," and sketches a directory tree
(`ios/PulseSocNative/Security/`, `SystemIntelligence/`, `Activities/`, …) — while
also saying: *"Use the project's real existing structure where possible. Do not
reorganize the entire iOS project merely to match this example."*

Those two instructions point in opposite directions here, because the repo has
already answered this question three times, consistently, and the answer is not
a folder tree. This document records what that answer is, why it is better than
the sketch for this codebase, and where each remaining capability lands inside
it.

---

## The structure that already exists

Three local Expo modules, each a directory under `mobile-native/modules/`,
each declared in `package.json` as a `file:` dependency, each autolinked into
the Pods project:

| Module | Apple surface | Swift files | Notes |
|---|---|---|---|
| `pulse-now-playing` | `MPNowPlayingInfoCenter`, `MPRemoteCommandCenter` | 1 | Lock-screen transport controls |
| `pulse-apple-translation` | `Translation.framework` (iOS 18+) | 8 + a 6-file Swift test harness | The richest one; weak-linked and `@available`-gated |
| `pulse-video-mixer` | AVFoundation composition | 1 | |

All three share the same skeleton, and it is worth naming because it is the
template for everything below:

```
modules/<name>/
  package.json              private, version 1.0.0, "main": "index.js"
  expo-module.config.json   { "platforms": ["apple"], "apple": { "modules": [...] } }
  index.js                  hand-written, no build step
  index.d.ts                hand-written, IS the contract
  ios/<Name>.podspec        deployment floor, framework linkage, swift_version
  ios/<Name>Module.swift    the ExpoModulesCore module class
```

`package.json:66-68` wires them; `ios/Podfile.lock:2782-2784` shows CocoaPods
resolving each from `../modules/<name>/ios`. There is no build step anywhere:
`index.js` and `index.d.ts` are checked in as source, which means the TypeScript
surface of a native capability is a file a reviewer can read in full.

**Decision: new Apple capabilities are new local Expo modules, following this
skeleton. Not a new folder tree inside the app target.**

---

## Why a module beats a folder in the app target

The sketch's `ios/PulseSocNative/Security/…` layout would put Swift into the
`PulseSoc` app target. That has four concrete costs here, none of them cosmetic.

**Reaching it from JS requires a bridging header.** The app target's only Swift↔JS
path is `PulseSoc-Bridging-Header.h`, which today imports exactly two things —
`RNCallKeep.h` and `RNVoipPushNotificationManager.h` — and exists solely because
those two pods are Objective-C only and `AppDelegate` has to call them from a
PushKit callback. Growing that header into a general-purpose bridge would put
every new capability on the same path as the call stack's most timing-sensitive
code.

**Linkage has nowhere to live.** A podspec is where you say *"weak-link this
framework."* An app-target folder has no equivalent, so the linkage decision
moves into the pbxproj — hand-maintained state that `expo prebuild` regenerates.
That is the exact failure the extension decision already had to write a
protection test against.

**It cannot be unit-tested.** `pulse-apple-translation` has six Swift test files
and its own harness because it is a self-contained unit with a declared
interface. Code in the app target is reachable only by building and launching
the app.

**Deletion stops being cheap.** A module is a directory and one `package.json`
line. Code in the app target is entangled with the target's build phases, and
the decision to remove a capability starts costing more than the decision to add
one — which is the wrong asymmetry for a plan that ships fourteen of them behind
flags.

The one thing the sketch is right about is the *grouping*. Module names carry it
instead of directories: `pulse-now-playing`, `pulse-apple-translation`,
`pulse-video-mixer` already read as a namespace.

---

## What the app target is allowed to contain

`AppDelegate.swift` is 213 lines and holds exactly the things iOS calls **before
React Native exists**, plus the two link entry points UIKit owns:

- `didFinishLaunchingWithOptions` — RN factory setup, and the PushKit registry
- `application(_:open:options:)` — custom-scheme links
- `application(_:continue:restorationHandler:)` — Universal Links / `NSUserActivity`
- the `PKPushRegistryDelegate` conformance and its CallKit reporting

That list is the rule, not a description. If iOS will call it before the JS
bundle is loaded, it belongs here; otherwise it belongs in a module. The PushKit
registry is the canonical case — the file explains it at length, because the
whole feature exists for a *terminated* app and a JS-side registration provably
cannot run in time.

The third bullet matters for the Apple plan specifically: **Handoff and Spotlight
result-opening both arrive through `continue userActivity`**, which already
exists and already forwards to `RCTLinkingManager`. Neither capability needs a
new delegate method; they need a new activity type and a JS-side handler.

### The gap this document found, and what was done about it

None of that was guarded. The realtime-audio manifest
(`config/realtime-audio-protected-paths.json`) lists 73 distinct paths — 65
TypeScript/TSX, 6 Python, and three pieces of its own scaffolding (the manifest
itself, a workflow, `CODEOWNERS`). **Not one is native source**: no `.swift`,
no `.m`, no `.podspec`, no `.pbxproj`, no `.entitlements`. The three existing
iOS protection tests stop just short: build version, APNs entitlement wiring,
target inventory and the 16.1 floor.

So the file implementing a hard-locked foundation could be regenerated by
`expo prebuild --clean` into a stock template, and the app would still build,
launch and render. It would simply never ring again, and iOS's enforcement of
the report-or-die rule means that once it starts failing it becomes permanent.

`tests/protection/test_ios_pushkit_callkit_bridge.py` now pins ten properties of
that file, mutation-verified 12/12 (ten mutations each turning its *named* test
red, two controls staying green). Two of those twelve failed on the first run
and both were bugs in the assertions rather than the harness — a completion
check that measured proximity instead of containment, and a forbidden-symbol
scan that only read as far as the *first* CallKit report, which is in the cancel
branch. Both are fixed and both mistakes are recorded in the file.

**Still recommended, not done:** adding `AppDelegate.swift` to the realtime-audio
manifest, so that *any* edit owes a human declaration rather than only the ten
enumerated regressions. It is complementary rather than redundant. It was not
done here because the manifest is itself a protected path, so the edit owes a
declaration and the full validation battery — that should be a deliberate change,
not a by-product of writing an architecture document.

---

## The availability problem, already solved three ways

Most of the remaining capabilities are OS-gated above the 16.1 floor: ActivityKit
is 16.1, Control Center controls are 18.0, `Translation` is 18.0. The translation
module worked this out and the pattern should simply be copied. It has three
layers, and all three are needed:

**1. The podspec weak-links the framework and keeps the low floor.** From
`modules/pulse-apple-translation/ios/PulseAppleTranslation.podspec`:

> Translation.framework does not exist before iOS 18. It MUST be weak-linked: a
> hard link makes dyld refuse to launch the app on iOS 15-17, which would turn a
> gracefully-degrading feature into a launch crash for those users.

and, on the floor:

> Deliberately the project's existing floor, NOT 18.0. Raising it here would drop
> every iOS 15-17 user off the app entirely just to reach a feature that is
> supposed to degrade to cloud fallback.

**2. Swift gates every use with `@available` / `if #available`.**

**3. JS degrades to a no-op.** `pulse-now-playing/index.js` is the shape:
`requireOptionalNativeModule(...)`, an exported `isNowPlayingSupported` boolean,
and every function returning early when the module is absent. That third layer is
what makes the same code run on Android and under jest without a mock.

A note for whoever touches the podspecs next: all three still declare
`:ios => '15.1'` while the app floor moved to 16.1 on 2026-09-19. That is **not**
a bug — a pod floor below the app floor is legal and inert — and "tidying" them
to 16.1 would silently delete the comment explaining why a low floor is
deliberate. Leave them.

---

## Extensions are a different mechanism, on purpose

WidgetKit, Live Activities, a Share Extension and Control Center controls cannot
be Expo modules, because an extension is a separate binary with its own bundle
id, entitlements and process — not a pod linked into the app.

Those are committed `PBXNativeTarget`s, decided in
`DECISIONS_DEPLOYMENT_TARGET_AND_EXTENSIONS.md` and guarded by
`tests/protection/test_ios_native_target_inventory.py`, which pins the target
count, each target's name *and product type*, and all four declarations of the
16.1 floor. Adding an extension means editing `EXPECTED_TARGETS` in the same
commit. That is the intent: deliberate and reviewable rather than accidental and
silent.

So the repo has exactly two native mechanisms, and which one to use is decided by
the OS, not by taste:

| | Expo module | Committed Xcode target |
|---|---|---|
| Runs in | the app process | its own process |
| Reached from JS | directly, typed | never — via App Group / shared storage |
| Linkage declared in | its podspec | the pbxproj |
| Guarded by | its own Swift tests | `test_ios_native_target_inventory.py` |
| Use for | frameworks the app calls | widgets, activities, share, controls |

---

## Where each capability lands

| # | Capability | Mechanism | Notes |
|---|---|---|---|
| 1 | App Attest / DeviceCheck | new Expo module | Backend verification is the real work; the module is thin |
| 2 | Live Activities | extension target + module | Module starts/updates; extension renders. Call state **read-only** |
| 3 | App Intents | **app target** (corrected 2026-09-19) | `APP_INTENTS_SIRI_SHORTCUTS.md`. All intents `openAppWhenRun = true` — `perform()` has no RN bridge. Explicit `authenticationPolicy` per intent. **No conformance to `AudioPlaybackIntent`/`AudioStartingIntent`**; no call intents. An extension target would forfeit `ForegroundContinuableIntent` (Finding 4) |
| 4 | Core Spotlight | new Expo module | Policy: `DECISIONS_CORE_SPOTLIGHT_INDEXING_POLICY.md`. Mechanism: `CORE_SPOTLIGHT.md`. Needs **no** Info.plist key — the blocker is the AppDelegate activity-type switch |
| 5 | BackgroundTasks | app target | Registration must happen in `didFinishLaunching`. Not currently planned |
| 6 | Sign in with Apple | `expo-apple-authentication` — **premise unverified** | Server/schema in `DECISIONS_SIGN_IN_WITH_APPLE.md`; client/framework in `SIGN_IN_WITH_APPLE.md`. "Off-the-shelf" rests on the package exposing `getCredentialState`, the revoked notification, `nonce`, `authorizedScopes` and `realUserStatus` — five open questions, not five answers. Check them before estimating |
| 7 | Universal Links | **already in the app target** | `continue userActivity` exists and forwards to `RCTLinkingManager` |
| 8 | StoreKit 2 | already implemented | `Configuration.storekit` is committed |
| 9 | MapKit | — | Not recommended; blocked on data that does not exist |
| 10 | Handoff | app target + Spotlight module | Same `continue userActivity` entry point as #7 |
| 11 | Keychain / Secure Enclave | `expo-secure-store` + new module | Access group is a pbxproj + entitlement change; see `DECISIONS_KEYCHAIN_ACCESS_GROUP.md` |
| 12 | WidgetKit | extension target | `WIDGETKIT.md`. First extension; ships with the App Group foundation. **Needs the App Group but not the keychain access group** — the app writes a snapshot, the widget never fetches. Static configuration only (the modern configurable path is 17.0). Content governed by the Core Spotlight allowlist |
| 13 | Share Extension | extension target | Memory-limited; must not publish silently |
| 14 | Action Button / Control Center | extension target | Blocked on #3 and an 18.0 floor |

Four rows — #2, #12, #13, and #14 — need the App Group and a second App ID. That is the
Wave 2 "extension foundation," and the reason the audit insists it be built once on purpose
rather than four times accidentally.

> **Corrected 2026-09-19.** This paragraph previously counted #3 among them and said "four"
> while listing five. #3 moved to the app target (see its row), so it needs neither the App
> Group nor a second App ID — which makes App Intents the one capability in this group that
> is *not* gated on Wave 2. #14 stays in the list: an Action Button assignment is an App
> Intent, but a Control Center control is a `ControlWidget`, which is an extension.

---

## The boundary rule, concretely

The brief states it as *"React Native: 'show order'. Swift: system integration.
Backend: authoritative order state."* Two things make that enforceable here
rather than aspirational:

**`index.d.ts` is hand-written and checked in.** There is no generated surface to
hide behind. `pulse-now-playing/index.d.ts` is five functions, one boolean and
two type aliases — if a module's `.d.ts` starts describing domain objects rather
than system operations, business logic has moved into Swift and the diff says so.

**No module may touch the audio session.** Verified: `AVAudioSession` appears in
none of the three modules. `pulse-now-playing` handles *lock-screen metadata and
remote commands* and deliberately not the session itself, which is what makes it
compatible with the ownership arbitration it sits beside. Any new module has the
same obligation — the realtime-audio policy is a repo-wide rule, not a
`src/`-only one.

---

## What was verified, and what was not

**Verified by reading:** the three modules' existence, file counts and configs;
their `package.json:66-68` declarations and `Podfile.lock:2782-2784,2961-2966`
resolution; both podspec comments quoted above; `index.js`'s
`requireOptionalNativeModule` degradation; `AppDelegate.swift` in full; the
bridging header's two imports; `UIBackgroundModes = [audio, voip,
remote-notification]`; the entitlements file's two keys; that
`config/realtime-audio-protected-paths.json` contains no native path; that no
test read `AppDelegate.swift` *before this work* (the new bridge test is the
first); that no module references `AVAudioSession`; and that the pbxproj
declares one native target, with the 16.1 floor stated four times.

**Not verified.** No Apple capability named in the table above has been
implemented, so the claim that this structure suits all of them is a projection
from three working examples, not a result. Specifically untested: that an Expo
module and a committed extension target coexist cleanly through a
non-clean `expo prebuild` — the target inventory test would catch the loss, but
the interaction itself has not been exercised because no extension exists yet.
