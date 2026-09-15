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
 * WHY THE OUTBOX DRAIN IS THE ONLY TASK REGISTERED HERE
 *
 * It is the one piece of reconnect work that belongs to no screen: the user's
 * unsent writes must go out whether or not the surface that created them is
 * still mounted. Everything else — the visible screen, the feeds, the deltas —
 * is registered by the surface that owns it, because only the surface knows how
 * to refresh itself without throwing away where the user was.
 */

import { drainOutbox } from "../mutations/outbox";
import { startConnectivityMonitor, stopConnectivityMonitor } from "../connectivity";
import {
  SYNC_PRIORITY,
  registerSyncTask,
  startReconnectOrchestrator,
  stopReconnectOrchestrator
} from "./reconnectOrchestrator";

export const OUTBOX_SYNC_TASK_ID = "core.outbox.drain";

let unregisterOutboxTask: (() => void) | null = null;

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
  startReconnectOrchestrator();
  startConnectivityMonitor();
}

export function stopOfflinePlatform(): void {
  unregisterOutboxTask?.();
  unregisterOutboxTask = null;
  stopReconnectOrchestrator();
  stopConnectivityMonitor();
}
