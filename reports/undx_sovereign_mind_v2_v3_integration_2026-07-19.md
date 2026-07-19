# UNDX V2/V3 integration — 2026-07-19

## Outcome

UNDX V2 was integrated and regression-tested, then V3 `SOVEREIGN MIND` was made the active server-side specification. V1 and V2 remain byte-preserved configuration candidates for rollback and migration comparison. The complete packs are never concatenated into inference requests.

Automated architecture status: PASS. Production release status: BLOCKED until the mandatory authenticated simulator, personally observed physical iPhone 16 Pro, privacy, model-quality, and production-performance gates are completed.

## Architecture

- `services/undx_policy.py` loads versioned YAML and compiles bounded request-specific identity, policy, domain, retrieval, tool, planning, skill, and verification context.
- `services/undx_architecture.py` extends the existing `pulse_ai_*` database/service boundary with durable missions and task nodes, versioned skills, capability self-model, typed production-tool mappings, idempotent operations, provenance, truth facts, knowledge edges, verification events, and revocable delegated policies.
- `services/pulse_ai_service.py` invokes the compiler and architecture from the existing UNDX Messenger flow. No second AI, conversation, memory, retrieval, messaging, upload, or tool backend was created.
- Existing canonical IDs and legacy production routes remain intact for native/WebView compatibility.

## Production routes reused

- `GET /api/pulse/comm/v2/search`
- `GET /api/pulse/comm/v2/people/search`
- `GET /api/pulse/comm/v2/conversations/<conversation_ref>/messages`
- `POST /api/pulse/comm/v2/conversations/<conversation_ref>/messages`
- `POST /api/pulse/posts`
- `POST /api/pulse/reels/create`
- `GET /api/pulse/intelligence/state`
- `GET /api/crypto/alerts`
- `POST /api/pulse/comm/v2/media/init`
- `PUT /api/pulse/comm/v2/media/upload/<attachment_id>`
- `POST /api/pulse/comm/v2/media/<attachment_id>/complete`

Additive endpoints on the existing Communications V2 blueprint:

- `PATCH /api/pulse-ai/memory/<memory_id>`
- `DELETE /api/pulse-ai/memory/<memory_id>`
- `GET /api/pulse-ai/missions/<mission_id>`
- `POST /api/pulse-ai/missions/<mission_id>/cancel`
- `POST /api/pulse-ai/tools/simulate`

## Automated evidence

- V1 bootstrap evaluation: PASS, 24 cases.
- V2 migration/architecture audit: PASS, 20 cases.
- V3 architecture audit: PASS, 17 cases.
- UNDX backend identity audit: PASS.
- Native UNDX conversation audit: PASS.
- Python compile: PASS.
- Native TypeScript typecheck: PASS.
- `git diff --check`: PASS.
- Active V3 config SHA-256: `366c3be310532d63df1deaf3a96a77b691bb2269fe336788b9a5610a2d64c475`.
- V2 config SHA-256: `ade28343f6d5b0649213de9bc41447445d183ad0b1c20fb1f9f1fddf057bc3d0`.

## Performance sample

1,000 warm request-context compilations: mean 0.0945 ms, p95 0.0707 ms, max 32.8177 ms; compiled request context 3,201 characters. These are local compiler measurements, not model TTFT or production end-to-end latency.

## Simulator and physical-device evidence

Earlier in this task, the `PulseSocNative.xcworkspace` Debug build succeeded, installed, and launched on the iPhone 16 Pro simulator; Metro bundled and the sign-in screen was observed. The existing `expo-notifications` keychain-entitlement warning remained. This is not an authenticated V3 core-flow PASS.

Earlier in this task, a signed Release build succeeded for the paired physical iPhone 16 Pro, installed with `xcrun devicectl`, and process launch was confirmed. UI state and the required Wi-Fi/cellular switching, background/foreground, lock/unlock, attachment, interruption, Low Power Mode, memory-pressure, thermal, account-isolation, and long-conversation scenarios were not personally observed. Physical-device QA is therefore NOT PASS.

## Privacy and security findings

- User ownership is enforced for mission resumption, cancellation, memory correction/deletion, delegated policy revocation, and graph retrieval.
- High-impact tools require confirmation; delegated policies exclude them.
- Write success requires a canonical entity ID before verification passes.
- Dry-run simulation performs no production write and redacts secret-like arguments.
- Retrieved or tool-provided instructions remain untrusted data; adversarial verification blocks tested injection and unbounded-agency phrases.
- A formal production privacy review and measured cross-user leak evaluation remain mandatory.

## Remaining release blockers

- Authenticated V3 simulator core-flow suite.
- Personally observed complete physical iPhone 16 Pro QA.
- Measured model identity, grounding, injection, unauthorized-action, duplicate-write, and canonical-ID thresholds.
- Formal privacy/security review and production performance measurements.
- Reviewed rollout approval and rollback rehearsal.
