# Message notification read reconciliation

## Verdict

**PASS (simulator), PARTIAL (physical device).**

The feature is now proven working end to end on a real iOS Notification Center:
a read message's alert disappears by itself, an unread one stays, another
account's alert for the *same message id* stays, and a non-message alert stays.
The physical-iPhone leg remains unproven for reasons that are environmental and
documented in **Device evidence and limitations** below.

The important finding of this pass is that the previously shipped
implementation **was inert in production**. It never ran. The root cause was a
one-line React defect in the app root, not a defect in the reconciliation logic.

Start: `main` at `f4a5f2f1413a4a7a60c9a6345aac57966d945a3c`.
Fix commit: `f65cecb5b4e2add67a1e3a510b443f58fdde62f9`, rebased onto `main` at
`f3c50d181a48f6a4fdedc783756c116a7ed50da6`.

## Root cause

### The original architectural cause

The active native app used `expo-notifications` for badge writes but never
enumerated or dismissed delivered notifications. A conversation read updated the
server watermark while its iOS Notification Center entries remained. Badge
clearing does not dismiss delivered requests. That gap is what the
reconciliation service closes.

### The cause that made the shipped fix do nothing

`mobile-native/App.tsx` initialized the session state like this:

```tsx
const [authState, setAuthState] = useState<AuthState>(stateFor("BOOTSTRAPPING"));
```

`stateFor` in `src/session/auth.ts` is not a pure value factory. It is a
constructor with module-level side effects:

```ts
export function stateFor(phase: SessionPhase, user: PulseUser | null = null): AuthState {
  const userId = Number(...);
  const scopeId = phase === "AUTHENTICATED" && userId > 0 ? userId : null;
  setMediaCacheScope(scopeId);
  setOutboxScope(scopeId);
  cancelMessageReconciliation();
  return { phase, status: statusForPhase(phase), user };
}
```

React evaluates a non-lazy `useState` argument on **every** render and uses the
result only on the first. So every re-render of the app root called
`stateFor("BOOTSTRAPPING")` again, discarded the returned state — and kept the
side effects. `BOOTSTRAPPING` resolves `scopeId` to `null`, so each discarded
call reset a signed-in user's outbox scope to anonymous and cancelled
reconciliation.

The reconciler's own guard then failed permanently for the rest of the process:

```ts
const scope = outboxScope();
const account = id(scope.slice(1));
if (!account || !scope.startsWith("u")) return result;
```

State was always correct, so nothing looked broken. All the damage was in the
discarded calls. This is why the feature passed every unit test and still did
nothing on a device.

The fix is to make the initializer lazy:

```tsx
const [authState, setAuthState] = useState<AuthState>(() => stateFor("BOOTSTRAPPING"));
```

### Proof the root cause is real, not assumed

Production request logs were checked for the reconciliation endpoint before the
fix. The only hits on `/api/pulse/communications/v2/notifications/reconcile` in
the retained window were a manual `curl/8.7.1` probe returning `401`.
**App-originated calls: zero.** After the fix, on the first foreground:

```
POST /api/pulse/communications/v2/notifications/reconcile
status=200 duration_ms=220 db_duration_ms=157 db_queries=58
UA: PulseSocNativeApp/1.0.1 (ios; Expo)
```

That is the first app-originated reconcile call ever observed.

### Proof the fix is complete

Every other non-lazy `useState` initializer in the app was swept: **34 call
sites**, all pure reads (`getActiveTimeZone`, `getManualTimeZone`,
`getPulseRadioState`, `Dimensions.get(...)`, `String(route.params...)`,
`emptyLiveStudioDraft()`, `getLayoutDirection()`, and similar). `stateFor` was
the only initializer mutating shared module state.

The other two `stateFor` call sites were verified correct and left alone:
`App.tsx` inside `bootstrapSession` is a deliberate scope reset before
`restoreSession()`, and `auth.ts`'s `createContext` default runs once at import.

## Payload contract

New Communications v2 pushes carry `schemaVersion: 1`,
`notificationType: "message"`, `messageNamespace: "comm_v2"`, `messageId`,
`conversationId`, `recipientUserId`, `senderId`, `sentAt`, and existing deep
links. Expo also receives a recipient-scoped conversation `threadId`.

The logical message key is deliberately distinct from the OS request identifier.
Dismissal requires the OS-assigned `request.identifier` returned by
`getPresentedNotificationsAsync()`; the payload's `messageId` is never used as a
dismissal handle.

## Behavior implemented

`mobile-native/src/core/messageNotificationReconciliation.ts` is the centralized
single-flight reconciler, keyed per account. It gets presented notifications,
strictly parses data, submits opaque identifiers to an authenticated server
decision endpoint, and dismisses only OS request identifiers confirmed as read,
deleted, blocked, or removed. It never identifies notifications by title,
sender, or preview text, and it never calls `dismissAllNotificationsAsync()`.

Unknown, malformed, unrelated, and wrong-account entries are preserved. Legacy
entries are removed only after the server proves notification ownership and the
message/conversation relationship; shared membership alone is deliberately
insufficient. Security, marketplace, payment, crypto, reaction, system,
missed-call, and Live notifications are outside the filter.

Reads are bounded by the displayed `through_message_id`; an arriving later
message remains unread. Offline reads become scoped durable `messenger.read`
operations, dismiss their exact local notifications immediately, and retry the
server acknowledgement. The reconciler runs after reads/retries, foreground and
session restoration, message sync, notification taps, and account transitions.
It recalculates the existing authoritative combined badge rather than
decrementing.

## Changed files

* `mobile-native/App.tsx` — the root-cause fix
* `mobile-native/src/session/__tests__/authStateInitializerIsLazy.test.tsx` — new
* Communications v2 service/routes/reconciliation module
* Central notification and Expo push payload builders
* Native Messenger, outbox, app lifecycle, notification routing, and session code
* Deterministic Python and Jest reconciliation tests

## Validation

| Gate | Command | Result |
|---|---|---|
| Types | `npx tsc --noEmit` (mobile-native) | **EXIT 0** |
| Native tests | `npx jest --silent` | **453 suites / 7855 tests passed**, 14.062s |
| Protection suite | `scripts/protection/run_protection_suite.py` | **682 checks across 46 suites passed** |
| Audio lock | `scripts/realtime_audio_change_gate.py --base f3c50d18 --head HEAD` | **EXIT 0** — no protected path changed |
| Python | reconciliation + receipt tests | **37 passed** |

The earlier report claimed typecheck was NO-GO on
`ProfileScreen.tsx:784: Cannot find name 'profileSurface'`. That claim is stale
and has been removed — the error is resolved and `tsc` exits 0.

## Device evidence

Simulator `E859950D-B187-4897-B389-05447C5AD796`, iPhone 17 Pro Max, iOS 26.5.
Build lineage was verified rather than assumed: the bundle container UUID moved
`B49A2DF3` → `99ADB22C` and `main.jsbundle` mtime moved from 22:08:35 (pre-fix)
to 22:40:10 (fixed).

Ground truth was read from production Postgres immediately before the run:

* `comm_v2_participants` conversation **6**, user 15 → `last_read_message_id = 1739`
  (message 1739 is READ)
* `comm_v2_participants` conversation **26**, user 15 → `last_read_message_id = 0`
  (message 1348 is UNREAD)
* both messages present in `comm_v2_messages`, `deleted_at = NULL`

The signed-in account was confirmed by reading `pulsesoc.native.session.user`
directly (`user_id: 15`) rather than inferring it from the profile keys in the
AsyncStorage manifest, which name a different id.

Notification Center was cleared to empty, the app backgrounded, and four pushes
delivered at `05:47:53Z`. The BEFORE capture shows a collapsed stack badged "4",
expanding to all four alerts. The app was foregrounded at `05:51:14Z`; the
server logged the reconcile call at `05:51:22Z`.

| | payload | server truth | result |
|---|---|---|---|
| A | msg 1739 / conv 6 / user 15 | read (`last_read=1739`) | **dismissed** |
| B | msg 1348 / conv 26 / user 15 | unread (`last_read=0`) | preserved |
| C | msg 1739 / conv 6 / **user 99** | read | preserved |
| D | security, non-message | — | preserved |

**C is the decisive case.** It carries byte-identical `messageId` and
`conversationId` to A and the same server-side read state. The only difference
is the recipient account field, and it survived. Dismissal is account-scoped,
not content-matched.

Badge after reconciliation read **856**, unchanged from the pre-run baseline of
856. This is the correct Stage 8 result: message 1739 was already read
server-side, so an authoritative recompute yields the same number. A blind
decrement would have produced 855.

Evidence artifacts: `BEFORE_reconcile.png`, `AFTER_reconcile.png`,
`AFTER_badge.png`, and the four `.apns` payloads.

Privacy (Stage 11): grepping production logs for reconcile and dismissal lines
outside the generic request-timing line returns nothing. No message ids, bodies,
sender names, tokens, or raw payloads are logged server-side.

## Limitations

**Physical iPhone verification is not complete.** Paired iPhone 16 Pro `P3r7or`
(`F45E640F-6D02-514E-877C-B764E8D6818F`) is available, but real APNs delivery to
it is blocked by a known, pre-existing environment mismatch: builds are signed
`aps-environment: development` and therefore mint sandbox device tokens, while
the production deployment does not set `APNS_USE_SANDBOX`. The resulting
`BadDeviceToken` is indistinguishable from a dead token. This is an
infrastructure condition unrelated to this change and is tracked separately. The
20-item physical matrix has not been run and **no physical-device PASS is
claimed**.

**Two message stores with independent id sequences.** Communications v2 writes
`comm_v2_messages`. Six live web routes write `pulse_messages` via
`pulse_send_conversation_message` (`bot.py:46222`). Nothing bridges them, and
`source_type` is the only separator. Notifications originating from the web
routes are therefore **preserved but never dismissed** — they fall outside the
comm_v2 namespace the reconciler is scoped to. Because bare integers from the
two spaces overlap, widening the reconciler to cover `pulse_messages` without
first namespacing the ids would risk dismissing the wrong alert. Preserving is
the correct failure mode under Stage 12 (unknown means preserve). Closing this
gap is follow-up work, not part of this change.

**iOS background execution is not guaranteed.** On a terminated secondary
device, the next foreground or cold start is the mandatory repair path.

**A parallel implementation was abandoned.** Tag
`notif-reconcile-parallel-work`, commit `44cd7655`. It was discarded because it
keyed dismissal on message ids drawn from both stores without namespacing them,
which is the id-space collision described above. The shipped approach scopes to
comm_v2 instead.

**Noted, not fixed:** the pre-existing `message_read` realtime publication in
`mark_read` fires to all other participants regardless of
`_read_receipts_allowed`.

## Rollback

Revert commit `f65cecb5`. It changes one line of `App.tsx` plus an added test
file; reverting restores the previous (inert) behavior with no data migration.
The payload fields are additive and older clients ignore them.
