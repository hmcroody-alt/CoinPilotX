# External Data Sharing Policy (Mission 4, Stages 5–6, 25, 27)

Default posture: **MINIMIZE**. Nothing leaves PulseSoc unless a documented
policy gate allows it, and then only the minimum indicator needed.

## The single gate

Every outbound provider call goes through `enrichment_policy.evaluate()`.
There is exactly one gate; adapters cannot bypass it because they receive the
payload only after `minimize()`.

Allowed purposes (closed set): `VULNERABILITY_TRIAGE`,
`SUPPLY_CHAIN_REVIEW`, `INFRASTRUCTURE_REPUTATION`, `MALWARE_INDICATOR_CHECK`,
`DEVICE_INTEGRITY_CHECK`.

Disallowed by construction: marketing/analytics, user profiling, bulk export,
any purpose not in the closed set (deny-by-default, SC15).

## What may be sent

- Closed-vocabulary indicators only: package coordinates, CVE ids, hashes,
  digests, IPs/domains/ASNs under an allowed purpose, vendor request ids.
- **File policy: hash lookup only.** Upload capabilities are absent from the
  provider vocabulary — there is no code path that could transmit file
  content, and tests assert no provider spec offers an upload capability.
- Never: raw user content, message text, media, secrets, credentials,
  internal identifiers (`pulse_id`, `user_id`, `email`, `phone`), or anything
  classifying above INTERNAL (`classification.external_share_allowed`).
- `minimize()` strips unexpected fields recursively and records what it
  stripped.

## Never query every visitor (Stage 18)

Infrastructure reputation (Cloudflare) is per-indicator, per-purpose, gated
and budgeted. There is no batch endpoint and no hook into request handling
that would enrich all traffic. Hosting/VPN ASN is context, never malice.

## Audit (Stage 25)

Every outbound share appends to `sentinel_external_share_audit`: provider,
capability, purpose, indicator type/ref, data classes sent, stripped fields,
response status, timestamp. Append-only — no update or delete path exists.
The audit stores digests/refs, not payload bodies.

## Cost controls

Per-provider budgets (minute/hour/day) with negative caching and
single-flight deduplication (Stage 7, 9). Budget exhaustion is a policy
denial: the result is UNKNOWN, never a retry storm.
