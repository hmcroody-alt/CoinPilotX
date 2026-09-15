/**
 * The promises the outbox makes about a write the user has already made.
 *
 * Several of these are the §91 mutations, written so the naive implementations —
 * cap the queue by dropping the oldest, let two drains run, share one queue
 * across accounts — fail here rather than losing somebody's message.
 */

import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  MutationNotQueueableError,
  NEVER_QUEUEABLE,
  OUTBOX_LIMITS,
  QUEUEABLE_OPERATIONS,
  __testing,
  clearOutbox,
  discardMutation,
  drainOutbox,
  enqueueMutation,
  failedMutations,
  isQueueableOperation,
  outboxSize,
  pendingMutations,
  registerOutboxHandler,
  retryMutation,
  setOutboxScope
} from "../outbox";

/** The one type the app actually queues today; used as the vehicle for the rest. */
const TYPE = "messenger.send";

function queue(idempotencyKey: string, stream = "conversation:1", payload: unknown = { body: idempotencyKey }) {
  return enqueueMutation({ type: TYPE, idempotencyKey, stream, payload });
}

/**
 * A clock that only moves forward from the real one.
 *
 * Backoff is compared against `createdAt`, so a mock that returns a small
 * absolute value puts every operation infinitely far in the future and nothing
 * is ever attempted — which reads as a bug in the outbox and is a bug in the
 * test. Offsetting real time keeps the comparison meaningful.
 */
let clockOffsetMs = 0;
const advanceClock = (ms: number) => {
  clockOffsetMs += ms;
};

beforeEach(async () => {
  __testing.reset();
  await AsyncStorage.clear();
  clockOffsetMs = 0;
  const realNow = Date.now.bind(Date);
  jest.spyOn(Date, "now").mockImplementation(() => realNow() + clockOffsetMs);
});

afterEach(() => {
  jest.restoreAllMocks();
});

describe("what may be queued at all", () => {
  it("refuses an unregistered operation rather than inventing a policy for it", async () => {
    await expect(
      enqueueMutation({ type: "something.new", idempotencyKey: "k", stream: "s", payload: {} })
    ).rejects.toBeInstanceOf(MutationNotQueueableError);
  });

  it("refuses every operation that must stay offline-unavailable", async () => {
    // §91: "payments succeed offline". Queueing a charge means telling the user
    // it worked and firing it forty minutes later, after they have left.
    for (const type of NEVER_QUEUEABLE) {
      expect(isQueueableOperation(type)).toBe(false);
      await expect(
        enqueueMutation({ type, idempotencyKey: `k-${type}`, stream: "s", payload: {} })
      ).rejects.toBeInstanceOf(MutationNotQueueableError);
    }
  });

  it("keeps the allowlist and the forbidden list disjoint", () => {
    // The assertion that makes adding "payment.charge" to QUEUEABLE_OPERATIONS a
    // failing test rather than a shipped behaviour change.
    for (const type of NEVER_QUEUEABLE) {
      expect(Object.keys(QUEUEABLE_OPERATIONS)).not.toContain(type);
    }
  });

  it("requires an idempotency key, since without one a retry is a duplicate", async () => {
    await expect(
      enqueueMutation({ type: TYPE, idempotencyKey: "", stream: "s", payload: {} })
    ).rejects.toThrow(/idempotencyKey/);
  });
});

describe("exactly once", () => {
  it("collapses a repeated enqueue of the same key into one operation", async () => {
    const first = await queue("abc");
    const second = await queue("abc");
    expect(second.id).toBe(first.id);
    expect(await outboxSize()).toBe(1);
  });

  it("does not send the same operation twice when two drains race", async () => {
    // §91: "queued message sends twice". Reconnect and a screen mount both call
    // drain; the window between the handler resolving and the queue being
    // rewritten is exactly long enough for the second walker to resend.
    const sent: string[] = [];
    registerOutboxHandler(TYPE, async (op) => {
      sent.push(op.idempotencyKey);
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
    await queue("once");

    await Promise.all([drainOutbox(), drainOutbox(), drainOutbox()]);
    expect(sent).toEqual(["once"]);
    expect(await outboxSize()).toBe(0);
  });

  it("removes an operation from storage only after its handler resolved", async () => {
    registerOutboxHandler(TYPE, async () => {
      throw new Error("network down");
    });
    await queue("survivor");
    await drainOutbox();
    expect((await pendingMutations()).map((op) => op.idempotencyKey)).toEqual(["survivor"]);
  });
});

describe("nothing the user wrote is deleted to make room", () => {
  it("refuses the new write when full instead of dropping the oldest", async () => {
    // §91: "eviction deletes an unsent message". A `slice(-N)` cap reads as
    // housekeeping and behaves as silent deletion of the message the user has
    // been waiting on longest.
    const limit = OUTBOX_LIMITS.maxOperations;
    for (let index = 0; index < limit; index += 1) await queue(`k${index}`, "conversation:1");

    await expect(queue("overflow")).rejects.toThrow(/full/i);
    const pending = await pendingMutations();
    expect(pending).toHaveLength(limit);
    expect(pending[0].idempotencyKey).toBe("k0"); // the oldest is still there
  });

  it("keeps an operation that exhausted its retries instead of discarding it", async () => {
    registerOutboxHandler(TYPE, async () => {
      throw new Error("rejected");
    });
    await queue("doomed");

    const { maxAttempts } = QUEUEABLE_OPERATIONS[TYPE];
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      await drainOutbox();
      advanceClock(OUTBOX_LIMITS.maxRetryDelayMs * 2);
    }

    expect(await pendingMutations()).toHaveLength(0);
    const failed = await failedMutations();
    expect(failed).toHaveLength(1);
    expect(failed[0].idempotencyKey).toBe("doomed");
    expect(failed[0].lastError).toContain("rejected");
  });

  it("gives up immediately on a permanent rejection rather than retrying it ten times", async () => {
    registerOutboxHandler(TYPE, async () => {
      throw Object.assign(new Error("blocked by moderation"), { permanent: true });
    });
    await queue("blocked");
    const result = await drainOutbox();
    expect(result.failed).toHaveLength(1);
    expect(result.retrying).toHaveLength(0);
    expect(await failedMutations()).toHaveLength(1);
  });

  it("lets a failed operation be retried or discarded, and only by the caller", async () => {
    registerOutboxHandler(TYPE, async () => {
      throw Object.assign(new Error("nope"), { permanent: true });
    });
    const op = await queue("second-chance");
    await drainOutbox();

    expect(await retryMutation(op.id)).toBe(true);
    expect(await failedMutations()).toHaveLength(0);
    expect((await pendingMutations())[0].attempts).toBe(0);

    expect(await discardMutation(op.id)).toBe(true);
    expect(await outboxSize()).toBe(0);
  });
});

describe("order", () => {
  it("delivers a conversation's messages in the order they were written", async () => {
    const sent: string[] = [];
    registerOutboxHandler(TYPE, async (op) => {
      sent.push(op.idempotencyKey);
    });
    await queue("m1");
    await queue("m2");
    await queue("m3");

    await drainOutbox();
    expect(sent).toEqual(["m1", "m2", "m3"]);
  });

  it("holds the rest of a conversation behind a message that will not send", async () => {
    // Delivering m3 while m2 is stuck puts a hole in the recipient's thread and
    // reorders the user's words. One visible failure with the rest stacked
    // behind it is the comprehensible failure.
    const sent: string[] = [];
    registerOutboxHandler(TYPE, async (op) => {
      if (op.idempotencyKey === "m2") throw new Error("still down");
      sent.push(op.idempotencyKey);
    });
    await queue("m1");
    await queue("m2");
    await queue("m3");

    const result = await drainOutbox();
    expect(sent).toEqual(["m1"]);
    expect(result.retrying.map((op) => op.idempotencyKey)).toEqual(["m2"]);
    expect(result.deferred.map((op) => op.idempotencyKey)).toEqual(["m3"]);
  });

  it("does not let one unreachable conversation stall every other one", async () => {
    const sent: string[] = [];
    registerOutboxHandler(TYPE, async (op) => {
      if (op.stream === "conversation:1") throw new Error("down");
      sent.push(op.idempotencyKey);
    });
    await queue("a1", "conversation:1");
    await queue("b1", "conversation:2");
    await queue("b2", "conversation:2");

    await drainOutbox();
    expect(sent).toEqual(["b1", "b2"]);
  });

  it("waits for a dependency before attempting the operation that needs it", async () => {
    const sent: string[] = [];
    registerOutboxHandler(TYPE, async (op) => {
      if (op.idempotencyKey === "upload") throw new Error("upload failed");
      sent.push(op.idempotencyKey);
    });
    const upload = await queue("upload", "upload:9");
    await enqueueMutation({
      type: TYPE,
      idempotencyKey: "message-with-attachment",
      stream: "conversation:4",
      payload: {},
      dependsOn: [upload.id]
    });

    await drainOutbox();
    // The message is in a different stream, so only the declared dependency
    // holds it back — which is the whole reason dependsOn exists.
    expect(sent).toEqual([]);
  });

  it("drains only the requested stream when asked for one", async () => {
    const sent: string[] = [];
    registerOutboxHandler(TYPE, async (op) => {
      sent.push(op.idempotencyKey);
    });
    await queue("a1", "conversation:1");
    await queue("b1", "conversation:2");

    await drainOutbox({ stream: "conversation:2" });
    expect(sent).toEqual(["b1"]);
    expect(await outboxSize()).toBe(1);
  });
});

describe("retry backoff", () => {
  it("does not re-attempt an operation before its backoff has elapsed", async () => {
    let attempts = 0;
    registerOutboxHandler(TYPE, async () => {
      attempts += 1;
      throw new Error("down");
    });
    await queue("patient");

    await drainOutbox();
    await drainOutbox(); // immediately again: still inside the backoff window
    expect(attempts).toBe(1);

    advanceClock(OUTBOX_LIMITS.maxRetryDelayMs * 2);
    await drainOutbox();
    expect(attempts).toBe(2);
  });

  it("caps the backoff so a long offline stretch does not outlast reconnect", async () => {
    registerOutboxHandler(TYPE, async () => {
      throw new Error("down");
    });
    await queue("capped");

    for (let attempt = 0; attempt < 6; attempt += 1) {
      await drainOutbox();
      advanceClock(OUTBOX_LIMITS.maxRetryDelayMs * 2);
    }

    const [op] = await pendingMutations();
    // Naive doubling would put attempt 6 at 64s and keep climbing past any
    // plausible session; the cap is what makes a reconnect actually drain.
    expect(op.attempts).toBe(6);
    expect(op.nextAttemptAt - op.lastAttemptAt!).toBeLessThanOrEqual(OUTBOX_LIMITS.maxRetryDelayMs);
  });
});

describe("one account's unsent writes are not another's", () => {
  it("does not show user A's queued message to user B", async () => {
    // §91 in its sharpest form: draining A's message from B's session would not
    // leak old data, it would send A's private words from B's account.
    setOutboxScope(1234);
    await queue("private-to-A");
    expect(await outboxSize()).toBe(1);

    setOutboxScope(5678);
    expect(await outboxSize()).toBe(0);
    expect(await pendingMutations()).toEqual([]);

    setOutboxScope(1234);
    expect((await pendingMutations())[0].idempotencyKey).toBe("private-to-A");
  });

  it("does not drain another account's queue", async () => {
    const sent: string[] = [];
    registerOutboxHandler(TYPE, async (op) => {
      sent.push(op.idempotencyKey);
    });
    setOutboxScope(1234);
    await queue("A-message");

    setOutboxScope(5678);
    await drainOutbox();
    expect(sent).toEqual([]);

    setOutboxScope(1234);
    expect(await outboxSize()).toBe(1);
  });

  it("cannot be made to collide with another scope by a crafted user id", async () => {
    setOutboxScope("1234.evil/../5678");
    const crafted = __testing.storageKey();
    setOutboxScope(1234);
    expect(crafted).not.toBe(__testing.storageKey());
    expect(crafted).not.toMatch(/[./]5678/);
  });

  it("clears only the scope it is asked to clear", async () => {
    setOutboxScope(1234);
    await queue("A-message");
    setOutboxScope(5678);
    await queue("B-message");

    await clearOutbox();
    expect(await outboxSize()).toBe(0);
    setOutboxScope(1234);
    expect(await outboxSize()).toBe(1);
  });
});

describe("durability", () => {
  it("survives a process restart, since the queue is the storage and not the memory", async () => {
    await queue("persisted");
    __testing.reset(); // as if the app relaunched: handlers and memory gone
    expect((await pendingMutations())[0].idempotencyKey).toBe("persisted");
  });

  it("treats a corrupt record as empty rather than throwing on every read", async () => {
    await queue("fine");
    await AsyncStorage.setItem(__testing.storageKey(), "{not json");
    expect(await pendingMutations()).toEqual([]);
    await expect(queue("after-corruption")).resolves.toBeTruthy();
  });

  it("does not attempt an operation whose type no longer has a handler", async () => {
    await queue("orphan");
    __testing.reset(); // no handler registered
    const result = await drainOutbox();
    expect(result.delivered).toEqual([]);
    expect(await outboxSize()).toBe(1);
  });
});
