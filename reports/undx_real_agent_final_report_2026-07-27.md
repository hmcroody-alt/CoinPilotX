# UNDX Real Agent — Production Implementation, Final Report

Commit `adf314d958c9ad89feba427d37ae4d56e26829ac` on `main` (parent `a45c27fdd1744239777c3b7405e716b8e5eb494c`).
Date 2026-07-27.

## 1. Result

**PARTIAL — backend PASS, native PARTIAL.**

Every backend condition for a PASS is met and proved by executing tests: two real capability packs run through the governed gateway against the real services, consequential actions require a bound single-use confirmation, every mutation is verified by an independent read-back, audit evidence is durable across a mid-flight audit failure, ordinary conversation still reaches the model provider, and a duplicate confirmation cannot repeat a mutation.

The reason this is not a full PASS is narrow and honest: the native side is proved by type-checking and unit tests only. `tsc --noEmit` is clean and the card-normalizer and chat-render tests pass, but the Confirm/Cancel controls have not been driven on a simulator or a device, because the native dependency install fails on a TLS certificate verification error in this environment. That is an environment blocker, not an agent-runtime defect, and it is documented in §12 rather than worked around.

## 2. Files changed

23 files, +6368 / −30.

Modified: `services/pulse_ai_service.py` (agent turn wired into send and confirm), `services/undx_architecture.py` (audit reservation, reconciliation flag, upsert result), `services/undx_policy.py`, `mobile-native/src/api/messenger.ts` (widened card union), `mobile-native/src/screens/ChatScreen.tsx` (card rendering, spent-token tracking, confirm handler).

New backend: `services/undx_agent_contracts.py`, `undx_capability_registry.py`, `undx_agent_policy.py`, `undx_tool_gateway.py`, `undx_verification.py`, `undx_agent_tools.py`, `undx_agent_runtime.py`.

New native: `mobile-native/src/undx/actionCards.ts`, `mobile-native/src/undx/__tests__/actionCards.test.ts`.

New tests: `tests/undx_agent/` — `bootstrap.py`, `harness.py`, `test_adversarial.py`, `test_audit_durability.py`, `test_confirm_path.py`, `test_crypto_alert_pack.py`, `test_end_to_end.py`, `test_transport_wiring.py`.

## 3. Runtime architecture

A turn arrives at `pulse_ai_service.send_message`, which calls `undx_agent_runtime.handle`. The runtime matches the text against the capability registry and, if it reaches a capability, calls `undx_tool_gateway.execute`. Nothing else in the system is allowed to call a capability executor.

The gateway is a fixed sequence: resolve the capability, validate arguments against the declared field specs, apply the policy engine, resolve or mint a confirmation, compute the idempotency key and check the ledger, reserve a pending audit row in a short transaction, execute the tool, run the declared verifier, upgrade the audit row, and build a receipt. `undx_agent_runtime.build_card` turns the receipt into the native card. The language model proposes; it has no path to a database write.

## 4. Executable capabilities

Crypto alerts: `list`, `get`, `create`, `pause`, `resume`, `update`, `delete` — all seven wired to `alert_engine` and all seven driven end to end. `test_the_whole_pack_executes_in_one_session` runs list → create → get → pause → resume → update → delete against one alert and asserts exactly seven settled ledger rows, each `verified`. Nothing in the pack is registered-but-unexecutable.

Notification preferences: read and update, wired to `pulsesoc_notification_system`, across the categories `global`, `posts`, `messages`, `reels`, `calls`, `alerts`.

## 5. Confirmation flow

One contract, `action_confirmation`. The normalizer accepts the legacy `confirmation_card` shape for backward compatibility and produces the same native component. The card carries the exact action, the exact target, the before state, the expected after state, the risk when consequential, an expiry, and the token.

Tokens are stored hashed, are single-use, are TTL-bounded, are bound to the acting user, and are bound to the pair (`action_id`, `argument_hash`). The binding is checked *before* the consuming update, so a mismatched replay cannot burn a valid token. Redemption replays the server's stored arguments — a client that restates different arguments at confirm time is refused rather than obeyed.

One deliberate design/spec tension is worth naming. The §2 walkthrough used "pause my Bitcoin alert" as the confirmation example, but pause is classified `reversible_write` with a `CONTEXTUAL` policy, so an explicit, unambiguous pause executes directly to `verified_success` and returns `undo_capability_id: "crypto.alerts.resume"` instead of interrupting the user with a confirmation card. Prompting for something that is one word away from being undone is friction without safety. The full 16-step confirmation journey is therefore proved on `crypto.alerts.delete`, which is `consequential_write` with an `ALWAYS` policy, and additionally on a non-explicit or ambiguous pause, where `CONTEXTUAL` does escalate to a confirmation.

## 6. Verification flow

Every write has a declared verifier in `undx_verification.VERIFIERS`. Verifiers never inspect the mutation's own return value; they take the requested arguments and re-read state through a separate service call. Four states, not interchangeable: `verified`, `verification_failed`, `verification_pending`, `impossible_to_verify`. Only `verified` permits a completed claim; a missing verifier yields `impossible_to_verify` rather than a silent pass.

This is what caught the one real correctness defect found during the work. UNDX offered notification categories — `reels`, `posts`, `alerts` — that PulseSoc's store does not have. Writes created orphan rows the delivery pipeline never consults, and reading back a nonexistent category returned `False`, so UNDX told users their reel notifications were already off and the verifier agreed. Fixed with a single alias map (`reels` → `likes`, `posts` → `social`, `alerts` → `crypto`) resolved identically by the executor, the reader and the verifier, plus two tests that fail if the vocabulary ever drifts from storage again.

## 7. Audit durability

The audit row is reserved before the mutation, in a short transaction that is released before execution — no lock is held across the tool call. After execution and verification the same row is upgraded in place. A real mutation therefore cannot occur with no durable trail.

If the post-execution audit write fails, the gateway does not repeat the mutation. It records a critical reconciliation event, preserves the idempotency key, and marks the operation for reconciliation. A later replay against an unsettled operation reports its true state rather than pretending the earlier attempt succeeded. `test_audit_durability.py` covers this with 11 tests.

## 8. Conversational fallback proof

`AgentResponse` defines `__bool__`; nothing decides "handled" from object truthiness. `test_ordinary_questions_still_reach_the_model_provider` asserts both halves for "What is artificial intelligence?", "Help me write a birthday message.", "What can UNDX do?", "explain how staking rewards work" and "why is my portfolio down this week": the runtime declines the turn, and the transport converts that into the `None` that lets `send_message` fall through to the provider. A companion test proves that hedged sentences naming a real capability ("should i delete my bitcoin alert") never complete a write.

## 9. Native confirmation UI proof

`actionCards.ts` normalizes both accepted server shapes into one component model; 26 tests across two suites cover normalization, the disabled/loading state, the success receipt, typed failure states, expiry, and duplicate confirmation (a spent token is tracked client-side and the control cannot be pressed twice). `tsc --noEmit -p tsconfig.json` exits 0. Not proved: rendering on a simulator or device.

## 10. Backend test results

Agent suite, 127 tests, all OK: `test_adversarial` 31, `test_audit_durability` 11, `test_confirm_path` 13, `test_crypto_alert_pack` 35, `test_end_to_end` 25, `test_transport_wiring` 12.

Existing suites, PASS: `test_content_translation` 6/6, `test_crypto_alert_edge_trigger` 8, `test_native_app_links` 4/4, `test_presence_service` 18, `test_pulse_region_preferences` 5/5, `test_seller_lifecycle` 87, `test_undx_platform_knowledge` 4/4.

Existing suites, NOT RUN — missing third-party package, PyPI unreachable from this sandbox (proxy 403): `test_pulse_comment_pagination` (werkzeug), `test_pulse_repost_toggle` (werkzeug), `test_pulse_settings_routes` (flask), `test_pulse_repost_routes` (stripe).

Audits PASS: `undx_agent_governance_audit`, `pulsesoc_undx_nexus_core_v4_audit`, `pulsesoc_undx_platform_knowledge_audit`, `pulse_notifications_preferences_audit`.

Audits NOT RUN — missing package: `pulsesoc_undx_pulsesoc_operator_v5_audit` (werkzeug), `crypto_alert_request_safety_audit` (stripe), `crypto_alert_reconciliation_audit` (stripe).

Audits FAIL, **pre-existing, not caused by this work**: `pulsesoc_notification_defaults_audit`, `pulsesoc_native_undx_chat_conversation_audit`, `pulse_ai_intelligence_upgrade_audit`. Each was re-run in a detached worktree at parent commit `a45c27f` and fails there identically.

Audit NOT RUN — blocked by a stale `.undx_brain_layer_audit_workspace/` directory this sandbox cannot delete: `undx_brain_layer_audit`. It passes at `a45c27f` in a clean worktree, so the working-tree failure is the artifact, not the code.

## 11. Native test results

`npx tsc --noEmit -p tsconfig.json` — exit 0.
`npx jest src/undx` — 2 suites, 26 tests, OK.
NOT RUN: the full 99-file Jest suite, Expo Doctor, simulator and physical device runs — see §12.

## 12. Certificate-blocked commands

```
cd mobile-native && npm install
```
fails with `UNKNOWN_CERTIFICATE_VERIFICATION_ERROR` against the registry endpoint. The same error blocks `npx expo-doctor` and any command that needs to resolve new native dependencies.

Status: **NOT RUN — environment TLS certificate verification failure.** The source is this sandbox's outbound TLS interception, not the project. No mitigation was applied: no `NODE_TLS_REJECT_UNAUTHORIZED=0`, no disabled SSL verification, no insecure HTTP fallback, no committed trust bypass. The staged diff was scanned for all four patterns and is clean.

## 13. Commit

`adf314d958c9ad89feba427d37ae4d56e26829ac`

```
feat(undx): add governed agent runtime with verified notification and alert actions
```

Staged deliberately: only the 23 UNDX files. Deliberately excluded and still untracked: `SETTINGS_HANDOVER.sh`, `release-assets/`, `reports/settings_release_blocker_2026-07-26.md`. No secrets, temp databases, logs, certificates or caches are in the commit.

## 14. Remaining limitations

The native confirmation UI is unproved on a real screen; that is the single largest gap and it is blocked on §12, not on code.

Four existing route tests and three audits could not run because their third-party packages cannot be installed here; they exercise Flask routes rather than the agent runtime, but they are genuinely unverified.

Three audits fail, and while all three fail identically at the parent commit, they remain failing.

Scope was held where instructed: no saved content, unfollow, messaging, autonomous posting, or additional capability packs. Only crypto alerts and notification preferences are executable.

The confirmation classification is a judgment call, not a proof. Pause and resume are treated as reversible and execute without a prompt; delete and the notification writes are consequential and always prompt. If product disagrees about where that line sits, it moves in `undx_capability_registry.py` alone — the gateway does not need to change.

Verification depends on the read path a service exposes. Where a property has no read path the receipt says `impossible_to_verify` and the user is told the action was accepted rather than confirmed. That is the honest answer, but it is still a weaker guarantee than `verified`.
