# UNDX Intelligence Bootstrap v1 Integration

Date: 2026-07-19

## Release judgment

**NOT RELEASE-READY.** The server-side configuration and deterministic policy
integration are implemented and their focused checks pass. Mandatory measured
model gates, the full authenticated simulator lifecycle matrix, and hands-on
physical iPhone 16 Pro scenarios remain incomplete. Commit and push evidence is
reported by the later V2/V3 release integration rather than this V1 snapshot.

## Configuration

- Server config: `backend/undx/config/undx_intelligence_bootstrap.yaml`
- Pack version: `1.0.0`
- Schema version: `1.0`
- SHA-256: `b6079e0a411369045fc1b3e63d4404591462834963f0ab1240a09e20d51275a6`
- Source and repository copies were byte-for-byte identical (`cmp -s`).
- The full YAML is not placed into inference requests. `services/undx_policy.py`
  compiles identity, epistemic, injection, security, response, domain, freshness,
  and request-relevant tool fragments with a 9,000-character hard limit.

## Files changed by this integration

- `backend/undx/config/undx_intelligence_bootstrap.yaml`
- `services/undx_policy.py`
- `services/pulse_ai_knowledge.py`
- `services/pulse_ai_service.py`
- `scripts/pulsesoc_undx_bootstrap_eval.py`
- `requirements.txt`
- `reports/undx_intelligence_bootstrap_v1_integration_2026-07-19.md`

The repository had extensive pre-existing dirty changes, including changes in
`services/pulse_ai_knowledge.py` and `services/pulse_ai_service.py`. This work did
not stage, commit, reset, or overwrite unrelated changes.

## Existing production infrastructure reused

- UNDX request: `POST /api/pulse-ai/message`
- UNDX conversation: `GET /api/pulse-ai/conversation`
- Messenger search: `GET /api/pulse/comm/v2/search`
- People/profile lookup: `GET /api/pulse/comm/v2/people/search`
- Conversation messages: `GET|POST /api/pulse/comm/v2/conversations/<conversation_ref>/messages`
- Post publish: `POST /api/pulse/posts`
- Reel publish: `POST /api/pulse/reels/create`
- Alerts: `GET /api/pulse/intelligence/state`
- Crypto alerts: `GET /api/crypto/alerts`
- Media: `POST /api/messages/media/init`, then `/upload`, then `/complete`
- Web retrieval: existing `services.pulse_ai_web_search.search`
- Existing `pulse_ai_*` conversation, message, memory, retrieval, safety,
  feedback, provider, persistence, and observability tables/services remain in use.

No duplicate AI, messaging, memory, retrieval, media, or tool backend was added.

## Evaluation results

- Bootstrap structural/policy eval: PASS for all 24 evaluation IDs present in
  the YAML. The surrounding request described more than 25, but this file has 24.
- Direct-send confirmation boundary: PASS.
- Draft-without-send boundary: PASS.
- Current-data web-tool selection: PASS.
- Canonical profile tool selection: PASS.
- Prompt-injection policy inclusion: PASS.
- Mapped HTTP route existence: PASS.
- Full-pack exclusion and compiled-context bound: PASS.
- Existing UNDX identity backend audit: PASS.
- Python compilation: PASS.
- Native TypeScript typecheck: PASS.
- `git diff --check`: PASS.
- Mandatory release mode: BLOCKED, exit code 2.

The static runner does not claim that live model quality thresholds have been
measured. The following YAML gates still need representative production-like
evaluation data: identity consistency, unauthorized action rate, cross-user data
leak rate, duplicate write rate, tool success rate, grounded answer rate, and
prompt-injection block rate.

## Performance

On this laptop, 2,000 cached request-context compilations completed in 109.534 ms:

- Mean: 0.054767 ms per compilation
- Throughput: 18,259.1 compilations/second
- Largest sampled compiled policy: 4,661 characters

These are local compiler-only measurements, not model or end-to-end latency.

## Simulator evidence

- Target: `PulseSoc iPhone 16 Pro`, iOS 26.5
- Workspace Debug build: PASS
- Bundle: `com.pulsesoc.nativeapp.dev`
- Install: PASS
- Process launch: PASS
- JavaScript bundle: PASS after Metro bundling
- Observed UI: PulseSoc sign-in screen rendered
- Existing warning observed: `expo-notifications` could not read persisted
  registration data because the Debug simulator build lacked a required Keychain
  entitlement.

Authenticated UNDX chat, streaming, long content, citations, confirmations,
retry/cancellation, reconnect, account isolation, Dynamic Type, VoiceOver, and
Reduce Motion were not completed in this run.

## Physical iPhone 16 Pro evidence

- Device: `P3r7or`, iPhone 16 Pro
- Signed Release build: PASS
- Bundle: `com.pulsesoc.nativeapp`
- Embedded production JavaScript bundle: PASS
- Install through CoreDevice: PASS
- Process launch through CoreDevice: PASS

The physical screen could not be captured or inspected through `devicectl`.
Therefore UI success is not claimed. Real Wi-Fi/cellular switching, background
and foreground, lock/unlock, streaming, long scrolling, attachments, push/deep
links, memory pressure, thermal behavior, account switching, and logout cleanup
remain unverified.

## Commit and push

- Commit: not created
- Push: not performed
- Reason: mandatory release gates are incomplete and the worktree contains
  extensive unrelated user changes.
