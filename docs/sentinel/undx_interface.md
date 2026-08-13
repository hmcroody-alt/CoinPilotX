# Sentinel ↔ UNDX Interface (Stage 18, hypothesis contract Stage 19)

Module: `services/sentinel/undx_interface.py`.

UNDX is intelligence, not root. This module is the **only** surface UNDX
gets: read-only, structured, redacted. There is no freeform-execution
entry point, no mutation function, and nothing here accepts SQL, shell,
or code. The adversarial suite asserts the module's public namespace
contains no function whose name includes transition / resolve /
suppress / delete / execute / restart / block.

## Read surfaces (allowlist, SC11/SC15)

`read(surface, category=…, limit=…)` with surface one of:

`recent_events`, `open_incidents`, `provider_health`

Anything else fails closed with an SC15 error. Every returned row passes
`classification.redact` at the **INTERNAL** ceiling — stricter than the
CONFIDENTIAL ceiling used for internal evidence (SC9). Responses carry
an explicit `authority_note`: model reads are advisory.

## submit_analysis(subject_type, subject_id, summary, confidence)

Stores a freeform analysis as a canonical UNDX event with severity
**`info` regardless of content** — a model cannot self-assign severity,
because severity feeds deterministic correlation and would otherwise be
a text-to-authority path (SC2/SC8). Confidence is clamped to [0, 1];
payload is marked `"authority": "ADVISORY"`.

## submit_hypothesis(incident_key, data) — the structured contract

When UNDX reasons about an incident it must return exactly this shape.
The field set is closed: **both missing and unknown fields reject**.

| Field | Rule |
|-------|------|
| `hypothesis` | Non-empty string (capped 2000 chars). |
| `confidence` | Numeric, within [0, **0.8**] — model opinion is DERIVED at best; exceeding the DERIVED trust ceiling rejects (SC2/SC4). |
| `supporting_evidence_ids` | List of ids (bare string rejected), cap 50. |
| `contradicting_evidence_ids` | Same rules. The model must say what argues *against* it. |
| `affected_domains` | List, cap 15. |
| `estimated_impact` | One of `none/low/medium/high/critical`. |
| `recommended_next_step` | String, cap 500. Documentation, never execution. |
| `required_authority` | One of `NONE` / `OWNER_REVIEW` / `OWNER_APPROVAL` — each names a **human** gate outside this interface. Self-granting values reject. |
| `missing_evidence` | List, cap 25. The model must say what it does *not* know. |

Other guarantees: unknown `incident_key` fails closed (SC15); the stored
event is UNDX / `model_hypothesis` / severity `info` / ADVISORY; nothing
in this module executes the recommended step (SC2, SC10).

## Why models cannot open incidents

Incidents are opened only by deterministic code: correlation rules,
deterministic detections, and invariant violations. `undx_interface`
exposes no such entry point — the boundary is structural, not
behavioral (SC2). Tests: `tests/sentinel/test_adversarial.py::TestUndxCannotMutate`,
`tests/sentinel/test_mission2_core.py::TestHypothesisContract`.
