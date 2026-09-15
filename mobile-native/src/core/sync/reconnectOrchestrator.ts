/**
 * What happens, and in what order, when the network comes back.
 *
 * WHY THIS IS ONE THING AND NOT SIX
 *
 * The obvious implementation is no implementation: every screen subscribes to
 * connectivity and refreshes itself when it sees `online`. That produces the
 * worst possible moment for the device. A link that has just recovered is weak,
 * a phone leaving a tunnel is already contending with the radio re-associating,
 * and at exactly that instant the app fires the message drain, three feed
 * refreshes, a notification poll and a media prefetch simultaneously. They
 * compete, several time out, the timeouts push connectivity back to `degraded`,
 * and the whole thing runs again. The user watches an app that is somehow
 * slowest right after the network returns.
 *
 * So reconnect is a sequence, not an event, and this module owns it.
 *
 * THE ORDER, AND WHY IT IS THIS ORDER
 *
 *   P0 AUTH             Nothing below is meaningful with a dead token. A refresh
 *                       here also converts "everything 401s" into one recoverable
 *                       failure instead of six confusing ones.
 *   P1 QUEUED_MUTATIONS What the user already did outranks anything we want to
 *                       show them. A message typed twenty minutes ago should
 *                       leave the device before we spend the recovered link
 *                       fetching a feed.
 *   P2 VISIBLE_SCREEN   The one surface the user is actually looking at. Ahead of
 *                       every other read, because a refresh they can see is worth
 *                       more than four they cannot.
 *   P3 DELTAS           Unread counts, message deltas, notifications — small,
 *                       cheap, and what tells the user something happened while
 *                       they were gone.
 *   P4 FEEDS            Home, Reels, Statuses. Large, and already showing cached
 *                       content, so they can wait.
 *   P5 WARMING          Speculative prefetch. Explicitly last, and skipped
 *                       entirely on a degraded link.
 *
 * Tiers run strictly one after another, and tasks within a tier run one after
 * another too. Parallelism here would recreate exactly the herd described above;
 * the cost is latency on work that is, by construction, ordered least-important
 * last.
 *
 * WHY IT IS DEBOUNCED
 *
 * A train, a lift, a building's edge — connectivity flaps. Each flap looks like
 * a reconnect. Running the sequence per flap means the P1 drain restarts before
 * it finished, which is the shape that sends a message twice. The debounce
 * window means a flapping link produces one run after it settles, and the outbox
 * is single-flight underneath as a second line of defence.
 *
 * WHY IT STOPS WHEN THE LINK DIES AGAIN
 *
 * A run checks connectivity between tasks and abandons the rest if the network
 * has gone. Continuing would burn battery generating failures, and each failure
 * is evidence that pushes connectivity further into `offline` — the app talking
 * itself into a worse state than the radio is actually in.
 *
 * WHAT THIS MODULE DELIBERATELY CANNOT DO
 *
 * A task is a refresh callback the surface supplies. There is no "reload",
 * "remount" or "reset" affordance, because the one thing a reconnect must never
 * do is throw away where the user was. Scroll position, the open composer, a
 * half-written message and the video that is mid-play all belong to the surface,
 * and the orchestrator is not given a way to touch them.
 */

import {
  canAttemptNetwork,
  connectivityState,
  markRecoveryComplete,
  shouldDeferHeavyWork,
  subscribeConnectivity
} from "../connectivity";

/**
 * Priority tiers. Lower runs first.
 *
 * Named rather than numbered at the call site so a task's place in the sequence
 * is a decision with a word attached, not a magic number someone nudges.
 */
export const SYNC_PRIORITY = Object.freeze({
  AUTH: 0,
  QUEUED_MUTATIONS: 1,
  VISIBLE_SCREEN: 2,
  DELTAS: 3,
  FEEDS: 4,
  WARMING: 5
} as const);

export type SyncPriority = (typeof SYNC_PRIORITY)[keyof typeof SYNC_PRIORITY];

export const RECONNECT_LIMITS = Object.freeze({
  /**
   * How long connectivity must hold before a run starts.
   *
   * Long enough that a lift or a tunnel mouth produces one run rather than four;
   * short enough that a genuine reconnect still feels immediate. The user's own
   * pull-to-refresh is never subject to it — that path is `runSyncNow`.
   */
  debounceMs: 1_200,
  /**
   * Ceiling on a single task. A task that hangs must not hold `recovering` open
   * for the whole app, and the connectivity authority's own ceiling would then
   * fire and mask the stall as a timeout somewhere else.
   */
  taskTimeoutMs: 20_000
});

export type SyncTask = {
  /** Stable id. Re-registering the same id replaces, so a remounting screen cannot register twice. */
  id: string;
  priority: SyncPriority;
  run: () => Promise<unknown>;
  /**
   * When true, a failure abandons the rest of the run.
   *
   * Auth uses it: with a dead token every task below would fail in a way that
   * looks like its own bug. Nothing else should — one feed failing to refresh is
   * not a reason to skip the others.
   */
  blocking?: boolean;
  /** Skipped on a degraded link. For speculative work that would compete with real requests. */
  heavy?: boolean;
};

export type SyncTaskOutcome = {
  id: string;
  priority: SyncPriority;
  status: "ok" | "failed" | "skipped";
  error?: string;
};

export type SyncRunResult = {
  reason: string;
  outcomes: SyncTaskOutcome[];
  /** True when the link died mid-run and the remaining tasks were abandoned. */
  abandoned: boolean;
};

const tasks = new Map<string, SyncTask>();
let debounceTimer: ReturnType<typeof setTimeout> | null = null;
let activeRun: Promise<SyncRunResult> | null = null;
let rerunPending = false;
let unsubscribeConnectivity: (() => void) | null = null;
let lastState: string | null = null;

/**
 * Register a refresh. Returns the unregister, which a screen must call on
 * unmount or the orchestrator will keep refreshing something nobody is looking
 * at — and, worse, keep a closure over its unmounted state alive.
 */
export function registerSyncTask(task: SyncTask): () => void {
  tasks.set(task.id, task);
  return () => {
    // Guarded so a late unmount cannot delete a task some other mount has since
    // registered under the same id.
    if (tasks.get(task.id) === task) tasks.delete(task.id);
  };
}

export function registeredSyncTaskIds(): string[] {
  return [...tasks.values()].sort(byPriorityThenRegistration).map((task) => task.id);
}

/**
 * Begin watching connectivity. Idempotent; calling twice does not double-subscribe.
 */
export function startReconnectOrchestrator(): void {
  if (unsubscribeConnectivity) return;
  unsubscribeConnectivity = subscribeConnectivity((snapshot) => {
    const previous = lastState;
    lastState = snapshot.state;
    // `recovering` is the authority's way of saying "a path exists and the app
    // is not consistent yet". That is precisely this module's cue, and it is the
    // only state that requires a run: `online` reached directly from `degraded`
    // never lost anything.
    if (snapshot.state === "recovering" && previous !== "recovering") scheduleRun("connectivity_recovered");
  });
}

export function stopReconnectOrchestrator(): void {
  unsubscribeConnectivity?.();
  unsubscribeConnectivity = null;
  lastState = null;
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = null;
}

/**
 * Ask for a run after the debounce window.
 *
 * Every trigger restarts the window, which is what makes a flapping link produce
 * one run rather than one per flap.
 */
export function scheduleRun(reason: string): void {
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    debounceTimer = null;
    void runSyncNow(reason);
  }, RECONNECT_LIMITS.debounceMs);
}

/**
 * Run the sequence immediately.
 *
 * Single-flight. A second caller joins the run already going rather than
 * starting a parallel one — two orchestrator runs would each drain the outbox
 * and each refresh every feed, which is the herd this module exists to prevent,
 * only self-inflicted.
 */
export function runSyncNow(reason = "manual"): Promise<SyncRunResult> {
  if (activeRun) {
    // Remember that something asked while we were busy. The trigger may have
    // been a new screen becoming visible, whose task would otherwise be missed
    // by the run already past its tier.
    rerunPending = true;
    return activeRun;
  }
  activeRun = executeRun(reason).finally(() => {
    activeRun = null;
    if (rerunPending) {
      rerunPending = false;
      scheduleRun("rerun_requested");
    }
  });
  return activeRun;
}

async function executeRun(reason: string): Promise<SyncRunResult> {
  const ordered = [...tasks.values()].sort(byPriorityThenRegistration);
  const outcomes: SyncTaskOutcome[] = [];
  let abandoned = false;

  for (const task of ordered) {
    if (abandoned) {
      outcomes.push({ id: task.id, priority: task.priority, status: "skipped" });
      continue;
    }
    if (!canAttemptNetwork()) {
      // The link died mid-run. Everything after this is a guaranteed failure
      // whose only effect is to push connectivity further down.
      abandoned = true;
      outcomes.push({ id: task.id, priority: task.priority, status: "skipped" });
      continue;
    }
    if (task.heavy && shouldDeferHeavyWork()) {
      outcomes.push({ id: task.id, priority: task.priority, status: "skipped" });
      continue;
    }

    try {
      await withTimeout(task.run(), RECONNECT_LIMITS.taskTimeoutMs, task.id);
      outcomes.push({ id: task.id, priority: task.priority, status: "ok" });
    } catch (error) {
      outcomes.push({
        id: task.id,
        priority: task.priority,
        status: "failed",
        error: String((error as Error)?.message || error || "sync task failed")
      });
      if (task.blocking) abandoned = true;
    }
  }

  // The exit from `recovering`, and the reason the authority holds that state
  // open rather than declaring itself online on the first successful request.
  // Reported even on an abandoned run: leaving the app pinned in `recovering`
  // because reconnect went badly would be a second, quieter failure.
  if (connectivityState() === "recovering") markRecoveryComplete();

  return { reason, outcomes, abandoned };
}

/**
 * Within a tier, registration order. `Array.sort` is stable and a Map iterates
 * in insertion order, so ties keep the order the surfaces registered in — which
 * is the only ordering anyone could reason about, and it stays put when a screen
 * re-registers, because `Map.set` on an existing key keeps its slot.
 */
function byPriorityThenRegistration(a: SyncTask, b: SyncTask): number {
  return a.priority - b.priority;
}

function withTimeout<T>(promise: Promise<T>, ms: number, id: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`sync task "${id}" timed out`)), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      }
    );
  });
}

/** Test seam. */
export const __testing = {
  reset() {
    tasks.clear();
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = null;
    activeRun = null;
    rerunPending = false;
    unsubscribeConnectivity?.();
    unsubscribeConnectivity = null;
    lastState = null;
  }
};
