# Device security — the keychain inventory, and the one item that had no policy

Written 2026-09-19. Capability #11 in `PULSESOC_APPLE_NATIVE_CAPABILITY_AUDIT.md`,
where the verdict is *PARTIALLY IMPLEMENTED — and the implemented part is
genuinely good*. This document is the evidence behind that verdict: the complete
inventory of what this app puts in the keychain and under which policy, the
mechanics that make the policy work, and the two things that were wrong.

The audit's recommendation for this capability is **"do not touch the working
parts"**, and it gives the reason: this area has the worst failure mode in the
whole plan, because a mistake here breaks *existing* working authentication
rather than failing to add a new feature. Everything below respects that. The one
behavioural change landed with this document is on the single item that had no
stated policy at all, and it is the narrowest change that gives it one.

---

## The inventory

Every keychain item the app writes, with the options it writes them under. Four
modules, ten `SecureStore.setItemAsync` call sites, and this is all of them — the
eleventh row is a legacy key that is read and deleted but never written.

| Key | Owner | `keychainService` | Accessibility | Authenticated |
|---|---|---|---|---|
| `…session.cookie` | `session/sessionStore` | `…app.session` | `AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY` | no |
| `…session.envelope.v1` | `session/sessionStore` | `…app.session` | same | no |
| `…session.user` | `session/sessionStore` | `…app.session` | same | no |
| `…session.biometric.userId` | `session/sessionStore` | `…app.session` | same | no |
| `…session.biometric.envelope.v2` | `session/sessionStore` | `…app.biometric` | same | **yes** |
| `…session.biometric.envelope.v1` | `session/sessionStore` | `…app.session` | same | no — read-only legacy |
| `…office.device.v1` | `privateOffice/officeLock` | `…app.office` | same | no |
| `…office.biometric.userId` | `privateOffice/officeLock` | `…app.office` | same | no |
| `…office.passcode.v1` | `privateOffice/officeLock` | `…app.office.biometric` | same | **yes** |
| `…push.installation_id` | `api/installationId` | *(default)* | same | no |
| `…push.registration` | `api/push` | *(default)* | same — **as of this commit** | no |

All keys are prefixed `pulsesoc.native.`, which is the namespace
`core/storageScope.ts` sweeps on sign-out. Every service name is `__DEV__`-split
between `com.pulsesoc.app.*` and `com.pulsesoc.nativeapp.dev.*`, so a development
build installed alongside a production build cannot read its items.

Two things the table is deliberately showing:

- **Not one item uses the default accessibility.** That matters more than it
  looks; see the next section.
- **The two authenticated items each sit on a service of their own.** Neither
  shares with an unauthenticated item.

One key that looks like it belongs here and correctly does not: the Private
Office relock preference (`…office.relock.v1`) is an ordinary `AsyncStorage`
setting — it is a duration, not a secret, and the server's grant TTL keeps
ticking regardless of what the device claims about elapsed time.

The Private Office's *actual* unlock grant is not in this table because it is
never persisted — `officeLock.ts` keeps it in plain module memory, on the stated
reasoning that "memory is exactly as durable as an unlock should be, and a token
that never touches disk cannot be exfiltrated from a backup or read by the next
account to sign in on this device."

---

## Why the accessibility class is the whole policy

`expo-secure-store` defaults to `kSecAttrAccessibleWhenUnlocked`
(`SecureStoreOptions.swift`: `keychainAccessible: SecureStoreAccessible = .whenUnlocked`),
which makes an item **unreadable while the screen is locked**. That is a
defensible default for a library and the wrong one for most items in this app,
for the reason `api/installationId.ts:41-54` sets out:

> Every moment this id is actually needed is a locked moment: a VoIP push
> arrives, CallKit rings on the lock screen, the user answers without unlocking,
> and the accept request has to name the device that answered.

`THIS_DEVICE_ONLY` is the second half of the decision, and it is not about lock
state at all — it blocks the item from migrating to another device through the
iCloud keychain or an encrypted backup. For a credential that is theft
prevention; for the installation id it prevents a subtler bug, because an id that
synced would name the wrong phone, "which is the exact confusion it exists to
prevent."

The failure mode of getting this wrong is that **nothing visibly breaks**. Every
keychain read in this app is wrapped in a `.catch()` that degrades to "nothing
stored", because that is the right thing to do on a startup path. So an item with
the wrong accessibility does not throw; it reads as absent, and whatever depended
on it silently takes the not-configured branch.

`installationId.ts` is the one place that refuses to collapse those two
facts, and its comment explains why the distinction is load-bearing: `searchKeyChain`
returns null only for `errSecItemNotFound` and *throws* for every other status,
so a throw means the keychain refused — on iOS, overwhelmingly a locked device.
Minting a new id on a refusal would overwrite the real one.

---

## What the separate services actually buy — corrected

The repo states a rule in three places: *never put an authenticated and an
unauthenticated item in the same keychain service.* The rule is right and the app
follows it. The stated reason was imprecise, and the precise version is more
useful, so it is worth recording.

Reading `SecureStoreModule.swift` from the installed
`expo-secure-store@15.0.8`, the library **already namespaces by authentication**.
`query(with:options:requireAuthentication:)` appends a suffix:

```swift
var service = options.keychainService ?? "app"
if let requireAuthentication {
  service.append(":\(requireAuthentication ? "auth" : "no-auth")")
}
```

and `get` searches three buckets in a fixed order (`:69-84`) — `:no-auth` first,
then `:auth`, then the unsuffixed legacy service.

So two **different** keys with different `requireAuthentication` values do not
collide even on one service; they land in genuinely separate buckets. The hazard
the rule removes is narrower and sharper: the **same key** written both ways.

1. A read prefers the unauthenticated copy. While both exist, the Face ID prompt
   never happens — the protected copy is unreachable, not merely second.
2. A successful `SecItemAdd` deletes the opposite alias (`:117-118`). So an
   unauthenticated write does not shadow the protected copy, it **destroys** it.

Giving each authenticated item its own service makes that collision structurally
impossible rather than merely unlikely, which is why it is the right rule even
though the library's suffixing already handles the easy case.

The library's cleanup only touches aliases of *the key being written*, which is
exactly why `sessionStore`'s v1 → v2 biometric migration has to delete the old
key by hand:

> The unprotected v1 copy is a standing bypass if it survives: expo-secure-store
> searches unauthenticated items *first*, so leaving it behind would let every
> later read succeed with no prompt at all.

That is the same failure in its cross-key form, where the library cannot help.

---

## What `requireAuthentication` actually gets you

Worth stating because it is the difference between real biometric protection and
decorative biometric protection. `set` with `requireAuthentication` builds a
`SecAccessControl` with **`.biometryCurrentSet`** (`SecureStoreModule.swift:105`),
which means:

- iOS — not the app's JavaScript — refuses to return the item without a live
  face match, so patching out the app's own prompt gains an attacker nothing;
  and
- iOS discards the item outright when the enrolled biometric set changes, so
  enrolling another face invalidates the saved credential for free.

It also requires `NSFaceIDUsageDescription` in Info.plist or the write throws
(`:100-102`) — present.

Both authenticated items are *convenience over an authority that lives
elsewhere*, which is the right shape. The biometric session envelope is
deliberately **not** the live session envelope, so cold-start auto-refresh cannot
resume a session without Face ID. The office passcode still goes to
`/unlock` like a typed one, because the server mints every grant — "a device with
Face ID patched out gains nothing because there is no local 'unlocked' bit to
flip."

---

## The two things that were wrong

Both were prose, and both were prose attached to security-relevant code — which
is the fourth time in this plan that **a description has outlived the thing it
describes**. Unlike the earlier three, one of these had a real (if latent) code
consequence.

### 1. The ownership docstring was wrong in three ways

`mobile-native/src/native/secureStore.ts` is the Phase 46 owner module for
`expo-secure-store`, and its docstring claimed:

> exactly one module in `src/` names `expo-secure-store`, which is what the
> Phase 46 guard in `native/__tests__/nativeOwnershipGuard.test.ts` checks.

The guard permitted three, by its own regex:

```ts
owner: /^(native\/|session\/sessionStore|api\/push)/
```

and both `session/sessionStore.ts` and `api/push.ts` did import the module
directly. It also said each caller holds its items "under its own
`keychainService`" — `api/push.ts` set none, and shares the library default with
`api/installationId.ts`.

**Fixed by making reality match the sentence**, not by softening the sentence:
both modules now import through the owner, and the guard is tightened to
`owner: /^native\//`, which is the same single-owner shape every other capability
in that file uses. The import change is source-only — `secureStore.ts` is
`export * from "expo-secure-store"` — so no behaviour moves with it.

### 2. One keychain item had no stated policy at all

`api/push.ts` wrote its cached push registration with **no options**:

```ts
await SecureStore.setItemAsync(PUSH_REGISTRATION_CACHE_KEY, JSON.stringify(registration))
```

so it inherited `whenUnlocked` and was unreadable on a locked device. Traced to
its consequences rather than assumed:

- `registerPushDevice` (`push.ts:140`) reads the cache to find the *previous*
  endpoint and revoke it when the token has rotated. A null cache skips the
  revoke, leaving an orphan endpoint registered on the backend.
- `unregisterPushDevice` (`:201`) reads it for the endpoints to revoke on sign
  out. It also fetches the live token independently, so the current endpoint is
  still revoked — only the cached variants and the installation-id hint are lost.

**Honest exposure: low, and not by design.** All four callers are foreground,
user-initiated actions — two settings screens and the two sign-out paths — so
the device is unlocked whenever the cache is read. It is also not reachable from
the locked VoIP path, because `api/calls.ts` deliberately does not import
`push.ts` (it would pull in a module-scope `setNotificationHandler`; see
`installationId.ts:9-13`).

That is a property of today's callers, not of the item. Background Tasks (#6) is
a planned capability in this same plan, and a `BGAppRefreshTask` that refreshes a
push registration is precisely the reader that would turn this from a latent trap
into a live bug. So it was fixed now, while it is cheap:

```ts
const KEYCHAIN_OPTIONS = {
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY
} as const;
```

**Deliberately no `keychainService`.** Adding one would move the item to a new
service and make every existing cached registration unreadable in a single
upgrade — a silent cache wipe that produces the orphan-endpoint outcome above for
every install at once. Sharing the default service with the installation id is
safe because both are unauthenticated, per the mechanics above. The accessibility
change needs no migration of its own: `cachePushRegistration` rewrites the item
on every successful registration, so it self-heals, the same way
`installationId`'s explicit `upgradeAccessibility` does.

---

## The guard that keeps this true

A paragraph saying "always set `keychainAccessible`" is exactly the kind of prose
this plan keeps finding rotted. So the rule is now executable:
`mobile-native/src/native/__tests__/keychainPolicyGuard.test.ts` reads the source
of every `SecureStore.setItemAsync` in `src/` and fails if any of them omits an
accessibility class.

It resolves the three shapes actually in use — an inline literal, a bare
identifier naming a module-scope options const, and a spread of one with extra
fields (`{ ...BIOMETRIC_KEYCHAIN_OPTIONS, authenticationPrompt }`) — and it walks
brackets rather than matching a regex, because two real call sites span multiple
lines and one passes a `JSON.stringify(...)` whose own parentheses and comma
would end a regex match early. A regex that stopped there would read the *second*
argument as the options object and pass a call that has no options at all.

**It was proved to discriminate, not merely to be green.** Three ways:

- A negative control inside the test file: a bare two-argument write is reported,
  each of the three valid shapes is not, and a named const that sets
  `keychainService` but *not* accessibility is still reported — otherwise the
  identifier branch would launder every future omission.
- A vacuity check: the analyser must find at least the ten call sites that exist,
  so a rename or a wrapper that leaves zero matches fails loudly instead of
  passing trivially.
- Against the real tree: reverting the `push.ts` fix turns it red, naming the
  exact call site. Restored immediately after.

---

## The plaintext fallback, and why its gate is what it is

`sessionStore` has an `AsyncStorage` fallback for when the keychain is
unwritable, which on a real device would mean writing a refresh token to
unencrypted storage. It is gated on `isLocalQaSession()` — a check on
`PULSE_API_BASE_URL` being `127.0.0.1` or `localhost`, and nothing else.

It is pointedly **not** gated on "are we on a simulator". That gate existed, and
is strictly wider: a simulator pointed at `pulsesoc.com` holds a real production
refresh token, so a device check would write one to unencrypted storage. The
accepted cost is that an unprovisioned simulator pointed at production loses its
session across cold starts.

Off-QA the fallback is absent rather than silent-failing into something worse:
the write is swallowed, the session simply does not persist across a cold start,
and nothing lands outside the keychain. This is why the audit's claim that
"AsyncStorage holds only non-sensitive cached metadata plus an enrollment marker
— no tokens" holds for any build that talks to production.

---

## What sign-out does not sweep, and why that is right

`core/storageScope.ts` deletes everything under `pulsesoc.native.` on sign-out
except an explicit keep-list, on the stated principle that "the default has to be
deletion" and an entry that cannot justify itself belongs on the other side of
the line. Two entries matter here:

- `pulsesoc.native.push.` — because `unregisterPushDevice` runs **first** on the
  sign-out path and reads the cached registration in order to revoke the endpoint
  remotely. Sweeping it first would destroy the token that call needs and leave
  the handset registered for an account that has left it. This is the same cache
  the fix above applies to, and it is a good illustration of why its readability
  is not a private detail of `push.ts`.
- `pulsesoc.native.session.` — because `signOut({ clearBiometrics })` owns the
  decision about the Face-ID-gated refresh token. Overriding that from a cache
  sweep would turn every ordinary sign-out into a full biometric
  un-enrollment.

---

## What is genuinely missing

The audit's gap list for this capability is short and this verification does not
lengthen it:

| Gap | Status |
|---|---|
| Secure Enclave key (`SecKeyCreateRandomKey` + `kSecAttrTokenIDSecureEnclave`) | absent — no device-resident private key, so no request signing and no hardware proof of possession |
| Certificate pinning | absent |
| `keychain-access-groups` entitlement | absent — decided separately in `DECISIONS_KEYCHAIN_ACCESS_GROUP.md`, which is the blocker for four later capabilities |

`requireAuthentication` already puts two items behind a Secure Enclave-backed
access control, so "Secure Enclave" is not wholly unused — what is missing is a
key the app *owns* and can sign with. That is a Wave 2+ item and it needs a
backend verification endpoint to be worth anything, which is the same shape as
App Attest (#1) and should be decided alongside it rather than separately.

---

## Standing risks

| Risk | Why it bites | Signal |
|---|---|---|
| A new keychain item written with no options | Inherits `whenUnlocked`; reads as absent on a locked device and every read here degrades silently | `keychainPolicyGuard.test.ts` fails in CI |
| An authenticated item added to an existing service | The same-key collision above: an unauthenticated write deletes the protected copy | none today — the rule is prose plus the inventory in this file |
| A `keychainService` changed on an existing item | Silently orphans every stored copy; looks like "the feature never ran here" | none — and this is why the push fix deliberately did not add one |
| The access group landing without the migration pattern | Items written without `accessGroup` are not readable with one | covered in `DECISIONS_KEYCHAIN_ACCESS_GROUP.md` |

The second row is the one with no alarm attached. A guard for it would have to
map key → service → authentication across the whole tree and assert no service
mixes the two; that is a natural extension of `keychainPolicyGuard.test.ts` and
is **recommended, not done**.

---

## What was verified, and what was not

**Verified by running:** `keychainPolicyGuard.test.ts` (new) plus
`nativeOwnershipGuard.test.ts`, `src/session/__tests__/*`,
`api/__tests__/installationId.test.ts` and
`privateOffice/__tests__/officeLock.test.ts` — **12 suites, 134 tests, all
passing** after the import rewires. The new guard was additionally shown to go
red against the real tree with the `push.ts` fix reverted. `tsc --noEmit` is
clean apart from three pre-existing `pulse-apple-translation` resolution errors
in files untouched here (that local module is not installed into
`node_modules`).

**Verified by reading:** every `SecureStore` call site in `mobile-native/src`
(ten writes, and their reads and deletes) and the options each one passes; the
installed `expo-secure-store@15.0.8` Swift implementation — the `whenUnlocked`
default in `SecureStoreOptions.swift`, the `:auth`/`:no-auth` service suffixing
and the three-bucket read order (`SecureStoreModule.swift:69-84`, `:172-176`),
the opposite-alias delete after a successful `SecItemAdd` (`:117-118`), and the
`.biometryCurrentSet` access control plus the `NSFaceIDUsageDescription`
requirement (`:97-109`); the QA-only plaintext fallback gate in
`sessionStore.ts`; and the sign-out keep-list in `core/storageScope.ts`.

**Not verified.** No device test was performed. Specifically:

- **No locked-device read was exercised.** The central claim — that
  `AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY` is what lets the installation id be read
  while CallKit rings on the lock screen — is taken from the Apple semantics of
  the accessibility constant and the mapping in `attributeWith(options:)`, not
  from a measurement on a locked iPhone 16 Pro. The same goes for the push cache
  fix.
- **The `push.ts` accessibility change was not observed self-healing on a real
  install.** The rewrite-on-every-registration argument is read from the code
  path, not watched.
- **No biometric behaviour was tested on hardware.** That `.biometryCurrentSet`
  discards the item when the enrolled set changes is Apple's documented
  behaviour, not something demonstrated here by enrolling a second face.
- The service-name `__DEV__` split is read from the source; no attempt was made
  to install a development and a production build side by side and confirm the
  isolation empirically.
