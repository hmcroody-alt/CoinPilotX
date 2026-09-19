# Core Spotlight — mechanism

Capability #4. This document covers **how** Spotlight indexing would work in PulseSoc.
**What** may be indexed is settled separately and is not restated here:
`DECISIONS_CORE_SPOTLIGHT_INDEXING_POLICY.md` owns the allowlist, the denylist, and the
purge triggers. Read that first; this document assumes it.

Status: **NOT IMPLEMENTED.** `grep -rniE "corespotlight|CSSearchable" mobile-native/ios
mobile-native/modules mobile-native/src` returns nothing. The single `NSUserActivity`
reference in first-party iOS code is the continuation entry point at
`AppDelegate.swift:64`.

Evidence below is the iOS 26.5 SDK headers under
`/Applications/Xcode.app/.../iPhoneOS26.5.sdk/System/Library/Frameworks/CoreSpotlight.framework/Headers`,
plus the repo. Nothing here has been run on a device.

---

## Finding 1 — the stated prerequisite is the wrong key, in three documents

`DECISIONS_CORE_SPOTLIGHT_INDEXING_POLICY.md:173` says:

> `NSUserActivityTypes` is added to `Info.plist` — the one Info.plist key this needs

`APPLE_NATIVE_ARCHITECTURE.md:210` and `APPLE_CAPABILITIES_AND_ENTITLEMENTS.md:153` repeat
it. All three are wrong for the API the policy implies, and `HANDOFF.md:175` already carries
the correction — the three were written before it and never caught up.

`CSSearchableItem.h:13-18`:

> When opening a document from Spotlight, the application's
> `application:willContinueUserActivityWithType:` method will get called with
> `CSSearchableItemActionType`, followed by `application:continueUserActivity:restorationHandler:`
> with an `NSUserActivity` where the userInfo dictionary has a key value pair where
> `CSSearchableItemActivityIdentifier` is the key and the value is the uniqueIdentifier used
> when creating the item.

`CSSearchableItemActionType` is `CORESPOTLIGHT_EXPORT` — a constant the system vends. It is
not an app-declared type, so there is nothing for the app to declare.

`NSUserActivityTypes` does something else. `NSUserActivity.h:22`:

> A user activity may be continued only in an application that (1) has the same developer
> Team ID as the activity's source application and (2) supports the activity's type.
> Supported activity types are specified in the application's Info.plist under the
> NSUserActivityTypes key. When receiving a user activity for continuation, the system
> locates the appropriate application to launch by finding applications with the target Team
> ID, then filtering on the incoming activity's type identifier.

That is cross-*device* continuation — Handoff — filtered by Team ID and type. A local
Spotlight tap is neither.

There *is* an Info.plist key in this framework, and it is a different feature.
`CSSearchableItem.h:25-33` documents `CSQueryContinuationActionType`, which lets the user
continue a Spotlight *query* into the app, and:

> The application should declare that it supports the query continuation by adding the
> CoreSpotlightContinuation key to its Info.plist

The policy document does not ask for query continuation, and it should not — continuing a
raw query string into PulseSoc's search means handing the search screen text the user typed
into a system field, which is a wider surface than the allowlist contemplates. So the
correct statement is: **`CSSearchableItem` indexing needs no Info.plist key and no
entitlement at all.** The "no entitlement, no App Group, no portal work" half of the policy
doc's item 2 was right; the key was not.

Corrected in this commit in all three places.

---

## Finding 2 — the actual prerequisite is routing, and it is already documented

Because the tap arrives at `application(_:continue:restorationHandler:)`, Core Spotlight
inherits `HANDOFF.md` Finding 1 wholesale. `AppDelegate.swift:62-69`:

```swift
public override func application(
  _ application: UIApplication,
  continue userActivity: NSUserActivity,
  restorationHandler: @escaping ([UIUserActivityRestoring]?) -> Void
) -> Bool {
  let result = RCTLinkingManager.application(application, continue: userActivity, restorationHandler: restorationHandler)
  return super.application(application, continue: userActivity, restorationHandler: restorationHandler) || result
}
```

Neither branch handles a Spotlight activity:

- `RCTLinkingManager` acts only on `NSUserActivityTypeBrowsingWeb` with a non-nil
  `webpageURL` (`RCTLinkingManager.mm:78`) — a Spotlight item carries its identifier in
  `userInfo[CSSearchableItemActivityIdentifier]` and has no `webpageURL` — and then
  `returns YES` unconditionally (`:82`).
- Expo's `LinkingAppDelegateSubscriber`, reached through `super`, is web-URL-only.

So the delegate returns `true` for an activity nobody consumed. The observable result, as
`HANDOFF.md` puts it: iOS foregrounds the app, considers the continuation handled, and
PulseSoc lands on whatever screen it was already on — no navigation, no error, no log line.

That failure will be read as "Spotlight indexing doesn't work". The index will be fine. Ship
the `switch userActivity.activityType` in the AppDelegate **before** any indexing code, or
the first device test of this capability debugs the wrong half.

---

## Finding 3 — PulseSoc structurally cannot answer a reindex request

`CSSearchableIndex.h:115-131` defines `CSSearchableIndexDelegate` with two `@required`
methods, the first being:

> The index requests that the delegate should reindex all of its searchable data and clear
> any local state that it might have persisted **because the index has been lost.**

and at `:110-111`:

> An application that is long running should provide a `CSSearchableIndexDelegate` conforming
> object to handle communication from the index. Alternatively, an app can provide an
> extension whose request handler conforms to this protocol and the extension will be called
> if the app isn't running.

PulseSoc can satisfy neither branch as it stands:

- It is not long-running. It is a foreground social app; when Spotlight loses its index the
  process is almost certainly dead.
- It has no extension target. `CSIndexExtensionRequestHandler` is the supplied base class,
  but an extension is a separate target in the Xcode project, and — decisively — every item
  the allowlist permits (own profile, saved library, education articles) comes from an
  authenticated PulseSoc API. An extension has no session. It would have to reach into a
  shared keychain group, which does not exist yet (it is Wave 2 work,
  `DEVICE_SECURITY.md`), and then make network calls from a process the user did not launch.

This is not a blocker, but it must be a *decision* rather than an omission. The honest
design is: **do not adopt `CSSearchableIndexDelegate`; treat the index as lossy and rebuild
from the app.** Concretely, re-index on foreground-after-authentication rather than once at
sign-in. The cost of getting this wrong is silent — entries vanish and nothing reports it —
which is the same shape as Finding 4.

The policy document's launch-time repair trigger ("App launch, if the index is non-empty and
nobody is signed in", `:165`) is the *purge* direction of the same idea and already covers
the security-relevant half. Finding 3 concerns only completeness.

---

## Finding 4 — indexed items expire after one month, by default, silently

`CSSearchableItem.h`:

> Searchable items have an expiration date or time to live. By default it's set to 1 month.

Two consequences point in opposite directions and both matter.

For **security**, this is a backstop: it bounds the policy document's central worry — "the
index entry outlives the permission" — at roughly 31 days even if a purge throws and the
launch-time repair never fires. It does not make the purge optional; 31 days of a
now-private profile being readable from the home screen is still the failure the policy
exists to prevent. But it is worth knowing the exposure is bounded rather than permanent.

For **correctness**, it is a trap. An implementation that indexes the saved library once at
sign-in and never again produces a Spotlight experience that works perfectly for a month and
then empties, for a user who did nothing. There will be no crash, no error, no log line, and
the indexing code will still be correct when read. Either set `expirationDate` explicitly
with a chosen policy, or re-index on a cadence shorter than the TTL — and say which in the
code, because the default is invisible at the call site.

Related, from the same header, `isUpdate`:

> If an item is marked as an update, but does not already exist in the index, it will be
> dropped during the attempted indexing.

So the natural optimisation — "this row already exists, mark it an update" — silently drops
every item whose index entry has expired. `isUpdate` and the one-month TTL interact to
produce a re-index that writes nothing.

---

## Finding 5 — `domainIdentifier` is the purge primitive the policy actually wants

The policy document specifies `deleteAllSearchableItems()` inside
`clearUserScopedMediaState()`. That is right for sign-out. It is too blunt for the other
three triggers at `:161-165` — un-saving one item, or a profile flipping to private, should
not drop the education articles.

`CSSearchableItem.h` documents the scoped alternative:

> Calling `deleteSearchableItemsWithDomainIdentifiers` with `<account-id>.<mailbox-id>` will
> delete all items with that domain identifier. Calling
> `deleteSearchableItemsWithDomainIdentifiers` with `<account-id>` will delete all items with
> `<account-id>` and any `<mailbox-id>`.

Prefix semantics on a dotted identifier. So `domainIdentifier = "<user-id>.<class>"` where
class is one of `profile` / `saved` / `education` gives, for free:

| Trigger | Call |
|---|---|
| Sign-out / account switch | `deleteSearchableItemsWithDomainIdentifiers(["<user-id>"])` |
| Profile flips to private | `…(["<user-id>.profile"])` |
| Item leaves saved library | `deleteSearchableItems(withIdentifiers: [itemId])` |

The header warns that the components "should not contain periods", which matters because
PulseSoc user ids are integers and the content classes are chosen here — both safe, but the
constraint should be asserted in code rather than assumed.

One caveat, from `CSSearchableIndex.h:71`: batch client state (<250 bytes) "will be reset
whenever `deleteAllSearchableItemsWithCompletionHandler` is called". Any resume cursor dies
with a full purge. That is correct behaviour, but a batched indexer that persists a cursor
must treat purge as a cursor reset, not merely as a delete.

---

## Finding 6 — one of the policy document's two open assumptions is now half-closed

`DECISIONS_CORE_SPOTLIGHT_INDEXING_POLICY.md:197-202` flags two assumptions. Status:

**`deleteAllSearchableItems()` on an empty index is a cheap no-op.** Still unverified — that
is a runtime cost question and needs a device. What *was* verified is narrower and was worth
checking separately, because reading an ObjC selector does not tell you its Swift name: the
spelling in the policy document compiles. `deleteAllSearchableItemsWithCompletionHandler:`
is `__nullable`-blocked, so both `deleteAllSearchableItems { _ in }` and the bare
`deleteAllSearchableItems()` typecheck against the real SDK. Verified with
`xcrun --sdk iphoneos swiftc -typecheck`, with a negative control
(`deleteEverySearchableItemNow()` → `error: value of type 'CSSearchableIndex' has no member`)
proving the harness discriminates.

**A `CSSearchableItem` is included in an encrypted device backup.** Not verified here
either; nothing in these headers speaks to it. It remains reasoning from documented
behaviour, and the policy does not depend on it.

A third gate belongs beside them: `CSSearchableIndex.h:35` exposes
`+ (BOOL)isIndexingAvailable`. Whatever the implementation does, it should branch on that
rather than assume, and it should log which way it branched — otherwise "indexing does
nothing on this build" and "indexing is unavailable on this device" are indistinguishable,
which is the same failure mode as Finding 2.

---

## Implementation shape, when it comes

There is no first-party Expo module for CoreSpotlight. The shape to copy is
`mobile-native/modules/pulse-now-playing/` — one podspec plus one Swift module file — which
already exists in this repo for the same reason (a system framework with no JS binding).

The JS surface should be narrow enough that the policy is enforceable in one place:

- `index(items: SpotlightItem[])` where `SpotlightItem`'s type union *is* the allowlist,
  so a DM or an Office record cannot be expressed. Policy item 4 asks for a test that the
  denylist rejects; a type that cannot represent a denied item is stronger than a test.
- `purge(scope)` mapping to the three calls in Finding 5.
- `onOpen(identifier)` — fed by the AppDelegate switch from Finding 2, not by Linking.

Flag default OFF, per the mission's standing rule.

---

## Owed

| # | Item | Blocks |
|---|---|---|
| 1 | `switch userActivity.activityType` in `AppDelegate.swift:62-69` | all of it (shared with HANDOFF, Live Activities, App Intents) |
| 2 | Decide and record: no `CSSearchableIndexDelegate`, rebuild on foreground-after-auth | implementation |
| 3 | Decide an explicit `expirationDate` / re-index cadence | implementation |
| 4 | Add `AppDelegate.swift` to the audio protected-path manifest | carried from HANDOFF; a separate audio-scoped change |
| 5 | Device check: `isIndexingAvailable`, and `deleteAllSearchableItems()` cost on an empty index | first device test |

Items 1 and 4 are already owed by `HANDOFF.md`; they are listed here because Core Spotlight
is the capability that makes item 1 user-visible.

---

## What was verified, and what was not

**Verified by reading, in this repo:** no CoreSpotlight symbol exists anywhere in
`mobile-native/ios`, `mobile-native/modules`, or `mobile-native/src`; `AppDelegate.swift:62-69`
verbatim as quoted; the three documents asserting `NSUserActivityTypes`
(`DECISIONS_CORE_SPOTLIGHT_INDEXING_POLICY.md:173`, `APPLE_NATIVE_ARCHITECTURE.md:210`,
`APPLE_CAPABILITIES_AND_ENTITLEMENTS.md:153`); that `modules/pulse-now-playing/` is a
podspec plus one Swift file.

**Verified against the iOS 26.5 SDK headers:** every quotation above, from
`CSSearchableItem.h`, `CSSearchableIndex.h`, `CSIndexExtensionRequestHandler.h`, and
`NSUserActivity.h`.

**Verified by compiling:** the Swift spelling of `deleteAllSearchableItems`, with a negative
control.

**Not verified.** Everything runtime. No indexing code exists, so no claim here about
behaviour on device — expiry, purge cost, indexing availability on Simulator, whether a
reindex request is ever actually delivered to an app with no delegate — has been observed.
SDK header text is documentation, not behaviour. The first person to write indexing code
should treat Findings 3, 4 and 6 as predictions to test, not as established facts.
