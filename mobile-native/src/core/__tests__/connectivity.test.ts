/**
 * The connectivity authority's contract.
 *
 * Most of these are mutation tests in the sense that matters: each one fails if
 * someone "simplifies" the module into the naive version that treats every error
 * as offline and every success as online.
 */

import {
  CONNECTIVITY_LIMITS,
  canAttemptNetwork,
  connectivityState,
  evidenceFromErrorCode,
  markRecoveryComplete,
  reportReachability,
  resetConnectivityForTests,
  shouldDeferHeavyWork,
  subscribeConnectivity
} from "../connectivity";

beforeEach(() => {
  resetConnectivityForTests();
  // Nothing in these tests should reach the network. A probe that escaped would
  // make the suite depend on a backend, so fail loudly rather than hang.
  (global as unknown as { fetch: unknown }).fetch = jest.fn(() => {
    throw new Error("no network in tests");
  });
});

afterEach(() => {
  resetConnectivityForTests();
});

describe("evidence classification", () => {
  it("treats only the two transport labels as transport failures", () => {
    expect(evidenceFromErrorCode("request_unreachable", 503)).toBe("unreachable");
    expect(evidenceFromErrorCode("request_timeout", 504)).toBe("timeout");
  });

  it.each([
    ["unauthenticated", "unauthorized", 401],
    ["forbidden", "csrf", 403],
    ["missing", undefined, 404],
    ["server error", undefined, 500],
    ["service unavailable without the transport label", undefined, 503]
  ])("counts a %s response as a completed round trip", (_label, code, status) => {
    expect(evidenceFromErrorCode(code as string | undefined, status)).toBe("round_trip");
  });

  it("treats a statusless failure as unreachable", () => {
    expect(evidenceFromErrorCode(undefined, 0)).toBe("unreachable");
  });
});

describe("a single failure is not an outage", () => {
  it("does not go offline on one transport failure", () => {
    reportReachability("unreachable");
    expect(connectivityState()).toBe("degraded");
    expect(canAttemptNetwork()).toBe(true);
  });

  it("goes offline once failures corroborate each other", () => {
    for (let i = 0; i < CONNECTIVITY_LIMITS.failuresBeforeOffline; i += 1) {
      reportReachability("unreachable");
    }
    expect(connectivityState()).toBe("offline");
    expect(canAttemptNetwork()).toBe(false);
  });

  it("resets the failure run when anything completes a round trip", () => {
    reportReachability("unreachable");
    reportReachability("round_trip", 40);
    reportReachability("unreachable");
    // Without the reset this second failure would be the second in a row and
    // would declare an outage on a working connection.
    expect(connectivityState()).toBe("degraded");
  });
});

describe("an HTTP error never means offline", () => {
  it("stays online across a long run of server errors", () => {
    for (let i = 0; i < 10; i += 1) {
      reportReachability(evidenceFromErrorCode(undefined, 500), 30);
    }
    expect(connectivityState()).toBe("online");
  });

  it("stays online across repeated auth failures", () => {
    for (let i = 0; i < 10; i += 1) {
      reportReachability(evidenceFromErrorCode("unauthorized", 401), 30);
    }
    expect(connectivityState()).toBe("online");
  });
});

describe("recovery is a state of its own", () => {
  it("returns to recovering rather than straight to online", () => {
    reportReachability("unreachable");
    reportReachability("unreachable");
    expect(connectivityState()).toBe("offline");

    reportReachability("round_trip", 50);
    expect(connectivityState()).toBe("recovering");
  });

  it("holds recovering while more requests succeed", () => {
    reportReachability("unreachable");
    reportReachability("unreachable");
    reportReachability("round_trip", 50);
    reportReachability("round_trip", 50);
    reportReachability("round_trip", 50);
    // Successful traffic during a drain is expected and is not evidence the
    // drain finished. Only the orchestrator knows that.
    expect(connectivityState()).toBe("recovering");
  });

  it("exits recovering when the orchestrator reports completion", () => {
    reportReachability("unreachable");
    reportReachability("unreachable");
    reportReachability("round_trip", 50);
    markRecoveryComplete();
    expect(connectivityState()).toBe("online");
  });

  it("defers heavy work for the whole recovery window", () => {
    reportReachability("unreachable");
    reportReachability("unreachable");
    reportReachability("round_trip", 50);
    expect(shouldDeferHeavyWork()).toBe(true);
    markRecoveryComplete();
    expect(shouldDeferHeavyWork()).toBe(false);
  });
});

describe("flapping", () => {
  it("does not re-enter offline immediately after leaving it", () => {
    reportReachability("unreachable");
    reportReachability("unreachable");
    reportReachability("round_trip", 50);
    markRecoveryComplete();
    expect(connectivityState()).toBe("online");

    // A marginal connection: two more failures arrive straight away. Without the
    // damper the app would repaint every screen between offline and online
    // several times a second.
    reportReachability("unreachable");
    reportReachability("unreachable");
    expect(connectivityState()).toBe("degraded");
  });

  it("still lets proof of reachability through instantly", () => {
    reportReachability("unreachable");
    reportReachability("unreachable");
    expect(connectivityState()).toBe("offline");
    reportReachability("round_trip", 20);
    expect(connectivityState()).toBe("recovering");
  });
});

describe("latency", () => {
  it("reports a healthy path as online", () => {
    reportReachability("round_trip", 120);
    expect(connectivityState()).toBe("online");
  });

  it("reports a working but very slow path as degraded", () => {
    reportReachability("round_trip", CONNECTIVITY_LIMITS.degradedLatencyMs + 1);
    expect(connectivityState()).toBe("degraded");
  });
});

describe("subscription", () => {
  it("delivers the current snapshot on subscribe", () => {
    const seen: string[] = [];
    const stop = subscribeConnectivity((snapshot) => seen.push(snapshot.state));
    expect(seen).toEqual(["online"]);
    reportReachability("unreachable");
    expect(seen).toEqual(["online", "degraded"]);
    stop();
    reportReachability("unreachable");
    expect(seen).toEqual(["online", "degraded"]);
  });

  it("exposes the last round trip as a fact, not a state name", () => {
    const seen: number[] = [];
    subscribeConnectivity((snapshot) => seen.push(snapshot.lastRoundTripAt));
    expect(seen[0]).toBe(0);
    reportReachability("round_trip", 10);
    // The state name did not move -- it was already online -- but "never
    // confirmed" becoming "confirmed" is real and subscribers must hear it.
    expect(seen[seen.length - 1]).toBeGreaterThan(0);
  });

  it("does not re-render subscribers on every healthy request", () => {
    const listener = jest.fn();
    subscribeConnectivity(listener);
    listener.mockClear();

    reportReachability("round_trip", 10);
    expect(listener).toHaveBeenCalledTimes(1);

    // A feed screen makes many requests in a burst. None of them change
    // anything a reader could see, so none of them should repaint the app.
    for (let i = 0; i < 25; i += 1) reportReachability("round_trip", 10);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("still reports state changes immediately, never coalesced", () => {
    const listener = jest.fn();
    subscribeConnectivity(listener);
    reportReachability("round_trip", 10);
    listener.mockClear();

    reportReachability("unreachable");
    expect(listener).toHaveBeenCalledTimes(1);
  });
});

describe("the passive half arms no timers", () => {
  /**
   * Reporting evidence must stay pure. A module singleton that arms a repeating
   * background timer the first time any request anywhere fails is a timer nobody
   * asked for -- it outlives the screen that caused it, and it made an unrelated
   * suite (`pulseApiTimeout.test.ts`, which asserts a successful request leaves
   * no pending work) fail. Active probing belongs to `startConnectivityMonitor`.
   */
  it("schedules nothing when driven to offline without a monitor", () => {
    jest.useFakeTimers();
    try {
      expect(jest.getTimerCount()).toBe(0);
      reportReachability("unreachable");
      reportReachability("unreachable");
      expect(connectivityState()).toBe("offline");
      expect(jest.getTimerCount()).toBe(0);
    } finally {
      jest.useRealTimers();
    }
  });

  it("schedules nothing when entering recovering without a monitor", () => {
    jest.useFakeTimers();
    try {
      reportReachability("unreachable");
      reportReachability("unreachable");
      reportReachability("round_trip", 20);
      expect(connectivityState()).toBe("recovering");
      expect(jest.getTimerCount()).toBe(0);
    } finally {
      jest.useRealTimers();
    }
  });
});

describe("cold start", () => {
  it("assumes reachable so a healthy launch shows no offline banner", () => {
    // Guessing offline would put an untrue banner on every cold start and clear
    // it a moment later. Guessing online costs at most one failed request.
    expect(connectivityState()).toBe("online");
    expect(canAttemptNetwork()).toBe(true);
  });
});
