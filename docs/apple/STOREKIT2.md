# StoreKit 2 — verified live, and the third stale gap report

Written 2026-09-19. Capability #8 in `PULSESOC_APPLE_NATIVE_CAPABILITY_AUDIT.md`.
Like Universal Links, this one is **already implemented**, so this document is a
verification rather than a plan.

Two prior reports in the repo root describe StoreKit as blocked on operator
action. Both descriptions are now stale, and this document settles each with
evidence rather than adding a third opinion to the pile.

---

## The two claims that were checked

| Prior claim | Source | Verdict |
|---|---|---|
| `APPLE_ROOT_CA_CERTS` — required — **MISSING (owner action)** | `PULSESOC_STOREKIT_STRIPE_UNIFIED_PAYMENTS_FINAL_REPORT.md:225` | **Stale.** Configured in production today. |
| With `BUSINESS_OS_ENTITLEMENTS=off`, "an App Store purchase grants an entitlement nothing reads — the buyer sees no premium. That alone is an App Review rejection." | `APP_REVIEW_READINESS_REPORT.md:132` | **Stale.** Closed by the canonical provider bridge. |

Note the same file that says `APPLE_ROOT_CA_CERTS` is MISSING at line 225 also
says it is SET at line 313 — the report contradicts itself, which is exactly why
this needed a measurement rather than a re-reading.

---

## Claim 1: are the trust anchors configured in production?

**Yes.** Settled by a black-box probe, no credentials required.

`services/business_os/entitlements/iap_api.py:69-78` has three distinguishable
outcomes, and the HTTP status alone identifies which one production is in:

| Status | Meaning |
|---|---|
| `404` | `BUSINESS_OS_IAP` off — the route is dark |
| `503 not_configured` | flag on, **trust anchors missing** |
| `400 verification_failed` | flag on, anchors loaded, verifier ran and rejected the payload |

```
$ curl -X POST https://pulsesoc.com/webhook/business-os/iap/apple \
       -H 'Content-Type: application/json' -d '{"signedPayload":"probe"}'
400
{"ok":false,"code":"verification_failed",
 "error":"Notification signature could not be verified."}
```

Production returns the third. So `BUSINESS_OS_IAP` is on, the Apple root is
loaded, and the verifier is live and correctly rejecting garbage.

**The probe was proved to discriminate** rather than merely always returning 400,
by driving the same function locally through all three states:

```
no anchors   -> None     | 503 not_configured
with anchor  -> verifier | None
bad path     -> None     | 503 not_configured
```

That third row matters: `APPLE_ROOT_CA_CERTS` is a *filesystem path*, not cert
material, so a variable that is set but points at a file the container does not
have degrades to exactly the unconfigured state. The repo ships the cert at
`certificates/apple/AppleRootCA-G3.pem` and `.env.example:572` points at
`/app/certificates/apple/AppleRootCA-G3.pem`. The production 400 proves the file
is genuinely present and parseable at that path, which a `railway variables`
read could not have proved.

---

## Claim 2: does an App Store purchase actually grant visible Premium?

**Yes — and the mechanism that makes it true was added after the report.**

The structural half of the report's claim is still accurate. Reading
`iap_apple.apply_verified_subscription_transaction` (`:533-616`), the Apple path
writes exactly two things:

- `upsert_provider_subscription(...)` → `business_os_ent_provider_subs`
- `sync_subscription_entitlements(...)` → the canonical grant store

It never writes the legacy `users` premium columns. Verified by extracting
`sync_subscription_entitlements` with `ast` rather than by a windowed grep — the
function is `premium_entitlement_service.py:667-693`, and across its whole body
the counts for `UPDATE users`, `users SET`, `premium_until` and `is_premium` are
all zero. So the Stripe path dual-writes and the Apple path does not; that much
of the report holds.

What changed is the **read** side. `services/premium_entitlement_service.py:464-478`
documents a canonical provider bridge added precisely for this, and it is wired
in rather than merely described — `_canonical_provider_premium` is defined at
`:481` and called at `:569`, inside the legacy reader, on the no-legacy-row path:

```python
row = cur.fetchone()
if row and _active_window(dict(row)):
    return True
# No live legacy row: consult the canonical store for provider-sourced
# grants (Apple/Google purchases write canonical-only). See bridge above.
if key in _BRIDGED_LEGACY_KEYS:
    return _canonical_provider_premium(user_id)
return False
```

Three design choices in that bridge are worth preserving, because each one is a
trap avoided:

1. **Evaluated live, never copied as a boolean.** Expiry, grace and revocation
   are re-checked on every read, so a refunded or lapsed Apple subscription
   loses Premium through the same path that granted it. A cached flag would
   have made refunds invisible.
2. **Scoped to provider sources only** (`apple_app_store`, `google_play`).
   Admin, promotion and trial canonical grants keep their "flag off = zero
   behaviour change" semantics. The bridge fixes the IAP hole without
   prematurely flipping the whole migration.
3. **Only the umbrella key is bridged** (`premium_access`). Per-feature legacy
   keys stay legacy, so the blast radius is one key rather than the catalogue.

And it fails closed in the useful direction: if the canonical package or table
is unavailable the bridge returns `False` and legacy behaviour is unchanged.

---

## The verification itself is genuinely strong

Worth recording because it is the part most likely to be weakened by a
well-meaning "simplification". `iap_apple.py` does **full JWS validation**, not
the decode-without-verify that most IAP integrations ship:

- ES256 signature checked against the `x5c` **leaf** certificate's public key,
  with the fixed-width R‖S (P1363) → DER conversion that `cryptography` requires
  (`:84-92`).
- The full chain is walked leaf → intermediate → root, **every adjacent link's
  signature verified** (`:114-129`), every certificate checked against its
  validity window (`:169`).
- The root must match an operator-supplied trust anchor. With no anchors it
  raises `no trust anchors provided; refusing to verify` (`:165`) — **fails
  closed**, which is the single most important property here.

On top of the crypto, `apply_verified_subscription_transaction` gates on:
product-id allowlist, plan mapping, bundle id against `expected_bundle_ids()`,
`type == "Auto-Renewable Subscription"`, `appAccountToken` binding, sandbox
environment (rejected unless `APPLE_IAP_ALLOW_SANDBOX`), `revocationDate`,
and a real expiry comparison against the current time.

One anti-fraud check deserves a specific mention:

```python
if prior is not None and str(prior_subject) != str(subject_id):
    raise AppleJWSError("transaction already belongs to another account")
```

An original transaction id is bound to the first account that redeemed it, so
one purchase cannot be replayed across accounts. That is the check most
commonly missing from receipt-validation code.

`tests/business_os/test_iap_apple.py` — 16 tests, all passing.

---

## Standing operational risks

Nothing here is blocked, but three things can silently un-fix it:

| Risk | Why it bites | Signal |
|---|---|---|
| `APPLE_ROOT_CA_CERTS` points at a path the container lacks | Degrades to the *unconfigured* state; a set-but-wrong variable looks healthy in the Railway dashboard | webhook returns `503 not_configured` |
| `APPLE_IAP_ALLOW_SANDBOX` left on in production | Sandbox transactions would grant real Premium | must be unset outside review/staging |
| Apple root CA rotation | The pinned G3 root eventually expires; chain validation checks validity windows, so an expired anchor fails closed | webhook starts returning `400` for *genuine* notifications |

The third is the one with no alarm attached today. It fails closed — correct —
but the failure is indistinguishable at the HTTP layer from ordinary garbage
traffic being rejected. **Recommended, not done:** an alert on a sustained
`verification_failed` rate against `/webhook/business-os/iap/apple`, since the
healthy steady state for genuine Apple notifications is zero.

---

## The pattern, for the third time

This is now the third capability in this plan where **a description of a gap
outlived the gap**:

- `aasa_health.py`'s docstring said the AASA claimed two paths; it claims ten.
- `PULSESOC_STOREKIT_STRIPE_UNIFIED_PAYMENTS_FINAL_REPORT.md` says the trust
  anchors are missing; they are loaded.
- `APP_REVIEW_READINESS_REPORT.md` says an App Store purchase grants nothing
  readable; the provider bridge closed that.

In all three cases the executable artefact stayed correct while the prose beside
it rotted, and in all three the stale prose was *more pessimistic* than reality —
which is the dangerous direction for a launch decision, because it invites
re-doing work that is already done, or worse, "fixing" something that is
currently right.

The `*_REPORT.md` files in the repo root are **point-in-time snapshots, not
current state.** They should be read with their date attached and re-measured
before being acted on. This document is subject to exactly the same rule: the
probe above is dated, and the only durable claims here are the ones a script can
re-assert.

---

## What was verified, and what was not

**Verified live, this session:** production returns `400 verification_failed` to
an unsigned payload at `/webhook/business-os/iap/apple`, proving both the IAP
flag and the trust anchors are configured; and that the same code path returns
`503 not_configured` for both an unset variable and a non-existent path, proving
the probe discriminates.

**Verified by reading:** the full JWS chain-validation implementation
(`iap_apple.py:84-205`); the fail-closed no-anchors branch (`:165`); every
transaction gate in `apply_verified_subscription_transaction` (`:533-616`)
including the cross-account replay check; that the Apple path writes only the
provider-subscription and canonical-grant stores and that
`sync_subscription_entitlements` contains no `UPDATE users`; the provider bridge
(`premium_entitlement_service.py:464-478`, `:481`, called at `:569`); and
`APPLE_LINK`-adjacent env declarations in `.env.example:572-581`.

**Verified by running:** `tests/business_os/test_iap_apple.py` — 16 passed.

**Not verified.** No purchase was made and no device was used. Specifically:

- **No sandbox StoreKit purchase was exercised end-to-end.** The full loop —
  purchase → verify → grant → Premium visible app-wide without a restart →
  restore → expiry/refund propagating via the ASSN v2 webhook — has not been
  run in this session. That needs the iPhone 16 Pro and a sandbox Apple ID, and
  it is the one test that would exercise the bridge under real conditions
  rather than by code reading.
- **`BUSINESS_OS_ENTITLEMENTS`'s actual production value was not read.** The
  bridge is written to make the answer not matter for Apple purchases, which is
  why it was not chased; the owner-authenticated `admin_overview` route
  (`premium_api.py:745`) reports `flag_mode` and is the correct way to check.
- The claim that the shipped cert is the current Apple root is taken from the
  filename and the successful production verification, not from comparing
  fingerprints against Apple's published root.
