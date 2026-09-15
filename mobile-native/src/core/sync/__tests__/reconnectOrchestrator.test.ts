/**
 * What the app does with a network that has just come back.
 *
 * The headline mutation from §91 is "reconnect launches every subsystem
 * simultaneously" — the implementation everyone writes first, where each screen
 * refreshes itself on the connectivity event. The concurrency assertion below is
 * what makes that fail here instead of on a phone leaving a tunnel.
 */

import {
  RECONNECT_LIMITS,
  SYNC_PRIORITY,
  __testing,
  registerSyncTask,
  registeredSyncTaskIds,
  runSyncNow,
  scheduleRun,
  startReconnectOrchestrator,
  stopReconnectOrchestrator
} from "../reconnectOrchestrator";
import {
  connectivityState,
  reportReachability,
  resetConnectivityForTests
} from "../../connectivity";
import { readFileSync } from "fs";
import { join } from "path";

/** Records the order tasks ran in, and the highest concurrency ever observed. */
function tracker() {
  const order: string[] = [];
  let active = 0;
  let peak = 0;
  const task = (id: string, priority: number, body?: () => Promise<unknown>) => ({
    id,
    priority: priority as never,
    run: async () => {
      active += 1;
      peak = Math.max(peak, active);
      order.push(id);
      try {
        return body ? await body() : await Promise.resolve();
      } finally {
        active -= 1;
      }
    }
  });
  return {
    task,
    order,
    get peak() {
      return peak;
    }
  };
}

beforeEach(() => {
  __testing.reset();
  resetConnectivityForTests({ state: "online" });
});

afterEach(() => {
  __testing.reset();
  stopReconnectOrchestrator();
});

describe("the order of a reconnect", () => {
  it("runs the tiers in priority order regardless of registration order", async () => {
    const t = tracker();
    registerSyncTask(t.task("feeds", SYNC_PRIORITY.FEEDS));
    registerSyncTask(t.task("warming", SYNC_PRIORITY.WARMING));
    registerSyncTask(t.task("auth", SYNC_PRIORITY.AUTH));
    registerSyncTask(t.task("deltas", SYNC_PRIORITY.DELTAS));
    registerSyncTask(t.task("outbox", SYNC_PRIORITY.QUEUED_MUTATIONS));
    registerSyncTask(t.task("screen", SYNC_PRIORITY.VISIBLE_SCREEN));

    await runSyncNow("test");
    expect(t.order).toEqual(["auth", "outbox", "screen", "deltas", "feeds", "warming"]);
  });

  it("sends what the user already wrote before fetching anything to show them", async () => {
    // The tier boundary that matters most: a message typed twenty minutes ago
    // outranks a feed refresh on a link that has only just come back.
    const t = tracker();
    registerSyncTask(t.task("home-refresh", SYNC_PRIORITY.FEEDS));
    registerSyncTask(t.task("outbox-drain", SYNC_PRIORITY.QUEUED_MUTATIONS));

    await runSyncNow("test");
    expect(t.order.indexOf("outbox-drain")).toBeLessThan(t.order.indexOf("home-refresh"));
  });

  it("never runs two tasks at the same time — the §91 mutation", async () => {
    // A weak, just-recovered link is the worst moment to issue six parallel
    // requests: they compete, several time out, the timeouts push connectivity
    // back down, and the sequence runs again.
    const t = tracker();
    const slow = () => new Promise((resolve) => setTimeout(resolve, 5));
    for (let index = 0; index < 6; index += 1) {
      registerSyncTask(t.task(`task-${index}`, index as never, slow));
    }

    await runSyncNow("test");
    expect(t.peak).toBe(1);
    expect(t.order).toHaveLength(6);
  });

  it("keeps registration order within a tier", async () => {
    const t = tracker();
    registerSyncTask(t.task("home", SYNC_PRIORITY.FEEDS));
    registerSyncTask(t.task("reels", SYNC_PRIORITY.FEEDS));
    registerSyncTask(t.task("statuses", SYNC_PRIORITY.FEEDS));

    await runSyncNow("test");
    expect(t.order).toEqual(["home", "reels", "statuses"]);
  });
});

describe("one run, not one per flap", () => {
  it("collapses a flapping link into a single run", async () => {
    jest.useFakeTimers();
    try {
      const t = tracker();
      registerSyncTask(t.task("drain", SYNC_PRIORITY.QUEUED_MUTATIONS));

      scheduleRun("flap-1");
      jest.advanceTimersByTime(RECONNECT_LIMITS.debounceMs - 100);
      scheduleRun("flap-2");
      jest.advanceTimersByTime(RECONNECT_LIMITS.debounceMs - 100);
      scheduleRun("flap-3");
      expect(t.order).toEqual([]); // nothing has run while it was still flapping

      jest.advanceTimersByTime(RECONNECT_LIMITS.debounceMs + 1);
      await Promise.resolve();
      await Promise.resolve();
      expect(t.order).toEqual(["drain"]);
    } finally {
      jest.useRealTimers();
    }
  });

  it("does not start a second run alongside one already going", async () => {
    // Two runs would each drain the outbox and each refresh every feed: the
    // herd this module exists to prevent, only self-inflicted.
    const t = tracker();
    registerSyncTask(t.task("drain", SYNC_PRIORITY.QUEUED_MUTATIONS, () => new Promise((resolve) => setTimeout(resolve, 10))));

    await Promise.all([runSyncNow("a"), runSyncNow("b"), runSyncNow("c")]);
    expect(t.order).toEqual(["drain"]);
  });

  it("only reacts to entering recovering, not to every connectivity emission", async () => {
    const t = tracker();
    registerSyncTask(t.task("drain", SYNC_PRIORITY.QUEUED_MUTATIONS));
    startReconnectOrchestrator();

    // A degraded blip and a return to online lost nothing, so there is nothing
    // to reconcile and no reason to spend the link on a full sequence.
    resetConnectivityForTests({ state: "online" });
    reportReachability("round_trip", 9_000); // -> degraded
    reportReachability("round_trip", 10); // -> online
    expect(connectivityState()).toBe("online");
    expect(t.order).toEqual([]);
  });
});

describe("when the link dies again mid-run", () => {
  it("abandons the rest instead of generating failures that push it further down", async () => {
    const t = tracker();
    registerSyncTask(
      t.task("outbox", SYNC_PRIORITY.QUEUED_MUTATIONS, async () => {
        resetConnectivityForTests({ state: "offline" });
      })
    );
    registerSyncTask(t.task("feeds", SYNC_PRIORITY.FEEDS));
    registerSyncTask(t.task("warming", SYNC_PRIORITY.WARMING));

    const result = await runSyncNow("test");
    expect(t.order).toEqual(["outbox"]);
    expect(result.abandoned).toBe(true);
    expect(result.outcomes.filter((o) => o.status === "skipped").map((o) => o.id)).toEqual(["feeds", "warming"]);
  });

  it("stands heavy work down on a degraded link but still does the rest", async () => {
    const t = tracker();
    registerSyncTask(t.task("outbox", SYNC_PRIORITY.QUEUED_MUTATIONS));
    registerSyncTask({ ...t.task("prefetch", SYNC_PRIORITY.WARMING), heavy: true });
    resetConnectivityForTests({ state: "degraded" });

    await runSyncNow("test");
    // A degraded path should still carry a message the user typed; it should not
    // carry a speculative prefetch that competes with it.
    expect(t.order).toEqual(["outbox"]);
  });
});

describe("a failing task", () => {
  it("does not stop the tiers below it", async () => {
    const t = tracker();
    registerSyncTask(
      t.task("home", SYNC_PRIORITY.FEEDS, async () => {
        throw new Error("home refresh failed");
      })
    );
    registerSyncTask(t.task("reels", SYNC_PRIORITY.FEEDS));

    const result = await runSyncNow("test");
    expect(t.order).toEqual(["home", "reels"]);
    expect(result.outcomes.find((o) => o.id === "home")?.status).toBe("failed");
    expect(result.outcomes.find((o) => o.id === "reels")?.status).toBe("ok");
  });

  it("abandons the run when auth fails, since everything below would 401", async () => {
    const t = tracker();
    registerSyncTask({
      ...t.task("auth", SYNC_PRIORITY.AUTH, async () => {
        throw new Error("refresh rejected");
      }),
      blocking: true
    });
    registerSyncTask(t.task("outbox", SYNC_PRIORITY.QUEUED_MUTATIONS));
    registerSyncTask(t.task("feeds", SYNC_PRIORITY.FEEDS));

    const result = await runSyncNow("test");
    expect(t.order).toEqual(["auth"]);
    expect(result.abandoned).toBe(true);
  });

  it("does not let a hung task hold the whole app in recovering", async () => {
    jest.useFakeTimers();
    try {
      const order: string[] = [];
      registerSyncTask({
        id: "hung",
        priority: SYNC_PRIORITY.DELTAS,
        run: () => new Promise(() => undefined)
      });
      registerSyncTask({
        id: "after",
        priority: SYNC_PRIORITY.FEEDS,
        run: async () => {
          order.push("after");
        }
      });

      const run = runSyncNow("test");
      await Promise.resolve();
      jest.advanceTimersByTime(RECONNECT_LIMITS.taskTimeoutMs + 1);
      const result = await run;

      expect(result.outcomes.find((o) => o.id === "hung")?.status).toBe("failed");
      expect(order).toEqual(["after"]);
    } finally {
      jest.useRealTimers();
    }
  });
});

describe("the exit from recovering", () => {
  it("is this orchestrator, not the first successful request", async () => {
    // The connectivity authority deliberately parks in `recovering` while the
    // drain runs. If it declared itself online on the first round trip, a screen
    // would claim a freshness it did not have and a user sending during the
    // window could watch their message queue behind an older one.
    resetConnectivityForTests({ state: "offline" });
    reportReachability("round_trip", 10);
    expect(connectivityState()).toBe("recovering");

    registerSyncTask({ id: "drain", priority: SYNC_PRIORITY.QUEUED_MUTATIONS, run: async () => undefined });
    await runSyncNow("test");
    expect(connectivityState()).toBe("online");
  });

  it("still exits recovering when the run went badly", async () => {
    // Leaving the app pinned in `recovering` because reconnect failed would be a
    // second, quieter failure on top of the first.
    resetConnectivityForTests({ state: "offline" });
    reportReachability("round_trip", 10);
    registerSyncTask({
      id: "auth",
      priority: SYNC_PRIORITY.AUTH,
      blocking: true,
      run: async () => {
        throw new Error("nope");
      }
    });

    const result = await runSyncNow("test");
    expect(result.abandoned).toBe(true);
    expect(connectivityState()).not.toBe("recovering");
  });
});

describe("registration", () => {
  it("replaces rather than duplicates when a screen re-registers the same id", async () => {
    const t = tracker();
    registerSyncTask(t.task("screen", SYNC_PRIORITY.VISIBLE_SCREEN));
    registerSyncTask(t.task("screen", SYNC_PRIORITY.VISIBLE_SCREEN));

    await runSyncNow("test");
    expect(t.order).toEqual(["screen"]);
  });

  it("stops refreshing a screen nobody is looking at once it unregisters", async () => {
    const t = tracker();
    const unregister = registerSyncTask(t.task("screen", SYNC_PRIORITY.VISIBLE_SCREEN));
    unregister();

    await runSyncNow("test");
    expect(t.order).toEqual([]);
    expect(registeredSyncTaskIds()).toEqual([]);
  });

  it("does not let a late unmount delete the task a newer mount registered", async () => {
    const t = tracker();
    const stale = registerSyncTask(t.task("screen", SYNC_PRIORITY.VISIBLE_SCREEN));
    registerSyncTask(t.task("screen", SYNC_PRIORITY.VISIBLE_SCREEN));
    stale(); // the old mount's cleanup, arriving after the new mount registered

    await runSyncNow("test");
    expect(t.order).toEqual(["screen"]);
  });
});

describe("what a reconnect must not touch", () => {
  const source = readFileSync(join(__dirname, "..", "reconnectOrchestrator.ts"), "utf-8");

  it("calls nothing on a task except its refresh", () => {
    // §91: "reconnect clears the viewport". Scroll position, the open composer,
    // a half-written message and the video mid-play belong to the surface. The
    // orchestrator cannot disturb them because it never invokes anything but
    // `run` -- which is a property of this file, so it is asserted here.
    const invocations = source.match(/\btask\.[A-Za-z]+\(/g) || [];
    expect([...new Set(invocations)]).toEqual(["task.run("]);
  });

  it("offers a task no reset, reload or remount affordance", () => {
    const declaration = source.slice(source.indexOf("export type SyncTask = {"));
    // Comments stripped first: the rule is about what a task can be asked to do,
    // and prose explaining why it cannot is not a violation of it.
    const fields = declaration
      .slice(0, declaration.indexOf("\n};"))
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/\/\/.*$/gm, "");
    for (const forbidden of ["reset", "reload", "remount", "clearCache", "scrollTo", "invalidate"]) {
      expect(fields).not.toContain(forbidden);
    }
  });
});
