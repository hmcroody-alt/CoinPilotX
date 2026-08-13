# External Provider Decisions (Mission 4)

Per-provider decision record. "CONFIGURED" means credentials present where
required; it is never a claim the provider works. No provider is FUNCTIONAL
until a successful call is recorded — none has been called yet.

| Provider | Decision | Trust class | Credentials | Kill switch (default OFF) | Notes |
|---|---|---|---|---|---|
| OSV.dev | **ADOPT** | AUTHORITATIVE_ECOSYSTEM | none needed | `SENTINEL_OSV_ENABLED` | Primary vulnerability source; free, keyless, per-package queries |
| NVD | **ADOPT** | AUTHORITATIVE_GOVERNMENT | optional `SENTINEL_NVD_API_KEY` (raises budget) | `SENTINEL_NVD_ENABLED` | CVE enrichment only; keyless at low budget |
| CISA KEV | **ADOPT** | AUTHORITATIVE_GOVERNMENT | none needed | `SENTINEL_KEV_ENABLED` | Bulk catalog sync; KEV∧deployed elevates incidents |
| GitHub Security | **ADOPT (needs creds)** | AUTHORITATIVE_REPOSITORY | `SENTINEL_GITHUB_APP_TOKEN` required | `SENTINEL_GITHUB_SECURITY_ENABLED` | Read-only alerts; secret **values** never read; no dismiss/autofix/merge |
| Cloudflare intel | **PILOT (needs creds)** | COMMERCIAL_INTELLIGENCE | `SENTINEL_CLOUDFLARE_INTEL_TOKEN` required | `SENTINEL_CLOUDFLARE_INTEL_ENABLED` | Per-indicator IP/domain/ASN reputation; never every visitor; hosting ASN ≠ malice |
| VirusTotal | **PILOT (needs creds)** | COMMUNITY_INTELLIGENCE | `SENTINEL_VIRUSTOTAL_API_KEY` required | `SENTINEL_VIRUSTOTAL_ENABLED` | Hash/URL/domain/IP lookup ONLY — no upload capability exists; confidence ceiling 0.5 |
| Device intel (server-verified) | **PILOT (needs creds)** | COMMERCIAL_INTELLIGENCE | `SENTINEL_DEVICE_INTEL_API_KEY` required | `SENTINEL_DEVICE_INTEL_ENABLED` | Abstraction only; no vendor chosen; see device_intelligence_provider_evaluation.md |
| Fingerprint (client SDK) | **DEFER** | — | — | — | Requires native SDK — forbidden (Stage 43) |
| MaxMind | **DEFER** | — | — | — | Overlaps Cloudflare contract; revisit if insufficient |

## Owner actions required to activate anything

1. Set `SENTINEL_EXTERNAL_INTEL_ENABLED=1` (master) plus the per-provider
   switch — every switch defaults OFF.
2. Provide credentials for credentialed providers (GitHub, Cloudflare,
   VirusTotal, device intel).
3. Wire the periodic sync calls into an existing worker (Stage 33 note in
   external_intelligence.md).

Until then the subsystem is inert by design: shipped, tested, and OFF.
