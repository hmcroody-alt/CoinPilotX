# Push Credentials Readiness Report

Date: 2026-06-07

## Scope

Verified native mobile push credential readiness for PulseSoc without printing private keys, tokens, APNs key material, Firebase private keys, or secret fragments.

## APNs Checks

- `APNS_KEY_ID`: loaded in Railway production.
- `APNS_TEAM_ID`: loaded in Railway production.
- `APNS_BUNDLE_ID`: loaded in Railway production and equals `com.pulsesoc.app`.
- `APNS_PRIVATE_KEY`: loaded in Railway production.
- APNs private key newline handling: implemented through escaped-newline normalization before validation.
- APNs provider safe initialization: verified in Railway production by validating the normalized PEM with `cryptography` without sending a notification.
- APNs private key exposure: no key value is printed by the audit.
- `.p8` files committed: none found in tracked files.

## Android FCM Checks

- `FCM_PROJECT_ID`: loaded in Railway production.
- `FCM_CLIENT_EMAIL`: loaded in Railway production.
- `FCM_PRIVATE_KEY`: loaded in Railway production.
- Firebase Admin dependency: added to production requirements as `firebase-admin>=6.5,<7`.
- Firebase Admin safe initialization: verified in Railway production through a named readiness app using sanitized service-account fields.
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

Railway production variables were verified with the redacted readiness audit. No APNs or FCM secret values were printed.

Command used:

```bash
railway run .venv/bin/python scripts/push_credentials_readiness_audit.py --json
```

Production result:

- `APNS_KEY_ID` loaded: `True`
- `APNS_TEAM_ID` loaded: `True`
- `APNS_BUNDLE_ID` equals `com.pulsesoc.app`: `True`
- `APNS_PRIVATE_KEY` loaded: `True`
- `FCM_PROJECT_ID` loaded: `True`
- `FCM_CLIENT_EMAIL` loaded: `True`
- `FCM_PRIVATE_KEY` loaded: `True`
- APNs provider initializable safely: `True`
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

Production push credential readiness is verified for APNs and Android FCM. The backend can safely initialize both native push providers, and no APNs `.p8` file is committed.
