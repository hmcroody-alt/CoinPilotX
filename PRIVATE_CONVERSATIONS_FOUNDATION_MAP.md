# PRIVATE CONVERSATIONS — FOUNDATION MAP

**Mission Stage 1 deliverable. Forensic map only — no product code was changed.**

| | |
|---|---|
| Repo | `/Users/hmcherie/Desktop/CoinPilotX` |
| Branch | `main` (ahead of `origin/main` by 6) |
| HEAD | `da5d530f44af77ce48d380f68d93aeecfb47f0b6` |
| Working tree at start | **dirty — concurrent agent work present, see §0.3** |
| Files changed by this mission so far | 1 (this document) |
| Date | 2026-09-05 |

---

## 0 — HEADLINE FINDINGS

### 0.1 The canonical messaging foundation already exists and is close to complete

`pulse_communications_v2/` (7,198 lines across 10 modules) is a single, governed,
v2-prefixed messaging authority. It already implements, in code, the large majority of
what Stages 16–35, 54–68 and 102 ask to be built:

conversations, participants with roles and membership state, messages with a
`client_message_id` idempotency identity **backed by a real partial unique index**,
reactions, read receipts, delivery status, typing with expiry, presence with a privacy
setting, per-conversation notification settings, blocks, reports, moderation events,
message deletions (per-user), edit with `edited_at`, forward, pin, archive, mute, unread
counts, conversation-scoped search, people search, and a "control center" that already
serves **media / links / pins / export** for a conversation.

**Consequence: building a Private Office messaging backend would create exactly the
second message ledger Stages 4 and 137 forbid.** The correct posture is a thin
*policy + context layer* over `pulse_communications_v2`, plus native UI. Section 12
scopes the actual gap accordingly.

### 0.2 The attachment-identity hazard named in Stages 30 and 114 is already solved — on the client

The divergence is real and is confirmed in code:

- `message_attachments.id` — **the canonical foundation media id**. This is what
  `/api/messages/media/<id>/access` and `/download` are keyed on, and nothing else.
- `comm_v2_attachments.id` — a **transport row id**, a different integer in the same
  message. `comm_v2_attachments.media_upload_id` carries the foundation id.

`pulse_communications_v2/service.py:2364` (`_attach_foundation_media`) writes the
foundation id into `media_upload_id` and builds `url = /api/messages/media/{foundation_id}/download`.

`mobile-native/src/media/messengerMediaAccess.ts` already documents the exact production
failure (`attachment_id=422 / media_upload_id=33`; asking for 422 is a hard 404), exposes
`attachmentIdFromMediaUrl()` to recover the canonical id from the URL, and refuses to
treat an unproven `attachmentId` as a foundation id — including on retry.

**Consequence: Private Conversations must consume `messengerMediaAccess.ts` as-is and add
regression coverage, not re-derive media identity.** Any new client media path that reads
`attachment.id` is the regression this mission is explicitly told to prevent.

### 0.3 Concurrent-work hazard — the tree is not clean

`git status -sb` at HEAD shows **9 modified and 4 untracked files**, all Private Office:

```
 M services/private_office/{audit,facts,model,records,schema,telemetry}.py
MM services/private_office/{obligation_projection,portfolio_projection}.py
 M services/private_office_routes.py
MM tests/private_office/test_capital_overview.py
?? PRIVATE_FACTS_SUPER_FOUNDATION_MAP.md
?? PRIVATE_OPERATIONS_FOUNDATION_MAP.md
?? services/private_office/operations.py
?? tests/private_office/test_private_fact_ledger.py
```

`git worktree list --porcelain` reports **21 worktrees**, 6 under `.claude/worktrees/`
on live `claude/*` branches — including `claude/unruffled-engelbart-95bf7d`
("private meetings model — lifecycle, waiting room, lock, both-system blocking") and
`claude/zen-heisenberg-143623` ("operations foundation map"). Both touch the same
Private Office surface this mission integrates with.

**Rules this imposes on the implementation phase:** explicit file staging only, no
`git add -A`, no `reset --hard`, no `clean -fd`, no stashing, no worktree deletion, and
`services/private_office/{audit,schema,telemetry}.py` must be edited by append/addition
only since another agent holds uncommitted lines in them.

### 0.4 `CLAUDE.md` is stale on two points

`CLAUDE.md` states the branch is `codex/emergency-live-audio-recovery` with modified
`bot.py` / `undx_*` files. The tree is on `main` with an entirely different dirty set.
It also gives the bash mount as `/sessions/happy-sweet-brahmagupta/mnt/CoinPilotX`; the
live mount is `/sessions/peaceful-keen-clarke/mnt/CoinPilotX`. Plans written against
either statement are planning against a tree that is not here.

---

## 1 — THE FOURTEEN MANDATED ANSWERS

| Question | Answer |
|---|---|
| **Canonical message ledger** | `comm_v2_messages` (`pulse_communications_v2/models.py:85`). Written only through `service.send_message()`. Legacy `pulse_messages` and `messages` exist but are superseded. |
| **Canonical conversation ledger** | `comm_v2_conversations` (`models.py:24`), membership in `comm_v2_participants` (`models.py:56`). |
| **Canonical attachment id** | `message_attachments.id` — the *foundation* id (`services/messenger_media_foundation.py:158`). `comm_v2_attachments.id` is transport only; `comm_v2_attachments.media_upload_id` is the foundation id. |
| **Canonical media access route** | `GET/POST /api/messages/media/<attachment_id>/access` → `bot.py:87863`, then `GET /api/messages/media/<attachment_id>/download` → `bot.py:87899`. Access mints a short-lived signed token (`mint_access_token`, TTL 900s default, `PULSESOC_MESSENGER_MEDIA_TOKEN_TTL_SECONDS`), carried as `?mt=` or `X-PulseSoc-Media-Token`. |
| **Canonical storage provider** | Cloudflare R2 via `services/media_storage.py` (private objects), with `storage/messenger_uploads` as the local private fallback. `storage_provider` on a comm_v2 attachment reads `messenger_media_foundation`. No permanent public object URL is issued. |
| **Read receipts** | `comm_v2_read_receipts` (per message/user: `delivered_at`, `seen_at`, `read_at`) **plus** the watermark on `comm_v2_participants.last_read_message_id` / `last_read_at` / `unread_count`. Written by `service.mark_read()`. Gated by `comm_v2_user_settings.read_receipts_enabled` via `_read_receipts_allowed()`. |
| **Group membership** | `comm_v2_participants(conversation_id, user_id)` unique, with `role` (default `member`), `membership_state` (default `active`), `left_at`. Server-derived on every read via `_conversation_access()` (`service.py:1182`). |
| **Message permissions** | `_conversation_access(cur, user_id, conversation_ref, join_public=False)` is the single gate; every read and write path calls it. Media uses the parallel `messenger_media_foundation.require_conversation_access()` (`:358`), which probes comm_v2 → pulse → legacy and returns the model name. |
| **Blocks / restrictions** | Two stores, both consulted: `comm_v2_blocks` (via `service._blocked_between()`, `:1111`) and the platform-wide `blocked_users` (via `messenger_media_foundation._conversation_blocked()`, `:339`). Blocks are enforced at send, at direct-open, and at media access. |
| **Push notifications** | `service._dispatch_message_side_effects()` (`:1886`) → `services/pulsesoc_notification_system`, dispatched async via `_dispatch_push_alert_async()` (`:2114`). Per-recipient policy from `_participant_push_policy()` (`:1144`); preview suppression from `_message_preview_hidden()` (`:1171`), backed by `comm_v2_user_settings.message_preview_privacy` and per-conversation `notification_json`. |
| **Voice messages** | Foundation `media_type='voice'`; MIME allowlist `audio/{webm,mpeg,mp4,wav,ogg}` with `MIME_ALIASES` for m4a; waveform capped at 512 points; 25 MB default (`MESSENGER_VOICE_MAX_MB`). Comm-v2 mirrors `voice_note=1`, `duration_seconds`, `waveform_json`. Validated by `service._validate_voice_upload()` (`:2550`). Contract test: `tests/test_messenger_voice_message_contract.py`. |
| **Existing Private Office security** | `services/private_office/security.py` — the **second lock**. Salted PBKDF2 hash in `private_office_security.passcode_hash`, never plaintext anywhere. Grants are opaque tokens bound to a device + session family, TTL 60s–24h (default 15m), revoked on lock/passcode-change. Headers `X-Office-Grant` / `X-Office-Device`. Failure code `PRIVATE_OFFICE_LOCKED` → **HTTP 423**. Progressive cooldown after 5 free attempts (30s/60s/5m/15m). |
| **What should be reused** | Everything in §2 through §7 below. Concretely: `pulse_communications_v2.service` for the entire ledger; `messenger_media_foundation` for all attachments; `/api/messages/media/*` for all media auth; `private_office.security` for the lock; `private_office.access` + `feature_matrix` + `tiers` for entitlement; `private_office.audit` + `telemetry` for governance; `private_office.meetings` (Agora) for meetings; `private_office.documents`, `records`/`operations`, `facts`, `relationships` for the integrations; `mobile-native/src/media/messengerMediaAccess.ts` and `src/api/messenger.ts` for the client. |
| **What is actually missing** | See §12. Short version: a bounded Private Office conversation *classification* on the canonical conversation row, a policy layer that enforces it, six route families, the cross-domain link records (conversation↔organization / project / meeting / document / operation / fact-proposal), the native screens, and the test batteries. **No new ledger, no new attachment store, no new RTC, no new push engine.** |

---

## 2 — CANONICAL MESSAGING FOUNDATION (`pulse_communications_v2`)

### 2.1 Tables (`models.py`, all `comm_v2`-prefixed, `ensure_schema` idempotent)

| Table | Owns |
|---|---|
| `comm_v2_conversations` | `conversation_type`, `title`, `description`, `owner_user_id`, `direct_key` (unique — this is what prevents duplicate 1:1 rooms, Stage 80), `community_id`, `channel_id`, `privacy`, `visibility`, `status`, `member_count`, `last_message_id/at`, `last_activity_at`, `deleted_at` |
| `comm_v2_participants` | `role`, `membership_state`, `joined_at`, `left_at`, `muted_until`, `pinned_at`, `notifications_level`, `last_read_message_id`, `unread_count` |
| `comm_v2_messages` | `public_id`, `message_type`, `body`, `reply_to_message_id`, `thread_root_message_id`, **`client_message_id`**, `delivery_status`, `moderation_status`, `metadata_json`, `edited_at`, `deleted_at` |
| `comm_v2_attachments` | transport row; **`media_upload_id` → foundation id**; `url`/`playback_url` point at `/api/messages/media/<foundation_id>/download` |
| `comm_v2_message_reactions` | unique `(message_id, user_id)` — one reaction per user per message |
| `comm_v2_read_receipts` | unique `(message_id, user_id)`; `delivered_at` / `seen_at` / `read_at` |
| `comm_v2_message_deletions` | per-user "delete for me" |
| `comm_v2_typing` | unique `(conversation_id, user_id)` with `expires_at` — **not** persisted as messages |
| `comm_v2_presence` | `status`, `last_seen_at`, `active_until` |
| `comm_v2_user_settings` | `presence_privacy`, `read_receipts_enabled`, `message_preview_privacy` |
| `comm_v2_conversation_settings` | per `(conversation, user)`: notification / appearance / privacy / media / accessibility / productivity JSON |
| `comm_v2_blocks`, `comm_v2_reports`, `comm_v2_moderation_events` | trust & safety |
| `comm_v2_conversation_items` | typed items attached to a conversation (`item_type`, `title`, `body`, `status`, `due_at`, `metadata_json`) — **the most likely host for pinned/linked context, see §12.3** |
| `comm_v2_communities`, `comm_v2_channels` | community/channel scoping |
| `comm_v2_live_streams` | Mux live, not in scope |

### 2.2 Bounded vocabularies (`service.py:20`)

```python
ALLOWED_CONVERSATION_TYPES = {"direct", "group", "room", "community_channel"}
ALLOWED_MESSAGE_TYPES = {"text","image","gif","video","audio","voice","file","media","system"}
```

`create_conversation()` (`:1381`) rejects anything else. **This is the governance point
Stage 5 requires** — but note the mission's four types (`DIRECT` / `GROUP` /
`ORGANIZATION_ROOM` / `PROJECT_ROOM`) are *three* Private Office flavours of the existing
`direct` / `group` / `room`. See §12.1 for the recommended representation.

### 2.3 Idempotent send is already real (Stages 67–68)

`MESSAGE_IDEMPOTENCY_INDEX = idx_comm_v2_messages_client_idem` over
`(conversation_id, sender_user_id, client_message_id)` with predicate
`client_message_id IS NOT NULL AND client_message_id <> ''`. It is installed at boot by
`_ensure_message_idempotency_index()` (`:447`), which **inspects the existing index shape
on both SQLite and Postgres**, counts duplicates first, and refuses to install over
dirty data (`blocked_by_duplicates`) rather than crashing. Health is exported at
`GET /admin/health/messenger-idempotency` and `service.message_idempotency_health()`.
`_message_for_client_id()` (`:1522`) returns the existing message on replay.

Existing coverage: `tests/test_messenger_send_idempotency.py`,
`tests/test_messenger_idempotency_index_health.py`.

### 2.4 Service API surface already available to a policy layer

Conversations: `create_conversation`, `list_conversations`, `mark_read`, `mark_unread`,
`toggle_pin`, `toggle_mute`, `archive_conversation`, `list_members`, `add_member`,
`conversation_presence`, `set_typing`, `typing_state`.

Messages: `send_message`, `list_messages`, `edit_message`, `delete_message`
(`delete_for="self"|"everyone"`), `set_reaction`, `toggle_message_pin`,
`forward_message`, `report_message`, `search_messages`, `search_people`, `block_user`.

Control center: `conversation_control_center`, `update_conversation_control_center`,
`conversation_control_media`, `conversation_control_links`, `conversation_control_pins`,
`conversation_control_export`, `conversation_control_action`.

Media: `stage_attachment_upload`, `_validate_foundation_attachment_ids` (`:1835`),
`_attach_foundation_media` (`:2364`).

AI: `ai_context_for_conversation(user_id, ref, limit=30)` (`:2918`) — **already the
authorized, bounded reader UNDX should use for Stages 47–51.**

Realtime: `poll_realtime_events` (`:2222`), `stream_realtime_events` (SSE, `:2255`).

### 2.5 HTTP surface (`pulse_communications_v2/routes.py`)

Blueprint `pulse_communications_v2`, dual-mounted under
`{API_PREFIX}` = `/api/pulse/communications/v2` and the alias `/api/pulse/comm/v2`.
Roughly 60 message/conversation routes, including `/direct/open`, `/groups`, `/rooms`,
`/conversations/<ref>/control-center{,/media,/links,/pins,/export,/action}`,
`/search`, `/people/search`, `/attachments/upload`, `/realtime`, `/realtime/stream`.

---

## 3 — MEDIA FOUNDATION (`services/messenger_media_foundation.py`, 1,040 lines)

- **Table**: `message_attachments`, created and column-migrated idempotently by
  `ensure_schema()` (`:156`). Carries `conversation_model` (`comm_v2` | `pulse` | legacy),
  `upload_status` ∈ {pending, uploaded, attached, failed, deleted}, `processing_status`
  ∈ {not_required, queued, processing, ready, failed} — **this is the Stage 69 state
  machine, already present.**
- **Lifecycle**: `init_upload` → `upload_file` (spooled, size-capped) → `complete_upload`
  → `attach_to_message`. Plus `retry_attachment`, `delete_attachment`, `get_attachment`.
- **MIME allowlist + per-type size caps** (`ALLOWED_MIME_TYPES`, `SIZE_LIMIT_ENV`):
  photo 15 MB, video 200 MB, voice 25 MB, file 50 MB — all env-overridable. **Server-side**,
  satisfying Stage 70.
- **Authorization**: `require_conversation_access()` (`:358`) — probes comm_v2, then
  pulse, then legacy `conversation_members`; raises `MessengerMediaError` with **403**
  `not_conversation_member` / `conversation_inactive` / `messaging_blocked`.
- **Access tokens**: `mint_access_token` / `access_token_state` / `access_token_user_id`
  (`:955–1012`). HMAC, bound to `(attachment_id, user_id)`, TTL default 900s.
  `attachment_download_target()` (`:1025`) resolves the byte source.
- **Gap for Stage 27**: `ALLOWED_MIME_TYPES` has **no PDF or Office document entries** —
  only image/video/audio. `MEDIA_TYPES` includes `"file"` and `SIZE_LIMIT_ENV` has a
  `file` cap, so the type exists but no MIME maps to it. **Document/PDF attachments in a
  conversation are currently impossible.** This is a genuine, small, well-bounded gap.

### 3.1 Routes (`bot.py`)

```
GET    /api/messages/media/<int:attachment_id>            bot.py:87848
GET|POST /api/messages/media/<int:attachment_id>/access   bot.py:87863   ← canonical grant
GET    /api/messages/media/<int:attachment_id>/download   bot.py:87899
POST   /api/messages/media/<int:attachment_id>/retry      bot.py:87935
DELETE /api/messages/media/<int:attachment_id>            bot.py:87950
```

Existing isolation coverage: `tests/test_messenger_media_auth_isolation.py`.

---

## 4 — PRIVATE OFFICE SECURITY AND ENTITLEMENT

### 4.1 The second lock (`services/private_office/security.py`, 773 lines)

Passcode: PBKDF2 hash only (`HASH_VERSION = "werkzeug-pbkdf2-v1"`), 6–32 chars,
`_BANNED_PASSCODES` refuses trivial values. Grants: opaque token, hashed at rest,
bound to device (`X-Office-Device`) and session family, `GRANT_TTL_DEFAULT_SECONDS = 900`
(floor 60, ceiling 86400). Invalidated by explicit lock, passcode change/reset, and by
`issued_at` predating the passcode's `changed_at`. Lockout: 5 free attempts then
`COOLDOWN_STEPS_SECONDS = (30, 60, 300, 900)`.

`ERR_LOCKED = "PRIVATE_OFFICE_LOCKED"` → HTTP **423**.

### 4.2 The one access decision (`services/private_office/access.py`)

`decide(resolved, feature_id)` returns exactly one of
`ALLOW` / `UNAVAILABLE` / `NOT_IMPLEMENTED` / `FEATURE_DISABLED` / `NOT_ENTITLED`.
Implementation state is consulted **before** tier, so an unbuilt feature never renders an
upgrade prompt. `UNAVAILABLE` is deliberately not a denial. This is the Stage 98/101/123
contract, already codified.

### 4.3 Feature matrix (`services/private_office/feature_matrix.py`)

Existing `feature_id`s: `advanced_undx`, `market_pulse`, `capital_graph`,
`private_briefings`, `private_facts`, `private_shield{,.breach_monitoring}`,
`private_office.operations`, `private_office.records`,
`private_office.document.extraction`, `private_meetings{,.recording,.transcription,.screen_share}`,
`relationship_intelligence`, `human_concierge`.

Implementation states: `IMPLEMENTED` / `SHADOW` / `PROVIDER_REQUIRED` / `DISABLED` /
`NOT_IMPLEMENTED`.

**No conversations feature id exists yet.** §12.5 proposes the bounded addition.

### 4.4 The shared route entry pattern

Every Private Office route module uses the same four-line entry, imported — never
copied — from `services/private_office_routes.py`:

```python
user = po_http._current_user()                    # → 401
resolved = po_http._resolve_for(user)
refusal  = po_http._gate(resolved, FEATURE_ID)    # → 403 / NOT_ENTITLED / UNAVAILABLE
locked   = po_http._office_lock_gate(user)        # → 423
```

with `po_http._no_store(...)` for every response and `po_http._with_cursor(work)` for DB
access. Reference implementation: `services/private_office_documents_routes.py:63`.

**Critical reading for Stages 12–13.** `_office_lock_gate(user)` gates *the calling
member's own* office. It is not the owner's passcode being demanded of a guest. So an
invited participant unlocking **their own** office is coherent; but a participant with no
Private Office entitlement cannot reach the conversation through the `/api/private-office/*`
surface at all. Since the conversation lives in the canonical ledger, that participant
still reaches it through ordinary Messenger — which is the honest resolution of Stage 60
and is stated as a decision in §12.7.

---

## 5 — PRIVATE OFFICE ROUTE FAMILIES ALREADY REGISTERED

| Module | Routes |
|---|---|
| `private_office_routes.py` | `/entitlement`, `/overview`, `/facts`, `/capital-graph{,/overview,/portfolio,/exposure,/obligations}`, `/entities/<id>{,/relationships}`, `/records/<view>{,/<id>/status}`, `/operations/overview`, `/attention`, `/security/{status,setup,unlock,lock,change,reset,biometric}` |
| `private_office_documents_routes.py` | `/documents{,/<id>,/<id>/content}`, `/claims/<id>/review` |
| `private_office_relationships_routes.py` | `/relationships{,/<id>,/<id>/briefing,/<id>/facts}` |
| `private_office_meetings_routes.py` | `/meetings` + 23 sub-routes (join, admit, deny, lock, role, remove, invites, token, recording, artifacts, intelligence, messages, rotate-code, hand, …) |
| `private_office_briefings_routes.py` | `/briefings{,/<id>,/<id>/actions,/why}` |
| `private_office_shield_routes.py` | `/shield{,/findings,/findings/<id>,/scan}` |
| `private_office_concierge_routes.py` | `/concierge{,/desk,/desk/<owner>/<req>,/requests,/requests/<id>,/requests/<id>/cancel,/requests/<id>/messages}` |
| `private_office_structured_records_routes.py` | structured record surface |

**A `private_office_conversations_routes.py` module following this exact pattern is the
correct home for the new surface.** It keeps `bot.py` untouched, which matters: `bot.py`
is protected by *diff content* under the realtime-audio gate.

---

## 6 — PRIVATE MEETINGS (Agora) — REUSE, DO NOT REBUILD

`services/private_office/meetings.py` (1,920 lines) owns the full meeting lifecycle:
`create_meeting`, `start_meeting`, `join_meeting`, `admit/deny_participant`,
`set_locked`, `set_role`, `remove_participant`, `leave_meeting`, `end_meeting`,
`rotate_code`, `mark_joined`, `mark_reconnecting`, plus invites, messages, recordings and
artifacts.

RTC: `_create_room_call()` (`:614`) inserts into **`communication_calls`** with
`provider='agora'`, `call_type='video'`, `call_scope='room'`, `conversation_id=0`.
`start_meeting()` reads `call_engine.agora_config_status()`. There is **no LiveKit** in
this path.

**Stage 44 integration shape:** Private Conversations calls `meetings.create_meeting()` +
invite the conversation's participants, then records a reference. It must not create a
`communication_calls` row itself, must not mint an RTC token itself, and must not touch
`services/pulsesoc_communications_engine.py`.

**Expected RTC blast radius for this mission: 0 files.**

---

## 7 — INTEGRATION TARGETS (all already governed writers)

| Domain | Module | Entry point for Private Conversations |
|---|---|---|
| Documents | `private_office/documents.py` (683 ln) | "Save to Documents" writes through the canonical document writer with provenance; the message attachment is **not** auto-imported (Stage 28) |
| Operations / Records | `private_office/records.py` (1,775 ln), `operations.py` (436 ln, **untracked — another agent's file**) | "Create Operation" with `source_conversation` / `source_message` provenance (Stages 39–40) |
| Private Facts | `private_office/facts.py` (1,777 ln) | Proposal only — conversation text must land as the existing user-asserted/derived state and go through review, never `VERIFIED` (Stages 41–42) |
| Relationships | `private_office/relationships.py` (418 ln) | Derive `last_message_at` / counts only; **no message text copied** (Stages 36–37, 85) |
| Briefings | `private_office/briefings.py` (442 ln) | Contribute "needs response" / "unresolved commitment" / "files shared" items (Stage 89) |
| Shield | `private_office/shield.py` (478 ln) | Membership-change and suspicious-file findings (Stage 90) |
| Concierge | `private_office/concierge.py` (460 ln) | Explicit scoped context handoff only (Stage 46) |
| Audit | `private_office/audit.py` (454 ln) | `ACTION_*` constants; **add conversation actions by append only** (§0.3) |
| Telemetry | `private_office/telemetry.py` (563 ln) | `EVENT_*` + `KIND_COUNT/FLAG/ENUM`; privacy-safe counters only |

---

## 8 — NATIVE CLIENT (`mobile-native/`, Expo 54 / RN 0.81.5)

### 8.1 Messenger client — reuse

| File | Role |
|---|---|
| `src/api/messenger.ts` | The whole comm-v2 client. Calls `/api/pulse/communications/v2/*`. Offline outbound queue, per-thread caches, polling sync driven by server-supplied `poll_interval_ms` / `sync_interval_ms`. |
| `src/media/messengerMediaAccess.ts` | **Canonical media identity + access-URL cache.** `MessengerMediaIdentity`, `attachmentIdFromMediaUrl()`, renew margin 60s, bounded recovery that never retries an unproven id. |
| `src/api/presence.ts` | Presence read/write. |
| `src/api/conversationDomain.ts` | SOCIAL vs COMMERCE partitioning with separate cache keys. **Private Office is a third classification and should extend this, not fork it.** |
| `src/screens/MessengerScreen.tsx` | Inbox list. |
| `src/screens/ChatScreen.tsx` | Thread + composer. |
| `src/screens/NewChatScreen.tsx` | Recipient picker. |
| `src/components/ConversationControlCenter.tsx` | Info / participants / media / links / pins / search / export. |

Realtime is **polling**, not sockets — though the server exposes SSE at
`/realtime/stream`, the client does not consume it. Do not introduce a socket layer.

State is AsyncStorage-cached, keyed
`pulsesoc.native.messenger.v2.{conversations,messages.<id>,outbound_queue}`.
There is **no Zustand store for messaging**; Stage 107's account-switch cache isolation
must be verified against these keys.

### 8.2 Private Office client — reuse

`src/api/privateOffice.ts`, `privateFeatures.ts`, `privateRecords.ts`, `capitalGraph.ts`,
`src/entitlements/canonicalTier.ts`.

Lock: `src/privateOffice/officeLock.ts` — injects `X-Office-Grant` and `X-Office-Device`,
holds the grant **in module memory only** (never on disk), Face ID via expo-secure-store
with `requireAuthentication: true`, relock preference key
`pulsesoc.native.office.relock.v1`. Gate wrapper: `src/privateOffice/PrivateOfficeLockGate.tsx`.

Tagged states: `privateFeatures.ts` `refusal()` already produces
`READY | LOADING | EMPTY | DENIED | LOCKED | NOT_ENTITLED | UNAVAILABLE | ERROR` —
**Stage 98 and Stage 123 are already satisfied by an existing helper. Reuse it.**

Screens: `PrivateOfficeScreen`, `PrivateFactsScreen`, `PrivateOperationsScreen`,
`PrivateDocumentsScreen`, `PrivatePeopleScreen`, `PrivateBriefingsScreen`,
`PrivateShieldScreen`, `PrivateConciergeScreen`, `PrivateOfficeSecurityScreen`,
`CapitalGraphScreen`, `CapitalEntityScreen`.

### 8.3 Navigation & i18n

Navigator: `src/navigation/AppNavigator.tsx` (Private Office routes ~lines 623–635,
registered unconditionally — the server decides visibility). Param list:
`src/navigation/types.ts`. A new sub-stack = add to `RootStackParamList` + a
`<Stack.Screen>` + locale keys.

i18n: `src/i18n/{engine,locales,store,hooks}.ts`, catalogs at
`src/i18n/catalogs/<locale>/<namespace>.json`, **11 locales**
(en, es, fr, ht, pt, de, ar, hi, ja, ko, zh). Hardcoded strings fail CI.
Parity test: `src/navigation/__tests__/navigatorLocalization.test.ts`.
Verify command: `npm run verify` (typecheck + i18n + jest).

---

## 9 — PROTECTION AND CI BOUNDARY

- Realtime audio manifest: `config/realtime-audio-protected-paths.json`
  (baseline `ce03e160…`, verified 2026-08-02). Protected coordinator paths include
  `mobile-native/src/core/realtimeAudioEngine.ts` and `realtimePublisherMedia.ts`.
  `bot.py` is gated by **diff content**, not path — only lines matching
  `backend_diff_patterns` trip it.
  Local gate: `python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD`.
- Protection suite: `scripts/protection/run_protection_suite.py`,
  CI `.github/workflows/protection.yml`, 21 subsystems.
- Relevant existing suites to keep green:
  `tests/test_messenger_media_auth_isolation.py`,
  `tests/test_messenger_send_idempotency.py`,
  `tests/test_messenger_idempotency_index_health.py`,
  `tests/test_messenger_voice_message_contract.py`,
  `tests/private_office/` (14 files).
- Backend OS registration lives in `services/backend_management_registry.py`
  (Stage 103) and the UNDX map in `services/undx_knowledge_map.py`.

**Planned blast radius: 0 protected audio paths, 0 Agora core files, 0 lines in `bot.py`.**

---

## 10 — WHAT MUST NOT BE BUILT

Each of these already has exactly one owner; a second one would be the failure this
mission is written to prevent.

| Forbidden | Existing owner |
|---|---|
| A second message ledger | `comm_v2_messages` |
| A second conversation ledger | `comm_v2_conversations` / `comm_v2_participants` |
| A second attachment store | `message_attachments` |
| A second media auth path | `/api/messages/media/<id>/access` |
| A second storage integration | `services/media_storage.py` (R2) |
| A second push engine | `services/pulsesoc_notification_system` via `_dispatch_message_side_effects` |
| A second presence/typing system | `comm_v2_presence` / `comm_v2_typing` |
| A second RTC provider or engine owner | `private_office/meetings.py` → `communication_calls` (Agora) |
| A second passcode / lock | `private_office/security.py` |
| A second entitlement decision | `private_office/access.decide()` |
| A second document repository | `private_office/documents.py` |
| A second facts system | `private_office/facts.py` |
| A second rate limiter | canonical limiter (reuse per Stage 102) |

---

## 11 — RISKS CARRIED INTO IMPLEMENTATION

1. **Attachment identity (Stages 30, 114).** Solved on the client; **not** yet regression-
   tested end to end. Highest-value test in the whole matrix.
2. **PDF/document MIME gap (§3).** `ALLOWED_MIME_TYPES` admits no documents. Stages 27/28/43
   cannot pass without extending the allowlist — server-side, with matching size caps.
3. **Encryption language (Stage 53).** Storage is private-object + signed short-lived URL.
   There is **no cryptographic E2EE**. No screen may say "end-to-end encrypted".
4. **Concurrent agents (§0.3).** `audit.py`, `schema.py`, `telemetry.py`,
   `private_office_routes.py` all carry another agent's uncommitted lines.
5. **Owner-lock vs participant-access (§4.4).** The single most likely design error is
   gating participant reads on the *owner's* office grant. Membership is the authority;
   the lock protects the *viewer's own* office surface.
6. **Dual-mounted route aliases.** Comm-v2 is served at both `/api/pulse/communications/v2`
   and `/api/pulse/comm/v2`. New routes must not add a third alias.
7. **Optional route packs boot inside `except Exception`.** A registration failure makes
   the feature vanish silently in production; check boot logs, not 404s.

---

## 12 — THE ACTUAL GAP

### 12.1 Conversation classification (Stages 4, 5, 8, 9)

Do **not** add `private_office_messages`. Do **not** add four new
`ALLOWED_CONVERSATION_TYPES` values that would leak into ordinary Messenger's type
switch. Recommended: keep the canonical `conversation_type` (`direct` / `group` / `room`)
and add a **bounded Private Office classification** — a small governed link/metadata row
keyed by `conversation_id` carrying `office_scope` ∈ {`DIRECT`, `GROUP`,
`ORGANIZATION_ROOM`, `PROJECT_ROOM`}, sensitivity ∈ {`PRIVATE`, `HIGHLY_SENSITIVE`}, and
the optional `organization_node_id` / `operations_project_id` links. Rejects unknown
strings. This satisfies Stages 5/8/9/52 without forking the ledger.

### 12.2 Policy layer

`services/private_office/conversations.py` — a governed reader/writer that composes
`comm_v2` service calls with `access.decide()`, membership re-verification, audit and
telemetry. No direct SQL against `comm_v2_*` outside the canonical service where a
service function exists.

### 12.3 Cross-domain links (Stages 33, 38, 39–45)

Pinned content, meeting references, linked operations and imported documents are all
**references to canonical objects**. `comm_v2_conversation_items` already has the shape
(`item_type`, `metadata_json`, `status`, `due_at`). Confirm before adding any table.

### 12.4 Routes

`services/private_office_conversations_routes.py`, following §4.4 exactly: list, create
(direct/group/org/project), thread read, send, members, info, shared media/files/links,
pinned, search, meeting launch, save-to-documents, create-operation, propose-fact,
ask-UNDX. All `_no_store`, all tagged-state.

### 12.5 Feature matrix + flag

One `feature_id` (`private_office.conversations`) and one flag
(`PRIVATE_CONVERSATIONS_ENABLED`). Not a dozen.

### 12.6 Native

A Private Conversations home + thread + info stack reusing `messenger.ts`,
`messengerMediaAccess.ts` and the `refusal()` state helper; registered in
`AppNavigator.tsx`; strings in all 11 locales.

### 12.7 Decisions this map takes a position on

- **Stage 60 (Messenger visibility).** Private Office conversations are canonical
  conversations and **do** appear in ordinary Messenger, carrying their Private Office
  classification so they never render as a casual DM. This is what makes a
  non-entitled invited participant reachable at all, and it is what keeps unread counts
  single-sourced (Stage 59).
- **Stage 2 (product name).** Canonical internal capability name:
  **`private_office.conversations`**; canonical route family
  `/api/private-office/conversations`; display name **Private Conversations**. One name,
  no drift.

---

## 13 — STAGE 1 VERDICT

| | |
|---|---|
| Foundation map | **PASS** — all 14 mandated questions answered from code, with file and line citations |
| Canonical message ledger identified | `comm_v2_messages` |
| Canonical attachment authority identified | `message_attachments` |
| Second ledger required | **NO** |
| Second attachment store required | **NO** |
| New RTC / mic / camera owner required | **NO (0)** |
| LiveKit | **NOT PRESENT, NOT ADDED** |
| Files changed by Stage 1 | 1 (this document) |
| Push | **NO** |
| Ready to implement | **YES**, with §11 risks and §0.3 concurrency rules carried forward |
