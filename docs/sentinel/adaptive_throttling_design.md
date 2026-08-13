# Adaptive Throttling — DESIGN ONLY (Mission 3, Stage 26)

**Status: NOT IMPLEMENTED. Nothing in this document exists in code, and
nothing in Sentinel may execute a throttle.** This is the policy
contract a future, human-approved implementation would have to satisfy
before a single line is written. It exists so that when throttling is
considered, the safety envelope is already decided — not improvised
under incident pressure.

## Why design-only

Throttling is the first Sentinel-adjacent capability that would ACT on
users instead of observing them. Even "just rate limiting" can lock real
people out of their accounts during a false positive. Under the Sentinel
constitution (SC3: observe, never enforce; Stage 35: all automation
OFF), any enforcement capability requires its own mission, its own
review, and the owner's explicit sign-off — this document is the
entry ticket, not the permission.

## Policy contract

Every throttle policy MUST be a structured, versioned record with ALL of
the following fields. A policy missing any field is invalid and must be
rejected at load time (fail closed):

| Field | Contract |
|---|---|
| `policy_id` / `version` | Immutable id + monotonically increasing version; changes are new versions, never edits. |
| `duration` | Hard-bounded lifetime of any applied throttle (e.g. ≤ 30 minutes). No indefinite throttle can exist. |
| `scope` | Exactly what is slowed: named endpoint family (e.g. login, recovery request) × subject class (one network ref, one account). Never platform-wide, never "all traffic". |
| `max_affected_users` | Absolute cap on distinct accounts a single policy activation may touch (e.g. ≤ 50). Exceeding the cap aborts the activation, not the cap. |
| `reason` | Mandatory human-readable justification bound to the triggering incident key(s). No incident, no throttle. |
| `confidence_threshold` | Minimum detection confidence to activate (e.g. ≥ 0.7 from ≥ 2 independent signals — same fusion rule as HIGH_RISK). Single-signal detections can never trigger it. |
| `expiry` | Every activation records `expires_at` at creation; expiry removal is automatic and unconditional, requiring no human and no healthy worker. |
| `verification` | Post-activation check the system must run and record: measured effect (requests slowed, accounts affected) vs. declared scope; any deviation auto-terminates the activation and opens an incident. |
| `kill_switch` | A single environment switch that instantly disables ALL throttling regardless of state, plus a per-policy switch. Default state: OFF. Kill-switch state is reported in self-health. |

## Additional invariants a future implementation must ship with

1. Throttle ≠ block: a throttle may delay, it may never deny outright,
   lock an account, or invalidate a session.
2. Full audit: every activation, verification result, and termination is
   an evidence-chain entry with the policy version and incident keys.
3. Owner visibility: active throttles appear in the owner summary while
   live, with affected-user counts (real counts only).
4. Exclusion honor: subjects excluded under Stage 27 can never be
   throttled by the excluded rule's incidents.
5. Invariant suite: "no active throttle without live incident",
   "no activation past expiry", "no activation above
   max_affected_users" — VIOLATED opens a SECURITY incident.
6. Testing: a false-positive suite proving normal users are never
   throttled must exist BEFORE the first production activation.

## Explicit non-goals

No autonomous escalation of duration or scope, no model-triggered
activation (UNDX output is never authority — SC2), no third-party
reputation input (Mission 4 territory), no reuse of this contract for
bans, suspensions, or financial holds — those are out of scope for
Sentinel entirely.
