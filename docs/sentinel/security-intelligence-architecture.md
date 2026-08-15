# Sentinel Security Intelligence Architecture

`python alert_worker.py` is the existing Railway worker reused for bounded
Sentinel polling. It invokes `services.sentinel.runtime` once per normal cycle.
With `SENTINEL_EXTERNAL_INTEL_ENABLED` absent, it makes **zero provider calls**.

```text
Provider read -> policy/budget/cache/circuit breaker -> normalized evidence
          -> correlation -> advisory risk fusion -> admin-only Sentinel views
```

| Provider | Boundary | Status |
| --- | --- | --- |
| Cloudflare Pro | Per-indicator read-only context; exact Pro API scope pending | PARTIAL |
| Sentry | Bounded security-relevant issue/release evidence | PARTIAL |
| Stripe Radar | Existing signature-verified inbox and authoritative payment records | IMPLEMENTED foundation |
| GitHub Security | Open Dependabot, code-scanning, secret-scanning alerts, read-only | IMPLEMENTED runtime, credential pending |
| OSV / NVD | Exact package/version lookup then CVE enrichment | IMPLEMENTED adapter |
| CISA KEV | Bounded public catalog refresh | IMPLEMENTED runtime, disabled by default |

Observations retain provider/capability/event identifiers, evidence digest,
source trust, timestamps, and expiry—not raw payloads. Deterministic keys
deduplicate vulnerability (`repo+package+version+CVE`), Cloudflare
(`zone+indicator+category+time bucket`), Stripe (provider event ID), GitHub
(`repository+alert family+alert ID`), and Sentry (`project+fingerprint`).

External evidence is advisory and cannot block a user, change a payment,
dismiss a finding, or modify a provider.

The privileged read-only Sentinel blueprint remains an explicit owner opt-in.
The established regression suite prevents automatic registration in `bot.py`;
the worker runtime does not need an HTTP surface to ingest evidence.
