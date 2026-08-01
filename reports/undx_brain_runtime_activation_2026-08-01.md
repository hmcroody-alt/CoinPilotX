# UNDX Brain Runtime Activation — 2026-08-01

## Result

The canonical `UndxAgentRuntime.handle()` path now invokes the existing Brain
modules before, during, and after governed tool execution. No parallel agent,
tool gateway, authorization system, or conversation store was introduced.

## Live ownership map

| Brain concern | Existing public entry point | Canonical caller | Runtime effect |
| --- | --- | --- | --- |
| Working context | `workspace.open_workspace` | `UndxAgentRuntime.handle` | Opens an owner-scoped workspace for the current request. |
| Attention | `attention.attend`, `attention.place_into` | `UndxAgentRuntime.handle` | Focuses candidate capabilities without granting permission. |
| Goal understanding | `goals.understand` | `UndxAgentRuntime.handle` | Repairs ambiguous requests before any tool mutation. |
| Planning and execution | `execution.execute` | `UndxAgentRuntime._act` | Wraps the canonical governed gateway and preserves its evidence checks. |
| Prediction | `prediction.predict`, `prediction.check` | `UndxAgentRuntime._act` | Predicts the expected state and compares it with independent read-back evidence. |
| Action selection | `selection.select` | `UndxAgentRuntime.handle` | Selects among authorized candidates; contested consequential choices clarify. |
| Calibration | `calibration.read_calibration` | `UndxAgentRuntime.handle` | Applies owner-scoped confidence corrections before a consequential write. |
| Metacognition | `metacognition.check_completion_claim` | `UndxAgentRuntime._act` | Removes completion claims when the evidence is insufficient or contradictory. |
| Homeostasis | `homeostasis.assess` | `UndxAgentRuntime._act` | Fails closed for unsafe writes and exposes safe degradation state for reads. |

## Safety invariants

- The model cannot authorize a tool or expand the attention-selected scope.
- Every mutation still goes through the existing governed gateway.
- A write cannot claim success without the existing independent verification
  result and the Brain prediction check.
- Goal repair, unresolved consequential selection, corrected confidence, and
  unsafe homeostasis return structured clarification or refusal components.
- Feature flags can return the request path to legacy behavior without creating
  a second implementation.
- Logs contain correlation and decision metadata, not private prompt content or
  credentials.

## Automated verification

- `tests/undx_agent`: 722 passed.
- `tests/undx_brain`: 821 passed.
- New production-shaped activation tests: 4 passed (goal repair, selection,
  prediction/execution/read-back, and flags-off legacy path).
- Native UNDX action cards: 30 passed.
- Presence service: 18 passed.
- Seller lifecycle: 87 passed.
- Native TypeScript typecheck: passed.
- V5 architecture audit: checks passed; release-ready remains false where the
  audit requires manual or production evidence.
- Backend identity audit: passed.
- Native conversation audit: passed after updating its stale localized subtitle
  assertion.
- Python compilation and `git diff --check`: passed.

Foundation verification reports 232 registered entries, with no missing,
unowned, or unavailable entries. Nineteen entries remain honestly marked
`PARTIAL` for broader work beyond this activation (including true multi-step
plan construction and adaptive runtime health); they are not represented as
fully complete.

## Simulator evidence

- Model: Xcode iPhone 17 Pro Max simulator.
- Runtime: iOS 26.5.
- Fresh Xcode derived-data build: `BUILD SUCCEEDED`.
- Installed bundle: `com.pulsesoc.nativeapp.dev`.
- Local API: `http://127.0.0.1:5050`; health returned HTTP 200.
- Metro: `http://127.0.0.1:8082` with local-only temporary QA account flags.
- The app authenticated a temporary QA account and visibly opened the canonical
  UNDX conversation. Existing verified alert receipts and confirmation behavior
  rendered correctly.

The simulator was rebuilt before the final commit during implementation. Per
release policy it must be rebuilt and reinstalled again from the exact pushed
commit before final simulator sign-off.

## Not claimed

- No physical iPhone behavior was observed in this mission.
- No production deployment or production HTTP behavior is claimed by this
  report.
- Full manual accessibility, network-transition, and all fourteen requested
  conversational scenarios are not represented as observed merely because the
  automated suites pass.

## Intentionally excluded

`verify-and-commit-undx-brain.command` was already untracked and contains stale
one-off workflow assumptions. It is not required by the runtime and is left
untracked rather than silently committed or deleted.
