# Proposal: a live CJ order path

**Status: steps 1, 3 and 4 of §4 are built. No live order can be placed, and
nothing this deployment can be configured to do places one.**

Written 2026-09-17 against `d09f3958`. Requested by Roody after the dropshipping
pricing fix landed, with the standing instruction that no real CJ order may be
paid or confirmed without separate explicit approval to spend money. This
document is the thing to argue with *before* that approval is asked for.

### What changed since it was written

The header used to read "Nothing here is implemented. No code in this document
exists." That stopped being true, and a status line that describes a proposal
after the code has landed is the same defect this subsystem already fixed in its
Sandbox badge: a constant that cannot track the thing it describes.

- **§2.1 paid-order allowlist** — landed as `b86dc2f9`. Applied at three gates,
  not the two named below; `quote_for_order` is a third.
- **§2.2 method split** — `CJAdapter.create_sandbox_fulfillment` /
  `create_live_fulfillment` over a shared `_create_fulfillment`. `dispatch`
  chooses by re-deriving the environment from the intent's own frozen
  `isSandbox`, which is covered by `snapshot_hash` — not from a parameter. An
  intent created in sandbox therefore cannot be sent live by a later
  configuration change; it is refused.
- **§2.3 steps 1–2** — `outbox.funding_state` is now written at insert
  (`FUNDING_APPROVAL_REQUIRED` for live, `FUNDING_NOT_READY` for sandbox), read
  by `claim`, and enforced by `require_funded` before any live send. The
  merchant-facing reason `supplier_funding_required` has words in the app.

**§4 step 5, the approval gate, has not been passed, and nothing past it is
built.** Three independent locks hold. Each was verified by mutation — the test
suite fails when any one of them is removed, which is the only evidence worth
anything here:

1. `policy.live_fulfillment_path_exists()` returns `False`, and `require_live`
   checks it *first*, before reading any environment variable. No combination of
   Railway variables reaches a live order;
   `test_no_environment_variable_reaches_a_live_order` asserts that over the
   product of every mode and flag value. Flipping this one line is what approval
   to spend money would authorise, and it needs an edit, a review and a deploy.
2. `funding_state` must equal exactly `"FUNDED"`, and nothing in `services/` or
   any root-level worker writes that value — pinned by a source walk, so the day
   something does write it is the day that test fails.
3. `connections.py` constructs `CJAdapter(environment="SANDBOX")` at both call
   sites, and `create_live_fulfillment` refuses unless it is `"LIVE"`.

Step 2 of the ordering — deploying `supplier_worker` with
`CJ_RECONCILIATION_ENABLED` — is still **not done** and remains a hard
prerequisite for step 6. The §5 questions are still open.

---

## 1. What already exists, and why that matters

The common failure mode for a document like this is to propose building
machinery that is already there. It is almost all already there. The sandbox
path is not a toy that would be thrown away for a real one — it is the real
path with the money removed.

Already built and exercised by 738 passing tests:

| Concern | Where | State |
|---|---|---|
| Intent row, immutable snapshot, digest | `fulfillment.py:490` `create_intent` | done |
| Idempotency key | `UNIQUE(connection_id, idempotency_key)`, `fulfillment.py:286` | done |
| One live order per customer order | partial index `uq_supplier_live_canonical_order` on `(order_id) WHERE superseded_at IS NULL`, `fulfillment.py:373` | done |
| Outbox state machine | `READY / SENDING / UNKNOWN / RECONCILE / LINKED / BLOCKED`, `fulfillment.py:158` | done |
| Ambiguous-write handling | `SupplierError(ambiguous_write=True)` → `UNKNOWN` → read-back, `fulfillment.py:914` | done |
| Never-sent proof for safe retry | `NEVER_SENT_STATES`, `_recoverable_intent` | done |
| Lease-based drain, attempts, backoff | `business_os_supplier_outbox.lease_token/available_at/attempts` | done |
| Order read-back and identity match | `_validate_observed`, `ORDER_IDENTITY_MISMATCH` | done |
| Webhook reconciliation | `webhooks.py:165` `_mark_dirty` → `RECONCILE` | done |
| Freight quote staleness | enforced twice, at freeze and at spend | done |
| Funding vocabulary | `FUNDING_STATES`, `outbox.funding_state` column | **column exists, never written** |

So this proposal is not "build an order system". It is four specific changes,
one of which is the only one that actually spends money.

---

## 2. The four changes

### 2.1 Require the customer order to be **paid** — do this one regardless

This is the one I would land even if live ordering is cancelled, and it is the
gap I did not expect to find.

`create_intent` (`fulfillment.py:618`) and `dispatch` (`fulfillment.py:861`)
both gate on:

```python
if canonical["status"] in {"cancelled", "refunded", "disputed"} or canonical["listing_type"] != "physical":
    raise FulfillmentError("order_not_eligible")
```

That is a **denylist**. An order sitting in `pending_payment` is not in the set,
so it passes. Today that is harmless because the only reachable path is sandbox
and `marketplace_orders` is empty in production — zero rows, no customer has
ever bought anything. The moment a live path exists it stops being harmless: it
is the difference between "we ship goods when paid" and "we ship goods unless
someone already told us not to."

Proposed: an allowlist, named as such.

```python
#: Statuses in which a real supplier order may be created. An allowlist because
#: the question is "has this been paid for", and a denylist answers a different
#: question -- "has anything gone visibly wrong yet" -- which is the same answer
#: only until a new status is added.
ORDERABLE_ORDER_STATUSES = frozenset({"paid", "processing", "fulfilled"})
```

with `pending_payment` and every unrecognised value refused. The existing
denylist stays as well; they are cheap and they fail in the same direction.

**Test:** parametrize over every status string that appears anywhere in the
codebase plus `""`, `None`, `"PAID "`, and an invented one, asserting only the
allowlist reaches intent creation. Negative control: the current denylist must
fail the `pending_payment` case.

**Risk if skipped: goods shipped for unpaid orders.** This is the highest-value
item in the document and it is independent of everything else.

### 2.2 Split the order method in two, rather than widening the sandbox one

`cj.py:665` `create_sandbox_fulfillment` does three jobs: it validates the
payload shape, it enforces sandbox, and it calls CJ. The tempting change is to
add an `if live:` branch. That would be wrong — it puts the money decision
inside a function whose 40 lines of shape validation are the part everyone
reads, and it makes every existing sandbox test a test of a function that can
now spend money.

Proposed shape:

- `_create_fulfillment(payload)` — private, all the shape validation, the
  `createOrderV2` call, the identity read-back. No environment opinion at all.
- `create_sandbox_fulfillment(payload)` — asserts `isSandbox == 1`, environment
  is SANDBOX, then delegates. Behaviour unchanged; existing tests unchanged.
- `create_live_fulfillment(payload)` — asserts `isSandbox == 0` **explicitly
  present as an int**, environment is LIVE, funding approved, then delegates.

`isSandbox=0` must be sent explicitly rather than omitted. CJ's default is not
ours to assume, and an absent field is indistinguishable from a serialisation
bug.

The two public functions must not be reachable from one call site that picks
between them with a boolean. `dispatch` chooses by the intent's own recorded
environment, frozen into `snapshot_json` at `create_intent` time, so an intent
created in sandbox can never be dispatched live even if the deployment flips
underneath it while the row sits in the outbox. That is the property a boolean
parameter would quietly destroy.

### 2.3 Funding: the actual money, and the only irreversible step

Today three functions refuse unconditionally:
`policy.require_funding_disabled()`, `fulfillment.fund_fulfillment()`,
`cj.CJAdapter.fund_fulfillment()`. The `funding_state` column exists with a
`FUNDING_NOT_READY` default and is never written.

CJ's `payType=3` is the only value the current payload builder allows, and it
means direct payment from the CJ account balance. So "funding" here is not a
card charge we control — it is CJ drawing down a prepaid balance held in the
merchant's CJ account. That reframes the risk usefully: the blast radius of a
bug is bounded by the balance, and a balance kept deliberately small is a real
control, not a comforting one.

Proposed sequencing, and I would not compress it:

1. `funding_state` starts being written, still always `FUNDING_NOT_READY`.
   Ship. Observe. No behaviour change.
2. `FUNDING_APPROVAL_REQUIRED` written for intents that would otherwise be
   ready. A merchant-facing surface shows them. Nothing sends. Ship. Observe.
3. Approval transitions to `FUNDED`, and `dispatch` sends **only** `FUNDED`
   intents in live mode. This is the step that spends money and is the step that
   needs Roody's explicit approval.

Step 3 is where a first live order happens. It should be one order, for one
cheap item, to a real address, watched.

### 2.4 The environment flag, and what it may not be

`policy.fulfillment_environment()` (landed in `d09f3958`) probes
`require_sandbox` and returns `SANDBOX` / `DISABLED` / `LIVE`, with `LIVE` gated
on `live_fulfillment_path_exists()` which returns a hardcoded `False`.

When the live path exists, that function starts returning `True` — and that is
the single line that makes a `Live` badge possible. It must not become an
environment variable. A merchant-visible claim that real orders are being placed
should follow the existence of code that places them, not a Railway variable
that someone can set on a Tuesday.

---

## 3. What is *not* in scope, and why

**Multi-merchant scale.** `policy.multi_merchant_scale_blocker()` returns
`BLOCKED_BY_EGRESS_ARCHITECTURE`. Railway egress IPs were measured as unstable
under one `CJ_EGRESS_GROUP` label (`CJ_EGRESS_ARCHITECTURE.md`) and CJ's ceiling
of three accounts and ten business calls per second is *per outbound IP*. One
merchant cannot breach a three-account ceiling, so this blocks scale, not the
first live order. It should stay blocking.

**Reconciliation in production.** `CJ_RECONCILIATION_ENABLED` is unset and no
`supplier_worker` runs in the Procfile. **This is a hard prerequisite, not a
nice-to-have:** `dispatch` settles a freshly-created order into `UNKNOWN` with
`awaiting_create_readback` and relies on a later tick to read it back and reach
`LINKED`. With no worker, a live order would be sent and then never confirmed,
and the webhook path pushes state to `RECONCILE` expecting the same worker to
drain it. Deploying the worker must precede step 3 above.

**A funding UI.** Out of scope here; step 2 above needs one and it should be
designed against a real `FUNDING_APPROVAL_REQUIRED` row rather than in advance.

---

## 4. Ordering

1. §2.1 paid-order allowlist — independent, do now
2. Deploy `supplier_worker` + `CJ_RECONCILIATION_ENABLED`, still sandbox —
   proves the drain works before anything irreversible depends on it
3. §2.2 method split — no behaviour change, all existing tests must stay green
4. §2.3 steps 1–2 — funding state observable, still nothing sent
5. **approval gate** — Roody, explicitly, to spend money
6. §2.3 step 3 + §2.4 — one watched live order

Rollback for each of 1–4 is a revert; none of them can have placed an order.
Rollback after 6 is not a revert, because an order placed at CJ stays placed —
which is the reason for the ordering.

---

## 5. Open questions for Roody

1. **Whose CJ balance?** `payType=3` draws on the CJ account the API key belongs
   to. For a single merchant that is theirs. Is that the intended commercial
   model, or is PulseSoc meant to front the cost and settle separately? The
   answer changes §2.3 substantially and I do not want to guess it.
2. **How small is the first live order?** I would suggest the cheapest catalogue
   item (production has variants costing $0.53) shipped to an address you can
   physically check.
3. **Does a merchant approve each order, or a standing authorisation?** Step 2
   of §2.3 assumes per-order approval, which is safest and most annoying.
