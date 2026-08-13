# External Provider Trust Model (Mission 4, Stage 3)

Trust describes the **source**, never the **subject**. A verdict from the most
trusted provider in the registry is still evidence about an indicator — it is
not guilt, and it authorizes nothing.

## External trust classes and confidence ceilings

| Trust class | Ceiling | Meaning |
|---|---|---|
| `AUTHORITATIVE_GOVERNMENT` | 0.90 | CISA KEV, NVD — official catalogs |
| `AUTHORITATIVE_ECOSYSTEM` | 0.85 | OSV.dev — ecosystem-run databases |
| `AUTHORITATIVE_REPOSITORY` | 0.85 | GitHub security alerts on our own repos |
| `COMMERCIAL_INTELLIGENCE` | 0.70 | Paid vendors (Cloudflare intel, device intel) |
| `DERIVED_EXTERNAL` | 0.60 | Conclusions derived from external data |
| `COMMUNITY_INTELLIGENCE` | 0.50 | Aggregated community signal (VirusTotal) |
| `UNVERIFIED_EXTERNAL` | 0.10 | Anything unvetted |

Every ceiling is **strictly below 1.0**: no external source can ever be as
confident as first-party observation. `external_observations.record()` clamps
confidence to the ceiling of the recording provider's trust class.

## Provider registry

`external_providers.PROVIDERS` declares eight providers with 7 provider types
and 9 lifecycle statuses. Key status rules:

- **CONFIGURED is not FUNCTIONAL.** `configured()` only says credentials are
  present where required. Keyless-capable providers (OSV, KEV, NVD) are
  CONFIGURED without a key; paid providers (`requires_credentials=True`) are
  NOT_CONFIGURED without one — that state is honest, not a failure.
- **Never called → unknown, not healthy (Stage 32).** FUNCTIONAL requires a
  recorded successful call; there is no other path to it.
- Missing token → `CONFIGURED=false`, never `FAILED`.
- Repeated failures → `DEGRADED` / `FAILED` recorded in the registry row and
  surfaced in self-health and the owner summary.

## What trust affects

Trust class sets the confidence ceiling and informs triage explanations. It
does **not** change authority: a KEV match on a deployed package elevates an
incident to owner attention — it does not trigger an upgrade, a block, or any
enforcement. Authority stays with policy plus a human (constitution SC2/SC10).
