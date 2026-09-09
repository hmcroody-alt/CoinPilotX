# CJ provider policy — what is confirmed, what is not

**Source:** written confirmation from CJ Developer Support, relayed 2026-09-09.
**Status:** current. This document is cited by
`services/business_os/suppliers/policy.py`; if the two ever disagree, the code
is wrong and this file is the thing to check it against.

This exists because for months the CJ integration was gated on a question
nobody could answer, and the gate was written as if the answer were "no". The
cost of that was not caution — it was a blocker that could not be cleared by
doing anything, because the flag it read described an approval that does not
exist as a product. What follows separates the three kinds of statement that
were previously tangled together: things CJ confirmed, things CJ did **not**
confirm, and things that are ours to decide per deployment.

## Confirmed by CJ

1. **The standard API model needs no separate provider approval.** A merchant
   generates an API key inside their own CJ account, hands it to PulseSoc, and
   PulseSoc may hold it server-side and call CJ on that merchant's behalf. There
   is no application, review, or allowlist step in front of this.
2. **Merchant-owned credential custody is the intended design**, not a
   tolerated workaround. The key belongs to the merchant's account; we are the
   software they chose to use it with.
3. **The model repeats independently per merchant.** Serving many merchants,
   each with their own key, is the same arrangement N times, not a different
   arrangement requiring different permission.
4. **Content usage.** CJ product images, videos and descriptions may be stored
   and displayed in merchant-facing tooling for the purpose of selling those
   products. No attribution requirement, no per-supplier permission, no fee.
5. **Sandbox is per order, via `isSandbox=1`.** There is no account-level
   sandbox switch. Sandbox orders are not charged, do not enter the OMS, and
   cannot be mixed with live orders in a single submission.
6. **Webhook registration** is `POST webhook/set`, authenticated with the
   `CJ-Access-Token` header. `openId` is *not* a parameter to `webhook/set`,
   but it is still required to verify inbound webhook signatures.
7. **Inactivity policy.** Seven days without a real order produces a warning;
   thirty days disables API access. Sandbox orders do not count toward this.

## Explicitly NOT confirmed — never claim these

The absence of an approval requirement is not the presence of an agreement.
None of the following exist, and no UI, dashboard, marketing surface, status
field or log line may imply otherwise:

- a partner, platform, white-label, reseller or OEM agreement;
- "Official CJ Partner" status of any kind;
- a master or PulseSoc-owned credential able to reach other merchants'
  accounts;
- any exemption from the per-IP infrastructure limits below.

`policy.PARTNER_AGREEMENT_HELD` is pinned `False` and reported as its own
status key precisely so that a technical confirmation cannot quietly grow a
partner badge. `tests/business_os/test_cj_policy_gates.py` asserts that no
status key containing "partner" is ever `True`.

## Hard infrastructure limits

These are CJ's, not ours, and there is **no whitelist exemption**:

| Limit | Value | Scope |
| --- | --- | --- |
| CJ accounts | 3 | per outbound IP |
| Business API calls | 10 / second | per outbound IP |

Both are enforced in `services/business_os/suppliers/quota.py`, in the
database rather than in process memory, so that multiple gunicorn workers
cannot each believe they hold the whole budget. A fourth account on one egress
group fails closed with `CJ_EGRESS_CAPACITY_EXHAUSTED`.

**The open gap is attestation, not enforcement.** `quota.py` counts per
`CJ_EGRESS_GROUP`, which is a label a human types into an environment variable.
CJ counts actual outbound IP addresses. Those two agree only if somebody
checked, and Railway's outbound address is not documented as static or
dedicated anywhere in this repository. A second region, a rotated address, or a
reused label would all keep our counter happy while CJ sees a fourth account on
one IP. Until an operator sets `CJ_EGRESS_IP_ATTESTED`,
`policy.multi_merchant_scale_blocker()` returns
`BLOCKED_BY_EGRESS_ARCHITECTURE`. Single-merchant use is unaffected: one
account cannot exceed a three-account ceiling however the IPs fall.

## What remains a deployment switch

Provider facts are constants in `policy.py`, not environment variables — a flag
would imply a deployment could decide that a merchant may not use their own
credential, which is not a decision a deployment gets to make. What genuinely
varies per environment is only:

- `CJ_NETWORK_ENABLED` — may this environment reach CJ at all. Production
  leaves it unset and is therefore dark regardless of everything above.
- `CJ_ENVIRONMENT_MODE` / `PRODUCTION_CJ_FULFILLMENT_ENABLED` — may this
  environment place non-sandbox orders. Both currently refuse.
- `REAL_CJ_FUNDING_ENABLED` — never on; `require_funding_disabled()` raises
  unconditionally, so the flag cannot authorize spending even if set.
- `CJ_EGRESS_IP_ATTESTED` — has someone verified the outbound IP claim above.

## Retired

`CJ_HOSTED_CREDENTIALS_APPROVED`, `CJ_MULTI_MERCHANT_SAAS_APPROVED`,
`CJ_SINGLE_MERCHANT_SANDBOX_ALLOWED` and `CJ_CONTENT_REDISPLAY_APPROVED` are no
longer read by any code. Setting any of them grants nothing and withholds
nothing; a test asserts that in both directions, because a variable name that
no longer has an effect is one somebody will eventually set expecting one. An
already-provisioned staging environment may still carry an inert
`CJ_HOSTED_CREDENTIALS_APPROVED=OFF`; that is harmless and is not treated as
configuration.

Earlier reports under `reports/cj-setup/` and `reports/cj-discovery/` were
written while the approval question was open and describe it as a live blocker.
They are superseded by this document rather than rewritten — they are an
accurate record of what was believed at the time, and editing them would
destroy the trail showing why the gate existed.

## Standing prohibition

Access is never to be preserved by creating fake real orders to defeat the
thirty-day inactivity rule. Sandbox orders do not count toward it, and that is
the only legitimate way this integration stays quiet without spending money.
