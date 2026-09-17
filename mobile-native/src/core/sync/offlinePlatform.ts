/**
 * The single call that turns the offline platform on.
 *
 * The pieces — connectivity, the mutation outbox, the reconnect orchestrator —
 * are each usable alone and each independently tested. What they are not is
 * self-starting: an authority nobody starts is an authority that reports a
 * plausible default forever, which is worse than none because it looks like it
 * is working. Wiring them together here rather than in `App.tsx` keeps that
 * assembly in one testable place, and keeps the app entry point from growing a
 * fourth effect that somebody later reorders.
 *
 * WHY THESE TWO TASKS ARE REGISTERED HERE AND NOTHING ELSE IS
 *
 * They are the reconnect work that belongs to no screen. The user's unsent
 * writes must go out whether or not the surface that created them is still
 * mounted, and Notification Center is not a screen at all — it outlives every
 * mount and, on a cold start, exists before the first one. Everything else —
 * the visible screen, the feeds, the deltas — is registered by the surface that
 * owns it, because only the surface knows how to refresh itself without
 * throwing away where the user was.
 */

import { drainOutbox } from "../mutations/outbox";
import { startConnectivityMonitor, stopConnectivityMonitor } from "../connectivity";
import { reconcileMessageNotifications } from "../../notifications/messageNotificationReconciler";
import {
  SYNC_PRIORITY,
  registerSyncTask,
  startReconnectOrchestrator,
  stopReconnectOrchestrator
} from "./reconnectOrchestrator";

export const OUTBOX_SYNC_TASK_ID = "core.outbox.drain";
export const NOTIFICATION_RECONCILE_TASK_ID = "core.notifications.reconcile";

let unregisterOutboxTask: (() => void) | null = null;
let unregisterNotificationTask: (() => void) | null = null;

export function startOfflinePlatform(): void {
  if (unregisterOutboxTask) return;
  unregisterOutboxTask = registerSyncTask({
    id: OUTBOX_SYNC_TASK_ID,
    priority: SYNC_PRIORITY.QUEUED_MUTATIONS,
    // Wrapped rather than passed directly: `drainOutbox` takes an options
    // object, and handing it the task runner's arguments would silently filter
    // the drain to a stream that does not exist.
    run: () => drainOutbox()
  });
  /**
   * Ordered AFTER the outbox drain, by priority, and that ordering is the whole
   * point of putting it here rather than in a screen effect.
   *
   * Reads taken offline are sitting in the queue. Reconciling before they drain
   * would ask the server about messages it still believes are unread, get
   * "unread" back, and preserve alerts for messages the user finished reading
   * on the train. DELTAS runs after QUEUED_MUTATIONS, so by the time this asks,
   * the server has been told.
   */
  unregisterNotificationTask = registerSyncTask({
    id: NOTIFICATION_RECONCILE_TASK_ID,
    priority: SYNC_PRIORITY.DELTAS,
    run: () => reconcileMessageNotifications({ trigger: "reconnected" }).then(() => undefined)
  });
  startReconnectOrchestrator();
  startConnectivityMonitor();
}

export function stopOfflinePlatform(): void {
  unregisterOutboxTask?.();
  unregisterOutboxTask = null;
  unregisterNotificationTask?.();
  unregisterNotificationTask = null;
  stopReconnectOrchestrator();
  stopConnectivityMonitor();
}
