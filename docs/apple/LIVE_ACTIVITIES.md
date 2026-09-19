# Live Activities + Dynamic Island

Capability #2. Status: **NOT IMPLEMENTED.** `grep -rn "ActivityKit\|ActivityAttributes\|Activity<\|NSSupportsLiveActivities"` across `mobile-native/`
returns nothing, and `PulseSoc.entitlements` contains only `aps-environment` and
`com.apple.developer.associated-domains` — no App Group.

This document's conclusion differs from the audit's. The audit identified calls and
livestreams as the qualifying activities. Both do qualify on user value, and the call one is
the one everybody will ask for. **It should not be the first one built**, and Finding 1 is
why.

Evidence: `ActivityKit.framework/Modules/ActivityKit.swiftmodule/arm64e-apple-ios.swiftinterface`
in the iOS 26.5 SDK (630 lines, read directly — this framework ships a Swift interface rather
than headers, so the declarations below are the compiler's own view of the API), plus this
repo. Nothing has been run.

---

## Finding 1 — `Activity.request` throws, and the throw would land mid-call

From the interface, `request` is `throws` on every overload (`:46`, `:52`, `:59`, `:66`,
`:73`). What it throws is `ActivityAuthorizationError` (`:558-570`), and the case list is
long:

```
attributesTooLarge, unsupported, denied, globalMaximumExceeded,
targetMaximumExceeded, unsupportedTarget, visibility, persistenceFailure,
missingProcessIdentifier, unentitled, malformedActivityIdentifier,
reconnectNotPermitted
```

Read that list as a list of things that happen on a normal user's phone during a normal
call. `denied` — the user turned Live Activities off for PulseSoc in Settings.
`globalMaximumExceeded` — the device already has too many activities from other apps.
`visibility` — the app wasn't in a state permitted to start one.

The audit's protection rule was "a Live Activity may **read** call state; it must never
start, end, mute, or route a call." That is necessary and not sufficient. The sharper rule
is:

**Live Activity failure must be unobservable to the call path.**

Not merely "doesn't drive the call" — *invisible to it*. No `try` whose error propagates into
a call function, no `await` that a call transition sits behind, no branch on the result. A
detached task that starts an activity and discards every outcome. Because the failure mode
that matters is not "the Live Activity is wrong", it is "the user's phone had eleven
activities open, `request` threw `globalMaximumExceeded`, and the call did not connect."

The same applies in reverse to `end`: `end` is `async` (`:177`, `:183`, `:189`) and a call
teardown must not wait on it.

This is a Wave 2 design constraint, and it is the reason the call activity should not be
first — not because it cannot be built safely, but because the first Live Activity in a
codebase is also the one that shakes out the extension target, the App Group, the
provisioning profile and the build config, and doing that shake-out against the call path is
the worst possible venue for it.

---

## Finding 2 — the call state source is a protected path, but it already publishes enough

The audit said a call Live Activity's state "must come from an existing published
observable, never from a new CallKit delegate." Checking which file that is turns the
guidance into a hard constraint:

`config/realtime-audio-protected-paths.json` category `audio_and_video_call_adapter`
contains `mobile-native/src/calls/callSessionStore.ts`.

So `callSessionStore.ts` is a protected path. *Reading* from it — importing the store,
subscribing to the snapshot — edits nothing and is fine. *Adding* a field to it, so the Live
Activity can show something the store does not already expose, is an edit to a protected
path, which `unrelated_mission_policy` forbids a non-audio mission from making. That is not
a soft preference; it is the gate that fails the build.

The good news is that the constraint is already satisfiable. `CallSessionSnapshot`
(`callSessionStore.ts:89-105`, composed with `baseMediaState` at `:62-84`) already publishes:

`title`, `callType`, `direction`, `sessionActive`, `connectedAtMs`, `everConnected`,
`connected`, `connecting`, `reconnecting`, `connectionQuality`, `audioEnabled`,
`participantCount`, `disconnectReason`.

That is a complete Dynamic Island. So the design rule is:

**the Live Activity's `ContentState` must be a pure projection of fields
`CallSessionSnapshot` exposes today.** If a field is wanted that does not exist, the answer
is not to add it — the answer is that the Live Activity does without it, or the work is
rescoped as an audio-locked mission with the full validation battery.

---

## Finding 3 — push-updated activities need a third APNs topic, and this repo already knows what that costs

Push-updated Live Activities use the topic `<bundle-id>.push-type.liveactivity`. That is a
third suffix alongside the plain alert topic and `.voip`.

`services/pulsesoc_voip_push.py:183-206` documents exactly what a wrong suffix costs, and the
reasoning transfers unchanged:

> The moment a second one does, every device on the other bundle is addressed with the wrong
> topic and APNs answers `DeviceTokenNotForTopic`. That answer is classified `invalid_device`
> and — unlike `BadDeviceToken` — is deliberately *not* replayed, so the token is revoked
> permanently rather than misrouted once. A wrong topic costs a handset its ability to ring
> for good.

The module also has the right pattern already built: `known_bundle_ids()` (`:164-180`) is an
explicit allowlist, and `topic_for_bundle` (`:203-206`) falls back rather than trusting a
client-supplied bundle, precisely so a bad registration cannot talk a device out of ever
ringing again.

The trap is that there are **two** senders in this codebase and only one of them has that
discipline. `services/pulsesoc_notification_system.py:2591` sends alert pushes with:

```python
headers={"authorization": f"bearer {auth_token}", "apns-topic": bundle_id, ...}
```

A bare `bundle_id`, no allowlist, no suffix machinery. Whichever sender grows Live Activity
support must adopt the VoIP module's model, not extend the notification system's.

---

## Finding 4 — Live Activity push tokens are per-activity, and the existing token lifecycle assumes the opposite

This is the finding most likely to be discovered as a production incident rather than in
review.

A Live Activity's push token comes from that activity's own `pushTokenUpdates` stream. It is
scoped to one activity instance and it stops being valid when the activity ends. That is
normal, expected, designed behaviour.

Every token-handling assumption in `pulsesoc_voip_push.py` runs the other way. Tokens there
are device-scoped and long-lived, and when APNs rejects one the module revokes it
(`:843-845`):

```python
if result.get("status") == "invalid_device":
    revoke_token(cur, user_id=int(user_id), token=token, reason="apns_unregistered")
```

with `invalid` computed at `:766` from `410 Unregistered` or `400 BadDeviceToken` /
`DeviceTokenNotForTopic`. Feed an ended activity's token through that path and the system
manufactures a revocation event for a device that is in perfect health, on a schedule set by
how often activities end — which is to say, constantly.

So: **Live Activity tokens do not go in the VoIP token store and do not go through that
classifier.** Separate storage, separate lifetime, and a rejection means "this activity is
over", never "this device is gone." Write that down before the first token is persisted,
because the two stores will look interchangeable to whoever adds the second one.

---

## Finding 5 — the audit's dismissal fear is real, and the SDK answers it

The audit said "a Live Activity that never ends is a persistent, un-dismissable annoyance on
the user's lock screen. Dismissal policy must be designed before any code." Correct. The
design has three pieces and all three exist in the API:

**`ActivityUIDismissalPolicy`** (`:453-457`) is `.default`, `.immediate`, or
`.after(Date)`. `.default` is the one that produces the audit's complaint — an ended activity
lingers on the lock screen. A call that has hung up should end `.immediate`.

**`ActivityContent.staleDate`** (`:408-410`, with `ActivityState.stale` added in 16.2 at
`:387-392`). This is the answer to the harder version of the problem: the app is not running,
the call ended, and nothing can update the activity. A `staleDate` lets iOS mark the content
as out of date on its own. Without one, an activity whose updater died shows a call as
ongoing indefinitely and the user cannot tell it is lying.

**`ActivityState`** (`:377-393`) is the observable: `pending` (26.0), `active`, `ended`,
`dismissed`, `stale`. Any cleanup logic must treat `ended` and `dismissed` as distinct —
`ended` is the app's doing, `dismissed` is the user's.

A concrete policy for a call activity: `.immediate` dismissal on hangup, and a `staleDate`
no more than a couple of minutes ahead, refreshed on every content update. The activity then
degrades to "stale" rather than to "lying" when the app dies.

---

## Finding 6 — there are two independent user switches, not one

`ActivityAuthorizationInfo` exposes both:

- `areActivitiesEnabled` (`:540`)
- `frequentPushesEnabled` (`:537`), with its own `frequentPushEnablementUpdates` stream
  (`:549`)

These are separate settings the user controls separately. An implementation that checks only
the first and then relies on frequent push updates will work on the developer's phone and
silently under-update on a user who left the second one off. Both must be read, and the
`…Updates` streams exist because both can change while the app runs.

Floors, from the interface's own `@available` annotations: **16.1** for ActivityKit at all,
**16.2** for `ActivityContent`-based `request`/`update` and for `ActivityState.stale`, 18.0
for `ActivityStyle`, 26.0 for `pending` and the `alertConfiguration` overloads.

The project floor is **16.1**, raised from 15.1 by `c8f05637` on 2026-09-19 —
`IPHONEOS_DEPLOYMENT_TARGET = 16.1` project-wide in the pbxproj, and
`"ios.deploymentTarget": "16.1"` in `Podfile.properties.json`, which feeds the Podfile's
`platform :ios` (`Podfile:19`). Note that this raises the *app* target only:
`DECISIONS_DEPLOYMENT_TARGET_AND_EXTENSIONS.md:135-143` records that RN's `post_install`
pins the 288 individual pods back to 15.1, so the end state is "app at 16.1, pods at 15.1".
That does not affect ActivityKit — the activity code lives in the app and the extension, not
in a pod — but it means a `grep` for the floor returns two different answers depending on
which file you land in. (The audit's §1 still said 15.1 and still said "nothing has been
implemented yet"; corrected in this commit. That decision shipped.)

So ActivityKit is now exactly *at* the floor rather than above it — but the API worth using
is not. `ActivityContent` and `staleDate` are 16.2, and Finding 5 argues `staleDate` is not
optional for an activity that can outlive its updater. The deprecated `contentState`-based
`request` (`:41-46`) works at 16.1 and gives no stale handling at all. Either accept a second
one-minor-version bump to 16.2, or accept that pre-16.2 devices get an activity that can lie
about an ended call. That is a product decision and it should be made explicitly rather than
by whoever writes the first `@available` guard.

---

## Finding 7 — the Live Activity extension cannot fetch, and that closes the keychain question for all four capabilities

*Added 2026-09-19, after `SHARE_EXTENSION.md` Finding 4 left this as the last open row.*

Three later documents converged, independently, on the same architectural rule:

> **On every Apple surface that runs outside the app process, the app owns the network and
> the out-of-process surface owns only a file in the shared container.**

App Intents got there because `perform()` has no RN bridge (`APP_INTENTS_SIRI_SHORTCUTS.md`
Finding 1). WidgetKit got there because `TimelineProvider` cannot report failure
(`WIDGETKIT.md` Finding 2). The Share Extension got there because its post-completion work is
a system-cancellable background task (`SHARE_EXTENSION.md` Findings 2–3). Each arrived at the
shape for its own reason, which is stronger evidence than any one of them alone.

`SHARE_EXTENSION.md` Finding 4 then used that to retire the keychain access group from the
extension foundation — and named Live Activities as the one capability of the four not yet
tested against the rule. This finding tests it.

**Live Activities do not merely satisfy the rule. They are its strictest instance: the
out-of-process surface owns nothing at all, not even a file.**

The evidence is a type with four members. `WidgetKit.swiftinterface:305-313`:

```swift
public struct ActivityViewContext<Attributes> where Attributes : ActivityKit.ActivityAttributes {
  public let activityID: Swift.String
  public let attributes: Attributes
  public let state: Attributes.ContentState
  public let isStale: Swift.Bool   // 16.2+
}
```

That is the entire input to the rendering closure. An id, the immutable attributes, the
current content state, and a staleness flag. There is no fetch, no provider, no snapshot
read, nothing the extension supplies for itself.

The contrast is in the same file, 220 lines apart, and it is the cleanest statement of the
difference between a widget and a Live Activity:

| | Initialiser | Takes a provider? |
|---|---|---|
| `StaticConfiguration` (a widget) | `init(kind:provider:content:)` (`:148`) | **yes** — `Provider : TimelineProvider`, and producing entries is the extension's job |
| `ActivityConfiguration` (a Live Activity) | `init(for:content:dynamicIsland:)` (`:369`) | **no** — there is no provider parameter to pass one to |

A widget extension *must* be given something to produce entries, which is why `WIDGETKIT.md`
has to argue about where those entries come from and lands on an App-Group snapshot written
by the app. A Live Activity extension is never asked. Every mutation enters through
`Activity.request` (`ActivityKit.swiftinterface:46-73`) and `Activity.update`
(`:147-171`) — static and instance methods on `Activity`, called in the app process — or,
in the push variant, through APNs from the backend. In neither path does the extension
originate anything.

### Two consequences

**1. #2 does not need the keychain access group, and the fourth row closes.** The table in
`SHARE_EXTENSION.md` Finding 4 can be completed: all four of the capabilities
`DECISIONS_KEYCHAIN_ACCESS_GROUP.md` cited as needing the entitlement turn out not to. Its
load-bearing third reason — "it is genuinely shared, four capabilities need it" — is now
false for all four rather than for three. The recommendation not to build it as a foundation
item stands on a complete argument rather than an extrapolation, and the app never takes an
entitlement that lets another binary read the user's refresh token.

The two things `SHARE_EXTENSION.md` preserved are still preserved: the read-old/write-new
migration analysis, and the owed hardware experiment proving a read with
`kSecAttrAccessGroup` set does not match an item written without one. Both belong to whoever
eventually needs the entitlement.

**2. The audit's stated prerequisite for #2 is wrong, and the error is the kind that costs
design time rather than a release.** §2 lists the entitlement as "`NSSupportsLiveActivities`
in Info.plist, plus an App Group to share state with the widget extension." The App Group
half does not apply to Live Activities. `ActivityViewContext.state` is delivered by the
system; nothing needs to be written to a shared container for a Live Activity to render.

This is not a saving — the same widget-extension target will ship WidgetKit widgets, which
*do* need the App Group, so the foundation cost is paid regardless. It matters because a
reader who believes the Live Activity reads shared state will design it to, and will
thereby import problems it does not have: a snapshot that can be stale, a purge obligation
(`DECISIONS_CORE_SPOTLIGHT_INDEXING_POLICY.md`'s three consumers would become four), and a
second source of truth racing `ContentState`. The correct mental model is that a Live
Activity's content has exactly one writer and it is always the app or the backend.

That also makes Finding 5's `staleDate` the *only* staleness mechanism in play, rather than
one of two — which is a good thing, because it is the one the system understands.

---

## Recommendation: the first Live Activity should be a media upload, not a call

The extension target, the App Group, the new App ID and provisioning profile, and the
`NSSupportsLiveActivities` key are a fixed cost paid once (shared, per
`APPLE_NATIVE_ARCHITECTURE.md`, with WidgetKit and the Share Extension). The question is
which activity pays it.

**Media upload / processing progress** is the better candidate on every axis that matters
here:

- **It touches nothing locked.** `mobile-native/src/media/` appears in no category of
  `config/realtime-audio-protected-paths.json`.
- **The state source already exists and is already a progress stream.**
  `MediaUploadManager.upload(asset, options, onProgress)` (`MediaUploadManager.ts:47`) emits
  `{stage, percent, message}` through the whole lifecycle — `validating` (`:83`), `resuming`
  (`:104`), `uploading` (`:126`), retry with attempt counts (`:124`), `finalizing` (`:170`).
  A Live Activity is close to a direct rendering of that object.
- **It needs no push at all**, because the app is what knows the progress. No
  `.push-type.liveactivity` topic, no per-activity token store, no Finding 3, no Finding 4,
  and no 17.2 floor.
- **It has a definite end**, which is exactly what Finding 5's dismissal policy wants and
  what an open-ended call does not reliably give.
- **It is the case with real user value**: a large reel upload is precisely when someone
  leaves the app, and right now they have no way to know whether it finished.

Build that one. It proves the entire extension foundation against a subsystem where a
mistake costs a wrong progress bar. Then, with the foundation known-good, revisit the call
activity under Findings 1 and 2.

---

## Owed

| # | Item | Blocks |
|---|---|---|
| 1 | Wave 2 foundation: App Group, extension target, App ID + profile, `NSSupportsLiveActivities` | everything; shared with WidgetKit and Share Extension |
| 2 | Raise the deployment floor to 16.2 | the `ActivityContent` / `staleDate` API, which is the one to use |
| 3 | Decide the dismissal + `staleDate` policy per activity type, in writing | any code (Finding 5) |
| 4 | If push updates are ever adopted: separate token store + classifier | Finding 4 |
| 5 | If push updates are ever adopted: topic via the `known_bundle_ids()` allowlist pattern, never a bare bundle id | Finding 3 |
| 6 | Call activity only: assert the `ContentState` is a projection of existing `CallSessionSnapshot` fields | Finding 2 |
| 7 | Correct audit §2's "plus an App Group to share state" — a Live Activity needs no shared container | Finding 7. Done in the same commit. |
| — | ~~Re-examine #2 against the "the app owns the network" rule~~ | **Closed by Finding 7.** It satisfies the rule more strictly than the other three, and no capability now needs `keychain-access-groups`. |

---

## What was verified, and what was not

**Verified by reading, in this repo:** no ActivityKit symbol anywhere under `mobile-native/`;
`PulseSoc.entitlements` has no App Group; `callSessionStore.ts` is listed under
`audio_and_video_call_adapter` in `config/realtime-audio-protected-paths.json`;
`CallSessionSnapshot`'s field list (`callSessionStore.ts:62-84`, `:89-117`);
`pulsesoc_voip_push.py` `known_bundle_ids` (`:164-180`), `topic_for_bundle` (`:183-206`),
the `invalid_device` computation (`:766`) and the revoke call (`:843-845`);
`pulsesoc_notification_system.py:2591` passing a bare `bundle_id` as `apns-topic`;
`MediaUploadManager.upload`'s `onProgress` signature (`:47`) and its stage emissions;
`mobile-native/src/media/` appearing in no protected category.

**Verified against the ActivityKit Swift interface:** every API name, availability
annotation and enum case cited above, with the line numbers given. Also that `request`
(`:46`, `:52`, `:59`, `:66`, `:73`) and `update` (`:147`, `:153`, `:159`, `:165`, `:171`)
are declared on `Activity<Attributes>` and nowhere else — Finding 7's claim that every
mutation enters through the app process is a claim about where the API lives, and it is
visible in the interface.

**Verified against the WidgetKit Swift interface:** `ActivityViewContext`'s complete member
list (`:305-313`); `ActivityConfiguration.init(for:content:dynamicIsland:)` taking no
provider (`:369`); and `StaticConfiguration.init(kind:provider:content:)` taking one
(`:148`). Finding 7 rests on the absence of a parameter, so it was checked by reading the
whole `ActivityConfiguration` declaration (`:368-379`) rather than by grepping for one name.

**Not verified.** Everything runtime. In particular: the `.push-type.liveactivity` topic
suffix and the 17.2 floor for push-updated activities are stated from documented behaviour —
they are not visible in the Swift interface and no Live Activity has been pushed from this
deployment. The claim that `MediaUploadManager`'s progress stream survives backgrounding
long enough to be worth an activity has **not** been tested and is the first thing to check
before building the recommendation above; if the upload itself is suspended when the app
backgrounds, the activity would freeze rather than complete, which changes the design.
