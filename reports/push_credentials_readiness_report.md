# Push Credentials Readiness Report

Date: 2026-06-07

## Scope

Verified native mobile push credential readiness for PulseSoc without printing private keys, tokens, APNs key material, Firebase private keys, or secret fragments.

## APNs Checks

- `APNS_KEY_ID`: audit support added; local shell value not loaded.
- `APNS_TEAM_ID`: audit support added; local shell value not loaded.
- `APNS_BUNDLE_ID`: audit support added; expected value is `com.pulsesoc.app`; local shell value not loaded.
- `APNS_PRIVATE_KEY`: audit support added; local shell value not loaded.
- APNs private key newline handling: implemented through escaped-newline normalization before validation.
- APNs provider safe initialization: implemented by validating the normalized PEM with `cryptography` without sending a notification.
- APNs private key exposure: no key value is printed by the audit.
- `.p8` files committed: none found in tracked files.

## Android FCM Checks

- `FCM_PROJECT_ID`: audit support added; local shell value not loaded.
- `FCM_CLIENT_EMAIL`: audit support added; local shell value not loaded.
- `FCM_PRIVATE_KEY`: audit support added; local shell value not loaded.
- Firebase Admin dependency: added to production requirements as `firebase-admin>=6.5,<7`.
- Firebase Admin safe initialization: implemented through a named readiness app using sanitized service-account fields when runtime values are present.
- Firebase private key exposure: no key value is printed by the audit.

## Synthetic Initialization Check

A local non-secret synthetic credential check was run using throwaway generated private keys. This verified:

- APNs escaped-newline private key normalization works.
- APNs PEM parsing works.
- Firebase Admin can initialize safely when FCM runtime fields are present.
- No real APNs or Firebase secrets were used or printed.

Synthetic result:

- APNs readiness: pass
- FCM readiness: pass
- Overall initialization path: pass

## Railway Runtime Verification

Railway CLI is installed, but this workstation's Railway session could not be used for runtime verification because the OAuth token refresh failed and requires `railway login` again.

After login, run this from the project root to verify the real Railway environment without exposing credentials:

```bash
railway run python3 scripts/push_credentials_readiness_audit.py
```

Expected production result:

- APNs provider initializable safely: `True`
- APNs bundle id expected: `True`
- Firebase Admin initializes safely: `True`
- Tracked APNs `.p8` files committed: `0`
- Overall ready: `True`

## Local Audit Result

Local shell environment check was intentionally redacted. It confirmed:

- No APNs/FCM secrets are loaded in the local shell.
- No tracked APNs `.p8` private key files are committed.
- The audit can run in non-secret local mode using `--allow-missing-runtime-env`.

## Files Added

- `services/native_push_readiness.py`
- `scripts/push_credentials_readiness_audit.py`

## Status

Code-level readiness is complete and the provider initialization path passes with synthetic non-secret keys. Final production verification requires a refreshed Railway login so the redacted audit can run inside the Railway runtime where the APNs and FCM variables were added.
