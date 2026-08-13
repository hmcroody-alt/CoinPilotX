# External Data Retention & Deletion (Mission 4, Stages 24–26)

## Retention

Every `SentinelExternalObservationV1` row carries a mandatory `expires_at`
(per-provider TTL at record time). Expiry is enforced at read time:

- Fresh-only queries (`kev_cve_ids`, threat-intelligence endpoint, fusion)
  filter on `expires_at > now`.
- Stale observations **degrade loudly**: staleness notes on surfaces, a
  `stale_external_observations` counter in self-health, and
  `stale_external_intelligence` in the owner summary. Stale data is never
  silently treated as current.

The share audit (`sentinel_external_share_audit`) is append-only and retained
as a permanent record of what left PulseSoc — deleting audit rows would be
destroying evidence about our own sharing, so no delete path exists.

## Deletion honesty (Stage 26) — no fake deletion

Sentinel does **not** pretend it can delete data from a vendor's systems.
There is deliberately no `delete_from_provider()` / "vendor forget" function:
tests assert its absence. What we can honestly guarantee:

1. **Local expiry is real.** Expired observations stop influencing every
   surface immediately (enforced by the read-time filters above).
2. **Minimization bounds the exposure.** Because only closed-vocabulary
   indicators (hashes, package coordinates, CVE ids, digests) are ever sent,
   there is no user content at any vendor to delete in the first place.
3. **The audit trail shows exactly what was shared** — provider, purpose,
   indicator, data classes, timestamp — so any future deletion request to a
   vendor can be scoped precisely and pursued contractually, by a human.

## User-data deletion requests

External observations reference threat indicators, not users. Internal
identifiers (`pulse_id`, `user_id`, `email`, `phone`) are barred from
outbound payloads by the minimization gate and classified SENSITIVE by
`classification.py`, so account-deletion flows have nothing to purge from
this subsystem beyond local rows — which expire regardless.
