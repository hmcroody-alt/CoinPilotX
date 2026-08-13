# Supply-Chain Security (Mission 4, Stages 14–17, 31)

## Inventory (Stages 14–15)

`supply_chain.parse_requirements` and `parse_package_lock` read the repo's
real manifests. The inventory is **parsed, never modified** — Sentinel has no
code path that writes to `requirements.txt` or `package-lock.json`.

Parsing honesty rules: a version is recorded only when the manifest pins one
(`==` for pip, resolved version in the lockfile). Unpinned dependencies are
recorded as `unpinned` — **no invented versions**. npm scope (prod/dev) and
directness are preserved.

## Applicability (Stage 16)

`assess_applicability` returns one of six states with reasons:

| State | Meaning |
|---|---|
| `DEPLOYED` | Present at a SHA known to be deployed |
| `PRESENT_IN_BUILD` | In the production dependency graph |
| `PRESENT_IN_REPO` | Dev-only / not shipped |
| `NOT_APPLICABLE` | Version provably outside affected range |
| `UNKNOWN` | Unpinned or undeterminable — investigate, not dismiss |
| `UNDER_INVESTIGATION` | Explicitly parked for human review |

`NOT_APPLICABLE` findings never open incidents — that is the false-positive
control. `UNKNOWN` triages to P3 with the reason "investigate, not dismiss".

## Triage (Stage 16–17)

`triage()` is deterministic and explainable: every priority comes with
reasons. KEV + live → P1; critical/high + live → P2; not-live → P3;
medium live → P3; else P4. P1/P2 require `OWNER_APPROVAL`; the recommended
next step is always phrased as advice — "upgrade is a human decision —
Stage 31". CVSS alone never sets priority; applicability does.

Incidents dedupe on (repo, ecosystem, package, vuln id, version, sha,
environment); recurrence grows `observation_count` instead of duplicating.

## What does not exist (Stage 31)

No auto-upgrade, no auto-PR, no auto-merge, no lockfile rewrite, no
`pip install` invocation. Tests assert these functions are absent from the
module, not merely unused.

## Counters

`summary_counts()` feeds the owner summary: known-exploited dependencies
(KEV ∧ deployed/build), deployed vulnerabilities, repository-only
vulnerabilities. Honest zeros when nothing has been scanned.
