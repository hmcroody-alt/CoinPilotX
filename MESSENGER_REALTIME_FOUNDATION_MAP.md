# MESSENGER REALTIME FOUNDATION MAP

**Mission:** Ultra-Fast Living Messenger Foundation (P0 / Core Social Experience)
**Stage:** 0 — Forensic ownership map. *No implementation is authorised until this map is accepted.*
**Date:** 2026-09-03
**Scope of evidence:** read-only inspection of `mobile-native/`, `pulse_communications_v2/`, `services/`, `bot.py`. Four parallel recon streams (native surface, realtime transport, delivery/read/unread, push/notification), with the load-bearing claims re-verified directly against source before writing.

---

## 0. How to read this document

Every path below is classified with the mission's own vocabulary:

| Class | Meaning |
| --- | --- |
| **REALTIME** | Server pushes state to the client without the client asking. |
| **POLLING** | Client repeatedly asks on a timer. |
| **REST** | One-shot request/response tied to a user action. |
| **DUPLICATE** | A second implementation of something that already exists elsewhere. |
| **LEGACY** | Still reachable, superseded, not the intended path. |
| **DEAD** | Present in the repository, unreachable from the active native app. |
| **CLIENT-GUESSED** | The client invents the value; no server truth backs it. |
| **SERVER-AUTHORITATIVE** | The server owns the value and the client only renders it. |

A claim without a `file:line` citation is not a claim. Where a stream could not verify something, it is marked **UNVERIFIED** rather than inferred.

---

## 1. The single most important finding

**There is no realtime transport in PulseSoc messaging. Not degraded — absent.**

No websocket exists anywhere the native app can reach. `mobile-native/src/` contains exactly two occurrences of the string `websocket`, and both are prose in comments explaining that there isn't one (`src/api/commerceInbox.ts:37`, `src/screens/CommerceInboxScreen.tsx`). SSE exists server-side but is double-gated off behind `PULSE_MAIN_APP_SSE_ALLOWED` and `PULSE_COMM_V2_SSE_ENABLED`, both unset.

The function that *looks* like a subscription is not one. `subscribeConversationUpdates` (`src/api/messenger.ts:25`, fired at `:919-924`) is an in-process JavaScript callback `Set`. It fires only when **this device's own code** writes the local cache. A message sent from another phone will never fire it. Every consumer that treats it as a live feed is being told a comforting lie by its own name.

What actually moves messages onto the screen is `ChatScreen`'s `sync()`: a 2.5-second interval that re-fetches **the newest 80 messages from the same route as the initial load**, then diffs client-side. It is not a delta fetch — the route exposes no `after_id` parameter. Consequences that follow directly and are not hypothetical:

- If more than 80 messages arrive between two ticks, the gap is **silent and permanent**. Nothing detects it; there is no conversation sequence number to notice a hole.
- The interval is **never cleared on background**, so a backgrounded-but-mounted ChatScreen keeps polling.
- Because reading is a side effect of polling (§4), that backgrounded screen also keeps marking the conversation read every 2.5 seconds.

Everything in Stages 1-57 that assumes "make the realtime layer faster" is misframed. There is no realtime layer to accelerate. **This mission builds one, or it builds nothing.**

---

## 2. Transport and event ownership

| Path | File:line | Class | Notes |
| --- | --- | --- | --- |
| `subscribeConversationUpdates` | `src/api/messenger.ts:25`, `:919-924` | **DEAD (as realtime)** | In-process callback Set. Local writes only. Misleadingly named. |
| ChatScreen `sync()` 2.5s loop | `src/screens/ChatScreen.tsx` | **POLLING** | Full re-fetch of newest 80. No `after_id`. Never cleared on background. |
| MessengerScreen inbox reload | `src/screens/MessengerScreen.tsx:131-134` | **POLLING** | Wholesale reload on *every* focus event. |
| `services/realtime_engine.py` | — | **DEAD (in prod)** | In-process event bus with a per-process `_event_id` counter. Two gunicorn workers ⇒ each has its own counter and its own invisible log. Cross-worker events are silently lost. |
| `/api/pulse/communications/v2/realtime` (`after_id` poll) | — | **DEAD** | Route exists, supports `after_id`, native calls it from nowhere. |
| SSE endpoints | — | **DEAD** | Double-gated off; both env flags blank. |
| Typing indicators | — | **SERVER-AUTHORITATIVE** | Correctly ephemeral, 5s `expires_at`. The one honest realtime-ish primitive in the system. |
| Presence service | `services/presence_service.py` | **SERVER-AUTHORITATIVE but UNWIRED** | Backend is sound. `ChatScreen`'s `peerPresence` is wired to nothing and is always `null`. |
| NetInfo / connectivity | — | **absent from the messaging path** | No reconnect semantics exist because there is no connection to lose. |

**Ownership verdict.** The only viable near-term transport is `realtime_engine` plus the already-built-and-unused `after_id` poll route — but **not before the event log moves out of per-process memory.** Shipping a subscription on top of a per-worker counter across two gunicorn workers produces intermittent, unreproducible message loss, which is strictly worse than today's honest 2.5s poll.

---

## 3. Send path and duplicate risk

**Optimistic send already exists.** Stage 2 is a repair, not a build. `createLocalMessage` (`src/api/messenger.ts:1440-1455`) mints `id = -Date.now()` and `client_message_id = "native-<n>"`; `ChatScreen.tsx:639-765` renders it immediately. A durable outbound queue exists under the AsyncStorage key `pulsesoc.native.messenger.v2.outbound_queue`. Drafts persist per conversation.

So the bubble does already appear when the finger leaves Send. What is broken is everything after that.

**Duplicate-risk register — the mission's named hard regression gate.** Five inputs can represent one logical message: the local bubble, the REST response, the (future) realtime echo, reconnect replay, and the push event. The reconciliation key is `messageKey = client_message_id || String(id)`. Three live defects break that reconciliation:

1. **`retryMessage` (`ChatScreen.tsx:785-797`) deletes the local message and re-sends with a fresh `client_message_id`.** If the original request actually succeeded and only the response was lost — the exact case retry exists for — the server now holds two messages with two different client ids and no way to know they are the same. **This is a guaranteed duplicate, not a race.**
2. **`CameraStudioScreen.tsx:415-433` is a second, independent send path that sets no `client_message_id` at all.** Its messages have no reconciliation key whatsoever. This is the single likeliest source of the duplicate messages already observed in production.
3. **The FlatList `keyExtractor` (`ChatScreen.tsx:1174`) changes when the server ack arrives** (negative local id → real id), forcing React to unmount and remount the bubble the user is watching. Not a data duplicate, but it is the visible flicker that makes an already-optimistic send feel slow.

**Additional native-surface findings:** the inverted FlatList sits at `ChatScreen.tsx:1167-1194`; `MessageBubble` (`:1970-2033`) is **not memoized**; every text bubble mounts the 457-line `ContentTranslation` component. Messenger bypasses the canonical Media Reliability Foundation via its own `uploadMessengerMedia` (`messenger.ts:951-1036`) and a raw `<Image>` at `ChatScreen.tsx:2158` — classified **DUPLICATE**. Forward does not exist.

---

## 4. Delivery, read, and unread

Schema lives in `pulse_communications_v2/models.py`, with a **second competing migration path** at `service.py:228-365` — classified **DUPLICATE**.

### 4.1 DELIVERED is structurally fake

`delivered_at` is written **only inside the recipient's read path** (`service.py:2495-2506`, and again at `:2717-2728`). Verified directly: the `INSERT OR IGNORE ... comm_v2_read_receipts (... delivered_at ...)` at `:2495-2502` and the `UPDATE ... SET delivered_at=COALESCE(NULLIF(delivered_at,''), ?)` at `:2503-2506` both sit inside `list_messages`, immediately above the `mark_read` call at `:2507`.

Two consequences:

- A sender's status jumps **Sent → Read**. "Delivered" as a distinct observable state does not exist.
- **A recipient who has disabled read receipts still leaks Delivered**, because Delivered is written by the same code path that read receipts gate. This is a privacy defect, not only a correctness one.

Per the mission's own rule — *never display SENT/DELIVERED/READ without authoritative state* — any Delivered indicator currently rendered is **CLIENT-GUESSED** and must be removed or made real.

### 4.2 Reading is a side effect of polling

`list_messages` calls `mark_read` **unconditionally** at `service.py:2507`, and `mark_read` (`:2696`, writing at `:2711`) sets `last_read_message_id`, `last_read_at`, and `unread_count=0` for the **entire** conversation. There is no foreground check, no visibility check, no scroll-position check.

Therefore: a ChatScreen that is mounted but backgrounded marks the whole conversation read every 2.5 seconds. Unread is destroyed by the act of observing it. **Five distinct read implementations exist**, of which this is the most damaging.

### 4.3 Two read models coexist

Both a cursor (`comm_v2_participants.last_read_message_id` — declared `service.py:269`, read at `:922`/`:958`/`:976`/`:1055`, written at `:2711` and `:2807`) **and** per-message rows in `comm_v2_read_receipts` exist, written independently. Classified **DUPLICATE**.

The good news for Stage design: **the cursor model is feasible and cheap.** The column already exists and is indexed, and message ids are global `AUTOINCREMENT`, hence monotonic within a conversation — so no per-conversation sequence number needs inventing. The work is not adding the cursor. The work is **removing the write side effects that currently corrupt it.**

### 4.4 Unread has no canonical source

Three implementations disagree:

| Source | Location | Class |
| --- | --- | --- |
| `_chat_unread_count_for_user` | `pulse_communications_v2/service.py` | chat-only |
| `pulse_badge_counts` | `services/notification_service.py` | sums three separate systems |
| Client-side guess | `src/api/notifications.ts:149-153` | **CLIENT-GUESSED** |

The tab badge is computed on the client at `AppNavigator.tsx:279-282`. It has **no account-switch reconciliation**, and it **omits commerce unreads** — contradicting the stated intent recorded at `src/core/unreadCounts.ts:331`.

---

## 5. Push and notification pipeline

### 5.1 What exists

Registration is `expo-notifications` (`package.json:60`). `performPushRegistration` obtains both the Expo token and the raw native token (`src/api/push.ts:100-103`) and POSTs them to `/api/push/subscribe` with `apns_token`/`fcm_token` mirrors (`:116-143`). A per-install `installation_id` lives in SecureStore (`:228-234`). Refresh revokes the old endpoint first (`:108-114`); `unregisterPushDevice` (`:165-199`) revokes and zeroes the badge (`:175`, `:191`).

**Two server-side token registries exist** — legacy `push_subscriptions` / `pulse_notification_devices` / `user_device_tokens` (`services/notification_service.py:2211-2225`) and the newer `notification_device_tokens` (`services/pulsesoc_notification_system.py:2169-2232`). Classified **DUPLICATE**.

**Two push senders exist**: `push_service._send_expo_push` (`services/push_service.py:738-770`) and `pulsesoc_notification_system._dispatch_push`. Double-send is prevented only by two defensive guards — `central_delivery_managed` (`services/notification_service.py:1125`) and `if not legacy_count` (`services/pulsesoc_notification_system.py:2597`).

The message payload (`pulse_communications_v2/service.py:1581-1610`) does carry `conversation_id` and `message_id` in its data block (`:1584-1585`), along with six redundant deep-link keys. Deep links themselves are conversation-only: web `/pulse/messages/{conversation_id}` (`:1574`), native `pulse://pulse/messages-v2?conversation={conversation_id}` (`:1575`).

### 5.2 What is missing

**`apns-collapse-id`, `thread-id`, and `collapse-id` are set nowhere in the repository.** Verified by repo-wide grep: the only matches are unrelated CSS classes (`static/css/pulse_messages_v2.css`) and Arena HTML attributes in `bot.py`. APNs headers are limited to `apns-topic`, `apns-push-type`, `apns-priority` (`services/pulsesoc_notification_system.py:2495`), with push type hard-coded `"alert"`.

**`dismissNotificationAsync`, `dismissAllNotificationsAsync`, and `getPresentedNotificationsAsync` have zero call sites anywhere in the repository.** Verified by the same grep. `UNUserNotificationCenter` appears zero times in `mobile-native/`, including the Swift under `modules/pulse-now-playing/`.

So: no notification the system has ever delivered has been assigned a stable identifier the client could address, and the client has never once asked the OS what is currently presented. **The removal half of the mission's target chain does not exist in any form.**

**No silent push is ever sent.** iOS declares `remote-notification` and `fetch` background modes (`app.json:25-29`, `ios/PulseSoc/Info.plist:68-72`) and `aps-environment` is set (`PulseSoc.entitlements:5`), but the server never emits `content-available` / `_contentAvailable` / `apns-push-type: background`, and every FCM message includes a `notification` block (`pulsesoc_notification_system.py:2412`, `:2445`). The capability is declared and unused. **Cross-device read-sync has no transport at all.**

**The deep link drops the message id.** `message_id` reaches the client in the data block but is discarded at `notificationTargetFromData` (`navigation/notificationRouting.ts:107`, `:120`, `:125`); routing navigates `Chat` with `conversationId` only (`:166-178`), and `linking.ts:127-128` maps `Chat` to `pulse/messages/:conversationId`. The chain the mission specifies — *notification 3812 → conversation 92 → cursor passes 3812* — currently loses 3812 at the client boundary.

### 5.3 Badge divergence — four values for one number

1. `pulse_communications_v2/service.py:1603` — chat-only unread.
2. `services/notification_service.py:1150-1151` — `pulse_badge_counts`, chat-or-alert. Overridden at `:1165` when `metadata["badge"]` is present, so for messages the comm-v2 value wins.
3. `services/pulsesoc_notification_system.py:2371` — `total_unread_count`.
4. `AppNavigator.tsx:279-282` — client store total, which overwrites whatever arrived.

**Plus an outright type bug.** `_push_payload` sets `"badge": True` (`pulsesoc_notification_system.py:2390`) and then spreads `**metadata` (`:2394`). For any notification type that does not inject a numeric `badge`, the value stays boolean `True`, and `_send_apns_token` coerces it with `_int(payload.get("badge"), 0)` (`:2484`) — producing an **APNs badge of exactly 1 regardless of real unread count.**

### 5.4 Client notification handling

The OS banner is **already globally suppressed**: `setNotificationHandler` (`src/api/push.ts:45-53`) returns `shouldShowAlert: false, shouldShowBanner: false, shouldShowList: true, shouldPlaySound: true, shouldSetBadge: true`. The in-app `InAppNotificationBanner` replaces it.

**Three `addNotificationReceivedListener` subscribers** fire on every notification, for three unrelated purposes: badge refresh (`AppNavigator.tsx:300`), banner (`components/InAppNotificationBanner.tsx:96`), and call polling (`calls/IncomingCallLayer.tsx:171`). Response routing is correctly singular (`navigation/notificationRouting.ts:91`, cold start at `:93`).

**No code anywhere checks the currently-open conversation**, so a banner fires for the thread the user is actively reading. Useful asset: `notificationDedupe.notificationStableId` (`:43-63`) **already computes `msg:<conv>:<msgid>`** client-side. The stable identity the mission wants exists on the client today; it has simply never been given to the OS or used for removal.

Two competing dedupe implementations with different keys and windows: `notificationDedupe` (server ids, 60s) vs `notificationRouting.ts:80-83` (OS identifier, 5s). Classified **DUPLICATE**.

---

## 6. Feasibility assessment

| Target | Verdict | Basis |
| --- | --- | --- |
| Read cursor as single source of read truth | **Feasible, cheap** | Column exists and is indexed (`service.py:269`); ids globally monotonic. Cost is removing side-effect writes, not adding storage. |
| Kill read-on-poll | **Feasible, contained** | One unconditional call at `service.py:2507`. Requires an explicit client-driven read endpoint plus a foreground/visibility signal. |
| Honest DELIVERED | **Requires new server work** | Nothing currently observes delivery. Needs either a client delivery-ack or a real transport ack. Cannot be faked without violating the mission's own rule. |
| Realtime transport | **Feasible only after de-processing the event log** | `realtime_engine`'s per-process `_event_id` across 2 gunicorn workers ⇒ silent cross-worker loss. Must move to shared storage (DB or Redis) first. |
| Stable notification identifier | **Feasible server-side; iOS caveat** | Ids already reach `push_metadata` (`service.py:1584-1585`). Expo push does not pass `apns-collapse-id` through, so a true OS-level identifier likely needs a Notification Service Extension or moving iOS onto the raw APNs sender at `pulsesoc_notification_system.py:2492-2497`. **Client-side stable identity is already free** via `notificationStableId`. |
| Immediate active-device removal on read | **Feasible, entirely net-new** | Zero existing call sites. Needs `getPresentedNotificationsAsync` + `dismissNotificationAsync`, a conversation→identifier map, and a hook in the read path (ChatScreen contains no notification code today). No architectural blocker. |
| Best-effort cross-device removal | **Blocked on transport** | Background modes declared but no silent push is ever emitted. Needs a new `apns-push-type: background` branch and/or data-only FCM. Must be described to users as best-effort — iOS background execution is not guaranteed. |
| Single notification owner service | **Feasible, moderate refactor** | 9 non-test call sites across 6 modules; `setNotificationHandler` is already singular. The three received-listeners are the consolidation work. |
| Foreground suppression for open conversation | **Feasible, small** | Single choke point at `InAppNotificationBanner.tsx:96-108`, which already parses `conversation_id`. Needs an active-conversation signal that does not exist yet. |
| Duplicate-prevention regression gate | **Must precede realtime work** | Adding an echo channel on top of the three live defects in §3 makes the existing production duplicate problem worse, not better. |

---

## 7. Where the mission spec and the codebase disagree

Recorded so the plan can be corrected before Stage 1, not discovered during it.

1. **"Make send local-first"** — already is (`messenger.ts:1440-1455`). The real work is ack reconciliation, retry identity, and the second send path in `CameraStudioScreen`.
2. **"Improve the realtime layer"** — there is none. This is a build, and it must start at the server-side event log, not at the client.
3. **"Fix Delivered"** — Delivered cannot be fixed in the client. It is not merely wrong; it is written by the wrong actor at the wrong time (`service.py:2495-2506`).
4. **"Remove the delivered notification on read"** — presupposes a notification identifier that has never existed on the wire and a dismissal API that has never been called.
5. **"Badge recalculates"** — presupposes a canonical unread count. Four sources disagree, and one of them returns boolean `True` (`pulsesoc_notification_system.py:2390`).

---

## 8. Recommended ordering (for approval — not yet authorised)

1. **Duplicate-prevention gate first.** Fix `retryMessage` identity (`ChatScreen.tsx:785-797`) and give `CameraStudioScreen.tsx:415-433` a `client_message_id`. Land the regression test before any transport work.
2. **Canonical unread + read cursor.** Remove `mark_read` from `list_messages` (`service.py:2507`); add an explicit read endpoint; pick one unread source; fix the boolean-badge bug.
3. **Honest status.** Stop rendering Delivered until it is real. Decouple delivery from the read path.
4. **De-process the event log**, then wire the existing `after_id` route.
5. **Notification identity + removal**, then foreground suppression, then best-effort cross-device.
6. **Render performance** (memoize `MessageBubble`, stabilise `keyExtractor`, lazy-mount `ContentTranslation`) — visible, cheap, and safely parallel to the above.

---

## 9. Safety constraints reaffirmed

No path in this mission touches livestream, video-call, or audio-call audio, and no protected Agora/LiveKit path is in scope. Voice-message playback is explicitly out of scope and will not be refactored merely because Messenger is improving. `docs/realtime_audio_change_policy.md` and `config/realtime-audio-protected-paths.json` remain binding; the `bot.py` content gate must be run against any backend diff:

```
python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD
```

Git discipline: explicit staging only. No `git add -A`, `git reset --hard`, `git clean -fd`, or force push. Concurrent agent work on the branch is preserved.

---

## 10. Stage 0 verdict

**COMPLETE.** Ownership is now understood well enough to implement — and the map has changed the shape of the mission in five material ways (§7). Per the spec's own instruction, *"Do not implement until ownership is understood,"* Stages 1-57 remain blocked pending acceptance of this document and confirmation of the §8 ordering.

**Unverified items carried forward:** whether account switching triggers `unregisterPushDevice`; whether `APNS_*` environment variables are configured in production (the raw APNs path is a silent no-op without them, `pulsesoc_notification_system.py:2461-2462`).
