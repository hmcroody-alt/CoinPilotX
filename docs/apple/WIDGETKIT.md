# WidgetKit

Capability #12. Status: **NOT IMPLEMENTED.** No `WidgetKit` import, no `TimelineProvider`,
no widget target, no App Group. The Xcode project has exactly **one** `PBXNativeTarget` —
`PulseSoc`, `com.apple.product-type.application` (`PulseSoc.xcodeproj/project.pbxproj:139-158`)
— so this would be the project's first extension.

The audit rates the value honestly and picks the right first widget. This document does not
argue with either. It argues with one structural claim underneath them, and it finds that
the privacy question WidgetKit raises has already been answered twice in this mission under
different names.

Evidence: `WidgetKit.framework/Modules/WidgetKit.swiftmodule/arm64e-apple-ios.swiftinterface`
in the iOS 26.5 SDK (1,731 lines), plus this repo. Nothing has been run.

---

## Finding 1 — `TimelineProvider` has no way to say "I failed"

`:557-566`:

```swift
public protocol TimelineProvider {
  associatedtype Entry : WidgetKit.TimelineEntry
  func placeholder(in context: Self.Context) -> Self.Entry
  func getSnapshot(in context: Self.Context, completion: @escaping (Self.Entry) -> Void)
  func getTimeline(in context: Self.Context, completion: @escaping (Timeline<Self.Entry>) -> Void)
}
```

Note what is absent. There is no `Result`, no `throws`, no error-carrying overload. The iOS
17 successor is the same (`AppIntentTimelineProvider`, `:1492-1503`): `snapshot(for:in:)` and
`timeline(for:in:)` are `async` but **not** `async throws`. Both return an entry or a
timeline, unconditionally.

So a widget cannot decline to render. Whatever went wrong — no network, no session, a 500,
an empty App Group container — the provider must still hand back an `Entry`, and whatever
that `Entry` contains is what appears on the user's home screen and stays there.

**This is the repo's own "error and empty must never co-render" rule, enforced by a type
system that offers you only one way out.** A `Progress` widget whose fetch failed and which
returns `Entry(progress: 0)` is indistinguishable from a user who has made no progress. A
messages widget that returns `Entry(unread: 0)` on a failure tells the user, on their home
screen, that nobody has written to them.

**The requirement: the `Entry` type must carry an explicit state, not just a value.**
Something with the shape `case ready(Summary) / case signedOut / case unavailable`, rendered
as three visibly different things. A default-initialised entry is the failure mode here, and
the framework will happily let you ship one.

There is direct precedent to copy rather than re-derive:
`modules/pulse-apple-translation/ios/AppleTranslationError.swift` is a typed failure enum
whose raw values are the wire contract with TypeScript, with derived properties encoding
what each failure permits. The same discipline applied to `Entry` is the whole of this
finding.

---

## Finding 2 — the widget should make no network calls at all, which removes the audit's stated blocker

The audit's "New target" line reads:

> **yes**, plus App Group, plus the keychain-access-group problem in §"Two structural facts"
> — a widget showing personalised content must read the session.

**It does not have to.** And on this codebase it should not.

A widget extension is a separate process. It has no Hermes runtime, no React Native bridge,
and no `pulseApi()` — the same fact that bounds what an App Intent can be
(`APP_INTENTS_SIRI_SHORTCUTS.md` Finding 1), except that a widget has no `openAppWhenRun`
escape hatch. A widget that fetches is therefore a second, parallel, Swift-native HTTP client
with its own auth, its own token refresh, and its own copy of a backend contract — which is
exactly the "shape (b)" the App Intents document recommended against, and here there is no
shape (a) to fall back on.

**The alternative: the app writes, the widget only reads.** The app — where the session, the
token refresh and the error taxonomy already live and already work — writes a small rendered
summary into the App Group container whenever the relevant value changes, then calls
`WidgetCenter.shared.reloadTimelines(ofKind:)` (`:588`). The widget's `getTimeline` reads that
file and returns. No network, no token, no keychain.

Three consequences, and the third is the reason this is not free:

1. **The keychain access group leaves the critical path.** `DECISIONS_KEYCHAIN_ACCESS_GROUP.md`
   work is still owed for other reasons, but WidgetKit stops waiting on it. The App Group is
   still required — it is the shared container — but an App Group is a much smaller thing
   than sharing a session token across a process boundary.
2. **`TimelineReloadPolicy.never` (`:1655`) becomes the natural policy**, with the app driving
   refreshes. The other two are `.atEnd` and `.after(Date)` (`:1654-1656`); neither helps a
   widget whose data can only change when the app runs.
3. **The widget goes stale when the app is not opened.** This is the real cost and it must be
   stated rather than discovered. A snapshot written on Tuesday is what the home screen shows
   on Friday.

That third point is a structural argument for exactly the choice the audit already made on
taste. Its two candidates:

| Candidate | Under an app-writes-only model |
|---|---|
| Progress / referral status | **Fine.** A slowly-changing number that only moves when the user acts in the app — so the snapshot is stale precisely when nothing has happened. |
| Unread count | **Broken.** The number changes because of what *other people* do, which is when the app is not running. A perpetually-wrong unread badge on the home screen is worse than no widget. |

The audit called Progress "the more compelling of the two." It is also the only one of the
two that works without building a parallel native client. And Finding 3 rules the other one
out on privacy grounds independently, so the two arguments agree.

---

## Finding 3 — a lock-screen widget is the Core Spotlight problem wearing a different hat

`WidgetFamily` (`:924-968`) has four accessory families. Three are available on iOS:

```swift
@available(iOS 16.0, watchOS 9.0, *) case accessoryCircular
@available(iOS 16.0, watchOS 9.0, *) case accessoryRectangular
@available(iOS 16.0, watchOS 9.0, *) case accessoryInline
```

At the 16.1 floor these are all available. They render **on the lock screen** — visible to
anyone holding the handset, with no passcode, no Face ID, and no session check.

That is the same property, exactly, that this mission has now written down twice:

| Where | The property |
|---|---|
| `DECISIONS_CORE_SPOTLIGHT_INDEXING_POLICY.md` | "a copy of content that outlives the permission it was derived from," readable from the home screen |
| `APP_INTENTS_SIRI_SHORTCUTS.md` Finding 3 | `authenticationPolicy` defaults to `alwaysAllowed`, so an intent surfacing user-scoped data runs on a locked phone |
| **here** | an accessory widget renders user-scoped content on a locked device, continuously, by design |

Three capabilities, one policy question. **The allowlist in
`DECISIONS_CORE_SPOTLIGHT_INDEXING_POLICY.md` is the right allowlist for widget content too**,
and reusing it is worth more than the reuse saves, because it means the privacy argument is
made once and cannot drift between three implementations.

Applying it as written:

- The signed-in user's **own** public profile — allowed.
- The **saved library**, title and thumbnail only — allowed.
- **Education/help articles** — allowed, and the only unconditional row.
- Direct messages and conversations — **denied.**

An unread-message count is a derived signal about the denied row. The policy's reasoning for
denying DMs ("authored by someone who consented to *one* reader inside *one* app") applies to
a count on a lock screen at least as strongly as to the message text in a search index. So
the unread-count widget is denied by a policy that already exists, without needing a new
argument — which is the point of having written the policy first.

**One mitigation worth knowing about, and its limit.** `WidgetRenderingMode` (`:1034-1046`)
has three cases — `fullColor`, `accented`, `vibrant` — and accessory widgets on the lock
screen render in the desaturated modes, not `fullColor`. That is a *rendering* constraint, not
a privacy one: it changes how the content looks, not who can read it. It does mean a
lock-screen widget **cannot honour a brand palette** — worth recording against the standing
black/white/green requirement for business surfaces, which an accessory widget structurally
cannot satisfy.

---

## Finding 4 — deleting the snapshot does not clear the widget

This is the bug this design will have if nobody writes it down.

`clearUserScopedMediaState()` (`mobile-native/src/media/mediaSessionCleanup.ts`) is the
established sign-out purge, and as of `53ec3679` it runs on all six paths that end a session.
Its own header documents the discipline precisely — the AsyncStorage sweep "is not enough now
that media is cached on disk," `accountScopedKeys` inverts the rule so deletion is the
default, and `resetJsonCacheMemory()` is there because a surviving in-memory Map leaves a
deleted key "perfectly readable by the next account."

A widget snapshot in the App Group container is a **third storage tier** that neither half of
that sweep can see. `accountScopedKeys` enumerates AsyncStorage; `clearAllMediaCaches` removes
files under the media cache scope. A file in `group.com.pulsesoc.*` is outside both. This is
the identical failure `storageScope.ts` was inverted to prevent, arriving in a location the
inversion does not cover.

**And deleting the file is still not sufficient.** A widget is not re-rendered because its
input file vanished. The system keeps showing the last timeline it was given until something
reloads it. So sign-out needs *two* operations, in order:

1. delete the App Group snapshot, and
2. call `WidgetCenter.shared.reloadAllTimelines()` (`:589`)

Skip (2) and the departing user's data stays legible on the home screen — and on the lock
screen — for as long as the previous timeline's entries remain current, which under
`.never` (Finding 2) is **indefinitely**. Skip (1) and the reload re-renders it from the file.

Both belong inside `clearUserScopedMediaState()`, not beside it, for the reason
`DECISIONS_CORE_SPOTLIGHT_INDEXING_POLICY.md` already gives for putting the Spotlight purge
there: a purge wired up separately inherits none of the six paths, and would have had exactly
the bug `53ec3679` fixed, silently.

---

## Finding 5 — a *configurable* widget is above the floor, and it drags in App Intents

Two configuration mechanisms exist, and the modern one is iOS 17:

```swift
// :1229 — legacy, built on Intents.framework
public struct IntentConfiguration<Intent, Content> ... where Intent : Intents.INIntent

// :64 / :1490-1492 — modern
public struct AppIntentConfiguration<Intent, Content> ... where Intent : AppIntents.WidgetConfigurationIntent
@available(iOS 17.0, ...) public protocol AppIntentTimelineProvider { ... }
```

So:

| Widget kind | Floor | Needs |
|---|---|---|
| Static (`StaticConfiguration`) | 14.0 | nothing extra |
| User-configurable, modern | **17.0** | a `WidgetConfigurationIntent` — i.e. capability #3 |
| User-configurable, legacy | 14.0 | `Intents.framework`, which `APP_INTENTS_SIRI_SHORTCUTS.md` declared moot |

At a 16.1 floor the modern configurable widget is **not available to every user**, and the
legacy route is the one this mission has already ruled out. **The first widget must be
static.** That is not a limitation worth fighting: a Progress widget has nothing to configure.

Worth noting for the sequencing note in the audit: #12's dependency on #3 is real but narrow.
It applies only to configurability, only above 17.0, and only through
`WidgetConfigurationIntent` — a different protocol from the `AppIntent` that
`APP_INTENTS_SIRI_SHORTCUTS.md` analysed, and one that does not run `perform()` on a schedule.
None of that document's Finding 1 reasoning transfers.

---

## Finding 6 — the "stale widget" problem has an Apple answer this project cannot use

`:812-822`:

```swift
@available(iOS 26.0, macOS 26.0, visionOS 26.0, watchOS 26.0, *)
public struct WidgetPushInfo : Swift.Sendable { public let token: Foundation.Data }

@available(iOS 26.0, ...)
public protocol WidgetPushHandler {
  init()
  func pushTokenDidChange(_ pushInfo: WidgetPushInfo, widgets: [WidgetInfo])
}
```

Push-driven widget reloads — the server telling a widget to refresh without the app running —
exist, are attached to a widget via `.pushHandler(_:)` (`:1631`), and are **iOS 26.0**. Nearly
ten major versions above this project's floor.

Recording it for one reason: it is the exact fix for Finding 2's stated cost, and a future
reader who finds `WidgetPushHandler` in the SDK should not spend time on it. It also
retroactively justifies the app-writes-only design — Apple's own answer to "the widget goes
stale" is a push channel, not a fetching widget.

(`ControlPushHandler` at `:551-554` is the same mechanism for Control Center controls, which
capability #14 covers and which is 18.0 regardless.)

---

## Floor

| Piece | Floor | vs. 16.1 |
|---|---|---|
| WidgetKit, `StaticConfiguration`, home-screen families | 14.0 | satisfied |
| `accessoryCircular` / `accessoryRectangular` / `accessoryInline` (lock screen) | **16.0** | satisfied |
| `systemExtraLarge` (iPad) | 15.0 | satisfied |
| `AppIntentConfiguration` / `AppIntentTimelineProvider` | **17.0** | **above** |
| `WidgetCenter.invalidateRelevance` / `currentConfigurations()` async | 18.0 | above |
| `WidgetPushHandler` | **26.0** | far above |

The audit's line — "satisfied at 15.1 for home-screen widgets (lock-screen widgets need
16.0)" — was written against the old floor and its parenthetical is now moot: **lock-screen
widgets are available.** Which makes Finding 3 a live decision rather than a deferred one.

---

## Owed

| # | Item | Why |
|---|---|---|
| 1 | The `Entry` type carries an explicit state, and a test asserts the failure state renders differently from zero | Finding 1; the protocol gives you no other way to fail |
| 2 | Record the decision: the widget makes no network calls; the app writes the snapshot | Finding 2; removes the keychain access group from this capability's critical path |
| 3 | Extend `DECISIONS_CORE_SPOTLIGHT_INDEXING_POLICY.md` to say its allowlist governs widget content too | Finding 3; one privacy argument, three consumers |
| 4 | Snapshot delete **and** `reloadAllTimelines()`, both inside `clearUserScopedMediaState()` | Finding 4; either one alone leaves the data on screen |
| 5 | First widget is static, not configurable | Finding 5; the modern configurable path is 17.0 |
| 6 | The `PBXNativeTarget` pinning test, before the first extension lands | one target today; the test is worth more before there are two |

---

## What was verified, and what was not

**Verified against the WidgetKit Swift interface**, at the line numbers given: `TimelineProvider`
and `AppIntentTimelineProvider` and the absence of any error channel in either; the nine
`WidgetFamily` cases with their per-platform availability; `WidgetCenter.reloadTimelines(ofKind:)`
and `reloadAllTimelines()`; the three `TimelineReloadPolicy` values; `WidgetRenderingMode`'s
three cases; `IntentConfiguration`'s `INIntent` constraint against `AppIntentConfiguration`'s
`WidgetConfigurationIntent`; `WidgetPushInfo` / `WidgetPushHandler` at 26.0 and
`ControlPushHandler` beside them.

**Verified by reading this repo:** no WidgetKit or `TimelineProvider` symbol anywhere; exactly
one `PBXNativeTarget` (`PulseSoc`, application type); `PulseSoc.entitlements` contains only
`aps-environment` and `associated-domains` — **no App Group**; `clearUserScopedMediaState`'s
two-tier sweep and its documented reasoning; `AppleTranslationError.swift`'s typed-failure
shape.

**Not verified.** Everything runtime. In particular: the timeline refresh budget, which the
audit correctly calls "stingier than most implementations assume" and which appears nowhere
in the interface — it is enforced by the system, not the type system, and the only way to
learn it is to run a widget. Also unverified is whether writing to an App Group container from
a React Native app requires a native module at all, or whether an existing Expo package
covers it; that should be checked before estimating, in the same way
`SIGN_IN_WITH_APPLE.md` flags the `expo-apple-authentication` surface. No widget has ever been
built, installed, or rendered from this codebase.
