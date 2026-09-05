# Production probes — 2026-09-04

Target: `https://pulsesoc.com`, Railway deployment `61319524-968c-4e09-8353-8362d4179e31`,
running the exact release SHA `a4226e29`.

These are Items 23–25 of the consolidation closeout: verify against production,
not against a test database, that the three subsystems this release touched are
actually deployed and actually behaving as their contracts claim.

## What could and could not be probed

Entering credentials is out of scope for this role, so every authenticated
surface can be verified only as far as *"the route is deployed and it refuses
me"*. That is a real check — a 401 and a 404 are different facts, and the
difference is exactly what a missing route pack would produce — but it is not a
functional check. Functional verification behind auth is deferred to device QA,
where the app signs in as its owner.

One probe was an exception. The Messenger idempotency state is readable from the
database catalog directly, so it did not have to be taken on the endpoint's
word. That is the probe that found something.

## Health and route registration

| Probe | Result |
|---|---|
| `/` | 200, 0.72 s |
| `/health` | `ok:true`, database connected, latency 8.32 ms, `route_packs:true` |
| `/health/ready` | `ok:true`, `status:ready`, postgresql, 12.06 ms |
| `/health/routes` | **13/13 route packs `registered:true, error:null`**, `missing:[]` |

The route-pack check matters more than it looks. Optional packs register inside
`except Exception` blocks so a broken feature cannot block boot — the documented
trade-off is that a subsystem can silently vanish in production and present as a
404 that looks like a routing bug. All 13 registered, including
`private_office` and `pulse_communications_v2`.

## Item 24–25 — Premium and Private Office

Routes were taken from the source rather than guessed. That correction mattered:
`/api/premium/tiers` and `/api/premium/plans` return 404 because **they do not
exist**, not because anything failed to deploy. Probing invented paths and
reading the 404 as a deployment failure would have been a fabricated defect.

| Route | Status |
|---|---|
| `/api/premium/status` | 401 `Login required.` (+ `login_url`) |
| `/api/premium/status-center` | 401 |
| `/api/pulse/premium/status-center` | 401 |
| `/api/premium/usage-center` | 401 |
| `/api/pulse/premium/usage-center` | 401 |
| `/api/pulse/premium/identity-effects` | 401 |
| `/api/pulse/premium/profile-theme` | 401 |
| `/api/private-office/entitlement` | 401 |
| `/api/private-office/facts` | 401 |
| `/admin/health/messenger-idempotency` | 403 `Admin access required.` |

Every one is deployed and gated. No route that should require auth is open. The
admin endpoint returns 403 rather than 401 because it distinguishes
"authenticated but not admin" from "not authenticated" — consistent with its
source.

**Verified:** deployment and gating. **Not verified:** behaviour behind the gate.

## Item 23 — Messenger idempotency: DEGRADED, and legitimately so

`/admin/health/messenger-idempotency` exists, per its own docstring, "so that a
deployment running on application-level idempotency alone is visibly DEGRADED
rather than indistinguishable from one with the database gate installed." It is
admin-gated, so instead of authenticating I ran the installer's own catalog
query — the same `pg_index` inspection and the same shape comparison from
`pulse_communications_v2/service.py` — directly against the production database.

**Result: the unique index is absent. Hard uniqueness is NOT in force.**

```
index idx_comm_v2_messages_client_idem on comm_v2_messages
  present: False

duplicate audit
  duplicate_groups: 11
  excess_rows:      17
  comm_v2_messages rows: 1006
```

This is the installer working correctly, not failing. It counts duplicates
*before* attempting creation, precisely so that "blocked by historical data" is
established by looking at the data rather than by pattern-matching a driver's
error string. Finding 11 violating groups, it declined to create the index,
recorded state `IDEMPOTENCY_INDEX_BLOCKED_BY_DUPLICATES`, and did not block
boot. It also did not delete anything.

### This release did not cause it

The obvious worry is that the release introduced the duplicates. It did not, and
the dates settle it in both directions:

- The installer itself first appears in `689a0e45`, dated **2026-09-04** — it
  shipped in *this* release. Before today production had no idempotency gate of
  any kind.
- The 11 duplicate groups span **2026-07-29 to 2026-09-03**. The newest predates
  the deployment. Every one was written by a production that had no constraint
  to violate.

So the sequence is: five weeks of unprotected sends accumulated 11 collisions;
this release added the detector; the detector found them and said so. The
degraded state is *newly visible*, not newly true.

### What the duplicates actually are

All 11 groups are in `conversation=5`, from `sender=1` and `sender=4`, with
`native-`-prefixed client ids — one conversation, and the client ids identify it
as native-app traffic. Each group's rows land within 0–13 seconds of each other.
That signature is a client retry racing itself, which is the exact defect the
index exists to prevent, so their existence is corroborating evidence that the
index is worth having rather than a reason to doubt it.

Largest groups: 5 rows (`native-1788463935767`), then three groups of 3. One
group is `type=voice`; the rest are `text`. None are soft-deleted.

### Why they were not cleaned up here

`scripts/messenger_idempotency_audit.py` names every offending row — it ran
clean against production and listed all 28 rows by id — and it is deliberately
read-only. Its reasoning is sound and I did not override it: deleting a
duplicate destroys a message a real person sent, and the copy worth keeping is
not always the oldest, because the survivor may be the one carrying reactions,
replies pointing at its id, or a read receipt. That is a judgement call for a
human with the conversation in front of them.

Deleting production rows is also outside what a release closeout should do
unilaterally. **Filed as a follow-up, not actioned.**

### Impact while it stands

The send path stays correct without the index — it does a lookup plus a
conflict-safe insert — it loses only the guarantee under a true race. Messenger
is not broken; it is running on application-level idempotency alone, which is
the documented DEGRADED state.

## Incidental finding: the audit script cannot run under `railway run`

`messenger_idempotency_audit.py` imports `bot` to reuse its connection helper,
and `bot` refuses to boot without a stable `FLASK_SECRET_KEY` when it detects a
deployed environment. `railway run` injects `RAILWAY_*` variables, so a
read-only audit run from a laptop trips a guard meant for gunicorn workers.

The guard is correct and I did not weaken it; the documented
`PULSESOC_ALLOW_EPHEMERAL_SECRET=1` opt-out is the intended escape and the audit
then ran fine. Worth a follow-up so the read-only path does not need it.

## Follow-ups raised here

1. **Resolve the 11 duplicate groups (17 excess rows) so the unique index can
   install.** Requires a human decision per group on which row survives. Until
   then Messenger has no database-level race guarantee. Row ids are listed by
   `scripts/messenger_idempotency_audit.py`.
2. **Re-run this probe after that cleanup** to confirm the state flips to
   `IDEMPOTENCY_INDEX_INSTALLED` with `hard_uniqueness_active: true`.
3. **Let the audit script connect without importing `bot`**, so a read-only
   audit does not require a secret-key opt-out.
