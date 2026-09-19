# The keychain access group — the thing four capabilities are actually blocked on

Written 2026-09-19. `APPLE_CAPABILITIES_AND_ENTITLEMENTS.md` identifies a shared
prerequisite behind four of the fourteen audited capabilities — Live Activities,
WidgetKit, Share Extension and Control Center — and singles out one part of it as
the part that gets missed:

> `mobile-native/src/session/sessionStore.ts` pins the keychain service to
> `com.pulsesoc.app.session` with **no** access group, so an extension **cannot
> read the session token today**. Any widget or share extension showing
> personalised content is blocked on that entitlement, and adding it later means
> migrating existing keychain items — cheaper to decide up front.

That is correct, and it was worth flagging. But "cheaper to decide up front"
stopped short of saying what the migration actually costs, which is the number
that decides whether this is a foundation item or a footnote. This document
measures it.

**Headline: it is cheaper than the warning implies, and the pattern it needs is
already written in the same file.** But it is not free, and the failure mode if
done carelessly is that every existing user is silently signed out.

---

## What is true today

`mobile-native/src/session/sessionStore.ts:39-42`:

```ts
const KEYCHAIN_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
  keychainService: __DEV__ ? "com.pulsesoc.nativeapp.dev.session" : "com.pulsesoc.app.session"
};
```

No `accessGroup`. `PulseSoc.entitlements` carries two keys and neither is
`keychain-access-groups` (`plutil -p` shows only `aps-environment` and
`com.apple.developer.associated-domains`). So the confirmation stands: nothing
outside the app process can read the session envelope.

Three items live under these options — the session cookie
(`pulsesoc.native.session.cookie`), the session envelope
(`pulsesoc.native.session.envelope.v1`, holding both access and refresh token),
and the biometric enrollment marker. A fourth, the biometric refresh token, sits
on a **separate** service by deliberate design (`:43-51`) because
expo-secure-store files authenticated and unauthenticated items separately.

---

## The first question was whether this is even possible without a native module

It is, and this materially lowers the cost. `expo-secure-store@15.0.8` exposes
`accessGroup` as a first-class option:

```
/**
 * Specifies the access group the stored entry belongs to.
 * @platform ios
 */
accessGroup?: string;
```

— `node_modules/expo-secure-store/build/SecureStore.d.ts:76-80`. The native half
is real, not a stub: `ios/SecureStoreModule.swift:187-188` sets
`kSecAttrAccessGroup` on the query dictionary.

So no custom native module, no patch, no fork. Worth stating plainly because the
alternative — hand-rolling keychain access in Swift to share a token with an
extension — is the kind of thing that turns a two-day foundation into a two-week
one.

---

## The cost, and where it actually bites

`accessGroup` is applied by a **single shared query builder**
(`SecureStoreModule.swift:172`), and that builder is used by every operation:

| Operation | Call site |
|---|---|
| get | `:44-46` (three queries — no-auth, auth, legacy) |
| set | `:90` |
| delete | `:117-118` |

This is the whole finding. Because the same dictionary is used for reads, adding
`accessGroup` does not merely change *where new items are written* — **it changes
what a read can find.**

iOS keychain semantics make the consequence concrete. A query with
`kSecAttrAccessGroup` absent searches every access group the app is entitled to;
a query with it present is restricted to that one. Items written today, with no
access group, land in the app's default group
(`$(AppIdentifierPrefix)com.pulsesoc.app`). A build that starts reading with
`accessGroup` set to a new shared group **will not find them.**

`getSessionCookie` then returns `null`, and it does so through a path that is
explicitly designed to be quiet about it (`:66-73`):

> Degrade to signed-out instead of rejecting startup: a re-login is a far better
> failure mode than a fatal "couldn't start PulseSoc" screen.

That behaviour is right, and it is exactly what makes this dangerous. A botched
access-group change does not crash and does not error. **Every user on the update
is simply signed out, at once, and the app looks like it is working.** That is
the single worst-case outcome in this document and the reason it gets its own
decision rather than riding along with the first widget.

---

## The migration pattern is already in this file

The file has done a keychain migration before, and did it well. Lines 21-38:

> v1 of the Face-ID refresh token: correct in every respect except that the
> keychain handed it back to anyone who asked. Kept read-only so devices enrolled
> before v2 migrate on their next unlock instead of being silently un-enrolled
> and forced to set Face ID up again.

Read-old / write-new / retire-old, driven by ordinary use rather than by a
migration step. That is the precedent, it is in the same module, and the
access-group change is a strictly easier instance of it — the *key* stays the
same, only the query attributes change.

**Decision: adopt that pattern rather than a flag day.**

1. Keep a `LEGACY_KEYCHAIN_OPTIONS` with no `accessGroup`.
2. Reads try the new options first, then fall back to legacy.
3. A legacy hit is immediately rewritten under the new options, then the legacy
   copy is deleted. (Delete only after the write is confirmed — `:117-118` shows
   delete is also access-group-scoped, so an early delete against the wrong group
   is a no-op that leaves a stale copy behind.)
4. The fallback stays for at least two releases, then goes.

Cost: one options constant, one fallback branch in three accessors, and tests.
Not a footnote, but nothing like a data migration.

---

## What must land together, and in what order

The entitlement and the code are not independent, and the wrong order is
observable in production.

| Step | Where | Note |
|---|---|---|
| 1. Enable Keychain Sharing on the App ID | Apple Developer portal | cannot be done from the repo |
| 2. `keychain-access-groups` in `PulseSoc.entitlements` | committed project | must list the shared group **and** keep the default |
| 3. App Group (`group.com.pulsesoc.app`) | portal + entitlements | separate mechanism; needed for shared *files*, not for the keychain |
| 4. Read-with-fallback in `sessionStore.ts` | client | ships **before or with** step 5 |
| 5. Write under the new access group | client | never before step 4 |

**Steps 4 and 5 must not be split across releases in the other order.** A build
that writes to the shared group while an older build on the same device reads
without fallback is fine — the older build's read has no access-group filter and
still matches. The reverse is not: a build that reads *only* from the shared
group, shipped before anything wrote there, signs everyone out.

The entitlement must **keep the default group as well as adding the shared one**.
An entitlement listing only the shared group changes where unqualified writes
land and makes the legacy fallback unable to see its own history.

Note also `sessionStore.ts:68-70`, which already anticipates this whole area:

> Keychain unreadable (e.g. an adhoc/simulator build lacks the
> `keychain-access-groups` entitlement → -34018 ...)

That `-34018` is the error an entitlement mismatch produces, and it is already a
known quantity here — consistent with the separate finding that ad-hoc-signed
simulator builds cannot exercise entitlement-gated behaviour at all. **This
change cannot be validated on the simulator.** It needs a device build with a
real provisioning profile, which makes it one of the few items in the Apple plan
with a hard device dependency before it can be called done.

---

## Decision

**Do it as its own piece of work, before the first extension, not as part of it.**

> **Challenged 2026-09-19 — the third reason below no longer holds, and it was the
> load-bearing one.**
>
> This decision was written before any extension had a concrete design. Three now do,
> and none of them reads the session:
>
> | Capability | Why it does not need the access group |
> |---|---|
> | #3 App Intents | `perform()` has no RN bridge, so the recommended shape is a Siri-addressable deep link; the app does the work in-process (`APP_INTENTS_SIRI_SHORTCUTS.md` F1) |
> | #12 WidgetKit | `TimelineProvider` cannot report failure, so the widget should never fetch — the app writes a snapshot into the App Group and calls `reloadTimelines` (`WIDGETKIT.md` F2) |
> | #13 Share Extensions | the extension's post-completion work is a system-cancellable background task, so it must not upload at all — it stages bytes and the app uploads (`SHARE_EXTENSION.md` F2–F3) |
> | #2 Live Activities | `ActivityViewContext` is four members handed to the renderer and `ActivityConfiguration` takes **no provider**; every mutation enters via `Activity.request`/`update` in the app, or via APNs (`LIVE_ACTIVITIES.md` F7) |
>
> The common shape — **the app owns the network; the out-of-process surface owns only a
> file in the shared container** — was arrived at independently three times, which is
> better evidence than any one of them alone.
>
> **All four rows are now filled in (updated 2026-09-19).** The fourth was added after the
> other three and is the strictest of them: a Live Activity extension does not own even a
> file, because there is no API through which it could fetch one. So the third reason above
> — "four capabilities need it" — is false for four of four, not three of four. **Nothing
> in the audited set requires this entitlement.**
>
> **Recommendation: do not build this as a foundation item.** Build the App Group, which
> every extension genuinely shares and which carries no credential. Leave
> `keychain-access-groups` unbuilt until a capability's design actually requires a
> credential outside the app process, and make that capability carry the justification,
> the migration and the release.
>
> The argument for building up front was "four capabilities need it, so decide it once
> rather than four times." The better outcome is that nothing needs it, so it is decided
> zero times — and the app never holds an entitlement that lets another binary read the
> user's refresh token. Note that this *strengthens* the reasoning below rather than
> contradicting it: the reason to hesitate was always that the failure mode is a silent
> mass sign-out, and the cheapest way to not have that failure mode is to not make the
> change.
>
> **Two things here survive the challenge and must not be lost.** The migration analysis
> (read-old/write-new, step 4 strictly before step 5) is still exactly right if the
> entitlement is ever adopted, and re-deriving it under deadline is how the mass sign-out
> happens. And the hardware experiment flagged below as this document's load-bearing gap
> stays owed — by whoever eventually needs the entitlement, not by the extension
> foundation.
>
> Full argument: `SHARE_EXTENSION.md` Finding 4.

Three reasons:

- The failure mode is a silent mass sign-out, and it should not be discovered
  inside a diff whose subject line is "add widget." A change that can log out
  every user deserves a release where that is the only thing being risked.
- It is device-only to validate, so it has a scheduling dependency the widget
  work does not.
- It is genuinely shared. Four capabilities need it, and doing it inside the
  first one means the second, third and fourth inherit a design decided by
  whichever happened to go first — which is the exact "build it once as a
  deliberate foundation, not four times as a side effect" point the entitlements
  doc already makes.

**Not decided here**, and deliberately left for when the first extension has a
concrete shape: whether the extension reads the session envelope directly or
reads a *narrower*, purpose-built item. Handing a widget the live refresh token
so it can render a follower count is more authority than the job needs, and a
read-only projection written by the app would be a smaller blast radius. That
choice does not change any of the plumbing above, so it does not gate it.

---

## What was verified, and what was not

**Verified by reading:** the absence of `accessGroup` in `KEYCHAIN_OPTIONS`
(`sessionStore.ts:39-42`); the entitlements file's two keys (`plutil -p`);
`accessGroup` support in `expo-secure-store@15.0.8` at both the TypeScript
(`SecureStore.d.ts:76-80`) and native (`SecureStoreModule.swift:187-188`) layers;
that one query builder (`:172`) serves get, set and delete (`:44-46`, `:90`,
`:117-118`); the existing v1→v2 biometric migration precedent (`:21-38`); the
signed-out-on-unreadable-keychain degradation (`:62-73`); and that
`src/session/` is **not** a protected path in
`config/realtime-audio-protected-paths.json`.

**Not verified — and this is the load-bearing gap.** The claim that a read with
`kSecAttrAccessGroup` set will not match an item written without one is standard
iOS keychain behaviour and follows from the code paths above, but it has **not
been demonstrated on a device here**. Everything in the migration section
depends on it. Before any of this ships, it should be proven directly: write an
item with no access group, then attempt to read it with one, on real hardware
with a real profile. If that read unexpectedly succeeds, the fallback is
unnecessary; if it fails as expected, the fallback is mandatory. Either way it is
a twenty-minute experiment that removes the only assumption this document rests
on, and it cannot be run on the simulator.
