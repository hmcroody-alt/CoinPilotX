/**
 * The one durable place a write waits when the network is not there.
 *
 * WHY THERE IS EXACTLY ONE
 *
 * A queue per feature is the shape that produces the failures this module
 * exists to prevent. Two queues means two drains, and two drains racing on
 * reconnect means the same write leaves the device twice. It also means two
 * retry policies, two capacity limits, and two answers to "have my messages
 * actually sent?" — which the user asks as ONE question. So surfaces do not own
 * queues; they register a handler and hand operations to this one.
 *
 * WHAT MAKES AN OPERATION SAFE TO REPLAY
 *
 * An operation that leaves this device may or may not reach the server, and a
 * response that does not come back is indistinguishable from one that was lost
 * on the way home. Retrying is therefore mandatory, and retrying is only safe if
 * the server can recognise the repeat. Every operation carries an
 * `idempotencyKey` for exactly that — the key is the promise that a second
 * delivery is a no-op, and an operation type that cannot make that promise has
 * no business being queued.
 *
 * WHAT IS NOT QUEUEABLE, AND WHY THE LIST IS SHORT BY CONSTRUCTION
 *
 * Queueing is default-deny: a type must appear in QUEUEABLE_OPERATIONS or
 * `enqueueMutation` refuses it. This is not bureaucracy. Payments, going live
 * and placing a call cannot be queued, because queueing them would mean telling
 * the user something succeeded when the only honest answer is "not right now."
 * A charge that fires forty minutes later, when the user has left the shop and
 * forgotten the purchase, is worse than a charge that never happened. The right
 * offline behaviour for those is an unavailable button and a reason, which is a
 * decision for the surface — this module's job is to make the wrong behaviour
 * impossible to reach by accident. NEVER_QUEUEABLE names the cases explicitly so
 * that adding one to the allowlist trips a test rather than shipping.
 *
 * WHY NOTHING IS EVER DROPPED TO MAKE ROOM
 *
 * An unsent message is user data the user believes they have written. The queue
 * is capacity-bounded — an unbounded one would eventually fail to persist and
 * lose everything at once — but when it is full, the NEW operation is refused
 * and the caller is told. It is never the old ones that go. A cap implemented as
 * "keep the most recent N" reads as housekeeping and behaves as silent deletion
 * of the oldest thing the user typed, which is the one they have been waiting on
 * longest.
 *
 * Exhausting the retry budget does not delete either. The operation stops being
 * attempted and stays durable, visible through `failedMutations()`, so a surface
 * can offer a retry instead of the write evaporating.
 *
 * WHY ORDER IS PER-STREAM AND NOT GLOBAL
 *
 * Messages in one conversation must arrive in the order they were typed. A
 * message in a different conversation has no such relationship, and making the
 * whole queue a single line means one unreachable recipient stalls every other
 * conversation behind it. So operations declare a `stream`, order is FIFO within
 * a stream, and a stuck head blocks only its own. `dependsOn` exists for the
 * rarer cross-stream case — an attachment upload that a message needs — where
 * the relationship is real and specific.
 *
 * WHY THE SCOPE IS PART OF THE KEY
 *
 * A queued write belongs to the account that wrote it. Draining user A's unsent
 * message from user B's session would send A's private words from B's account —
 * not a leak of old data but an active, attributable message the user never
 * wrote. Storage is namespaced per account for the same reason the media cache
 * is, and a drain only ever sees the active scope.
 */

import AsyncStorage from "@react-native-async-storage/async-storage";

const ANON_SCOPE = "anon";
const STORAGE_PREFIX = "pulsesoc.native.outbox.v1";
const ENVELOPE_VERSION = 1 as const;

/**
 * Operations that may wait for the network.
 *
 * `ordered` means the stream is a strict line: a failure at the head holds the
 * rest back, because delivering message three before message two is worse than
 * delivering both late. `maxAttempts` bounds the retrying — past it the
 * operation is surfaced as failed rather than retried forever against an error
 * that is clearly not transient.
 */
export const QUEUEABLE_OPERATIONS = Object.freeze({
  "messenger.send": { maxAttempts: 10, ordered: true },
  /**
   * "I read this conversation" — queued when the user opens a thread offline.
   *
   * It qualifies on the test that matters for this list: replaying it late does
   * exactly what doing it on time would have done. A read is idempotent (the
   * server stamps first-read and leaves it alone), it is not a charge or a
   * broadcast, and arriving forty minutes later is not a lie — the user really
   * did read it, forty minutes ago.
   *
   * It is deliberately NOT ordered. Reads do not form a sequence the way
   * messages do; two conversations read offline have no relationship, and
   * making the stream strict would let one unreachable conversation hold back
   * every other read behind it. Each conversation is its own stream instead.
   *
   * It exists because the notification side already acted: opening the thread
   * dismissed its alerts locally and immediately, which is right and works
   * offline. Without the queue the server would never learn, so the badge — which
   * is recomputed from the server — would keep counting messages the user has
   * read and can no longer see an alert for.
   */
  "messenger.markRead": { maxAttempts: 8, ordered: false }
} as const satisfies Record<string, { maxAttempts: number; ordered: boolean }>);

export type QueueableOperationType = keyof typeof QUEUEABLE_OPERATIONS;

/**
 * Named so that making one of them queueable is a deliberate, reviewed act.
 *
 * Default-deny already blocks these; this list is what turns "somebody adds
 * payment.charge to the allowlist" from a silent behaviour change into a failing
 * test with a reason attached.
 */
export const NEVER_QUEUEABLE: readonly string[] = Object.freeze([
  "payment.charge",
  "payment.payout",
  "payment.refund",
  "wallet.transfer",
  "live.start",
  "live.end",
  "call.start",
  "call.join"
]);

/** Retry backoff. Bounded so a long offline stretch does not push the next try past reconnect. */
export const OUTBOX_LIMITS = Object.freeze({
  /** Refusal point. Generous enough for a genuinely long offline stretch. */
  maxOperations: 500,
  baseRetryDelayMs: 2_000,
  maxRetryDelayMs: 5 * 60_000
});

export type OutboxOperation = {
  /** Unique per queued write. Distinct from the idempotency key: two operations may legitimately share neither. */
  id: string;
  type: string;
  /** What the server dedupes on. For messenger this is the `client_message_id`. */
  idempotencyKey: string;
  /** FIFO ordering scope, e.g. `conversation:42`. */
  stream: string;
  payload: unknown;
  createdAt: number;
  /** Operation ids that must land before this one is attempted. */
  dependsOn: string[];
  attempts: number;
  lastAttemptAt: number | null;
  /** Earliest time a retry is allowed. Honours backoff. */
  nextAttemptAt: number;
  lastError: string | null;
  /** True once the retry budget is spent. Still durable; never attempted automatically again. */
  failed: boolean;
};

export type EnqueueInput = {
  type: string;
  idempotencyKey: string;
  stream: string;
  payload: unknown;
  dependsOn?: string[];
};

/**
 * Thrown when a caller tries to queue something that must not be queued.
 *
 * A distinct type because the surface's response is different in kind: not
 * "we'll send it later" but "this needs a connection", which is a different
 * sentence on screen.
 */
export class MutationNotQueueableError extends Error {
  readonly type: string;
  constructor(type: string) {
    super(`Operation "${type}" cannot be queued offline`);
    this.name = "MutationNotQueueableError";
    this.type = type;
  }
}

/** Thrown when the queue is full. The NEW write is refused; nothing existing is lost. */
export class OutboxFullError extends Error {
  constructor(size: number) {
    super(`Outbox is full (${size} pending operations)`);
    this.name = "OutboxFullError";
  }
}

/**
 * Delivers one operation. Resolving means the server has it.
 *
 * Throwing is a retry unless the error carries `permanent: true`, which means
 * the server rejected the content itself and replaying it would only produce the
 * same rejection more slowly.
 */
export type OutboxHandler = (operation: OutboxOperation) => Promise<void>;

const handlers = new Map<string, OutboxHandler>();
let scope = ANON_SCOPE;
let sequence = 0;
let inFlight: Promise<OutboxDrainResult> | null = null;

export function registerOutboxHandler(type: string, handler: OutboxHandler) {
  handlers.set(type, handler);
}

/**
 * Point the outbox at an account. Pass `null` for signed out.
 *
 * Non-alphanumerics are stripped for the same reason the media cache strips
 * them: the scope becomes part of a storage key and must not be forgeable from
 * a user-controlled value.
 */
export function setOutboxScope(userId: number | string | null) {
  scope = userId ? `u${String(userId).replace(/[^A-Za-z0-9]/g, "")}` || ANON_SCOPE : ANON_SCOPE;
}

export function outboxScope(): string {
  return scope;
}

export function isQueueableOperation(type: string): boolean {
  return Object.prototype.hasOwnProperty.call(QUEUEABLE_OPERATIONS, type);
}

/**
 * Queue a write for later delivery.
 *
 * Returns the existing operation when the idempotency key is already queued: a
 * caller retrying an enqueue must not produce two operations that the server
 * would then have to dedupe, because the server only sees the second one after
 * the first has already been delivered.
 */
export async function enqueueMutation(input: EnqueueInput): Promise<OutboxOperation> {
  if (!isQueueableOperation(input.type)) throw new MutationNotQueueableError(input.type);
  if (!input.idempotencyKey) throw new Error("enqueueMutation requires an idempotencyKey");

  const queue = await readQueue();
  const existing = queue.find((op) => op.idempotencyKey === input.idempotencyKey);
  if (existing) return existing;
  if (queue.length >= OUTBOX_LIMITS.maxOperations) throw new OutboxFullError(queue.length);

  sequence += 1;
  const now = Date.now();
  const operation: OutboxOperation = {
    id: `op-${now.toString(36)}-${sequence.toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    type: input.type,
    idempotencyKey: input.idempotencyKey,
    stream: input.stream || input.type,
    payload: input.payload,
    createdAt: now,
    dependsOn: input.dependsOn ? [...input.dependsOn] : [],
    attempts: 0,
    lastAttemptAt: null,
    nextAttemptAt: now,
    lastError: null,
    failed: false
  };
  await writeQueue([...queue, operation]);
  return operation;
}

export type OutboxDrainResult = {
  delivered: OutboxOperation[];
  /** Attempted and thrown; still queued, with backoff applied. */
  retrying: OutboxOperation[];
  /** Retry budget spent or permanently rejected. Still durable. */
  failed: OutboxOperation[];
  /** Not attempted this pass: backoff, an unmet dependency, or a blocked ordered stream. */
  deferred: OutboxOperation[];
};

const EMPTY_RESULT: OutboxDrainResult = { delivered: [], retrying: [], failed: [], deferred: [] };

/**
 * Deliver what is due.
 *
 * Single-flight by construction. Two callers — a reconnect and a screen mount,
 * which is the normal shape of coming back online — must not both walk the queue,
 * because the window between "handler resolves" and "queue is rewritten" is
 * exactly long enough for the second walker to send the same operation again.
 * Concurrent calls join the run already in progress instead.
 */
export function drainOutbox(options: { stream?: string } = {}): Promise<OutboxDrainResult> {
  if (inFlight) return inFlight;
  inFlight = runDrain(options).finally(() => {
    inFlight = null;
  });
  return inFlight;
}

async function runDrain(options: { stream?: string }): Promise<OutboxDrainResult> {
  const queue = await readQueue();
  if (!queue.length) return EMPTY_RESULT;

  const result: OutboxDrainResult = { delivered: [], retrying: [], failed: [], deferred: [] };
  const remaining: OutboxOperation[] = [];
  // Streams whose head could not be delivered. An ordered stream stops here; an
  // unordered one keeps going, since its operations have no relationship.
  const blockedStreams = new Set<string>();
  const deliveredIds = new Set<string>();

  for (const operation of queue) {
    const now = Date.now();
    const config = QUEUEABLE_OPERATIONS[operation.type as QueueableOperationType];
    const handler = handlers.get(operation.type);

    const skip =
      operation.failed ||
      !config ||
      !handler ||
      (options.stream && operation.stream !== options.stream) ||
      operation.nextAttemptAt > now ||
      operation.dependsOn.some((id) => !deliveredIds.has(id) && queue.some((other) => other.id === id)) ||
      (config.ordered && blockedStreams.has(operation.stream));

    if (skip) {
      remaining.push(operation);
      // Anything not delivered holds its ordered stream — including an operation
      // that has given up. Letting the next one past would put the user's words
      // on the recipient's screen in an order they never wrote them in, with a
      // hole where the failure was. Blocking instead means the user sees one
      // failure with the rest stacked behind it, and retrying or discarding it
      // releases them. `deferred` reports the stall so a surface can say so.
      if (config?.ordered) blockedStreams.add(operation.stream);
      // Pre-existing failures are not re-reported here; `failedMutations()` is
      // the query for those. `result.failed` means "gave up during this pass".
      if (!operation.failed) result.deferred.push(operation);
      continue;
    }

    try {
      await handler(operation);
      deliveredIds.add(operation.id);
      result.delivered.push(operation);
    } catch (error) {
      const attempts = operation.attempts + 1;
      const permanent = Boolean((error as { permanent?: boolean })?.permanent);
      const exhausted = permanent || attempts >= config.maxAttempts;
      const next: OutboxOperation = {
        ...operation,
        attempts,
        lastAttemptAt: now,
        nextAttemptAt: now + backoffFor(attempts),
        lastError: String((error as Error)?.message || error || "send failed"),
        failed: exhausted
      };
      remaining.push(next);
      (exhausted ? result.failed : result.retrying).push(next);
      if (config.ordered) blockedStreams.add(operation.stream);
    }
  }

  await writeQueue(remaining);
  return result;
}

function backoffFor(attempts: number): number {
  const delay = OUTBOX_LIMITS.baseRetryDelayMs * 2 ** Math.max(0, attempts - 1);
  return Math.min(delay, OUTBOX_LIMITS.maxRetryDelayMs);
}

/** Everything still waiting, in queue order. */
export async function pendingMutations(stream?: string): Promise<OutboxOperation[]> {
  const queue = await readQueue();
  return queue.filter((op) => !op.failed && (!stream || op.stream === stream));
}

/** Operations that gave up. Durable, and a surface's cue to offer a manual retry. */
export async function failedMutations(stream?: string): Promise<OutboxOperation[]> {
  const queue = await readQueue();
  return queue.filter((op) => op.failed && (!stream || op.stream === stream));
}

/** Put a failed operation back in the line. The only way `failed` is ever cleared. */
export async function retryMutation(id: string): Promise<boolean> {
  const queue = await readQueue();
  const index = queue.findIndex((op) => op.id === id);
  if (index < 0) return false;
  queue[index] = { ...queue[index], failed: false, attempts: 0, nextAttemptAt: Date.now(), lastError: null };
  await writeQueue(queue);
  return true;
}

/**
 * Drop an operation the user explicitly abandoned.
 *
 * The only deletion path, and it is caller-initiated. Nothing in this module
 * removes a pending operation to make room or to tidy up.
 */
export async function discardMutation(id: string): Promise<boolean> {
  const queue = await readQueue();
  const next = queue.filter((op) => op.id !== id);
  if (next.length === queue.length) return false;
  await writeQueue(next);
  return true;
}

export async function outboxSize(): Promise<number> {
  return (await readQueue()).length;
}

/**
 * Erase one account's queue. Signing out, not housekeeping.
 *
 * Deliberately NOT called on logout by default: unsent messages are user data,
 * and a user who signs out and back in expects to find them. The caller decides.
 */
export async function clearOutbox(forScope: string = scope): Promise<void> {
  await AsyncStorage.removeItem(storageKey(forScope)).catch(() => undefined);
}

function storageKey(forScope: string = scope): string {
  return `${STORAGE_PREFIX}.${forScope}`;
}

async function readQueue(): Promise<OutboxOperation[]> {
  try {
    const raw = await AsyncStorage.getItem(storageKey());
    if (!raw) return [];
    const parsed = JSON.parse(raw) as { v?: number; operations?: OutboxOperation[] };
    const operations = parsed?.v === ENVELOPE_VERSION && Array.isArray(parsed.operations) ? parsed.operations : [];
    return operations.filter(isUsableOperation);
  } catch {
    // A corrupt queue is not recoverable by guessing, but it must not make every
    // subsequent write throw. Returning empty leaves the bad record in place for
    // the next write to replace rather than deleting evidence eagerly.
    return [];
  }
}

function isUsableOperation(op: unknown): op is OutboxOperation {
  const candidate = op as OutboxOperation;
  return Boolean(candidate && typeof candidate.id === "string" && typeof candidate.idempotencyKey === "string" && candidate.idempotencyKey);
}

async function writeQueue(operations: OutboxOperation[]): Promise<void> {
  await AsyncStorage.setItem(storageKey(), JSON.stringify({ v: ENVELOPE_VERSION, operations }));
}

/** Test seam. Not exported from the app's public surface. */
export const __testing = {
  storageKey,
  reset() {
    handlers.clear();
    scope = ANON_SCOPE;
    sequence = 0;
    inFlight = null;
  }
};
