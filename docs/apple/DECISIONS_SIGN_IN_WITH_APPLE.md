# Sign in with Apple — the decisions, before the code

Written 2026-09-19. Sign in with Apple is the next Wave 2 item in
`PULSESOC_APPLE_NATIVE_CAPABILITY_AUDIT.md` — highest value, no extension target,
no interaction with any audio protection lock. This document settles the
questions that have to be answered *before* an endpoint exists, because two of
them are one-way doors: where the Apple identifier is stored, and what happens
when Apple hands back an email that already belongs to somebody.

Nothing here is implemented. Every claim carries a `file:line` so the next person
can check it rather than trust it.

---

## The starting position: greenfield, and that is unusual

There is **no Apple sign-in code anywhere** — no `expo-apple-authentication` in
`mobile-native/package.json`, no `com.apple.developer.applesignin` in
`PulseSoc.entitlements`, no `usesAppleSignIn` in `app.json`, no
`AuthenticationServices` import. Case-insensitive sweeps for `ASAuthorization`,
`identityToken` and `appleid.apple.com` return only documentation and unrelated
IAP prose (`services/business_os/entitlements/premium_api.py:152` is about
Apple-*signed* receipts, not auth).

More surprising: **there is no third-party login of any kind.** No Google, no
Facebook, no magic link, no OTP. The only credential path is a password
(`check_password_hash`, `bot.py:7530`) plus biometric re-unlock of a locally
stored envelope (`mobile-native/src/session/biometricAuth.ts`). The `users` table
carries `telegram_user_id` / `telegram_username` (`bot.py:115556-115558`), but no
login path consumes them.

Two consequences, and they pull in opposite directions.

**It means SIWA is probably not compelled.** App Store Review Guideline 4.8
("Login Services") bites when an app *uses* a third-party or social login
service, requiring an equivalent privacy-preserving option alongside it. PulseSoc
uses none, so the trigger condition is absent. *This should be re-read against
the current guideline text before it is relied on in a review response* — it is
the kind of rule Apple revises — but on the present reading SIWA here is a
**conversion decision, not a compliance obligation.** That matters: a
compliance-driven SIWA can be minimal and defensive, whereas a conversion-driven
one has to actually be good, and is allowed to be deferred if something else
converts better.

**It also means there is no precedent to copy.** There is no "find-or-create an
external identity" helper to extend, no reviewer intuition about account
linking, and no existing answer to "two credentials, one human." Every structural
choice below is being made for the first time, and whatever is chosen becomes the
pattern the next provider inherits.

---

## Decision 1 — a new `/api/mobile/auth/apple` route, not a branch inside `/login`

`POST /api/mobile/auth/login` (`bot.py:7504`) cannot be reused, and the reason is
not stylistic. It runs four gates in sequence, and a SIWA user fails two of them
by construction:

| Gate | Line | SIWA user |
|---|---|---|
| account restriction | `bot.py:7523` | fine — must be preserved |
| email confirmed | `bot.py:7527` | **fails** — no confirmation flow ever ran |
| password present + correct | `bot.py:7530` | **fails** — there is no password |
| failed-login velocity | `bot.py:7513` | fine — must be preserved |

Threading a `provider` flag through that function would mean a conditional
bypass of the password check living inside the password login handler. That is
the shape of code that later gets one condition wrong and becomes an
authentication bypass. A separate route that *never* accepts a password is
structurally safer than one that sometimes ignores it.

One subtlety worth recording, because it is easy to misread the source: the email
gate is `if user.get("email") and not int(user.get("email_verified") or 0)`
(`bot.py:7527`). It is conditioned on *having* an email. An account with no email
at all passes it. Accounts with no email are already legal —`create_account`
accepts phone-only (`bot.py:7565`). So the schema already tolerates the exact
shape a Hide My Email user can decay into.

**What the new route must return.** Login ends with three lines
(`bot.py:7574-7576`) that emit *three* auth artifacts at once — a bearer access
token and refresh token from `issue_mobile_security_tokens` (`bot.py:32100`), a
Flask session cookie, and a persistent refresh cookie via
`set_persistent_session_cookie` (`bot.py:7576`). The
SIWA route must emit all three identically. Emitting only the bearer produces an
app that looks signed in and then fails every cookie-authenticated path — a bug
that would present as random 403s rather than as a login failure.

Gate obligations, all default-deny: the route needs `@public_route(reason=...)`
from `services/route_auth.py:314` or
`tests/protection/test_route_auth.py:161` (`test_new_routes_must_declare_their_auth`)
fails the whole protection suite — a gate inside the function body does not
count. Any new `os.getenv` must be added to `.env.example` or
`tests/protection/test_environment_contract.py` fails. And per
`tests/protection/test_signing_key_separation.py`, the route must issue tokens
through the existing signing path rather than introducing a third secret.

---

## Decision 2 — the Apple identifier goes in a **new table**, not a column on `users`

The `users` table has nowhere to put an Apple `sub` today. The choice is a new
column (`users.apple_sub`) or a provider-identity table. **Take the table.**

Three reasons, in descending order of how much trouble they save:

**`users` is created twice and the second one wins.** `CREATE TABLE ... users` at
`bot.py:919` and again at `bot.py:115548`, then widened by `add_columns_if_missing`
at `bot.py:115568+`. This is the same double-definition hazard `CLAUDE.md` already
documents for `webhook_app`. A column added to the wrong definition is silently
discarded, and the failure surfaces as an `UndefinedColumn` in production long
after the commit. A new `CREATE TABLE IF NOT EXISTS` has exactly one home and
cannot be added to the wrong one.

**One user will eventually have more than one identity.** A column models
one-provider-forever. The moment Google or a second Apple bundle id appears, a
column becomes a migration and a table becomes a row.

**The relay email is not the account email.** Apple's Hide My Email returns a
`@privaterelay.appleid.com` address that the user can revoke at any time. Storing
it in `users.email` conflates "the address Apple gave us" with "the address this
person wants mail at," and the two have different lifetimes. The table holds the
relay separately, as provenance.

Sketch, to be written idempotently in `init_db()` per project convention:

```
user_external_identities
  provider          TEXT     -- 'apple'
  provider_subject  TEXT     -- Apple's `sub`; opaque, and stable per developer *team*
                             -- (see the qualification below — it is not permanent)
  user_id           INTEGER
  email_at_link     TEXT     -- what Apple returned; may be a relay, may be NULL
  is_private_relay  INTEGER
  linked_at         TEXT
  UNIQUE (provider, provider_subject)
```

**The `UNIQUE (provider, provider_subject)` is the load-bearing line.** It is the
one thing preventing two accounts from claiming the same Apple identity under
concurrent first-sign-ins, and it must be enforced by the database, not by an
application pre-check — see Decision 3 for why that distinction is not academic
here.

---

## Decision 3 — link to an existing account **only** on a verified, non-relay email match

This is the one-way door and the only genuine security decision in the set.

Apple returns `email` and `email_verified` *on the first authorization only*.
On every subsequent sign-in you get the `sub` and nothing else. So the linking
question is answered once per user, permanently, on a single request.

**The policy:**

| Apple returns | Existing account with that email | Action |
|---|---|---|
| verified, real address | none | create a new account, link |
| verified, real address | exists | **link to it** |
| private relay | none | create a new account, link |
| private relay | exists (improbable) | **do not link — create a new account** |
| no email (repeat sign-in) | n/a | resolve by `sub` only |
| no email (**first** sign-in — scope declined) | n/a | create a new account with no email; **not an error** |
| unverified | anything | **refuse to link; create new** |

> **Row added 2026-09-19** (`SIGN_IN_WITH_APPLE.md` Finding 3). `authorizedScopes` on the
> credential is what the user *granted*, not what was requested, and there are only two
> scopes — the email one can be declined. A first sign-in with email declined produces the
> same empty field as a repeat sign-in with none of the same context: there is no prior row
> to resolve against. Without this row an implementer reading the table treats a declined
> scope as a failed sign-in. The resulting account shape is already legal here — see the
> `bot.py:7527` / `bot.py:7565` reading two sections up, which establishes that an account
> with no email at all passes the login email gate.

Auto-linking on a verified email is safe *specifically for Apple* because Apple
is the authority for the address it is asserting and it tells you whether it
verified it. That is not true of every provider, and this table must not be
copied to the next one without re-deriving it.

Refusing to link a relay address is the non-obvious half. A relay is issued
per-app and is not an address the user gave anyone else, so an existing account
already holding one is far more likely to be an attacker who registered it than
a coincidence.

**There is a race here, and it is pre-existing.** `users.email` has **no UNIQUE
constraint** — the column is a plain `email TEXT` in both definitions.
(The three `email TEXT UNIQUE` hits in `bot.py` at lines 116227, 122339 and
123801 belong to `leads`, `admin_users` and `employees` — not `users`.)
Uniqueness is enforced only by an application-level pre-check in `create_account`
(`bot.py:6604`): `SELECT user_id FROM users WHERE lower(email)=lower(?) AND
email!='' LIMIT 1`. Check-then-insert with no constraint underneath is a race,
and adding a second signup path multiplies the window rather than creating it.

The SIWA work does **not** get to fix that — retro-fitting a unique index on a
live `users` table with unknown duplicate state is its own piece of work with its
own rollback plan. What the SIWA work *must* do is refuse to depend on it: the
`UNIQUE (provider, provider_subject)` above is in the new table precisely so the
Apple path is correct regardless of what `users.email` does. Resolve by `sub`
first, always; treat email as a linking hint, never as an identity.

---

## Decision 4 — verify the identity token server-side, and declare PyJWT explicitly

The client gets an identity token from `ASAuthorization` and posts it. The server
must verify it: RS256 signature against Apple's JWKS at
`https://appleid.apple.com/auth/keys`, selected by `kid`, plus `iss`, `aud`,
`exp` and the nonce. **Never trust the client's claim of who it is** — the
`user` field in the Apple credential is attacker-controlled; only the signature
is not.

> **Confirmed and sharpened 2026-09-19.** RS256 is right (the audit's §6 said ES256 and has
> been corrected). Fetched live, that endpoint returns **three** RSA keys with distinct
> `kid`s, all `alg: RS256` — which is the concrete reason `kid` selection is not optional:
> there is no single "Apple public key" to pin, and pinning one breaks login on rotation day.
>
> **The nonce needs one more thing than this paragraph says.** Verifying a nonce means
> comparing the token's claim against a value *the server already knew*. If the client
> generates the nonce and posts it alongside the token, the server compares a client-supplied
> value to a client-supplied value and a replayed token simply carries its matching nonce.
> The check must be backed by a server-issued, single-use, short-TTL challenge — which is a
> second piece of unwritten infrastructure beside the JWKS gap. If that is not built, **omit
> the nonce check rather than ship a version of it that reads like replay protection and is
> not.** `SIGN_IN_WITH_APPLE.md` Finding 4.
>
> Note also that `identityToken` is `nullable` on the credential. A nil token must fail loudly
> on the client and never reach this route.

Two things about this repo make it harder than it sounds.

**There is no JWKS machinery anywhere.** Nothing in the codebase fetches a remote
key set. Key fetch, `kid` selection, caching and rotation are all new code. The
closest prior art is genuinely good —
`services/business_os/entitlements/iap_apple.py` hand-rolls an ES256 JWS verifier
with `verify_and_decode_jws` (:187), `validate_chain` (:144) and an explicit
rejection of `alg != ES256` — but it verifies against an **embedded `x5c`
chain**, and SIWA verifies against a **remote JWKS by `kid`**. The structure
ports; the key-acquisition half does not.

**PyJWT is used in production but is not in `requirements.txt`.**
`services/pulsesoc_notification_system.py:2561` does `import jwt` inside a
try/except and signs APNs tokens with it at :2570. It arrives only transitively,
via `firebase-admin`. `cryptography==48.0.0` *is* declared
(`requirements.txt:8`).

**Decision: add `PyJWT[crypto]` to `requirements.txt` explicitly**, and do it as
its own commit before any SIWA code. An undeclared transitive dependency that
production APNs already depends on is a latent outage — the day `firebase-admin`
drops or renames it, push signing breaks, and nothing in the repo explains why.
Declaring it is correct independent of SIWA; SIWA is just what surfaced it.

**Done 2026-09-19** — `PyJWT[crypto]>=2.5,<3`, ahead of any SIWA code, since it
fixes a live risk on its own.

The failure it removes is worse than "push breaks", because the breakage lies
about its own cause. The import sits inside a `try/except` that returns
`{"status": "config_missing", "message": "APNs dependency missing: ..."}`
(`services/pulsesoc_notification_system.py:2559-2562`) — which the caller
surfaces alongside the genuine "APNs credentials are not configured" path. A
missing *package* would therefore be investigated as a missing *environment
variable*. There is direct precedent for the whole shape three lines up in the
same file: the `Pillow` comment in `requirements.txt` records an undeclared
import that failed on every deploy and silently discarded the dimensions of all
318 uploaded images.

The floor deliberately mirrors `firebase-admin`'s own `pyjwt[crypto]>=2.5.0`, so
the declaration cannot narrow what pip already resolves. Verified rather than
assumed — `pip install --dry-run --report` for `PyJWT[crypto]>=2.5,<3` reports
**zero packages installed or upgraded**. The installed version is 2.13.0, and
`pip show pyjwt` gives `Required-by: firebase-admin` and nothing else, which is
the whole problem in one line.

Note this does *not* also satisfy the JWKS half. `jwt.decode` can verify RS256
given a key, but acquiring Apple's key by `kid`, caching it, and handling
rotation is still unwritten.

Note the CI consequence, which is mild: `requirements.txt` matches the
"indirect audio-affecting changes" pattern at
`.github/workflows/realtime-audio.yml:100`. That workflow is explicit in its own
comment (:92-94) that such paths **do not require a change declaration** — they
only have to run the release-blocking suite. So this costs a suite run, not a
declaration.

(Aside, found while reading that regex: the `^backend/.*(livekit|room|token)`
clause matches nothing. There is no `backend/` directory, and LiveKit is retired.
It is dead pattern left from the pre-Agora era. Harmless, but it means the rule
is narrower than it reads.)

---

## Decision 5 — the account must outlive the email

Apple users can revoke the relay, revoke the app's access, or change the
forwarding address, and none of those events arrive as a webhook unless server-
to-server notifications are configured. The invariant that keeps this from
becoming a support queue:

**`(provider, provider_subject)` is the identity. Email is contact information.**
A revoked relay must degrade the account to "we cannot email this person" — which
the schema already tolerates, since `users.email` is nullable and the login email
gate is conditioned on having one (`bot.py:7527`). It must never degrade to "we
cannot identify this person."

The corollary is a product requirement, not a technical one: a SIWA-only account
needs a path to add a real email or phone later, or it is permanently
unrecoverable if the user loses their Apple ID. That path does not exist today
and should be scoped with the feature rather than after it.

> **Qualified 2026-09-19** — `SIGN_IN_WITH_APPLE.md` Findings 1 and 2, from the
> `ASAuthorizationAppleIDCredential` / `ASAuthorizationAppleIDProvider` headers.
>
> **The `sub` is not promised to be permanent.** The header says it "will be stable across
> the 'developer team'" and that "the value may change upon user disconnecting from the
> identity provider." So an app transfer to another Apple Developer team changes *every*
> user's `sub` at once — the framework has a `Transferred` credential state for exactly this,
> which the enum carries and its own docstring omits — and a revoke-then-return user is not
> guaranteed the same string either.
>
> This does not change the decision: `(provider, provider_subject)` is still the right
> identity and email is still worse. What it changes is the status of the paragraph above.
> The alternative-credential path is not a nice-to-have for lost Apple IDs; it is the **only**
> mitigation for a team transfer or a revoke-and-return, and it belongs inside the feature's
> scope.
>
> **Revocation is not silent on the client.** See the correction under "not decided" below —
> two iOS 13 mechanisms give the app a revocation signal with no backend work at all.

---

## Where the code goes

| Layer | File | Detail |
|---|---|---|
| Entitlement | `PulseSoc.entitlements` | `com.apple.developer.applesignin` = `["Default"]` |
| Expo config | `app.json` | `ios.usesAppleSignIn: true` — **trips `dependency_watch`**, needs the full battery + a declaration |
| Dependency | `mobile-native/package.json` | `expo-apple-authentication` — same trap |
| Client UI | `src/screens/LoginScreen.tsx:364` | beside `ManualLoginForm`; existing submit is `submitManualSignIn` (:147) |
| Client session | `src/session/auth.ts:201` | new `signInWithApple()` beside `signIn`; reuses `persistSessionEnvelope` verbatim |
| Client API | `src/api/auth.ts:52` | new call beside `login()` — matches the CI "indirect" pattern |
| Server route | `bot.py` near :7504 | `/api/mobile/auth/apple`, dual-registered under `/api/pulse/...` like its neighbours |
| Server verify | new `services/apple_identity.py` | JWKS fetch + cache + RS256 verify |
| Schema | `bot.init_db()` | `user_external_identities`, idempotent |

**Portal work, which cannot be done from the repo:** enable Sign in with Apple on
the `com.pulsesoc.app` App ID; create a Service ID and a key if the web leg is
ever wanted. The web leg is **not** needed for the native app alone, and should
be skipped in the first pass — it is a separate credential set and a separate
redirect flow, and nothing consumes it yet.

Two of the seven rows above (`app.json`, `package.json`) are
`dependency_watch`-trapped (`config/realtime-audio-protected-paths.json:507-513`),
so unlike the 16.1 deployment floor, **this one genuinely does owe a declaration**
and the full validation battery. That is not avoidable by cleverness here: the
entitlement and the native module both have to be real.

---

## What is not decided, and is deliberately left open

- **Whether to build this at all, now.** Guideline 4.8 does not appear to compel
  it. It is a conversion play against a password-only signup, and it competes for
  the same wave as the extension foundation.
- **Server-to-server notifications** (Apple's revocation webhook). Real work,
  separate endpoint, and the feature is coherent without it — accounts just go
  stale silently rather than promptly.

  > **Corrected 2026-09-19.** Right about the webhook, wrong about "silently."
  > `ASAuthorizationAppleIDProviderCredentialRevokedNotification` and
  > `getCredentialStateForUserID:completion:` are both **iOS 13.0** and need no backend at
  > all: the first fires while the app is running, the second answers on demand at launch or
  > foreground. This is not equivalent to the webhook — it only reaches a device that still
  > has the app installed — but it closes most of the gap for a few lines of client code, so
  > it should be *in* scope rather than deferred with the webhook. The handling of `Revoked`
  > vs `NotFound` vs `Transferred` is not obvious and is specified in
  > `SIGN_IN_WITH_APPLE.md` Finding 2. What stays open is only the webhook itself.
- **Retro-fitting a unique index on `users.email`.** Named here because SIWA made
  it visible, explicitly *not* scoped into SIWA.
- **Android parity.** SIWA on Android is a web flow and a different set of
  credentials. Out of scope for this document entirely.

## What was verified, and what was not

Verified by reading the files cited: the absence of all Apple-auth code; the
login route's four gates and its three-artifact response; the double `users`
definition; the absence of a UNIQUE constraint on `users.email` (and that the
three `email TEXT UNIQUE` hits belong to other tables); PyJWT's absence from
`requirements.txt` alongside its use at `pulsesoc_notification_system.py:2561`;
the absence of any JWKS fetch; the protection-suite gates; the CI indirect-path
regex.

**Not verified:** the current text of App Store Review Guideline 4.8 — the claim
that SIWA is not compelled rests on the trigger being "uses a third-party login
service," which is true of this app but is a reading of a rule that changes.
Nothing else in this document depends on it.
