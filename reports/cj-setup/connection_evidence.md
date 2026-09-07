# CJ merchant connection and vault evidence

Scope: backend-only, local synthetic fixtures. No real CJ credential read, API
entry created, Railway variable changed, live provider call, purchase, or funding
operation was performed by this implementation subtask.

## Canonical authorities

- `services/business_os/store/service.py::_require_biz_permission` resolves the
  existing S1 membership role. `store.read` is required to inspect and
  `store.manage` to connect; account-hold context is honored before provider I/O.
- Store identity is `business_os_store_storefront.storefront_id`; its joined
  `business_id` is checked, never inferred from a shop/store display name.
- Merchant identity derives from `business_os_business.owner_user_id`, not the
  caller. Ownership transfer does not transfer an old supplier credential.
- Unique durable account-owner claim prevents two merchants racing to claim the
  same verified CJ account. Explicit reauthentication preserves account/shop
  binding and the existing connection ID; it cannot silently replace either.

## Secret authority

Dedicated `business_os_supplier_credential_vault` stores only authenticated
AES-256-GCM ciphertext and metadata. The ordinary connection row stores an opaque
reference. The design reuses the existing Private Office encryption primitive
and at-rest threat model, with a separate supplier namespace.

Required operator-provisioned secrets, with no default/fallback:

- `SUPPLIER_CREDENTIAL_KEYS`: `key_id:32-byte-key` keyring; hex or base64 material.
- `SUPPLIER_CREDENTIAL_KEY_ACTIVE`: explicit active key ID (first ring entry if
  omitted); missing named key fails closed.
- `SUPPLIER_ACCOUNT_INDEX_KEY`: separate stable 32-byte key for HMAC account/key
  fingerprints. It does not rotate with the ciphertext key. Changing it requires
  an explicit ownership/quota-index migration, not a blind deployment change.

None were populated with real secret material. Invalid/missing vault or index
configuration refuses connection before CJ authentication. Structured JSON-array
AAD binds merchant, business, store, connection, reference, provider and version.
Moving/tampering ciphertext or changing any binding fails decryption. Existing
ciphertext keys remain readable during keyring rotation until retired.

This protects database dumps/backups, not a compromised process or environment.
Internal worker capabilities return decrypted credentials only in memory and are
not HTTP handlers or UNDX tools. The internal SecretBundle also deliberately
refuses ordinary JSON serialization. Public responses use explicit allowlists;
safe repr and errors contain no credential values. Malicious shop fields echoing a
credential are rejected, not reflected.

## Lifecycle and health

Authentication validates actual returned aware expiry timestamps, independently
checks settings identity, retrieves shops, and requires explicit active shop
selection before storing a connection. Discovery returns only a bounded shop
allowlist and no credentials. No global/default merchant account exists.

Refresh uses a durable 120-second compare-and-swap lease and version fence,
shared across processes through `services.db`. Only one concurrent refresher
calls CJ. A missing refresh-response openId retains the previously verified ID;
an explicit changed ID fails. Returned expiration metadata remains authoritative.
Failed refresh requires reauthentication, with no silent alternate-account or
repeat-refresh fallback. Worker hydration shares the same refresh/verification
path while requiring the full persisted tenant tuple.

Stored credentials alone are not health: expiry or verification older than five
minutes projects AUTH_EXPIRED/VERIFICATION_REQUIRED. Explicit provider suspension
projects reactivation guidance rather than requesting unnecessary key rotation.
429 and quota errors remain RATE_LIMITED, never inventory state. Provider points
and quotas are persisted through a numeric-only projection. Admin health is
aggregate-only without merchant/account/credential identifiers.

## Executed checks

`/Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python -m pytest -q tests/business_os/test_cj_connections.py tests/business_os/test_cj_vault.py`

Result: **58 passed**, local SQLite and injected synthetic provider fixtures.
Coverage includes cross-merchant and cross-store read/token denial, own-shop
binding, viewer/manager RBAC, business transfer, unavailable storage, invalid and
expired auth, refresh identity/expiry, real two-thread refresh singleflight,
suspension/429/unavailable health, encrypted-only DB and audit records, secret
echo rejection, key rotation, and authenticated ciphertext tampering.

Three isolated runtime mutants were applied without editing production files,
then selected unchanged regression tests were run:

| Mutant | Pytest exit | Outcome |
| --- | --- | --- |
| Remove canonical business permission check | 1 | Killed |
| Add access token to public connection projection | 1 | Killed |
| Remove cryptographic tenant AAD binding | 1 | Killed |

These are synthetic contract/security proofs, not a live CJ or Railway sandbox
acceptance result. HTTP authentication, request telemetry scrubbing, deployments,
and provider approval remain integration-layer responsibilities and require
their own evidence before activating live network access.

## Additional adapter and quota verification

Two additional synthetic suites were added after the adapter/quota agent stopped:
`tests/business_os/test_cj_adapter.py` (58 cases) and
`tests/business_os/test_cj_quota.py` (36 cases). The combined four-suite run passed
**152 tests** in 0.79 seconds after the final serialization-tripwire addition.

Adapter checks include exact HTTP method/path/payload contracts; auth expiry and
refresh; settings/shop identity; bounded nested catalog pagination; exact product
and variant IDs; verified, zero and unknown inventory separated by warehouse;
quote totals preserved without summing components; unknown costs; 429 and
suspension without retries or fake inventory changes; all-secret provider-content
echo rejection; explicit sandbox guards; timeout-after-create ambiguity;
read-back order/tracking identities; subscription-list scope; locked mutation and
funding methods; and both default live-network gates.

Durable quota checks include two independent controllers sharing SQLite pacing;
actual parallel admission races; fixed-IP 3-account cap; pending-to-verified slot
promotion; conservative multiple-key merge; authenticated QPS ceiling; reserved
critical points; malformed quota metadata; late responses unable to replenish
newer debits; shared-egress 429 pause; HTTP-date Retry-After; valid 7,200-second
Retry-After not shortened; bounded fallback; and recovery only with fresh proof.

Three more isolated runtime mutants were killed (pytest exit 1):

| Mutant | Outcome |
| --- | --- |
| Remove adapter sandbox assertion | Killed |
| Convert UNKNOWN inventory into IN_STOCK | Killed |
| Immediately retry provider 429 without backoff | Killed |

No source file was changed to run the mutants; each used a fresh process with a
single runtime monkeypatch and selected unchanged tests. No live network was used.

## Final integration supersedes earlier partial suite counts

Root's final combined CJ run passed 271 tests; the existing commerce/store/
marketplace/bootstrap/inbox subset passed 37. The checked-in mutation runner now
includes invalid-signature acceptance and blindly retrying an ambiguous create:
8/8 killed, with passing baselines and assertion-only failure classification.
See `CJ_ROLLOUT_AND_ACCEPTANCE.md` for the complete final evidence and limits.

The final integration also fixes credential-sensitive shared middleware body
caching/sampling, Flask's implicit AuthBundle dataclass serialization, one
canonical order being enqueued across two owned store connections, and invalid
provider order-ID coercion. These have executable regression tests, not only a
static review finding. Funding/production/network gates remain dark.

## Final subtask verification and review

Final six-suite command, after all connection telemetry and route-tripwire work:

`/Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python -m pytest -q tests/business_os/test_cj_connections.py tests/business_os/test_cj_vault.py tests/business_os/test_cj_adapter.py tests/business_os/test_cj_quota.py tests/business_os/test_cj_routes.py tests/business_os/test_cj_gateway.py`

Result: **195 passed in 1.35 seconds**. This is a subset of the root task's final
combined acceptance suite, not its total.

The additional route/gateway tests use real Flask signed-cookie sessions and the
shared Commerce CSRF gate with a minimal session-backed bot identity resolver.
They prove failed login, CSRF and forged bearer denial; actual cross-merchant
connection access denial; strict request body/size checks; no Flask credential
body cache; recursive secret-field and dataclass credential-output blocking;
safe errors; preservation of long provider Retry-After; admin authorization;
and caller-side sandbox rejection before the fulfillment handler.

Gateway checks prove ownership before cache reads, connection/business/store cache
scope, membership revocation during a provider read, immutable scoped snapshots,
retail fields isolated in unpublished merchant import drafts, exact canonical
product/variant binding, canonical product transfer during network I/O denied,
single-flight cache races, bound-shop subscription queries, and 429 without zero
inventory or snapshot fabrication.

`record_activity` now captures numeric provider quota and safe health failures
after hydration; successful sync updates `last_sync_at`, and unrelated reads do
not fabricate CONNECTED status. Admin status reports initialized vs absent worker
and webhook-inbox state, queue counts and lag without asserting worker execution
when no completion has been observed.

Reproducible anti-vacuity runner:

`/Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python scripts/verify_cj_security_mutations.py`

The initial six cases each recorded baseline pytest exit 0, mutant exit 1, and
`killed=true`. Root owns additional fulfillment/webhook mutation cases.

Security review identified and root repaired: canonical orders could previously
be enqueued twice through different owned stores/connections; intent dispatch
failed to record connection/quota health after hydration; Flask dataclass JSON
serialization could bypass the output guard; route Retry-After was capped; and
global bot middleware could consume/cache supplier credential request bodies
before the route. Root owns the dedicated global-middleware boundary tests and
the final combined evidence for those integration-layer changes.
