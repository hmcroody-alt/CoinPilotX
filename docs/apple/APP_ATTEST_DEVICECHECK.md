# App Attest / DeviceCheck

Capability #1. Status: **NOT IMPLEMENTED**, and the recommendation at the end of this
document is still **defer** — but for a sharper reason than the audit gave, and with one
cheaper thing worth doing instead.

`grep -rniE "app.?attest|devicecheck|DCDevice|DCAppAttest"` across `services/`, `bot.py`,
`mobile-native/src`, `mobile-native/ios` and `mobile-native/modules` returns two hits, both
false positives: a local variable `deviceCheck` in `LiveStudioScreen.tsx:168` that is a
go-live readiness row, unrelated to the framework.

SDK evidence is `iPhoneOS26.5.sdk/System/Library/Frameworks/DeviceCheck.framework/Headers`.
Backend evidence is this repo. Nothing here has been run.

---

## Finding 1 — the abuse surface is real, specific, and already wired

The audit described the value as "raises the cost of scripted signup, referral farming, and
ad-credit fraud." That is true but vague enough to be unfalsifiable. The concrete version:

**PulseSoc already has a device identity, it is already a rate-limit bucket key, and it is
whatever the client says it is.**

`bot.py:3155-3158` computes it on every request:

```python
device_hash = pulse_security_core.device_fingerprint(
    request.headers.get("User-Agent", ""),
    request.headers.get("X-PulseSoc-Device-Id", "") or request.headers.get("X-Device-Id", ""),
)
g.pulse_device_hash = device_hash
```

`pulse_security_core.py:107-111` is a salted SHA-256 of exactly those two client-controlled
strings. And `:147-151` makes it a bucket:

```python
keys = [
    f"ip:{ip_hash}:{rule.action}:{path}",
    f"user:{int(user_id or 0)}:{rule.action}:{path}" if user_id else "",
    f"device:{device_hash}:{rule.action}:{path}" if device_hash else "",
]
```

Signup is `RateRule(6, 300, "auth_signup")` (`:41-42`); login is 10/300s; checkout is
8/300s at `high` severity.

So changing one request header mints a fresh device bucket on every high-risk route.

**Scope it honestly, though.** All three keys are evaluated in the same loop, so rotating
`X-PulseSoc-Device-Id` defeats *one of three tiers*; the IP-hash bucket and, once
authenticated, the user bucket still apply. An attacker behind a single IP gains nothing
against the IP tier. The device tier exists to catch the case the IP tier cannot — many IPs,
one machine — and that is precisely the case where a client-asserted identifier is worthless.

This is the argument for App Attest in this codebase, and it is a better one than "anti-abuse
in general": there is an existing control whose input is unauthenticated, and App Attest is
the only mechanism that authenticates it.

---

## Finding 2 — "the same machinery as the StoreKit verifier" is half true

The audit points at `services/business_os/entitlements/iap_apple.py:74-159` as a template.
Reading it, the reusable half is real and substantial:

- `validate_chain(certs, *, trust_anchors, now)` (`:144-169+`) — anchors by SHA-256
  fingerprint, verifies every adjacent link, checks every validity window, and **refuses to
  verify with no anchors** (`:164-165`). That is the right shape for App Attest's
  `attStmt.x5c`, which chains to the Apple App Attest Root CA.
- `_verify_cert_signed_by` handles EC and RSA issuers (`:114-129`).
- `_raw_p1363_to_der` (`:83-90`) converts fixed-width R‖S to DER, which App Attest's
  assertion signatures need for the same reason.

The half that does **not** transfer is the envelope. StoreKit gives a JWS —
`_split_jws` at `:74-80` expects "exactly three dot-separated segments". An App Attest
attestation object is **CBOR**: a map with `fmt = "apple-appattest"`, `attStmt` (`x5c`,
`receipt`), and `authData`, whose nonce must be recomputed as
`SHA256(authData ‖ clientDataHash)` and matched against a custom OID extension in the leaf
certificate. None of that exists here.

Concretely: **`requirements.txt` has `cryptography==48.0.0` and `PyJWT[crypto]>=2.5,<3`, and
no CBOR library at all.** App Attest verification is a new dependency plus a new parsing
layer, not a variation on the existing one. That distinction is the difference between
"Medium" and the audit's (correct) "Large".

---

## Finding 3 — the environment split is the APNs trap again, and the repo already holds the fix

App Attest is entitled by `com.apple.developer.devicecheck.appattest-environment`, whose
value is `development` or `production`. A development-signed build produces attestations
that are only valid against Apple's development App Attest environment.

This is structurally identical to the APNs failure already recorded for this project: a
dev-signed build mints sandbox tokens, the production deployment does not know, and the
resulting error is indistinguishable from a legitimately bad credential. The App Attest
version is **worse**, because the consequence of getting it wrong is not a lost notification
— it is a verification failure, and under an enforcing gate a verification failure is a
locked-out user.

The fix is already proven in this repo and requires no invention.
`mobile-native/ios/PulseSoc/PulseSoc.entitlements`:

```xml
<key>aps-environment</key>
<string>$(PULSESOC_APS_ENVIRONMENT)</string>
```

The entitlement is a build setting, so the value follows the configuration instead of being
a literal someone has to remember to flip. **Any App Attest entitlement must be added the
same way** — `$(PULSESOC_APPATTEST_ENVIRONMENT)` — and the server must be told which
environment a given attestation came from rather than inferring it, for exactly the reason
the APNs note gives: the two failures look the same.

Note also that `app.json`'s `expo.ios.entitlements` is `{}` — the entitlements file is the
source of truth here, and it is not generated from `app.json`. Editing the wrong one is the
same class of mistake as `BACKGROUND_TASKS.md` Finding 1.

---

## Finding 4 — report-only mode limits blast radius, not scope

The audit's mitigation — "must ship behind a flag in *report-only* mode first, measured, and
only then enforced" — is right and should not be softened. But it is worth being explicit
that report-only is **not a smaller project**. To report, you still need:

the native module, the key lifecycle, the challenge endpoint, the CBOR parser, the full
chain and nonce verification, the key-id store, and the assertion counter.

The only thing report-only withholds is the `deny`. Anyone planning this work who treats
"just report-only for now" as a way to halve the estimate will find the halving isn't there.
What report-only genuinely buys is the measurement: a false-negative rate observed on real
devices before any user can be locked out. That is worth the whole cost — it is just not a
discount.

---

## Finding 5 — three implementation traps from the headers

**Retrying attestation wrong degrades the device's own reputation.**
`DCErrorServerUnavailable` (`DCError.h`):

> If you receive this error, try the attestation again later using the same key and the same
> value for the `clientDataHash` parameter. Retrying with the same inputs helps to preserve
> the risk metric for a given device.

The instinctive retry — fresh challenge, fresh key — is the wrong one, and its cost is
invisible: the device still attests successfully, it just scores worse. A retry path must
persist both the key id and the client data hash across the failure.

**`DCErrorInvalidKey` collapses three different causes.** From the same header: attesting an
already-attested key, asserting with an unattested key, or the service rejecting the key
outright. The first two are client bugs and the third is the security signal. They arrive as
one code. This is the same shape as `BGTaskSchedulerErrorCodeNotPermitted` in
`BACKGROUND_TASKS.md` — client-side state must disambiguate, because the error will not.

**Attestation is per-key, and Apple wants one key per account.**
`DCAppAttestService.h`:

> Create a unique key for each user account on a device. Otherwise it's hard to detect an
> attack that uses a single compromised device to serve multiple remote users running a
> compromised version of your app.

PulseSoc supports account switching, and its session-end cleanup deliberately clears all
accounts (`mediaSessionCleanup.ts:16-19`, per the Core Spotlight policy doc). So the key-id
store is per-account state with a defined lifetime, not an install-scoped singleton — which
puts it in the same bucket as the Wave 2 keychain work in `DEVICE_SECURITY.md` and is
another reason not to start here.

---

## Finding 6 — what the capability cannot do, stated before anyone expects it

Two limits are worth writing down now, because both will otherwise be discovered as bugs.

**It does not work everywhere.** `DCAppAttestService.supported` is the required gate, and
the header is explicit that it is `false` on Mac, on Mac Catalyst, and for iOS apps running
on Apple silicon. PulseSoc ships iPhone-only today (`app.json` `supportsTablet: false`), so
this is latent rather than live — but it becomes live the day the app is made available on
Apple silicon Macs, and at that moment an enforcing gate locks out every such user. The
header is also explicit that `generateKey` **fails inside app extensions** regardless of
`supported`, with watchOS 9 extensions as the only exception. Wave 2 adds a Share Extension
and a widget extension; neither can ever attest.

**It authenticates the app instance, not the person.** A key lives in the Secure Enclave and
does not survive reinstall or restore-to-new-device. A legitimate user who reinstalls
produces a brand-new, never-seen key — the identical signal an abuser produces. That is why
attestation raises cost rather than proving identity, and it caps what this feature can
honestly be sold as internally. Any policy of the form "block unattested devices" must have
an answer for the user who just restored their phone, and "contact support" is not an answer
at 11 MAU.

And the point the header makes that the whole design rests on:

> A compromised version of your app could falsify the verification result, thus circumventing
> App Attest.

Verification happens on the server or it has not happened. That matches the mission's
standing rule that the backend is the authority.

---

## Recommendation: defer — and do the cheap thing instead

The audit recommended deferral on scale grounds and asked whether it earns its cost at 39
users / 11 MAU. It does not, and the case is stronger than the audit put it: production has
never processed a real payment, so the highest-value target an abuser has — the checkout
path — currently has nothing behind it.

But Finding 1 identified a live weakness, and there is a response to it that costs roughly
nothing and needs no Apple API:

**The rotation is itself the signal.** `X-PulseSoc-Device-Id` being unauthenticated means an
abuser must supply a *new* value for each attempt. A count of distinct `device_hash` values
per `ip_hash` per window is a direct measurement of the behaviour App Attest would block, it
uses data the request path already computes (`g.pulse_device_hash`), and it answers the
audit's open question with evidence instead of judgement: if that count is flat, App Attest
has nothing to buy; if it spikes, the deferral is over and there is a baseline to measure the
report-only rollout against.

Do that first. It is the measurement that makes the later decision falsifiable.

---

## Owed, if this is ever picked up

| # | Item | Why it is first |
|---|---|---|
| 1 | Instrument distinct `device_hash` per `ip_hash` per window | Decides whether the rest is worth doing |
| 2 | Decide what it gates — signup, referral claim, IAP; not everything | Attesting everything is the failure mode |
| 3 | `$(PULSESOC_APPATTEST_ENVIRONMENT)` build setting, never a literal | Finding 3 |
| 4 | Choose a CBOR dependency and add it to `requirements.txt` | Finding 2; also triggers the dependency-watch battery |
| 5 | Per-account key-id store with a defined clear path | Finding 5; couples to Wave 2 keychain work |
| 6 | Report-only for a measured period, with the false-negative rate as the gate to enforce | Finding 4 |

---

## What was verified, and what was not

**Verified by reading, in this repo:** no App Attest or DeviceCheck symbol exists anywhere
in `services/`, `bot.py`, or `mobile-native/`; `bot.py:3155-3158`; `pulse_security_core.py`
`device_fingerprint` at `:107-111`, `HIGH_RISK_RATE_RULES` at `:38-58`, and the three-key
bucket construction at `:147-151`; `iap_apple.py` `_split_jws` (`:74-80`),
`_raw_p1363_to_der` (`:83-90`), `_load_x5c` (`:96-107`), `_verify_cert_signed_by`
(`:114-129`) and `validate_chain` (`:144-169`); `requirements.txt` carrying
`cryptography==48.0.0` and `PyJWT[crypto]` and no CBOR library;
`PulseSoc.entitlements` using `$(PULSESOC_APS_ENVIRONMENT)` and `app.json`'s
`expo.ios.entitlements` being empty; `supportsTablet: false`.

**Verified against the iOS 26.5 SDK headers:** every quotation in Findings 5 and 6, from
`DCAppAttestService.h` and `DCError.h`.

**Not verified.** Everything runtime, and specifically: the CBOR structure of an attestation
object and the leaf-certificate OID extension are stated from documented behaviour, not from
an attestation object obtained on hardware — no App Attest code exists to produce one. The
claim that `supported` is false on the Simulator is *not* made here; the header does not say
so, and it was not tested. The distinct-device-per-IP figure recommended above has not been
measured; that is the point of recommending it.
