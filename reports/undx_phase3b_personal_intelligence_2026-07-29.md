# UNDX Phase 3B — Personal Intelligence Layer

Date: 2026-07-29  
Branch: `release/undx-nexus-core-v4`  
Starting commit: `22227f3efddcb5bb89bc8a418876d5ac56a60dab`

## Result

Phase 3B adds 27 server-authoritative, read-only capabilities. The executable
registry moves from 43 to 70 capabilities. No financial, authentication,
publishing, call, Live-control, destructive, message-send, or privileged
moderation operation was enabled.

The personal-intelligence service creates no database. It retrieves from
canonical PulseSoc services and tables and attaches source, source ID, timestamp,
authorization scope, confidence, and native route to facts. Missing data produces
an empty result, not model-generated facts.

## New capabilities

- Activity: `activity.daily_summary`
- Notifications: `notifications.inbox.list`, `notifications.explain`,
  `notifications.group_summary`
- Search: `search.global`, `search.people`, `search.content`,
  `search.messages`, `search.activity`
- Settings: `settings.inspect`, `settings.explain`, `settings.recommend`
- Security: `security.sessions.list`, `security.activity.summary`,
  `security.device.list`
- Marketplace: `marketplace.search`, `marketplace.listing.summary`,
  `marketplace.order.status`
- Premium: `premium.status`, `premium.entitlements`
- Advertising: `ads.performance.summary`
- Live: `live.search`, `live.summary`, `live.performance`
- Learning: `learning.search`, `learning.progress`
- Memory inspection: `memory.activity.inspect`

## Architecture

`POST /api/pulse-ai/message`
→ deterministic/planner capability proposal
→ server registry
→ policy and QA cohort
→ governed tool gateway
→ `services.undx_personal_intelligence_service`
→ canonical PulseSoc service/table
→ source-backed native result card
→ audit receipt and correlation ID

The native client remains on its canonical UNDX conversation and renders the
existing typed result-card contract. There is no Phase 3B-only backend, route,
conversation, memory store, search store, or native registry.

## Live local backend proof

QA account: temporary local account `nativeqa_phase3b_0729`  
Backend: `http://127.0.0.1:5050`  
Agent mode: enabled, reads enabled, writes disabled  
Data: controlled QA-only records

| Question | Capability | Correlation ID | Outcome | Observed result |
|---|---|---|---|---|
| What happened today? | `activity.daily_summary` | `9e6c5d1ecffc` | `verified_success` | 5 source-backed facts |
| Summarize my notifications. | `notifications.group_summary` | `a85959c4c5f1` | `verified_success` | Owner-scoped grouped summary |
| Find everything about Phase 3B. | `search.global` | `c169d7642326` | `verified_success` | 5 authorized results |
| Show my privacy settings. | `settings.inspect` | `4daabb9c5d8e` | `verified_success` | Canonical settings revision |
| What devices are logged in? | `security.sessions.list` | `5bb58b8560fd` | `verified_success` | 11 redacted owner sessions |
| What plan am I on? | `premium.status` | `214f7b80d01a` | `verified_success` | Canonical entitlement state |
| Search marketplace for camera. | `marketplace.search` | `e50eac78497e` | `verified_success` | Authorized empty result |
| Find a course about editing. | `learning.search` | `0146f799fe9b` | `verified_success` | Published-catalog empty result |
| What do you know about my PulseSoc activity? | `memory.activity.inspect` | `f031ad7180d4` | `verified_success` | 5 retrieved facts; no sensitive memory write |

The activity response contained facts from Notifications, Posts, Reels, Status,
and Crypto Alerts. Each returned fact included its canonical source, source ID,
timestamp, `self_account_only` scope, confidence, and native destination.

## Automated verification

| Gate | Result |
|---|---|
| Phase 3B privacy/provenance suite | PASS — 7 tests |
| Complete UNDX agent suite | PASS — 237 tests |
| Knowledge-map suite | PASS — 37 tests |
| Native TypeScript typecheck | PASS |
| Python compilation | PASS |
| Registry/executor parity | PASS — 70/70 |
| Command benchmark | PASS — 2,016/2,016 unique commands |
| Git whitespace validation | PASS |
| Native `/pulse/ai` notification/QA routing regression | PASS — 10 tests |

The privacy suite proves that private profiles, private content, non-member
messages, another account's sessions/security events, and another account's
orders do not enter results.

## Simulator evidence

Simulator: iPhone 17 Pro Max  
Build: fresh Xcode Debug build  
Result: `BUILD SUCCEEDED`  
Bundle: `com.pulsesoc.nativeapp.dev`  
Runtime: Expo SDK 54 development client  
Backend selection: local Metro configuration with
`EXPO_PUBLIC_PULSE_API_BASE_URL=http://127.0.0.1:5050`

Observed:

- Fresh uninstall/install/launch completed.
- Local development bundle loaded.
- Temporary QA authentication completed.
- The QA start route exposed and then fixed a real `/pulse/ai` routing defect:
  notification routing had treated UNDX as an unknown target and fallen back to
  Activity Inbox.
- The canonical native UNDX conversation visibly opened after the route fix.
- “What happened today?” was typed and sent from the simulator.
- UNDX returned the `activity.daily_summary` content card with five authorized
  records.
- The first visible record showed “Phase 3B QA attention,” its source
  (`notifications`), timestamp, and an actionable native `Open` link.
- The native `Open` action visibly navigated to the server-backed Notifications
  screen.
- Final observed activity turn: correlation ID `7ba61d8ca660`, message IDs
  `33`/`34`, capability `activity.daily_summary`, status `verified_success`.
- Observed notification-summary turn: correlation ID `74f6335d7fd1`,
  capability `notifications.group_summary`, status `verified_success`.

Evidence:

- `reports/evidence/undx_phase3b_simulator_launch_2026-07-29.png`
- `reports/evidence/undx_phase3b_simulator_app_2026-07-29.png`
- `reports/evidence/undx_phase3b_simulator_authenticated_2026-07-29.png`
- `reports/evidence/undx_phase3b_simulator_live_activity_2026-07-29.png`
- `reports/evidence/undx_phase3b_simulator_activity_source_cards_2026-07-29.png`
- `reports/evidence/undx_phase3b_simulator_notification_cards_2026-07-29.png`

The evidence demonstrates the canonical local route, visible UNDX input and
response, source-backed native card, and native destination. It does not claim
physical-device observation.

No physical-device validation was requested or performed in this phase.

## Memory foundation

`memory.activity.inspect` is retrieval-only. It stores no sensitive copy and
reports:

- source and source ID,
- timestamp,
- confidence,
- owner scope,
- native route,
- `storage: source_retrieval_only`,
- `sensitive_memory_written: false`,
- `deletion_support: source-owned`.

Deletion remains controlled by the canonical source system.

## Remaining blocked domains

- Message sending and destructive message operations
- Financial transactions, purchases, billing changes, refunds, payouts and transfers
- Autonomous posting and publishing
- Calls and Live control
- Destructive account/content deletion
- Password, MFA and session-revocation mutations
- Privileged moderation
- Scheduled/delegated writes without revocable mandate support

## Final judgment

Backend Phase 3B capability, authorization, registry, governance, benchmark and
regression gates: **PASS**.

Native build/install and server-backed Activity surface: **PASS**.

Required visible UNDX chat demonstration in the iPhone 17 Pro Max simulator:
**PASS**.

Phase 3B is closeable for its requested simulator-first, read-only scope. Physical
device validation remains outside this phase and is not claimed.
