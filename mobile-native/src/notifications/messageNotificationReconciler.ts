import * as Notifications from "expo-notifications";
import { fetchMessageReadState } from "../api/messageNotifications";
import { refreshUnreadCounts } from "../core/unreadCounts";
import {
  DeliveredClassification,
  MessageNotificationRef,
  classifyNotificationData,
  ownershipFor
} from "./messageNotificationContract";

/**
 * The one place in this app that removes a delivered notification.
 *
 * WHY THERE IS EXACTLY ONE
 *
 * Before this module, nothing in the app had ever called a delivered-
 * notification API — not `getPresentedNotificationsAsync`, not
 * `dismissNotificationAsync`, not anything. `expo-notifications` was wired for
 * delivery, tap-routing and badges only, so a message alert survived until the
 * user swiped it away by hand. The fix is a reconciliation layer, and a
 * reconciliation layer only works if it is the sole writer: two call sites each
 * holding half the rules is how "dismiss the read ones" becomes "dismiss the
 * ones the other one hadn't got to yet".
 *
 * WHAT IT WILL NOT DO
 *
 * `dismissAllNotificationsAsync()` is never called. It is the obvious
 * implementation and it is wrong: it would take out missed calls, security
 * alerts, order updates and Live notifications along with the read messages,
 * and it would do so invisibly, because clearing everything looks exactly like
 * clearing the right things when Notification Center ends up empty either way.
 * Every dismissal below names a single OS request identifier.
 *
 * THE DECISION TABLE
 *
 * For each delivered notification:
 *
 *   not a message payload            -> preserve (Stage 12: unknown is preserve)
 *   message, recipient != me         -> preserve, do not even ask the server
 *   message, recipient == me, read   -> dismiss
 *   message, recipient == me, gone   -> dismiss (deleted/left; ownership proven)
 *   message, recipient == me, unread -> preserve
 *   message, recipient unknown, read -> dismiss (server answered for ME, so it
 *                                       is mine by construction)
 *   message, recipient unknown, gone -> preserve (`obsolete` cannot tell
 *                                       "deleted" from "belongs to the other
 *                                       account on this handset")
 *   anything else                    -> preserve
 *
 * BADGE
 *
 * Recomputed from the server, never decremented by the number of things
 * dismissed. The count of alerts sitting in Notification Center is not the
 * unread count and never was: a muted thread has unreads and no alert, and a
 * notification the user already swiped had an alert and is still unread.
 */

export type ReconcileTrigger =
  | "conversation_opened"
  | "conversation_synced"
  | "marked_read"
  | "notification_tapped"
  | "message_removed"
  | "conversation_removed"
  | "sender_blocked"
  | "foreground"
  | "cold_start"
  | "session_restored"
  | "account_changed"
  | "reconnected"
  | "remote_read"
  | "manual";

export type ReconcileResult = {
  trigger: ReconcileTrigger;
  /** Delivered notifications examined. */
  inspected: number;
  /** Delivered notifications classified as this account's messages. */
  candidates: number;
  /** OS request identifiers actually dismissed. */
  dismissed: number;
  /** Dismissals attempted that the OS refused. */
  failed: number;
  /** Everything not dismissed, by reason. Counts only — never ids. */
  preserved: Record<string, number>;
  /** True when a lookup failed and the pass deliberately did less. */
  degraded: boolean;
  /** True when the pass was skipped entirely (no session, already running). */
  skipped: boolean;
  skipReason?: string;
};

type ReconcileOptions = {
  trigger: ReconcileTrigger;
  /**
   * Restrict the pass to one conversation. Notifications for every other
   * conversation are left alone, which is what makes "opening one thread must
   * not clear another thread's alerts" structural rather than incidental.
   */
  conversationId?: number;
  /**
   * Message ids the caller already knows are read, from its own local state.
   * Trusted without a server round-trip for the same reason: the caller is the
   * screen that just displayed them.
   */
  knownReadMessageIds?: number[];
  /**
   * Skip the server lookup entirely. Set when offline. The pass still runs and
   * still dismisses anything locally proven read — dismissal is an OS-local
   * operation and does not need the network.
   */
  localOnly?: boolean;
};

/** The signed-in account, published by the auth layer. 0 means signed out. */
let signedInUserId = 0;

/**
 * Set from `stateFor` in `session/auth.ts`, which is the single constructor for
 * every AuthState and therefore the only place that observes every identity
 * transition — sign-in, restore, expiry, sign-out, account switch. The media
 * cache and the mutation outbox are scoped from that same line for the same
 * reason. Scoping from screens instead would mean one missed screen lets one
 * account dismiss another's notifications.
 *
 * DELIBERATELY A PURE SETTER — IT DOES NOT RECONCILE
 *
 * The obvious move is to kick a pass from here: this is the one place that sees
 * an account switch, and `AppNavigator` does not remount across one. It was
 * written that way first and it was wrong. `stateFor` is a constructor, called
 * on every auth-state construction rather than only on transitions, and giving
 * it a network side effect means every screen that reads `useAuth` drags a
 * reconciliation pass behind it. The jest suite said so immediately: 449 green
 * suites in 11s became 451 suites in 30s with one unrelated screen test tipping
 * over a `waitFor` budget. That is the visible half. The invisible half is a
 * production app doing OS and network work inside an identity constructor.
 *
 * The identity trigger lives in `AppNavigator` instead, as an effect keyed on
 * the signed-in user id — the lifecycle layer, where I/O belongs, and which
 * re-runs on a switch precisely because the id it is keyed on changed.
 */
export function setNotificationScope(userId: number | null) {
  const next = Number(userId || 0);
  signedInUserId = Number.isSafeInteger(next) && next > 0 ? next : 0;
}

export function getNotificationScope(): number {
  return signedInUserId;
}

/**
 * Single-flight. Lifecycle triggers arrive in clusters — a cold start fires
 * `cold_start`, then `session_restored`, then `foreground`, then `reconnected`,
 * inside a second or two — and three concurrent passes would each read the same
 * delivered list and race to dismiss the same identifiers, producing spurious
 * failures and three badge writes. A pass that arrives while one is running
 * joins it rather than queueing behind it: the running pass reads the delivered
 * list AFTER the caller's read transition has already been committed locally,
 * so its answer is at least as fresh as a fresh pass would be.
 */
let inFlight: { userId: number; promise: Promise<ReconcileResult> } | null = null;

export function reconcileMessageNotifications(options: ReconcileOptions): Promise<ReconcileResult> {
  const scope = signedInUserId;
  /**
   * Coalesce only within one account.
   *
   * The running pass is pinned to the account that started it and aborts if the
   * identity changes under it. So a caller arriving after a switch must NOT join
   * it — it would be handed the aborted result and the incoming account would
   * never get a pass of its own. This is exactly the path `setNotificationScope`
   * takes, which is why the check is here rather than left to the caller.
   */
  if (inFlight && inFlight.userId === scope) {
    return inFlight.promise.then((result) => ({
      ...result,
      trigger: options.trigger,
      skipped: true,
      skipReason: "coalesced"
    }));
  }
  const entry: { userId: number; promise: Promise<ReconcileResult> } = {
    userId: scope,
    promise: null as unknown as Promise<ReconcileResult>
  };
  entry.promise = runReconcile(options).finally(() => {
    // Only clear our own slot. A pass started after a switch has already
    // replaced `inFlight`, and a blind `inFlight = null` here would drop it.
    if (inFlight === entry) inFlight = null;
  });
  inFlight = entry;
  return entry.promise;
}

function emptyResult(trigger: ReconcileTrigger, skipReason: string): ReconcileResult {
  return {
    trigger,
    inspected: 0,
    candidates: 0,
    dismissed: 0,
    failed: 0,
    preserved: {},
    degraded: false,
    skipped: true,
    skipReason
  };
}

async function runReconcile(options: ReconcileOptions): Promise<ReconcileResult> {
  const trigger = options.trigger;
  // Signed out means no read state to reconcile against, and dismissing on the
  // strength of a payload alone would let a stale alert be cleared by whoever
  // picks the phone up next. Logging out while a pass is running lands here on
  // the next pass, not mid-flight; the in-flight pass is bounded and its
  // dismissals were already authorised by the account that started it.
  if (!signedInUserId) return emptyResult(trigger, "signed_out");

  /**
   * Pinned for the life of the pass.
   *
   * Every await below is a place the account can change underneath us — a sign
   * out, or a switch, lands on the module singleton while this pass is between
   * the delivered-list read and the dismissal loop. Reading the mutable
   * `signedInUserId` at each step would let one pass filter candidates as
   * account A, ask the server as account B (the token is whatever is current),
   * and then dismiss A's alerts on B's answers. Pinning it makes the pass a
   * statement about one account, and the re-check before dismissing turns a
   * mid-flight change into an abort rather than a mix.
   */
  const passUserId = signedInUserId;

  let delivered: Notifications.Notification[] = [];
  try {
    delivered = await Notifications.getPresentedNotificationsAsync();
  } catch {
    return emptyResult(trigger, "delivered_unreadable");
  }
  if (!Array.isArray(delivered) || !delivered.length) {
    // Nothing delivered is still a legitimate moment to true up the badge: the
    // user may have swiped the alerts away by hand while unread messages
    // remain, and the badge must reflect the messages, not the alerts.
    const badge = await applyAuthoritativeBadge();
    return { ...emptyResult(trigger, "nothing_delivered"), degraded: !badge };
  }

  const preserved: Record<string, number> = {};
  const preserve = (reason: string) => {
    preserved[reason] = (preserved[reason] || 0) + 1;
  };

  type Candidate = { identifier: string; ref: MessageNotificationRef };
  const candidates: Candidate[] = [];

  for (const item of delivered) {
    const identifier = String(item?.request?.identifier || "");
    if (!identifier) {
      preserve("no_os_identifier");
      continue;
    }
    const classification: DeliveredClassification = classifyNotificationData(item?.request?.content?.data);
    if (classification.kind !== "message") {
      preserve(classification.reason);
      continue;
    }
    const ref = classification.ref;
    if (options.conversationId && ref.conversationId !== options.conversationId) {
      preserve("other_conversation");
      continue;
    }
    const owner = ownershipFor(ref, passUserId);
    if (owner === "theirs") {
      preserve("other_account");
      continue;
    }
    candidates.push({ identifier, ref });
  }

  if (!candidates.length) {
    const badge = await applyAuthoritativeBadge();
    return {
      trigger,
      inspected: delivered.length,
      candidates: 0,
      dismissed: 0,
      failed: 0,
      preserved,
      degraded: !badge,
      skipped: false
    };
  }

  const locallyRead = new Set((options.knownReadMessageIds || []).filter((id) => Number.isSafeInteger(id) && id > 0));

  /**
   * "The user opened this thread, so its alerts can go" — used ONLY offline.
   *
   * Online, the extra round-trip buys exactness and is worth it. Opening a
   * conversation proves the messages rendered up to that moment; it says
   * nothing about a message whose push lands a heartbeat later, while the
   * thread is still open. Asking the server closes that window, because the
   * server's answer is per-message and reflects what the mark-read actually
   * covered.
   *
   * Offline there is no such answer to be had, and the choice is between
   * acting on the thread-open proof or leaving the user to clear alerts for
   * messages they are looking at. We act, and accept the narrow race: the
   * loser is a message that arrived in the seconds after the user opened the
   * thread they are still reading.
   */
  const conversationProvesRead = Boolean(options.conversationId) && Boolean(options.localOnly);

  const needsServer = candidates.filter(
    (candidate) => !conversationProvesRead && !locallyRead.has(candidate.ref.messageId)
  );

  let readIds = new Set<number>();
  let obsoleteIds = new Set<number>();
  let degraded = false;

  if (needsServer.length && !options.localOnly) {
    const answer = await fetchMessageReadState(needsServer.map((candidate) => candidate.ref.messageId));
    readIds = new Set(answer.read);
    obsoleteIds = new Set(answer.obsolete);
    degraded = Boolean(answer.degraded);
  } else if (needsServer.length) {
    degraded = true;
  }

  const toDismiss: Candidate[] = [];
  for (const candidate of candidates) {
    const messageId = candidate.ref.messageId;
    if (conversationProvesRead || locallyRead.has(messageId)) {
      toDismiss.push(candidate);
      continue;
    }
    if (readIds.has(messageId)) {
      toDismiss.push(candidate);
      continue;
    }
    if (obsoleteIds.has(messageId)) {
      // `obsolete` means deleted, hidden, or not-a-participant, and the server
      // cannot tell those apart in one answer. Acting on it is safe only when
      // the payload itself named this account as the recipient; otherwise the
      // notification may belong to the other account on this handset, and
      // "you're not in that conversation" is exactly what the server would say
      // about it.
      if (ownershipFor(candidate.ref, passUserId) === "mine") {
        toDismiss.push(candidate);
      } else {
        preserve("obsolete_unproven_owner");
      }
      continue;
    }
    preserve(degraded ? "read_state_unavailable" : "still_unread");
  }

  // The account changed while we were asking. Everything computed above is a
  // statement about `passUserId`, and acting on it now would remove the
  // incoming account's alerts using the outgoing account's read state. Abort
  // with nothing dismissed; `setNotificationScope` has already queued a fresh
  // pass for whoever is signed in now.
  if (signedInUserId !== passUserId) {
    return { ...emptyResult(trigger, "account_changed_mid_pass"), inspected: delivered.length, preserved };
  }

  let dismissed = 0;
  let failed = 0;
  for (const candidate of toDismiss) {
    try {
      // One identifier at a time, by the handle the OS assigned. Never the
      // logical `notificationKey`, which iOS has never heard of.
      await Notifications.dismissNotificationAsync(candidate.identifier);
      dismissed += 1;
    } catch {
      // A dismissal the OS refuses is not a correctness problem: the alert
      // stays, which is the same state we started from, and the next pass will
      // try again. It is counted so a systemic refusal is visible.
      failed += 1;
    }
  }

  const badgeApplied = await applyAuthoritativeBadge();

  const result: ReconcileResult = {
    trigger,
    inspected: delivered.length,
    candidates: candidates.length,
    dismissed,
    failed,
    preserved,
    degraded: degraded || !badgeApplied,
    skipped: false
  };
  logReconcilePass(result);
  return result;
}

/**
 * Set the OS badge from the server's unread counts.
 *
 * Never `badge - dismissed`, never `0` because the screen looked empty. Returns
 * false when it declined to write, so the caller can mark the pass degraded.
 *
 * The `loadedAt` guard is the important line. `refreshUnreadCounts` swallows
 * its failures and hands back the last snapshot, and on a cold start that
 * snapshot is the module's zero-initialised one. Writing it would clear the
 * badge of every unread message the user has, on the strength of a request that
 * failed — a blind zero wearing an authoritative answer's clothes.
 *
 * ON `totalCount` RATHER THAN `combined`
 *
 * `core/unreadCounts.ts` documents `combined` (= totalCount + commerceCount) as
 * "what the phone's app icon wants", but the icon has always been set from
 * `totalCount`, so business↔customer thread unreads have never appeared on it.
 * That is a pre-existing gap in the Commerce Inbox's badging, not in message
 * reconciliation, and widening the number here would change what the icon counts
 * in the same change that alters when it is written — two effects, one commit,
 * and the first would read as a regression in the second. Kept as-is and raised
 * separately. What this function fixes is the WRITE, not the SCOPE.
 */
async function applyAuthoritativeBadge(): Promise<boolean> {
  const snapshot = await refreshUnreadCounts();
  if (!snapshot || snapshot.loadedAt <= 0) return false;
  /**
   * Read the raw value. NOT `Number(snapshot.totalCount || 0)`.
   *
   * `NaN` is falsy, so `NaN || 0` is `0` — the coercion converts the one value
   * that means "this number is broken" into the one value that means "you have
   * nothing to read", and does it before `Number.isFinite` ever gets to look.
   * A count that arrived corrupted would have cleared the badge while passing
   * through a guard written specifically to stop blind zeroes.
   */
  const total = Number(snapshot.totalCount);
  if (!Number.isFinite(total) || total < 0) return false;
  try {
    await Notifications.setBadgeCountAsync(Math.max(0, Math.trunc(total)));
    return true;
  } catch {
    return false;
  }
}

/**
 * Re-apply the authoritative badge without touching notifications.
 *
 * Exported so the navigator's existing badge refresh can share this guard
 * rather than keeping its own unguarded `setBadgeCountAsync`.
 */
export async function applyBadgeFromUnreadCounts(): Promise<boolean> {
  return applyAuthoritativeBadge();
}

/**
 * Counts and a trigger name. No ids, no conversation ids, no sender, no title,
 * no body, no payload. A log line that named the conversations someone had just
 * read would be a message-activity log, which is precisely the thing a
 * messaging app must not keep lying around in a device console.
 */
function logReconcilePass(result: ReconcileResult) {
  if (typeof __DEV__ === "undefined" || !__DEV__) return;
  const preservedTotal = Object.values(result.preserved).reduce((sum, count) => sum + count, 0);
  console.log(
    `[notifications] reconcile trigger=${result.trigger} inspected=${result.inspected} ` +
      `candidates=${result.candidates} dismissed=${result.dismissed} failed=${result.failed} ` +
      `preserved=${preservedTotal} degraded=${result.degraded}`
  );
}

/** Test-only reset of the module singletons. */
export function __resetNotificationReconciler() {
  signedInUserId = 0;
  inFlight = null;
}
