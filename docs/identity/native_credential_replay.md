# Native provider credentials are single-use — claimed, built, and why

**Status:** implemented and merged into `claude/apple-identity-federation` (PR #163).
**Owner of the fix:** that branch. **This is the only replay mechanism.** If you
are about to add a second one, read "For another agent" at the bottom first.

Written 2026-10-03.

---

## The finding

A native Apple/Google ID token is a **bearer credential**. `verify_id_token`
proves it was issued by the provider, for this audience, and has not expired --
and all three of those stay true for every *subsequent* presentation of the same
bytes. Verification cannot distinguish the first use from the hundredth.

Measured against the real verifier rather than assumed:

```
verify_id_token(token, nonce="")  -> ACCEPTED
verify_id_token(token, nonce="")  -> ACCEPTED AGAIN   (identical bytes)
```

So anything that obtains the token once -- a hostile SDK inside the app process,
a jailbroken device, a logging sink, a proxy that terminates TLS -- could mint an
unlimited number of sessions until `exp`, typically an hour.

### The nonce cannot close it, for three independent reasons

1. **Omitting it skips rather than fails.** `services/oidc_tokens.py` guards the
   comparison with `if nonce:`, so an absent expected nonce bypasses the check
   instead of failing closed.
2. **It is chosen by the client, not minted by this server.** An attacker holding
   a stolen token reads its own `nonce` claim and presents that value back, so
   the comparison succeeds for exactly the party it is supposed to stop. A value
   the attacker controls on both sides is not a check.
3. **Native Google cannot supply one at all.** `@react-native-google-signin`
   v16.1.5 has no nonce field anywhere in its typings. A mandatory nonce would
   not harden native Google; it would delete it.

Apple is different -- `expo-apple-authentication` exposes `nonce?: string` and
the sheet binds it into the token -- so the nonce is still passed for Apple,
where it earns its place catching an SDK or configuration mismatch. It is not a
replay defence on either provider.

### A live functional defect fell out of the same measurement

The app minted a client nonce, never handed it to the Google SDK, and still
POSTed it. The server therefore took the `if nonce:` branch, found no `nonce`
claim in the token, and raised `missing_nonce` -- **native Google sign-in refused
100% of attempts.** Invisible in production only because the audience/config gate
answers 503 first while `GOOGLE_SIGNIN_NATIVE_CLIENT_IDS` is unset.

Fixed server-side (`verify_assertion(assertion, nonce="")`) rather than in the
app, deliberately: a phone running an older build will keep sending a nonce, and
the fix must not depend on which build is installed.

---

## What was built

`services/federated_replay.py` -- a server-authoritative single-use consumption
ledger. The server is the only party in it, so there is nothing for a client to
be honest about.

- **Key:** `sha256(raw compact JWT)`. The token itself is never written, never
  logged, never returned. A stored ID token is a stored credential; the digest
  answers the only question asked of it ("have I seen exactly this before?")
  without being replayable by whoever can read the table.
- **Decision primitive:** `INSERT OR IGNORE` into `UNIQUE(credential_hash)`,
  then `rowcount == 1`. An INSERT rather than a `SELECT`-then-`INSERT` because
  the whole guarantee is the database serialising two simultaneous presentations
  -- a read-then-write lets both observe an unconsumed credential and both
  proceed, which is precisely the race an attacker with a captured token would
  run deliberately.
- **Why not a caught `IntegrityError`:** on PostgreSQL a failed statement aborts
  the entire transaction, so a caught `UniqueViolation` would leave the sign-in's
  own connection poisoned and take down every statement after it.
  `services/db.py:942` rewrites `INSERT OR IGNORE` to `ON CONFLICT DO NOTHING`,
  so the conflict is resolved by the database without ever raising.
- **Call site:** `bot.py` `federated_native_profile`, *after* signature, issuer,
  audience and expiry are verified (so an unauthenticated flood cannot write
  rows) and *before* `external_identity.resolve` can turn the assertion into a
  session or a signup ticket (so no outcome below that point is reachable twice).
- **Fail-closed:** the `except` catches `ReplayError` and nothing else, on
  purpose. A ledger that cannot say whether a credential was already honoured has
  two answers available, and "come in" is the one that cannot be taken back.

### Persistence

`federated_credential_uses (id, credential_hash UNIQUE, provider, consumed_at,
expires_at)` plus `idx_federated_credential_uses_expires`. Created from
`bot.init_db()` only -- never on demand from a route, because a sign-in is
already mid-transaction on its own connection and a second connection would wait
on a write lock the caller still holds. Not in `AUTO_PK_TABLES`, matching the
`oauth_login_states` precedent.

Retention follows the credential's own `exp` (capped 24h, floored 1h), purged
inline on the write path -- not from a worker, because the sibling
`oauth_login_state.purge_expired` has **zero call sites in this repository** and
its table therefore grows without bound. A retention strategy that depends on a
caller nobody wrote is not a retention strategy.

---

## Contracts this changes

| Surface | Before | After |
| --- | --- | --- |
| `POST /api/mobile/auth/federated` | a token verified repeatedly, repeatedly | second presentation of the same bytes -> `401 invalid_provider_response` |
| native Google leg | `missing_nonce` on every attempt | no nonce asked for; verified on signature/issuer/audience/expiry |
| schema | -- | one new table + one index, additive and idempotent |
| auth events | -- | `federated_refused` gains `reason=credential_replayed` |

Nothing changes for: the canonical `sub` identity model, the refusal to auto-link
on email equality (`account_link_required`), the banned/disabled/access-revoked
gates, legal acceptance, or the web Apple and web Google flows.

---

## Evidence

- `tests/test_federated_replay.py` (17) -- the ledger, an 8-thread barrier race
  asserting exactly one winner *and* `attempts-1` refusals, retention bounds, the
  route, and a storage outage admitting nobody.
- `tests/test_oidc_token_verification.py` (33) -- **new because the cryptographic
  floor under every federated suite had no test at all.** Real 2048-bit RSA keys,
  real signatures, only `requests.get` stubbed so JWKS parsing, kid indexing and
  caching all run as in production.
- `scripts/prove_replay_defence_is_load_bearing.py` -- removes each load-bearing
  piece in turn. **8 of 8 mutations turn the suite red**, including swallowing a
  storage failure into a success. An earlier retention test *survived* because it
  computed its ceiling from `MAX_RETENTION_SECONDS` itself; it now asserts
  against an independently chosen number.
- `scripts/verify_federated_replay_on_postgres.py` -- all checks pass against a
  real `postgres:18`, covering both `services/db.py` rewrites and the 10-way
  race. Falsified by mutation (`INSERT OR IGNORE` -> `INSERT`), which produced
  exactly the `InFailedSqlTransaction` poisoning the design exists to avoid.

---

## Still owner-blocked (not a code gap)

Native Google remains gated at 503 until `GOOGLE_SIGNIN_NATIVE_CLIENT_IDS` is
set; the iOS OAuth client ID, the Apple `.p8`, and the Google API Services User
Data Policy consent are owner actions. The provider buttons stay inactive.

---

## For another agent

**Do not implement a second replay mechanism.** Two ledgers that disagree about
whether a credential was spent is strictly worse than one, and the failure is
silent: the looser of the two decides.

If you need single-use semantics for a *different* credential, the extension
point is `federated_replay.consume(provider, credential, expires_at_epoch=...)`
with a new `provider` value -- the table is keyed on the digest, not on anything
provider-specific, and the retention bound already comes from the credential. If
what you need is single-use semantics for something that is not a bearer token
(a ticket, a state parameter, an idempotency key), that is `oauth_login_state`
or a new table, not this one -- but read its missing `purge_expired` call site
before copying its shape.
