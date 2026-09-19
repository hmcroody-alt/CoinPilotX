# Core Spotlight — what may be indexed, and what must never be

Written 2026-09-19. `PULSESOC_APPLE_NATIVE_CAPABILITY_AUDIT.md` §4 rates Core Spotlight the
best value-per-unit-effort in the whole Apple plan, and then makes it conditional:

> **Prerequisites** — an explicit written policy on what is indexable. That policy is the
> deliverable, not the code.

This is that policy. It is written before any indexing code exists, which is the only order
that works: an index is trivial to add and its mistakes are silent, durable, and outside the
app's own gates.

---

## The property that decides everything below

A Spotlight entry is **a copy of content that outlives the permission it was derived from.**

Every privacy gate in PulseSoc is evaluated *at fetch time*, server-side, against the
session making the request. `profile_visibility` is checked when the profile is served
(`bot.py:108916`). A block is applied when the content is queried. An Office read requires a
grant minted for that session and device. All of these produce a decision that is correct at
the instant it is made.

An index entry persists that decision indefinitely, in a database owned by iOS, readable
from the home screen by anyone holding the unlocked handset. Nothing re-evaluates it. If the
app indexes a profile and the owner later goes private, or blocks the viewer, or deletes the
account, **the index still has it**, and nothing in the original gate knows the copy exists.

So the question is never "is the user allowed to see this right now." It is:

> Would it still be acceptable for this text to be readable from the home screen a year from
> now, after every permission that justified it has been revoked?

For most of what a social app holds, the answer is no. That is why this policy is short on
what may be indexed and long on what may not.

---

## Deny by default — and the repo already argued this

`mobile-native/src/core/storageScope.ts` had this exact fight about local storage and
resolved it by inverting the rule. Its reasoning transfers without modification:

> The root cause is the direction of the list, not its contents. An allowlist of things to
> delete has to be updated by someone who remembers it exists, and the failure mode when
> they forget is a privacy leak that nothing reports.

**Decision: the indexable set is an allowlist, enumerated here, and everything else is
excluded.** A new content type added next year is not indexed until someone adds it to this
document with a justification. The cost of forgetting is a missing search result, not a leak.

This is the opposite direction from `storageScope`'s inversion, and deliberately so. There,
the default had to be *deletion*; here, the default has to be *non-publication*. Both are
"the safe thing happens when someone forgets."

---

## The allowlist

Only three things, and each earns its place by being content the user themselves authored or
deliberately collected, whose exposure is not revocable by a third party.

> **Scope widened 2026-09-19 — this allowlist is no longer only about Spotlight.**
>
> Two later capabilities turned out to raise the identical question, and re-deriving the
> answer for each would be three chances to get it differently:
>
> | Capability | The surface | Source |
> |---|---|---|
> | Core Spotlight (#4) | an index entry readable from the home screen | this document |
> | App Intents (#3) | `authenticationPolicy` defaults to `alwaysAllowed`, so an intent runs on a locked phone | `APP_INTENTS_SIRI_SHORTCUTS.md` Finding 3 |
> | WidgetKit (#12) | an `accessory*` widget renders on the **lock screen**, continuously | `WIDGETKIT.md` Finding 3 |
>
> **Decision: the allowlist and denylist below govern all three.** Nothing goes into a
> Spotlight index, an intent's user-visible result, or a widget's rendered content unless it
> appears in the table beneath this note.
>
> This is not a widening of what may be exposed — it is the same three rows applied to two
> more surfaces. It has one immediate consequence worth naming, because it decides a product
> question without a new argument: an **unread-message count** is a derived signal about the
> denied "direct messages and conversations" row, so the unread-count widget is denied by a
> policy that already existed. The reasoning for denying DMs — authored by someone who
> consented to one reader inside one app — applies at least as strongly to a count on a lock
> screen as to message text in a search index.
>
> The three purge triggers below also apply per-surface; `WIDGETKIT.md` Finding 4 records the
> widget-specific trap, which is that deleting the data does not clear the rendered surface.

| May be indexed | Why it is safe | Constraint |
|---|---|---|
| The signed-in user's **own** profile | Their own content. No third party can revoke it, and it is already public-by-default (`profile_visibility` defaults to `'public'`, `bot.py:115653`). | Only while `profile_visibility = 'public'`. Purge the entry the moment it flips to `private`. |
| The user's **saved library** (`pulsesoc.native.saved.library`, `src/api/saved.ts:5`) | A deliberate, explicit act of collection by the user, on their own device. | Title and thumbnail only — never body text of another user's post. |
| **Education / help articles** (`/education/*`, already an AASA-claimed path) | First-party published content. Public by construction, identical for every user, revocable by nobody. | None. This is the only genuinely unconditional entry. |

Note what the third row buys: it is the one category that needs no purge, no privacy check
and no staleness reasoning, because it is not user data at all. If Spotlight ships in one
stage, it should ship as this row alone.

---

## The denylist, with reasons

| Never indexed | Why |
|---|---|
| **Anything in Private Office** | See below. This is the categorical one. |
| Direct messages and conversations (`pulsesoc.native.messenger.v2.conversations`) | The content most damaging to leak and the least defensible to copy. A DM is authored by someone who consented to *one* reader inside *one* app, not to a device-global index. |
| Any other user's profile, post, reel or status | Revocable by that user at any time via block, going private, or deletion — and the index cannot hear any of those events. This is the staleness property in its purest form. |
| Recent searches (`pulsesoc.native.search.recent`, `src/api/search.ts:5`) | Queries are often more sensitive than results, and surfacing them in the system search UI is a confusing loop besides. |
| Marketplace / commerce conversations | Same argument as DMs, plus transaction detail. |
| Anything derived from a user the viewer has blocked, or who has blocked them | The block model is server-side (`blocked_users`, `bot.py:120456`) and the client has no reliable local view of it. An index cannot enforce a rule it cannot evaluate. |
| Drafts and unsent composer content | Not published by the user even inside the app. Indexing it would make a half-written post searchable before it is a post. |

### On `profile_visibility` specifically

The audit says indexing "must respect `users.profile_visibility`." That is correct but not
sufficient, and the gap is worth naming so nobody implements the letter of it.

`profile_visibility` is **binary and profile-scoped** — the only accepted values are
`public` and `private` (`bot.py:108745`). It governs whether the *profile* is viewable. It
says nothing about the visibility of any individual post, message, or Office item belonging
to that user. A check that reads "is this user public? then index their content" is a
faithful reading of the audit sentence and is wrong.

The allowlist above sidesteps this entirely by never indexing another user's content under
any visibility setting.

---

## Private Office is categorical, and the reason is instructive

`mobile-native/src/privateOffice/officeLock.ts` describes a design that goes out of its way
to leave nothing on disk:

> the grant lives in plain module memory: memory is exactly as durable as an unlock should
> be, and a token that never touches disk cannot be exfiltrated from a backup or read by the
> next account to sign in on this device.

Every Office read requires a server-minted grant bound to the session and device. Face ID is
explicitly "convenience, never authority." There is deliberately no local "unlocked" bit to
flip, so patching the binary gains an attacker nothing.

A Spotlight entry for Office content would defeat all of it at once. It is a plain-text copy
on disk, readable from the home screen with no grant, no passcode and no biometric, included
in device backups, and surviving the account switch the grant model is specifically built to
survive. Every property that design bought would be given back by one `CSSearchableItem`.

**Nothing under `privateOffice/` is indexable, and no future exception is contemplated.** If
Office ever wants search, it is in-app search behind the existing grant, not Core Spotlight.

---

## Purge

### Where it hooks

`mobile-native/src/media/mediaSessionCleanup.ts` already exists to remove user-scoped local
state, and as of `53ec3679` it runs on **every** path that ends a session — the two sign-out
functions plus the four paths where a session dies without one (refresh rejection, QA
rejection, and an envelope/refresh `userId` disagreement, which is an account switch
discovered mid-request).

**Decision: the Spotlight purge goes inside `clearUserScopedMediaState()`, not beside it.**
That single placement inherits all six paths and, more importantly, inherits every path
added in future by whoever next has to end a session. A purge wired up separately would have
had exactly the bug that commit fixed — and would have had it silently.

This ordering matters: the purge placement was not safe to decide until those paths were
unified, which is why that fix landed first.

### What the purge call is

`CSSearchableIndex.default().deleteAllSearchableItems()` — everything, not the signing-out
account's domain.

The precedent is directly on point. `mediaSessionCleanup.ts:16-19` clears every account's
media cache rather than just the departing one, because "a handset that has hosted three
accounts should not still hold the first two's private media because only the third bothered
to sign out." Identical reasoning applies: a domain-scoped delete leaves the first two
accounts' entries indexed forever, since nothing will ever again run cleanup on their
behalf.

### The other purge triggers

Purging at sign-out is necessary and not sufficient. Also required:

| Trigger | Why |
|---|---|
| The user's own profile flips to `private` | The one allowlist row that is revocable. |
| An item leaves the saved library | Un-saving must remove the entry, or un-save becomes cosmetic. |
| App launch, if the index is non-empty and nobody is signed in | Repairs any purge that failed or was interrupted mid-flight. Cheap, and the only defence against a purge that threw. |

---

## What must be true before any code ships

1. This document is the policy; the allowlist is enumerated in code as a single constant
   with each entry pointing back here, not spread across call sites.
2. ~~`NSUserActivityTypes` is added to `Info.plist` — the one Info.plist key this needs.~~
   **Corrected 2026-09-19: no Info.plist key is required.** `CSSearchableItemActionType` is
   a system-exported constant, and `NSUserActivityTypes` gates cross-device continuation,
   not local Spotlight taps (`CORE_SPOTLIGHT.md` Finding 1). The rest of the original item
   stands: no entitlement, no App Group, no portal work. The real prerequisite is the
   AppDelegate activity-type switch — `CORE_SPOTLIGHT.md` Finding 2 — without which a
   Spotlight tap foregrounds the app and navigates nowhere.
3. The purge lives inside `clearUserScopedMediaState()`.
4. A test asserts the denylist directly — that indexing a DM, another user's profile, or
   anything Office-scoped is rejected — rather than only asserting the allowlist works. A
   test suite that only proves indexing succeeds is satisfied by indexing everything.
5. Feature-flagged off by default, per the mission's standing rule.

---

## What was verified, and what was not

**Verified by reading:** `profile_visibility` accepts only `public`/`private`
(`bot.py:108745`) and defaults to `'public'` (`bot.py:115653`); it gates profile reads at
`bot.py:108916`; `blocked_users` exists (`bot.py:120456`); the local cache keys named above
(`src/api/saved.ts:5`, `src/api/search.ts:4-5`, `src/api/messenger.ts:20-29`); the Private
Office grant model (`privateOffice/officeLock.ts:1-32`); that `clearUserScopedMediaState`
clears all accounts deliberately (`mediaSessionCleanup.ts:16-19`); and that it now runs on
all six session-end paths (`53ec3679`).

**Not verified.** No Core Spotlight code exists, so nothing here has been exercised. Two
specific assumptions are worth stating as assumptions:

- That `deleteAllSearchableItems()` on a fresh install with an empty index is a cheap no-op,
  which the launch-time repair trigger relies on. Untested here.
- That a `CSSearchableItem` is included in an encrypted device backup. The argument against
  indexing Office content does not depend on it — the home-screen readability point stands
  alone — but the backup claim specifically is reasoning from documented behaviour, not
  something demonstrated on hardware in this session.

Neither gates the policy. Both should be checked by whoever writes the first indexing code.
