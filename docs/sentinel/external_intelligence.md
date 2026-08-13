# Sentinel External Intelligence (Mission 4)

External threat intelligence in Sentinel is **read-only enrichment**. A vendor
verdict is evidence, never a decision. The canonical flow is:

    external evidence → internal corroboration → policy → human authority

Nothing in this layer can block, suspend, ban, seize, patch, upgrade, dismiss,
or merge anything. Those are not verbs this subsystem has.

## Modules

| Module | Role |
|---|---|
| `external_providers.py` | Provider registry, source trust, budgets, circuit breakers |
| `enrichment_policy.py` | Single policy gate: purpose, kill switches, minimization, cache, single-flight, audit |
| `external_observations.py` | `SentinelExternalObservationV1` envelope, indicator vocabulary, expiry |
| `vuln_adapters.py` | OSV / NVD / CISA KEV normalizers + query functions |
| `supply_chain.py` | Dependency inventory, applicability, explainable triage, findings |
| `github_security.py` | GitHub security alerts (read-only), attestation results |
| `external_contracts.py` | Cloudflare / VirusTotal / device-intel contracts |
| `external_fusion.py` | Provider-fusion with the external-evidence ceiling |

## Non-negotiable invariants

- **External-evidence ceiling (Stage 22):** external evidence alone caps at
  `EXTERNAL_ONLY_RISK_CAP = 0.6`, below the Mission 3 HIGH_RISK line (0.7).
  Only internal corroboration can cross that line.
- **Unavailable = UNKNOWN, never SAFE (Stage 8):** a failed provider, an open
  circuit, an exhausted budget, or a missing credential produces UNKNOWN.
- **CONFIGURED ≠ FUNCTIONAL (Stage 2):** credentials present is not proof the
  provider works. Never-called means unknown, not healthy.
- **Disagreement is preserved (Stage 23):** provider verdicts are stored and
  surfaced side-by-side; they are never averaged into a fake consensus.
- **No raw user content leaves PulseSoc (Stage 6):** hash/digest lookups only.
  File-upload capabilities do not exist in the vocabulary.
- **Mandatory expiry (Stage 24):** every observation has `expires_at`; stale
  intelligence degrades loudly (staleness notes, self-health counters).

## Kill switches (all default OFF)

`SENTINEL_EXTERNAL_INTEL_ENABLED` (master), `SENTINEL_OSV_ENABLED`,
`SENTINEL_NVD_ENABLED`, `SENTINEL_KEV_ENABLED`,
`SENTINEL_GITHUB_SECURITY_ENABLED`, `SENTINEL_CLOUDFLARE_INTEL_ENABLED`,
`SENTINEL_DEVICE_INTEL_ENABLED`, `SENTINEL_VIRUSTOTAL_ENABLED`.

The master switch gates everything; per-provider switches gate each adapter.
Enabling any of them is an owner decision.

## Scheduling (Stage 33)

No new scheduler was added. Periodic work (KEV catalog sync, inventory
refresh, OSV re-query of the inventory) is designed to run from the existing
worker primitives (`alert_worker` / `pulse_worker` loops or a cron-style call
into `vuln_adapters.kev_sync` and `supply_chain.refresh_inventory`). All entry
points are plain functions taking an injectable `fetch` and `conn`, so wiring
them into a worker is one call each — an owner decision, not done implicitly.

## Surfaces

- Admin API (read-only, unwired by default): `/api/admin/sentinel/threat-intelligence`,
  `/vulnerabilities`, `/supply-chain`, `/providers/<id>/health`,
  `/external-observations/<id>`.
- Owner summary: supply-chain counts, external threat matches, provider
  degradations, staleness — real counts, honest zeros.
- UNDX: `external_threat_context` read surface only; UNDX may not dismiss,
  upgrade, block, revoke, hold, upload, or call providers.
