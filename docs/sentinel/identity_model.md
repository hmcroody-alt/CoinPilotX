# Sentinel Identity Model — Mission 3

Detection ≠ guilt. Everything in this document describes how Sentinel
OBSERVES, CORRELATES, SCORES, EXPLAINS, ESCALATES and RECOMMENDS on
identity threats. Nothing in it bans, blocks, locks out, invalidates a
session, seizes funds, or suspends anyone — those verbs do not exist in
the identity modules, and the adversarial suite asserts they never appear.

Modules: `services/sentinel/identity_trust.py`, `sequences.py`,
`identity_detections.py`, plus extensions to `entities.py`, `store.py`,
`incidents.py`, `graph.py`, `invariants.py`, `observability.py`,
`undx_interface.py`.

## 1. Entities (Stage 2)

New entity types: `session`, `device`, `network`, `ip`, `asn`,
`auth_attempt`, `recovery_attempt`. Entity ids are HASHED references —
`ip:`/`network:` ids come from `sha256(ip)[:16]` at the bridge; no raw
address, auth token, session token, or password is ever an entity id.

## 2. Session trust (Stage 3)

`SessionTrust` is a validated, frozen contract:

- `trust_state ∈ {TRUSTED, NORMAL, ELEVATED, HIGH_RISK, UNKNOWN, STALE}`
- **UNKNOWN is never TRUSTED.** TRUSTED requires AUTHORITATIVE/MEASURED
  provenance AND `risk_score ≤ 0.2`; unknown provenance fails closed to
  UNKNOWN. Enforced at construction and again by invariant.
- Every non-zero risk needs at least one reason — no unexplained risk.
- Mandatory bounded expiry (`ttl ≤ 7d`, default 4h).

## 3. Device trust — honest labeling (Stage 4)

PulseSoc "device identity" is `sha256(salt : client_device_id :
user_agent)` (`services/pulse_security_core.py`). That is
**CLIENT_REPORTED**: forgeable by an attacker, rotated by a reinstall.
It is used for correlation only, never treated as hardware
fingerprinting. Device-derived risk is capped at `DEVICE_RISK_CAP = 0.6`
and every device reason carries the `CLIENT_REPORTED` label. Incident
text repeats the disclosure.

## 4. Network observations (Stage 5)

Internal-only: hashed network refs from our own request logs. No
external IP reputation, ASN feed, or threat list participates — that is
Mission 4, behind its own trust model (Stage 34).

## 5. Risk dimensions (Stage 15)

Seven named dimensions, each in `[0,1]`, each with its own reasons:
`credential_risk`, `recovery_risk`, `session_risk`, `device_risk`,
`network_risk`, `admin_risk`, `behavioral_risk`. There is no opaque
composite: the overall score is simply the maximum dimension, so an
operator can always answer "why".

## 6. Decay & freshness (Stage 16)

Every stored observation has `expires_at`. `latest()` degrades an
expired row to `STALE` with `risk_score = 0` and `expired: true`;
`active_high_risk()` excludes expired rows entirely.
`INV_EXPIRED_RISK_INACTIVE` asserts stale high risk can never be active.

## 7. Confidence fusion (Stage 17)

`HIGH_RISK` requires ≥ 2 independent high dimensions (`≥ 0.7`) **and**
evidence refs. A single signal — however loud — caps at `ELEVATED`, and
the reason string says so honestly. Confidence never exceeds the
source-trust ceiling (DERIVED = 0.8); assessments are labeled DERIVED
even when built from AUTHORITATIVE rows, because the judgment is
computed.

## 8. Contradicting evidence (Stage 18)

Assessments carry `contradicting` — known device, zero failed logins,
previously seen network. It is stored, surfaced in the owner incident
detail, and REQUIRED in the UNDX identity context: a model reasoning
about identity must see what argues AGAINST the hypothesis.

## 9. Detections (Stages 7–14, 19–20)

Deterministic SQL + arithmetic only; conservative thresholds; every
incident embeds measurement, threshold, and safe recommendations.

| Rule | Incident type | Threshold |
|---|---|---|
| ATO chains 1–4 (temporal sequences) | ACCOUNT_TAKEOVER_SUSPECTED | ordered chain in bounded window; PARTIAL if optional step missing — never faked to FULL |
| ID1 stuffing fan-out | CREDENTIAL_STUFFING | ≥ 6 accounts AND ≥ 12 failures / network / 30m |
| ID2 stuffing fan-in | CREDENTIAL_STUFFING | ≥ 5 networks against one account / 30m |
| ID3 recovery abuse V2 | RECOVERY_ABUSE | ≥ 5 distinct targets / network / 60m — target identifiers withheld (enumeration resistance preserved) |
| ID4 session compromise | SESSION_ANOMALY | any platform `refresh_token_reuse` / `refresh_device_mismatch` |
| ID5 session burst | SESSION_ANOMALY | ≥ 6 logins / account / 30m |
| ID6 shared device | DEVICE_ANOMALY | ≥ 5 users / device / 7d — 2–4 users is ordinary life; incident says shared ≠ malicious |
| ID7 network many accounts | COORDINATED_IDENTITY_ABUSE | ≥ 8 accounts / network / 60m — CGNAT/campus caveat in text |
| ID8 admin unseen network | ADMIN_IDENTITY_ANOMALY | network hash unseen in 30d history; brand-new admins are never accused; **never auto-lockout** |
| ID9 admin baseline deviation | ADMIN_IDENTITY_ANOMALY | today ≥ 3 × rolling-14d median (floor 10) |

Correlation (Stage 20): all ATO-shaped firings for one subject collapse
into ONE incident per subject per day; re-runs land as observations on
the same incident.

## 10. Behavioral baselines (Stages 13–14)

Explainable arithmetic, no ML: median of active days over a rolling 14d
window; deviation = today ≥ 3 × median. Fewer than 5 active days →
`baseline_available: false` — no baseline, no accusation. User baselines
use **security-relevant signals only** (login cadence): no messages, no
interests, no ad data, no social graph — ever.

## 11. Exclusions (Stage 27)

The only exemption mechanism is `sentinel_detection_exclusions`:
mandatory reason, author, bounded expiry (≤ 90d), policy version, and an
evidence-chain entry. There are no code-level exceptions.

## 12. Privacy (Stage 28)

Never stored or surfaced by identity code: raw Pulse IDs, auth tokens,
session tokens, refresh tokens, passwords, message content, payment
data, raw IP addresses. Device/network identifiers are salted hashes
produced upstream. The UNDX surface additionally passes everything
through `classification.redact` at the INTERNAL ceiling; operational
metadata (states, hashed refs, timestamps, Sentinel's own reasons) is
classified INTERNAL explicitly so contradicting evidence stays visible
without weakening any secret class. Recovery-abuse incidents echo counts
only — enumeration resistance is preserved end to end.

## 13. Retention & minimization (Stage 29)

- `sentinel_identity_risk`: append-only observations; every row expires
  (≤ 7d TTL cap, default 4h). Expired rows are inert (never live risk)
  and eligible for pruning after 90 days.
- `sentinel_sequence_firings`: dedupe/cooldown bookkeeping; prunable
  after cooldown + 30 days.
- `sentinel_detection_exclusions`: hard-capped at 90 days by contract.
- Events/evidence follow the Mission 1/2 retention policy (evidence is
  append-only and hash-chained; identity adds no new raw-data class).
- Minimization: identity code reads only the platform tables it names
  (`mobile_security_sessions`, `admin_session_logs`, bridge events) and
  only the columns it uses; queries are bounded (LIMIT + time windows,
  Stage 30) and indexed.

## 14. What Sentinel may recommend (Stage 25)

`SAFE_RECOMMENDATIONS` is the complete vocabulary: rate limits,
step-up/re-authentication, owner review, guided credential rotation —
all explicitly human-applied. Nothing in Sentinel executes any of them
(Stage 35: every automation switch stays OFF).
