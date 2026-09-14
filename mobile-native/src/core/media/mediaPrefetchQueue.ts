/**
 * The single queue every speculative media fetch goes through.
 *
 * WHY A QUEUE AT ALL
 *
 * The naive version of "preload the next few items" is a loop that calls
 * Image.prefetch on everything the list knows about. On a fast scroll through a
 * feed that is thirty parallel requests competing for one radio, and the one
 * request that matters -- the image the user is looking at right now -- is
 * queued behind twenty guesses about what they might look at next. The page
 * gets slower precisely because it tried to get faster.
 *
 * So every warm goes through here, and the queue enforces three things the
 * call sites cannot enforce individually:
 *
 *   1. a bound on how many fetches are in flight at once,
 *   2. an ordering where visible content is served before guesses,
 *   3. a way to abandon guesses that turned out to be wrong.
 *
 * (3) matters more than it looks. A user who flings past forty items leaves
 * behind forty predictions that are now worthless, and without cancellation
 * they still have to drain before anything on screen can load.
 */

/**
 * Deliberately declared here rather than imported from the RTC adaptation
 * controller, even though the two unions are spelled identically today.
 *
 * The audio import boundary forbids the media modules from reaching into the
 * real-time core, and the reason it gives is the right one: a module that
 * imports the RTC controller is a module that could start resolving capture and
 * publish settings outside the two adapters that hold the audio lease. Sharing
 * a three-member string union is not worth standing on that line.
 *
 * They are also measuring different things. The RTC tier is derived from a
 * call's uplink statistics; this one is derived from how long our own image
 * warms took. If one of them ever needs a fourth band, it will be the one whose
 * signal changed -- so the types drifting apart is the correct outcome, not a
 * bug waiting to happen.
 */
export type NetworkTier = "good" | "fair" | "weak";

/* -------------------------------------------------------------------------- */
/* PRIORITY                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * Lower is more urgent. The band names describe the user's relationship to the
 * content, not the fetch: VISIBLE is "on screen now", DISTANT is "several
 * swipes away and may never be reached".
 */
export const MEDIA_PRIORITY = Object.freeze({
  VISIBLE: 0,
  NEXT: 1,
  NEAR: 2,
  AHEAD: 3,
  DISTANT: 4
});

export type MediaPriority = 0 | 1 | 2 | 3 | 4;

export const LOWEST_PRIORITY: MediaPriority = 4;

/**
 * A running task at this priority or worse may be cancelled to make room for
 * something more urgent.
 *
 * P0 is excluded by construction: it is on screen, so cancelling it would be
 * cancelling the thing the user is currently waiting for. Everything from P1
 * down is a prediction, and a prediction is always worth less than a fact.
 */
export const PREEMPTIBLE_FROM: MediaPriority = 1;

/**
 * How many fetches may be in flight, by observed network quality.
 *
 * These are deliberately small. Above roughly four concurrent media requests on
 * a phone the per-request latency rises faster than the throughput does, so the
 * first byte of the image you are actually looking at arrives later than it
 * would have with a shorter queue.
 */
export const CONCURRENCY_BY_TIER: Readonly<Record<NetworkTier, number>> = Object.freeze({
  good: 4,
  fair: 2,
  weak: 1
});

/* -------------------------------------------------------------------------- */
/* CANCELLATION                                                                */
/* -------------------------------------------------------------------------- */

export class PrefetchCancelledError extends Error {
  constructor(public readonly key: string) {
    super(`prefetch cancelled: ${key}`);
    this.name = "PrefetchCancelledError";
  }
}

/**
 * Deliberately not AbortController.
 *
 * The primitives being cancelled -- Image.prefetch, an expo-av load -- have no
 * abort support, so a real AbortSignal would imply a guarantee that cannot be
 * kept. What cancellation actually means here is "stop waiting for this, stop
 * occupying a slot, and do not write the result into the cache", which this
 * models honestly.
 */
export type PrefetchSignal = {
  readonly cancelled: boolean;
  /** Throws PrefetchCancelledError if cancellation has already happened. */
  throwIfCancelled(): void;
  onCancel(listener: () => void): void;
};

class CancelToken implements PrefetchSignal {
  private flag = false;
  private listeners: Array<() => void> = [];

  constructor(private readonly key: string) {}

  get cancelled() {
    return this.flag;
  }

  throwIfCancelled() {
    if (this.flag) throw new PrefetchCancelledError(this.key);
  }

  onCancel(listener: () => void) {
    if (this.flag) {
      safely(listener);
      return;
    }
    this.listeners.push(listener);
  }

  cancel() {
    if (this.flag) return;
    this.flag = true;
    const pending = this.listeners;
    this.listeners = [];
    for (const listener of pending) safely(listener);
  }
}

function safely(fn: () => void) {
  try {
    fn();
  } catch {
    /* a listener must not be able to break the queue */
  }
}

/* -------------------------------------------------------------------------- */
/* TASKS                                                                       */
/* -------------------------------------------------------------------------- */

export type PrefetchRequest = {
  /** mediaCacheKey(identity, rendition). Identical keys are one task. */
  key: string;
  priority: MediaPriority;
  /**
   * The surface that asked, e.g. "reels" or "status:42". Cancelling a tag drops
   * every outstanding guess a screen made when the user leaves it.
   */
  tag: string;
  run: (signal: PrefetchSignal) => Promise<void> | void;
};

export type PrefetchCancelReason = "tag" | "key" | "preempted" | "reset" | "superseded";

export type PrefetchQueueEvent =
  | { type: "media_prefetch_started"; key: string; priority: MediaPriority; tag: string }
  | { type: "media_prefetch_completed"; key: string; priority: MediaPriority; tag: string; durationMs: number }
  | { type: "media_prefetch_failed"; key: string; priority: MediaPriority; tag: string; reason: string }
  | { type: "prefetch_cancelled"; key: string; priority: MediaPriority; tag: string; reason: PrefetchCancelReason };

type QueuedTask = PrefetchRequest & { seq: number; token: CancelToken };

export type MediaPrefetchQueueOptions = {
  networkTier?: NetworkTier;
  onEvent?: (event: PrefetchQueueEvent) => void;
  /** Injected so tests do not depend on wall-clock timing. */
  now?: () => number;
};

/* -------------------------------------------------------------------------- */
/* THE QUEUE                                                                   */
/* -------------------------------------------------------------------------- */

export class MediaPrefetchQueue {
  private pending = new Map<string, QueuedTask>();
  private running = new Map<string, QueuedTask>();
  private seqCounter = 0;
  private tier: NetworkTier;
  private readonly onEvent: (event: PrefetchQueueEvent) => void;
  private readonly now: () => number;

  constructor(options: MediaPrefetchQueueOptions = {}) {
    this.tier = options.networkTier ?? "good";
    this.onEvent = options.onEvent ?? (() => undefined);
    this.now = options.now ?? (() => Date.now());
  }

  get concurrencyLimit() {
    return CONCURRENCY_BY_TIER[this.tier];
  }

  get networkTier() {
    return this.tier;
  }

  /**
   * Shrinking the limit does not cancel work already in flight. Those requests
   * have already paid their setup cost; killing them would waste it and the
   * bytes would be re-fetched moments later. The new limit applies to what
   * starts next.
   */
  setNetworkTier(tier: NetworkTier) {
    if (this.tier === tier) return;
    this.tier = tier;
    this.pump();
  }

  get pendingCount() {
    return this.pending.size;
  }

  get runningCount() {
    return this.running.size;
  }

  has(key: string) {
    return this.pending.has(key) || this.running.has(key);
  }

  /**
   * Submit a warm request.
   *
   * Duplicate keys collapse. A key already in flight is left alone -- it is
   * going to produce the bytes either way, and restarting it to record a better
   * priority would throw away real progress for a bookkeeping change. A key
   * still waiting is promoted if the new request is more urgent, which is the
   * path that matters: the item the planner guessed at as P3 two screens ago is
   * now the one on screen, and it must jump the line rather than be fetched
   * twice.
   */
  enqueue(request: PrefetchRequest) {
    const running = this.running.get(request.key);
    if (running) {
      if (request.priority < running.priority) running.priority = request.priority;
      return;
    }

    const queued = this.pending.get(request.key);
    if (queued) {
      if (request.priority < queued.priority) {
        queued.priority = request.priority;
        // Re-stamp so a promoted task sorts ahead of items already waiting in
        // its new band rather than behind them.
        queued.seq = this.seqCounter++;
      }
      // `run` and `tag` are deliberately NOT replaced. A promotion is a
      // statement about urgency, and callers legitimately re-submit a key with
      // a placeholder body just to raise it -- overwriting the original body
      // would turn the promotion into a silent no-op that reports success.
      return;
    }

    this.pending.set(request.key, {
      ...request,
      seq: this.seqCounter++,
      token: new CancelToken(request.key)
    });
    this.pump();
  }

  cancelKey(key: string, reason: PrefetchCancelReason = "key") {
    const queued = this.pending.get(key);
    if (queued) {
      this.pending.delete(key);
      queued.token.cancel();
      this.emitCancelled(queued, reason);
    }
    const running = this.running.get(key);
    if (running) {
      // Left in `running` until its promise settles; the settle path sees a
      // cancelled token, frees the slot, and discards the result.
      running.token.cancel();
      this.emitCancelled(running, reason);
    }
  }

  /**
   * Drop everything a surface asked for. This is what a screen calls on blur:
   * the predictions it made are about a list the user is no longer looking at.
   */
  cancelTag(tag: string, reason: PrefetchCancelReason = "tag") {
    for (const task of [...this.pending.values()]) {
      if (task.tag === tag) {
        this.pending.delete(task.key);
        task.token.cancel();
        this.emitCancelled(task, reason);
      }
    }
    for (const task of [...this.running.values()]) {
      if (task.tag === tag) {
        task.token.cancel();
        this.emitCancelled(task, reason);
      }
    }
    this.pump();
  }

  /** Cancel every outstanding task whose key matches, e.g. all renditions of one asset. */
  cancelMatching(predicate: (key: string) => boolean, reason: PrefetchCancelReason = "key") {
    const keys = new Set<string>([...this.pending.keys(), ...this.running.keys()]);
    for (const key of keys) {
      if (predicate(key)) this.cancelKey(key, reason);
    }
  }

  /** Keep only these tags. Cheaper at the call site than cancelling by name. */
  cancelTagsExcept(keep: Iterable<string>, reason: PrefetchCancelReason = "tag") {
    const kept = new Set(keep);
    const tags = new Set<string>();
    for (const task of this.pending.values()) tags.add(task.tag);
    for (const task of this.running.values()) tags.add(task.tag);
    for (const tag of tags) {
      if (!kept.has(tag)) this.cancelTag(tag, reason);
    }
  }

  reset() {
    for (const task of [...this.pending.values()]) {
      this.pending.delete(task.key);
      task.token.cancel();
      this.emitCancelled(task, "reset");
    }
    for (const task of [...this.running.values()]) {
      task.token.cancel();
      this.emitCancelled(task, "reset");
    }
  }

  /** Test/telemetry view. Sorted the way the queue would actually drain. */
  snapshot() {
    return {
      running: [...this.running.values()].map((t) => ({ key: t.key, priority: t.priority, tag: t.tag })),
      pending: this.sortedPending().map((t) => ({ key: t.key, priority: t.priority, tag: t.tag })),
      limit: this.concurrencyLimit,
      tier: this.tier
    };
  }

  /* ---------------------------------------------------------------------- */

  private sortedPending() {
    return [...this.pending.values()].sort((a, b) => a.priority - b.priority || a.seq - b.seq);
  }

  /**
   * Start as much work as the limit allows, most urgent first, then -- if the
   * most urgent thing waiting is more urgent than something already running --
   * take a slot away from the least urgent running task.
   *
   * The preemption half is the reason a P0 cannot be starved. Without it, four
   * slow P4 guesses issued a moment before the user stopped scrolling would
   * hold every slot until they finished, and the visible image would wait on
   * content that is off screen and may never be seen.
   */
  private pump() {
    for (;;) {
      const next = this.sortedPending()[0];
      if (!next) return;

      if (this.running.size < this.concurrencyLimit) {
        this.start(next);
        continue;
      }

      const victim = this.lowestPriorityRunning();
      if (!victim || victim.priority < PREEMPTIBLE_FROM || victim.priority <= next.priority) return;

      victim.token.cancel();
      this.emitCancelled(victim, "preempted");
      this.running.delete(victim.key);
      // The victim was a legitimate prediction, so it goes back in line rather
      // than being lost -- it will run again when the urgent work clears.
      this.pending.set(victim.key, {
        ...victim,
        seq: this.seqCounter++,
        token: new CancelToken(victim.key)
      });
      this.start(next);
    }
  }

  private lowestPriorityRunning(): QueuedTask | null {
    let worst: QueuedTask | null = null;
    for (const task of this.running.values()) {
      if (task.token.cancelled) continue;
      if (!worst || task.priority > worst.priority || (task.priority === worst.priority && task.seq > worst.seq)) {
        worst = task;
      }
    }
    return worst;
  }

  private start(task: QueuedTask) {
    this.pending.delete(task.key);
    this.running.set(task.key, task);
    const startedAt = this.now();
    this.onEvent({ type: "media_prefetch_started", key: task.key, priority: task.priority, tag: task.tag });

    Promise.resolve()
      .then(() => task.run(task.token))
      .then(
        () => this.settle(task, startedAt, null),
        (error) => this.settle(task, startedAt, error)
      );
  }

  private settle(task: QueuedTask, startedAt: number, error: unknown) {
    // Only clear the slot if it is still ours. A cancelled task can be replaced
    // in `running` by a re-enqueued one with the same key before it settles.
    if (this.running.get(task.key) === task) this.running.delete(task.key);

    if (!task.token.cancelled) {
      if (error) {
        this.onEvent({
          type: "media_prefetch_failed",
          key: task.key,
          priority: task.priority,
          tag: task.tag,
          reason: reasonOf(error)
        });
      } else {
        this.onEvent({
          type: "media_prefetch_completed",
          key: task.key,
          priority: task.priority,
          tag: task.tag,
          durationMs: Math.max(0, this.now() - startedAt)
        });
      }
    }

    this.pump();
  }

  private emitCancelled(task: QueuedTask, reason: PrefetchCancelReason) {
    this.onEvent({ type: "prefetch_cancelled", key: task.key, priority: task.priority, tag: task.tag, reason });
  }
}

function reasonOf(error: unknown): string {
  if (error instanceof PrefetchCancelledError) return "cancelled";
  if (error instanceof Error && error.name) return error.name;
  return "error";
}
