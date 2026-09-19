# Handoff — the receiving half is real, and it is not Handoff's

Written 2026-09-19. Capability #10 in `PULSESOC_APPLE_NATIVE_CAPABILITY_AUDIT.md`,
recorded there as **FOUNDATION EXISTS**.

That status is accurate but the phrase is misleading in a way worth correcting,
because the correction is the useful part of this document.

`application(_:continue:restorationHandler:)` is not the Handoff delegate method.
It is **the continuation entry point**, and iOS delivers four unrelated things
through it: a Handoff continuation from another device, a universal-link tap, a
Spotlight result tap (`CSSearchableItemActionType`), and a Siri/App Intents
continuation. PulseSoc implements it, and the implementation exists — and is
correct — **for universal links**, which are live in production today (see
`UNIVERSAL_LINKS.md`).

So the honest reading of #10 is not "Handoff is half built." It is: *the method
Handoff would arrive through is already occupied by a different feature, and the
way it is occupied has consequences for the next three capabilities that want to
use it.* Those consequences are this document.

---

## The chain, verified end to end

`ios/PulseSoc/AppDelegate.swift:62-69`:

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

Two consumers, both invoked. The `let` before the `||` is deliberate and right:
writing `RCTLinkingManager… || super…` would let Swift short-circuit the second
call away whenever the first returned true, and the first always does.

| Hop | File | What it does |
|---|---|---|
| 1 | `RCTLinkingManager.mm:74-83` | acts **only** on `NSUserActivityTypeBrowsingWeb` with a non-nil `webpageURL` (`:78`); posts `kOpenURLNotification` → RN `Linking`'s `"url"` event |
| 2 | `ExpoAppDelegate.swift:211-216` | forwards to `ExpoAppDelegateSubscriberManager` |
| 3 | `ExpoAppDelegateSubscriberManager.swift:287-308` | fans out to every subscriber that `responds(to:)` the selector |
| 4 | `LinkingAppDelegateSubscriber.swift:22-37` | acts **only** on `NSUserActivityTypeBrowsingWeb` (`:27`); posts `onURLReceivedNotification` → expo-linking listeners |
| 5 | `src/navigation/linking.ts` | React Navigation consumes RN `Linking` and resolves the path against ~100 declared screens plus three custom resolvers |

Of the four installed Expo subscribers that could respond
(`expo-iap`, `expo-iap/onside`, `expo-notifications`, `expo-dev-launcher`),
**none implements `continue:`.** In a release build hop 3 fans out to exactly one
subscriber: `LinkingAppDelegateSubscriber`. That single fact matters twice below.

---

## Finding 1 — the app claims to have handled every continuation

`RCTLinkingManager` returns `YES` unconditionally (`RCTLinkingManager.mm:82`). It
checks the activity type only to decide whether to *post a notification*, not to
decide its return value. So `result` in the AppDelegate is always `true`, and
`super… || result` is therefore always `true`.

PulseSoc tells iOS it handled the continuation for **every** activity type,
including ones neither consumer looked at.

**Today this is inert.** No non-web activity type can be delivered, because
nothing in the app creates an `NSUserActivity`, no `NSUserActivityTypes` key
exists in any Info.plist, `app.json` or `app.config.js`, and the only occurrence
of the string `NSUserActivity` anywhere in `src/`, `ios/` or `modules/` is the
parameter declaration above. Verified by grep; there is genuinely nothing else.

**It stops being inert the moment capability #4 (Core Spotlight) ships.** A tap
on a Spotlight result delivers a continuation with
`activityType == CSSearchableItemActionType`. Hop 1 ignores it (not web). Hop 4
ignores it (not web). The delegate returns `true`. iOS foregrounds the app,
considers the continuation handled, and PulseSoc lands on whatever screen it was
already on — no navigation, no error, no log line.

The symptom is "Spotlight indexing doesn't work." The cause is routing. The two
look identical from the outside and the wrong one is much more expensive to
investigate, which is why it is written down here before anyone builds #4.

The same trap catches a Siri/App Intents continuation (#3) and a genuine Handoff
continuation, for the same reason.

**The fix is small and belongs to whichever capability lands first:** switch on
`userActivity.activityType` in the AppDelegate, route `CSSearchableItemActionType`
(and any declared custom type) to its own handler, and forward only
`NSUserActivityTypeBrowsingWeb` down the existing chain. Not done here — there is
nothing to route to yet, and a switch with one live branch is a change that reads
as complete while doing nothing.

## Finding 2 — the restoration handler is handed to two chains and called by neither

The same `restorationHandler` closure is passed to `RCTLinkingManager` *and* to
`super`. UIKit documents it as call-at-most-once.

Neither calls it, for different reasons:

- `RCTLinkingManager` ignores the parameter entirely — it has no restorable
  objects to return.
- Expo's manager does not pass it through. It builds a **counting wrapper**
  (`ExpoAppDelegateSubscriberManager.swift:292-304`) that forwards to the real
  handler only once `subscribersLeft` reaches zero, decremented each time a
  subscriber calls its copy. `LinkingAppDelegateSubscriber` returns without ever
  calling it (`:34`, `:36`). With exactly one responding subscriber, the counter
  never reaches zero and **the real `restorationHandler` is never invoked.**

Harmless today — there is nothing to restore, and UIKit tolerates a delegate that
returns without restoring. It becomes a live hazard the moment a Handoff or
Spotlight handler is added *next to* the existing calls rather than in front of
them: now two independent paths hold the same one-shot closure. The
activity-type switch in Finding 1 also fixes this, by making exactly one path
reachable per continuation.

## Finding 3 — two notifications per universal-link tap, consumed once

Both hop 1 and hop 4 act on a web activity, so a single universal-link tap posts
two different notifications: `kOpenURLNotification` (RN `Linking`) and
`onURLReceivedNotification` (expo-linking).

That is a duplicate-handling shape, and the reason it does not produce duplicate
navigation is narrow: **nothing in `src/` imports `expo-linking`.**
`git grep expo-linking -- src` returns exactly one hit and it is a *string* in
`native/capabilityRegistry.ts`, not an import. Navigation goes through React
Navigation, which listens on RN `Linking`. So the expo-linking notification is
posted into a room with nobody in it.

This is a property of what the app currently imports, not of the platform. A
future `import * as Linking from "expo-linking"` with a URL listener — a very
natural thing for someone to write, since `expo-linking` is a declared
dependency (`package.json:57`) — would start handling every universal link twice.

Worth knowing; not worth a guard yet, because the second handler would have to be
written deliberately and the failure is immediate and obvious rather than silent.

## Finding 4 — the capability registry does not know about two of these

`src/native/capabilityRegistry.ts` calls itself the "single source of truth for
what PulseSoc can do locally" and says UNDX "must consult this registry rather
than guessing." Two entries are off:

- **`deep_links` names `expo-linking` as its `native_dependency`.** The dependency
  actually doing the work is React Native's `Linking` via React Navigation;
  expo-linking's contribution is a notification nobody listens to (Finding 3).
- **There is no record for the lock-screen / Control Centre transport controls**,
  which demonstrably ship — see `SYSTEM_CONTROLS.md`. A registry consulted by
  UNDX that omits a shipped capability will have UNDX answer that PulseSoc cannot
  do something it does.

**Not changed here.** `CapabilityId` is a typed union consumed by
`undxVisibleCapabilities`, so adding an id changes what an assistant surface
advertises to users. That is a product decision, not a documentation fix, and
this document is not the place to make it unilaterally. Recorded as owed.

---

## What the sending half would actually cost

Unchanged from the audit's assessment, restated with the above in mind:

1. Declare `NSUserActivityTypes` in Info.plist with the app's custom type(s) —
   currently absent entirely.
2. Create an `NSUserActivity` when a screen becomes current, set `title`,
   `userInfo`, `webpageURL` and `isEligibleForHandoff`, and make it current.
3. Handle the incoming custom type in the AppDelegate — which requires Finding 1
   to be fixed first.

Step 2 is the same object Core Spotlight uses (`isEligibleForSearch` /
`isEligibleForPublicIndexing` are sibling flags on `NSUserActivity`), which is
why the audit recommends doing Handoff only as a by-product of #4. Note the
precision, because it is easy to get backwards: **`CSSearchableItem`-based
Spotlight indexing does not require `NSUserActivityTypes`** — `CSSearchableItemActionType`
is a system type. The declaration is required for *custom* activity types, which
is the Handoff path and the `NSUserActivity`-donation flavour of Spotlight.

## Is it worth building at all

No, and this is not a close call. Handoff pays off across iPhone ↔ Mac ↔ iPad.
PulseSoc has one native client, iPhone-only (`"supportsTablet": false` in
`app.json`). The only other surface is the web client, and Handoff-to-Safari
hands the user a web page they could have reached by tapping a link.

There is nowhere to hand off *to*. Build the receiving-side routing fix as part
of Core Spotlight; do not build the sending side as a feature.

## The naming trap, measured

"Handoff" is ordinary domain vocabulary in this codebase, and it is *everywhere*.
`git grep -lic handoff -- src ios modules` returns **62 files**. Not one of them
is about Apple Handoff.

The word carries at least seven unrelated senses:

| Sense | Example identifiers |
|---|---|
| Composer state persisted between screens | `saveShareComposerHandoff`, `stashPreviewHandoff`, `createComposerHandoff` |
| Marketplace checkout → Stripe sheet | `readCheckoutHandoff`, `CheckoutHandoff` |
| Share-sheet replay guard | `shareHandoffNonce`, `lastShareHandoffNonceRef` |
| Verification review flow | `reviewHandoff`, `reviewHandoffCopy` |
| Order fulfilment transition | `confirm_handoff` |
| Moderation appeals | `unblock_handoff`, `mute_handoff`, `recordMuteHandoff` |
| UNDX conversation turn | "the handoff turn" |

Seven files are *named* for it — `shareComposerHandoff.ts`, `previewHandoff.ts`,
`createComposerHandoff.ts`, `MarketplaceCheckoutQuantityHandoff.test.tsx` and
three sibling test files.

This matters in a specific way. Anyone assessing "is Handoff built?" by grepping
gets 62 hits and a directory of files named for the feature, which reads as
*substantially built*. The truth is the exact opposite: zero of the 62 touch
`NSUserActivity`, and the only occurrence of that symbol in the entire
repository is the AppDelegate parameter declaration quoted above. The signal and
the reality point in opposite directions, which is the most expensive kind of
naming collision there is.

The grep that actually answers the question is `git grep -n NSUserActivity`, not
`git grep -i handoff`.

---

## What is owed

| Item | Where | Trigger |
|---|---|---|
| Switch on `activityType` before forwarding (Findings 1 + 2) | `AppDelegate.swift:62-69` | **Prerequisite for #4 Core Spotlight and #3 App Intents** |
| Add `ios/PulseSoc/AppDelegate.swift` to the audio protected-path manifest | `config/realtime-audio-protected-paths.json` | carried forward from `APPLE_CAPABILITIES_AND_ENTITLEMENTS.md` — the file holds the PushKit/CallKit path |
| Decide whether the capability registry should advertise now-playing controls, and fix `deep_links`'s dependency | `src/native/capabilityRegistry.ts` | product decision |
| Device validation of a universal-link tap | iPhone 16 Pro | **owed**; the simulator cannot test associated domains |

---

## What was verified, and what was not

**Verified by reading, at `929d44b0`:** `AppDelegate.swift` in full, including the
`||` ordering; `RCTLinkingManager.mm:74-83` in the installed React Native, which
is where the unconditional `return YES` lives; `ExpoAppDelegate.swift:211-216`;
`ExpoAppDelegateSubscriberManager.swift:281-308` including the counting
restoration wrapper; `LinkingAppDelegateSubscriber.swift` in full; that none of
the other four installed Expo subscribers implements `continue:`; the complete
`src/navigation/linking.ts`; the absence of `NSUserActivityTypes` from `ios/`,
`app.json` and `app.config.js`; the absence of any `NSUserActivity`,
`CoreSpotlight` or `CSSearchable` reference outside the AppDelegate parameter;
that no file in `src/` imports `expo-linking`; and the 62-file "handoff" grep,
counted rather than estimated — an earlier draft of this document asserted that
grep found three files and nothing else, which was wrong by a factor of twenty
and wrong in the direction that makes the trap worse.

**Not verified.** No device test, and for this capability that gap is wider than
usual because every claim here is about what iOS *delivers*:

- **No continuation of any kind was observed arriving.** Finding 1 is derived
  from the return value in RN's source and the type checks in both consumers, not
  from watching a `CSSearchableItemActionType` land and do nothing — which cannot
  be watched yet, because nothing indexes anything.
- **Universal-link receipt was not re-tested here.** It was verified in
  `UNIVERSAL_LINKS.md`; this document traces the code path it travels, and adds
  no new evidence about whether a tap lands.
- **Finding 2's "the restoration handler is never invoked" was not instrumented.**
  It follows from one responding subscriber and a counter that only that
  subscriber could decrement. No breakpoint was set.
- **No Handoff was attempted**, there being no second device class to attempt it
  between.
