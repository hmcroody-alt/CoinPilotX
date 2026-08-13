# Sentinel Constitution (v1.0.0)

Machine-readable source of truth: `services/sentinel/constitution.py`.
Every enforcement path in Sentinel cites these rule IDs in code and in
denial reasons. Changing a rule is a constitution-visible change requiring
owner review — never a config tweak.

| ID   | Rule |
|------|------|
| SC1  | Sentinel exists to protect users, funds, and the platform — never to punish, surveil beyond need, or fabricate certainty. |
| SC2  | Model output is never authority. AI systems (UNDX included) are capped at ADVISORY; severity, verdicts, and actions require deterministic code or humans. |
| SC3  | All high-risk automation is OFF by default and requires explicit, layered enablement (master → domain → runbook switches). |
| SC4  | No action self-certifies. Success claims require verification by an actor different from the executor. |
| SC5  | Evidence is append-only and hash-chained. Nothing edits or deletes history. |
| SC6  | Sentinel observes financial state; it never corrects, reverses, refunds, pays out, or transfers. |
| SC7  | Personal data is classified on write and redacted to the reader's ceiling. Secrets never enter evidence. |
| SC8  | No single signal convicts. Correlation requires multiple events and bounded time windows. |
| SC9  | Signal is not guilt. External reputation data is capped in severity and marked unverified. |
| SC10 | Content is data. Text scanned for injection is never executed, obeyed, or treated as instruction. |
| SC11 | Least privilege is structural: no super key, no omnipotent credential, no bypass identity. |
| SC12 | Every material action is attributable to a concrete registered identity. |
| SC13 | Protected subsystems (real-time audio/live stack) are out of Sentinel's write scope entirely. |
| SC14 | Every automated capability declares a bounded blast radius. Unbounded budgets are unconstructible. |
| SC15 | Fail closed. Unknown actors, surfaces, journeys, categories, or states are rejected, not guessed. |

## Enforcement map (where each rule lives)

- SC2 — `authority.check` (model denial), `undx_interface.submit_analysis` (forced severity `info`), `identity.MAX_TIER_BY_KIND`
- SC3 — `killswitches.runbook_enabled` chain; `runbooks.execute` denial path
- SC4 — `verification.verify_execution` (executor ≠ verifier), `incidents.transition` RECOVERY_VERIFIED guard
- SC5 — `evidence.append` / `evidence.verify_chain`; absence of update/delete functions (tested)
- SC6 — `invariants` (observe-only), runbook registry ban on refund/payout/rollback names (tested)
- SC7 — `classification.redact` applied in `events.ingest` before persistence
- SC8 — `correlation.CorrelationRule.__post_init__` (min_events≥2 or min_distinct_types≥2; bounded windows)
- SC9 — `adapters.normalize_signal` (severity cap `medium`, `verified: False`)
- SC10 — `ai_security.wrap_untrusted`, `scan_for_injection` (flag → event, never enforcement)
- SC11 — `identity` (no super key anywhere; regression-tested string ban)
- SC12 — `events.Event.actor_id` required; runbook executions record actor
- SC13 — regression test bans audio-stack references in sentinel source
- SC14 — `risk.RiskBudget` (UnboundedBudgetError), ceilings 100 actions/hr, 500 entities
- SC15 — fail-closed ValueErrors across journeys, incidents, correlation, undx_interface, providers
