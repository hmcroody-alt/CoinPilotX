# Sentinel Threat Model & Self-Defense (Stage 30)

Sentinel is itself an attack target: a system that observes everything and
can (eventually) act is exactly what an attacker wants to subvert. This
document enumerates the threats Sentinel defends against *in its own
design*, and the residual risks V1 accepts.

## T1 — Prompt injection through observed content

**Vector**: hostile text in posts/messages/emails crafted to be read by
UNDX or an operator ("ignore previous instructions", fake approval
phrases like the UNDX write-approval string).
**Defense**: content is data (SC10). `ai_security.scan_for_injection`
flags heuristically (honestly labeled `heuristic_regex_v1` — NO FAKE AI);
`wrap_untrusted` neutralizes nested boundary markers; a flag produces a
capped-severity event, never punishment or execution. UNDX reads only
redacted, structured surfaces — never raw content with authority.

## T2 — Model escalation (UNDX going beyond advisory)

**Vector**: a model output treated as a command; a future developer wiring
model verdicts into enforcement.
**Defense**: structural, not procedural — `identity` caps model tier at
ADVISORY (construction error otherwise); `authority.check` denies models
above SUGGEST regardless of grants; `undx_interface` exposes zero mutation
entry points (regression test scans exports); `submit_analysis` forces
severity `info`.

## T3 — Sentinel used as a financial weapon

**Vector**: compromised or buggy automation issuing refunds, payouts,
transfers, rollbacks.
**Defense**: Sentinel writes only `sentinel_*` tables (regression-tested);
invariants are read-only; the runbook registry structurally rejects
financially destructive names; no shipped runbook mutates anything (SC6).

## T4 — Kill-switch bypass / default-on drift

**Vector**: automation running because someone forgot to disable it.
**Defense**: deny-by-default chain — master, domain, and per-runbook
switches must ALL be explicitly on (SC3); emergency switch overrides
everything including ingest; defaults are regression-tested OFF.

## T5 — Evidence tampering / audit-trail erasure

**Vector**: attacker (or embarrassed operator) editing history.
**Defense**: sha256 hash chain from a fixed genesis; `verify_chain`
recomputes every link and is itself an invariant and a runbook; no
update/delete functions exist in the module (tested); tampering and row
deletion are both detected (SC5).

## T6 — Super-key theft

**Vector**: one credential that unlocks everything.
**Defense**: no such credential exists (SC11). The regression suite bans
the string patterns from source so one cannot be quietly added.

## T7 — External-signal poisoning

**Vector**: a vendor feed marking innocent users as critical-risk to
trigger automated punishment.
**Defense**: adapter severity capped at `medium`, `verified: False`,
signal-is-not-guilt doctrine (SC9); single signals cannot open incidents
(SC8); V1 has no punishment machinery at all.

## T8 — Blast-radius runaway

**Vector**: a correct-but-looping automation touching every account.
**Defense**: unbounded budgets are unconstructible (SC14); ceilings
100 actions/hr, 500 entities; exhaustion denies without exception paths.

## T9 — Injection into Sentinel's own store (SQL)

**Vector**: hostile event payloads breaking out of queries.
**Defense**: parameterized queries only via services/db.py; no string-
interpolated SQL against user input; no eval/exec/subprocess in the
package (regression-tested).

## T10 — Collateral damage to protected systems

**Vector**: Sentinel touching the real-time audio/live stack.
**Defense**: SC13 — sentinel source may not even *name* the protected
stack (regression-tested); no imports of audio modules; api.py is unwired.

## Accepted residual risks (V1, explicit)

1. Kill switches are env vars: anyone with deploy-env access can enable
   automation. Mitigation is Railway access control; V1 adds no second
   factor. (Phase 3: approval records in-band.)
2. Injection detection is heuristic regex — it will miss novel phrasing
   and flag some benign text. It is labeled as such; its output is
   evidence, never verdict.
3. `BudgetTracker` is in-process; multi-worker deployments would need a
   shared budget store before any mutating runbook ships (Phase 2 gate).
4. The evidence chain detects tampering but cannot prevent a DB admin
   from rewriting the whole chain; external anchoring is a later phase.
5. api.py trusts the platform's existing admin-session check; it is
   read-only and unwired, so exposure is currently zero.
