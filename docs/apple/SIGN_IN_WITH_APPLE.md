# Sign in with Apple — the client half

Capability #6. Status: **NOT IMPLEMENTED.** No `expo-apple-authentication`, no
`com.apple.developer.applesignin`, no `usesAppleSignIn` in `app.json`, no
`AuthenticationServices` import anywhere.

`DECISIONS_SIGN_IN_WITH_APPLE.md` already settles the server questions — the route, the
schema, the linking policy, the token verification, the dependency. It is the authority on
all of that and this document does not restate it.

This document covers what that one did not: **what the framework actually hands you, and
what it declines to promise.** Four of the six findings below contradict or extend a claim
in the decisions doc, and one of them touches its load-bearing invariant.

Evidence: the `AuthenticationServices.framework` headers in the iOS 26.5 SDK, this repo, and
one live fetch of Apple's published key set. Nothing has been run on a device.

---

## Finding 1 — the opaque user id is not promised to be permanent

Decision 5 in the decisions doc states the invariant the whole design rests on:

> **`(provider, provider_subject)` is the identity. Email is contact information.**

and Decision 2's schema sketch comments the column as `-- Apple's `sub`; stable, opaque,
never reused`.

The header is more careful than that. `ASAuthorizationAppleIDCredential.h:39-41`:

```objc
/*! @abstract An opaque user ID associated with the AppleID used for the sign in. This
 identifier will be stable across the 'developer team' ...

 The identifier will remain stable as long as the user is connected with the requesting
 client.  The value may change upon user disconnecting from the identity provider.
 */
@property (nonatomic, readonly, copy) NSString *user;
```

Two qualifications, both load-bearing:

**"stable across the *developer team*."** The `sub` is scoped to the Apple Developer team,
not to the app. If `com.pulsesoc.app` is ever transferred to another team — a sale, a
restructure, a move off a personal account — **every user's `sub` changes at once**. The
framework has a state for exactly this, and the enum carries it while the docstring does not
(`ASAuthorizationAppleIDProvider.h:20-25`):

```objc
typedef NS_ENUM(NSInteger, ASAuthorizationAppleIDProviderCredentialState) {
    ASAuthorizationAppleIDProviderCredentialRevoked,
    ASAuthorizationAppleIDProviderCredentialAuthorized,
    ASAuthorizationAppleIDProviderCredentialNotFound,
    ASAuthorizationAppleIDProviderCredentialTransferred,
};
```

The comment above it documents three constants and says the completion block "will return
one of 3 possible states" (`:40`). There are four. `Transferred` is the app-transfer case and
it is undocumented in its own header.

**"may change upon user disconnecting."** A user who revokes PulseSoc in Settings and later
signs in again is not guaranteed the same `sub`.

**What this changes.** Not the decision — `(provider, provider_subject)` is still the right
identity, and email is still worse. What it changes is the *unqualified* phrasing. An
identity that can change is an identity that needs a recovery path, and the decisions doc
already names the one that covers it, as a product note at the end of Decision 5:

> a SIWA-only account needs a path to add a real email or phone later, or it is permanently
> unrecoverable if the user loses their Apple ID

That is currently framed as a nice-to-have for lost Apple IDs. It is in fact the **only**
mitigation for a team transfer or a revoke-and-return, and it should be scoped as a
requirement of the feature rather than a follow-up. Without it, a SIWA-only account is
recoverable only for as long as Apple chooses to keep returning the same string.

---

## Finding 2 — revocation *does* have a client-side signal, and the decisions doc left it open

The decisions doc lists under "not decided, deliberately left open":

> **Server-to-server notifications** (Apple's revocation webhook). Real work, separate
> endpoint, and the feature is coherent without it — accounts just go stale silently rather
> than promptly.

That is right about the webhook and wrong about "silently." Two client-side mechanisms exist,
both at **iOS 13.0**, both requiring zero backend work.

`ASAuthorizationAppleIDProvider.h:27`:

```objc
AS_EXTERN NSNotificationName const ASAuthorizationAppleIDProviderCredentialRevokedNotification
```

and `:44`:

```objc
- (void)getCredentialStateForUserID:(NSString *)userID
                         completion:(void (^)(ASAuthorizationAppleIDProviderCredentialState,
                                              NSError * _Nullable))completion;
```

So the app can (a) observe an `NSNotification` when the credential is revoked while it is
running, and (b) ask, at any point it likes, what the current state of a stored `user` id is.
A launch-time or foreground-time `getCredentialState` call is a few lines and answers the
question the webhook was going to answer.

**Two things to be precise about, because this is easy to over-sell.**

It is not equivalent to the webhook. This only fires on a device that still has the app
installed and still has that Apple ID signed in. A user who revokes and never opens the app
again is invisible to it. The webhook covers that; this does not.

And the right *response* is not obvious. `Revoked` means the user withdrew Apple's
authorization — it does not mean they want their PulseSoc account deleted, and it must not
sign them out of a session they are actively using. The defensible behaviour is to mark the
identity unusable for *future* sign-ins and prompt for an alternative credential — which
again is Finding 1's recovery path.

`NotFound` needs its own treatment: the header notes (`:42`) that an error is also passed in
that case, and `NotFound` is what you get on a device where the user simply signed out of
iCloud. Treating it as revocation would sign out a perfectly valid user for changing phones.

**Recommendation:** move this out of "left open." It is a small, well-bounded piece of client
work that meaningfully narrows the gap the webhook was meant to close, and it is available
below every floor this project has ever considered.

---

## Finding 3 — everything the server needs is optional, including the token

Four of the credential's properties are `nullable`, and one of them is the entire basis of
authentication (`ASAuthorizationAppleIDCredential.h:55-67`):

```objc
@property (nonatomic, readonly, copy, nullable) NSData *authorizationCode;
@property (nonatomic, readonly, copy, nullable) NSData *identityToken;
@property (nonatomic, readonly, copy, nullable) NSString *email;
@property (nonatomic, readonly, copy, nullable) NSPersonNameComponents *fullName;
```

A nil `identityToken` must be a hard, loud client-side failure that never reaches the network.
A request that posts `{"identityToken": null}` to a route whose job is to verify a token is
the exact request that finds out whether the route's null-handling is correct — and it should
not be a production user who finds out.

**The scopes are a response value, not a request value.** `:49-51`:

```objc
/*! @abstract This value will contain a list of scopes for which the user provided
 authorization.  These may contain a subset of the requested scopes ... The application
 should query this value to identify which scopes were returned as it maybe different from
 ones requested. */
@property (nonatomic, readonly, copy) NSArray<ASAuthorizationScope> *authorizedScopes;
```

There are exactly two scopes, `ASAuthorizationScopeFullName` and `ASAuthorizationScopeEmail`
(`ASAuthorization.h:15-16`), and the user can decline either. **Decision 3's linking table has
no row for this.** Its rows are:

| Apple returns | Existing account with that email |
|---|---|
| verified, real address | none / exists |
| private relay | none / exists |
| no email (repeat sign-in) | n/a |
| unverified | anything |

"No email (repeat sign-in)" is annotated `resolve by `sub` only`, which is correct for a
repeat sign-in. But a **first** sign-in with the email scope declined produces the same
empty field with none of the same context: there is no prior row to resolve against. The
missing row is:

| Apple returns | Existing account | Action |
|---|---|---|
| no email, and no prior row for this `sub` | n/a | create a new account with no email; do not treat the absence as an error |

This is a legal account shape here — the decisions doc establishes it itself, noting that
`create_account` accepts phone-only (`bot.py:7565`) and that the login email gate is
conditioned on *having* an email (`bot.py:7527`). So the schema tolerates it. The linking
policy just needs to say so, or an implementer reading that table will treat a declined scope
as a failed sign-in.

**`fullName` has a home and a trap.** `users.display_name` exists in the winning table
definition (`bot.py:115551`), so there is somewhere to put it. The trap is that it is an
`NSPersonNameComponents`, not a string — rendering it is a locale decision — and like `email`
it arrives on the first authorization only. A `display_name` not captured on that one request
cannot be re-requested.

---

## Finding 4 — a nonce the client invented protects nothing

`ASAuthorizationOpenIDRequest.h:34-37`:

```objc
/*! @abstract Nonce to be passed to the identity provider.  This value can be verified with
 the identity token provided as a part of successful ASAuthorization response. */
@property (nonatomic, copy, nullable) NSString *nonce;
```

Note what the header does *not* say. It does not say to hash it — the widely-followed
convention of sending `SHA256(rawNonce)` and comparing against the token's claim is Apple
*documentation*, not an API contract, and it is not visible anywhere in this header. More
importantly, **it does not say who generates it.**

The decisions doc lists the nonce among the things to check: "plus `iss`, `aud`, `exp` and
the nonce." That is right and incomplete. Verifying a nonce means comparing the token's
`nonce` claim against a value *the server already knew*. If the client generates the nonce
and posts it alongside the token, the server is comparing a client-supplied value to a
client-supplied value, and an attacker replaying a captured token simply replays the matching
nonce with it. The check passes and provides nothing.

The nonce is only worth its code if the server issues it: a short-TTL, single-use challenge
that the client requests, passes to `ASAuthorizationOpenIDRequest.nonce`, and returns with
the token — and that the server then **consumes**.

**There is no such machinery in this repo.** That is one more piece of unwritten
infrastructure to put beside the JWKS gap the decisions doc already names, and it is worth
saying plainly: a nonce check that is not backed by a server-issued challenge is best left
out entirely rather than implemented in a form that reads like replay protection and is not.

`state` (`:29-32`) is a separate field with the same shape and the same caveat; the
`authorizationCode` docstring calls out that the code is "bound to the specific transaction
using the state attribute" (`ASAuthorizationAppleIDCredential.h:53`).

---

## Finding 5 — cancel is not a failure, and the repo already has the right shape for this

`ASAuthorizationError.h:15-36` defines ten cases. The three that matter on iOS:

```objc
ASAuthorizationErrorUnknown          = 1000,
ASAuthorizationErrorCanceled         = 1001,
ASAuthorizationErrorInvalidResponse  = 1002,
ASAuthorizationErrorNotHandled       = 1003,
ASAuthorizationErrorFailed           = 1004,
ASAuthorizationErrorNotInteractive   = 1005,   // iOS 15.0+
```

`Canceled` (1001) arrives through the *same* delegate callback as every real failure —
`authorizationController(controller:didCompleteWithError:)`
(`ASAuthorizationController.h:25`). There is no separate cancellation path. A user who swipes
the sheet away produces an `NSError`, and an implementation that maps every `NSError` to "sign
in failed" will show an error toast to a user who deliberately changed their mind. That is a
small bug with an outsized effect on a signup funnel, which is the entire justification for
building this capability.

**This repo has already solved this exact problem, in Swift, and the solution is worth
copying rather than re-deriving.** `modules/pulse-apple-translation/ios/AppleTranslationError.swift`
defines a string-raw-valued failure enum whose values are the wire contract with TypeScript,
with two derived properties — `isRecoverable` and `permitsCloudFallback` — that encode which
failures the JS layer may retry and which it must treat as terminal. Its `requestCanceled`
case is explicitly excluded from fallback, with the reasoning written in the file:

> falling through on cancellation is how a scroll-away turns into billable Google traffic

The file even records why it imports nothing: the enum "must be constructible on every iOS
version" so that `unsupportedOSVersion` can be returned from a device that cannot load the
framework at all. SIWA wants the identical structure — a typed enum, cancel separated from
failure by construction rather than by an `if` at the call site.

---

## Finding 6 — the floor is 13.0, and three related claims in the audit are wrong

**Floor.** Sign in with Apple is `API_AVAILABLE(ios(13.0))` on every symbol above. Against a
16.1 project floor it is the least constrained capability in the entire audit. The only
part above the floor is `ASUserAgeRange` (`ASAuthorizationAppleIDCredential.h:30-34`, iOS
17.0), which is additionally gated on an entitlement Apple grants by request — its `Unknown`
case is documented as what you get when "the project is missing the required entitlement to
support child accounts." For a social platform that is worth knowing exists; it is not part
of a first pass.

Three corrections, all to `PULSESOC_APPLE_NATIVE_CAPABILITY_AUDIT.md` §6 and §3:

**1. The signing algorithm is RS256, not ES256.** §6 says "verify Apple's identity token
(JWT, ES256, keys from `https://appleid.apple.com/auth/keys`...)". Fetched live on
2026-09-19, that endpoint returns **three** keys:

```
{'kty': 'RSA', 'kid': '8nTlO3M2Bk', 'use': 'sig', 'alg': 'RS256'}
{'kty': 'RSA', 'kid': 'GNCf4J83Oh', 'use': 'sig', 'alg': 'RS256'}
{'kty': 'RSA', 'kid': 'nP41CGTvOz', 'use': 'sig', 'alg': 'RS256'}
```

`DECISIONS_SIGN_IN_WITH_APPLE.md` Decision 4 already says RS256 and is correct. The ES256 in
the audit is almost certainly contamination from the IAP verifier
(`services/business_os/entitlements/iap_apple.py`), which genuinely is ES256 — the same
half-truth this mission has now hit three times, where an Apple verifier in the repo is
assumed to generalise to a different Apple verifier.

The three-key response is also the concrete argument for something Decision 4 states but does
not motivate: **`kid` selection is mandatory.** There is no "Apple's public key" to pin. There
are three at any moment, and pinning one means a broken login on the day Apple rotates.

**2. "a new `apple_sub` column with a unique index"** — superseded by Decision 2, which takes
a `user_external_identities` table instead, for three reasons the audit had not yet seen (the
double `users` definition being the sharpest). The audit line should point at the decision
rather than continue to name a column.

**3. §3's "a codebase whose only Swift files today are `AppDelegate.swift` and the
`pulse-now-playing` module"** is false, and it is the sentence the audit uses to argue that
native work here is expensive. There are **three** local Swift modules:

| Module | Swift files |
|---|---|
| `pulse-now-playing` | 1 |
| `pulse-video-mixer` | 1 |
| `pulse-apple-translation` | 8, plus a 6-file test harness |

`pulse-apple-translation` is a fully worked example of the thing SIWA would need: a podspec
that holds the floor below the feature's own requirement and weak-links the framework
(`PulseAppleTranslation.podspec:13-29`), an `@available`-gated implementation, a typed error
contract shared with TypeScript, and Swift tests that build as a plain host executable via
`scripts/test_apple_translation_swift.sh` — no Xcode test target, no SwiftPM package. The
precedent for "real native work in this codebase" exists and is good. The audit's effort
estimate for every Swift capability should be read against that, not against a codebase with
one Swift file in it.

---

## The one thing that could invalidate the plan, and it is unverified

`APPLE_NATIVE_ARCHITECTURE.md` row 6 and the decisions doc both route this through
`expo-apple-authentication` — "off-the-shelf; the work is server-side." That is very likely
right, and it is the reason this capability is rated Medium rather than Large.

**It is not verified here.** The package is not installed, so nothing in this repo can tell
you what its JavaScript surface exposes. Findings 2 and 4 each name something a thin wrapper
may or may not surface:

| Needed | Framework symbol | Exposed by the Expo package? |
|---|---|---|
| revocation state on launch | `getCredentialStateForUserID:` | **unknown** |
| revocation while running | `...CredentialRevokedNotification` | **unknown** |
| server-issued nonce | `ASAuthorizationOpenIDRequest.nonce` | **unknown** — and if exposed, whether it hashes for you or expects a hash |
| scopes actually granted | `authorizedScopes` | **unknown** |
| real-user hint | `realUserStatus` | **unknown** |

The first question to answer when this work starts is therefore not a server question. It is:
install the package, read its `index.d.ts`, and check those five rows. If three or more are
missing, the "off-the-shelf" premise is wrong and this becomes a fourth local Swift module —
which, per Finding 6, is a well-trodden path here, but a different estimate.

---

## A free anti-fraud signal, and it lands where App Attest would have

`realUserStatus` (`ASAuthorizationAppleIDCredential.h:69-71`) returns
`ASUserDetectionStatus`, iOS 13.0, no entitlement, no infrastructure:

```objc
ASUserDetectionStatusUnsupported,
ASUserDetectionStatusUnknown,
ASUserDetectionStatusLikelyReal,
```

The header is unusually prescriptive about how to use it (`:16`): new users in the ecosystem
also get `Unknown`, "so you should not block these users, but instead treat them as any new
user through standard email sign up flows."

`APP_ATTEST_DEVICECHECK.md` recommends deferring App Attest and instrumenting first. Worth
recording the overlap: `likelyReal` is a genuine device-attested signal about the *signup*
path — the exact path App Attest would have been aimed at — arriving free with a capability
already rated the highest-value item in the audit. It is strictly weaker than App Attest and
is not a substitute for it. But it is a signal that costs one field on one request, and it
should be stored on the identity row (`user_external_identities`) at link time, because like
`email` and `fullName` it is a first-authorization value and cannot be asked for again.

---

## Owed

| # | Item | Why |
|---|---|---|
| 1 | Re-word Decision 2's `provider_subject` comment and scope the alternative-credential path **into** the feature | Finding 1; the id is not promised to be permanent and the recovery path is its only mitigation |
| 2 | Move the credential-state check out of "left open"; specify `Revoked` / `NotFound` / `Transferred` handling separately | Finding 2; iOS 13, no backend |
| 3 | Add the missing "first sign-in, email scope declined" row to Decision 3's table | Finding 3; the shape is already legal in the schema |
| 4 | Decide: server-issued nonce, or no nonce check at all | Finding 4; the middle option is the dangerous one |
| 5 | A typed failure enum modelled on `AppleTranslationFailure`, with cancel separated by construction | Finding 5 |
| 6 | Fix the audit: RS256 not ES256, table not column, three Swift modules not one | Finding 6 |
| 7 | Install `expo-apple-authentication` and check the five-row table above **before** estimating | the plan's Medium rating depends on it |
| 8 | Store `realUserStatus` on the identity row at link time | first-authorization value; unrecoverable if skipped |

---

## What was verified, and what was not

**Verified against the AuthenticationServices headers in the iOS 26.5 SDK**, at the line
numbers given: every nullability annotation on `ASAuthorizationAppleIDCredential`; the `user`
docstring's two stability qualifications; the four-case credential-state enum against its
three-case docstring; `ASAuthorizationAppleIDProviderCredentialRevokedNotification` and
`getCredentialStateForUserID:`; the two authorization scopes; `nonce` and `state` on
`ASAuthorizationOpenIDRequest`; the ten `ASAuthorizationError` cases and the single
`didCompleteWithError:` delegate callback; `ASUserDetectionStatus` and `ASUserAgeRange` with
their availability.

**Verified live, once, on 2026-09-19:** `https://appleid.apple.com/auth/keys` returns three
RSA keys, all `alg: RS256`, distinct `kid`s. This is a point-in-time observation of a
rotating endpoint — the count will change; the algorithm and the necessity of `kid` selection
will not.

**Verified by reading this repo:** no `expo-apple-authentication` in `package.json`; no
Apple keys in `app.json`; `users.display_name` at `bot.py:115551`; `PyJWT[crypto]>=2.5,<3`
now declared at `requirements.txt:41`; no JWKS fetch anywhere in Python; the three Swift
modules and their file counts; `AppleTranslationError.swift`'s enum and its two derived
properties; the translation podspec's floor and weak-link comments.

**Not verified.** Everything runtime, and specifically: the Expo package's JavaScript surface
(the five-row table is five open questions, not five answers); whether `realUserStatus` is
useful in practice at this app's volume; and whether `Transferred` behaves as the enum name
implies, which cannot be tested without actually transferring an app between teams. No Apple
sign-in has ever been performed against this codebase.
