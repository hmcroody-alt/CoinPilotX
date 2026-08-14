# Sentinel Mission 5 — Payout Security

Module: `services/sentinel/financial_detections.py` (payout threat
signals), gated by `SENTINEL_PAYOUT_RISK_ENABLED` (chains on the master
detection switch). Payouts are the exit door for stolen value, so they get
sequence-aware attention — and exactly zero enforcement authority.

## Signal model

Payout threat analysis combines independent signals (destination change
recency, post-recovery timing, velocity anomaly, amount anomaly, …):

- **A single signal never flags.** One signal caps the composite at ≤ 0.4
  and produces no finding (tested). A seller changing their bank account is
  a life event, not an attack.
- **Seasonal/expected spikes are damped** and do not flag (tested) — a
  holiday sales rush is HIGH VOLUME != FRAUD in action.
- **Two or more independent signals** produce PAYOUT_ABUSE_SUSPECTED with
  an explicit note that Sentinel *cannot execute* any response — the
  finding text itself carries "cannot execute" (tested).
- Unknown signal names raise instead of being silently absorbed.

## Relationship to FAT chains

The highest-value payout threat is the ATO drain: identity compromise
followed by payout request. That correlation lives in the FAT sequence
chains (see financial_ato.md); payout signals feed the same review queue.

## Authority ceiling — the point of this design

Sentinel cannot issue, execute, cancel, retry, hold, or re-route a payout.
`issue_payout`, `execute_payout`, `cancel_payout`, `retry_payout`,
`hold_funds`, and `alter_payment_routing` are FORBIDDEN_CAPABILITIES:
absent from every module surface (scanned), refused with evidence by the
lock's `attempt()`, with no bypass parameter, no admin override, and no
kill switch that enables them. A PAYOUT_ABUSE_SUSPECTED incident is a
prioritized request for human review — the payout itself proceeds or halts
only by owner/platform action outside Sentinel.
