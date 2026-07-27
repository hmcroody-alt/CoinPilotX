// @testing-library/react-native rather than react-test-renderer directly:
// react-test-renderer ships no type declarations and @types/react-test-renderer
// is not a dependency here, so importing it puts a permanent TS7016 in `tsc
// --noEmit`. A test that forces the typecheck to stay red is a test that trains
// people to ignore the typecheck. The library re-exports the same act().
import { act, render } from "@testing-library/react-native";
import { createElement } from "react";

// PulseApiError lives in api/pulseApi, which transitively imports the session
// store and therefore the AsyncStorage/SecureStore native modules. Mocked here
// following the convention in src/screens/__tests__/StatusScreen.reaction.test.tsx,
// because the guard's behaviour has nothing to do with storage.
jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn()
}));

jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(),
  setItemAsync: jest.fn(),
  deleteItemAsync: jest.fn()
}));

import { PulseApiError } from "../../api/pulseApi";
import {
  actionKey,
  describeSocialActionError,
  isRecoverableSocialError,
  useSocialActionGuard,
  type SocialActionGuard
} from "../actionGuard";

/**
 * Mounts the hook and hands back a live reference to it. The guard's contract is
 * about ordering across awaits, so the tests drive it directly rather than
 * through a rendered button — a button can only ever demonstrate one tap
 * sequence, and the defects being guarded against are concurrent.
 */
function mountGuard(): { guard: () => SocialActionGuard; unmount: () => void } {
  let latest: SocialActionGuard | null = null;
  function Probe() {
    latest = useSocialActionGuard();
    return null;
  }
  // Not wrapped in act(): RNTL's render already wraps its own mount, and nesting
  // it breaks the library's host-component detection ("Can't access .root on
  // unmounted test renderer").
  const tree = render(createElement(Probe));
  return {
    guard: () => {
      if (!latest) throw new Error("guard not mounted");
      return latest;
    },
    unmount: () => tree.unmount()
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("action key", () => {
  it("namespaces by action so two actions on one item never share a lock", () => {
    expect(actionKey("like", 7)).toBe("like:7");
    expect(actionKey("save", 7)).not.toBe(actionKey("like", 7));
  });
});

describe("useSocialActionGuard duplicate prevention", () => {
  it("drops a second call for the same key while the first is in flight", async () => {
    const { guard, unmount } = mountGuard();
    const first = deferred<string>();
    const request = jest.fn(() => first.promise);

    let firstRun!: Promise<string | undefined>;
    let secondRun!: Promise<string | undefined>;
    await act(async () => {
      firstRun = guard().run("like:1", request);
      secondRun = guard().run("like:1", request);
    });

    expect(request).toHaveBeenCalledTimes(1);
    await act(async () => {
      first.resolve("ok");
      await firstRun;
    });
    expect(await secondRun).toBeUndefined();
    expect(await firstRun).toBe("ok");
    unmount();
  });

  it("allows concurrent calls for different keys", async () => {
    const { guard, unmount } = mountGuard();
    const request = jest.fn(async () => "ok");
    await act(async () => {
      await Promise.all([guard().run("like:1", request), guard().run("like:2", request)]);
    });
    expect(request).toHaveBeenCalledTimes(2);
    unmount();
  });

  it("releases the lock so a later tap on the same key succeeds", async () => {
    const { guard, unmount } = mountGuard();
    const request = jest.fn(async () => "ok");
    await act(async () => {
      await guard().run("like:1", request);
    });
    await act(async () => {
      await guard().run("like:1", request);
    });
    expect(request).toHaveBeenCalledTimes(2);
    unmount();
  });

  it("releases the lock after a failure, so a failed action can be retried", async () => {
    const { guard, unmount } = mountGuard();
    const failing = jest.fn(async () => {
      throw new Error("network down");
    });
    await act(async () => {
      await guard().run("like:1", failing, { onError: () => undefined });
    });
    await act(async () => {
      await guard().run("like:1", failing, { onError: () => undefined });
    });
    expect(failing).toHaveBeenCalledTimes(2);
    unmount();
  });

  it("permits a superseding call and reports the newer result", async () => {
    const { guard, unmount } = mountGuard();
    const slow = deferred<string>();
    let firstRun!: Promise<string | undefined>;
    let secondRun!: Promise<string | undefined>;
    await act(async () => {
      firstRun = guard().run("react:1", () => slow.promise, { supersede: true });
      secondRun = guard().run("react:1", async () => "second", { supersede: true });
    });
    await act(async () => {
      slow.resolve("first");
      await Promise.all([firstRun, secondRun]);
    });
    expect(await secondRun).toBe("second");
    expect(await firstRun).toBeUndefined();
    unmount();
  });
});

describe("useSocialActionGuard stale-response rejection", () => {
  it("discards a stale success so it cannot stomp a newer optimistic update", async () => {
    const { guard, unmount } = mountGuard();
    const slow = deferred<string>();
    const applied: string[] = [];

    let firstRun!: Promise<string | undefined>;
    await act(async () => {
      firstRun = guard().run("react:1", () => slow.promise, {
        supersede: true,
        onResult: (value) => applied.push(value)
      });
      await guard().run("react:1", async () => "newer", {
        supersede: true,
        onResult: (value) => applied.push(value)
      });
    });

    await act(async () => {
      slow.resolve("stale");
      await firstRun;
    });

    expect(applied).toEqual(["newer"]);
    unmount();
  });

  it("discards a stale failure so it cannot roll back to an old count", async () => {
    const { guard, unmount } = mountGuard();
    const slow = deferred<string>();
    const rollbacks: string[] = [];

    let firstRun!: Promise<string | undefined>;
    await act(async () => {
      firstRun = guard().run("react:1", () => slow.promise, {
        supersede: true,
        onRollback: () => rollbacks.push("first"),
        onError: () => undefined
      });
      await guard().run("react:1", async () => "newer", { supersede: true });
    });

    await act(async () => {
      slow.reject(new Error("too late"));
      await firstRun;
    });

    expect(rollbacks).toEqual([]);
    unmount();
  });

  it("applies the optimistic update before the request is issued", async () => {
    const { guard, unmount } = mountGuard();
    const order: string[] = [];
    await act(async () => {
      await guard().run("save:1", async () => {
        order.push("request");
        return "ok";
      }, {
        optimistic: () => order.push("optimistic"),
        onResult: () => order.push("result")
      });
    });
    expect(order).toEqual(["optimistic", "request", "result"]);
    unmount();
  });

  it("rolls back and reports a message when the request fails", async () => {
    const { guard, unmount } = mountGuard();
    const order: string[] = [];
    let message = "";
    await act(async () => {
      await guard().run("save:1", async () => {
        throw new PulseApiError("nope", 500);
      }, {
        optimistic: () => order.push("optimistic"),
        onRollback: () => order.push("rollback"),
        onError: (text) => {
          message = text;
        }
      });
    });
    expect(order).toEqual(["optimistic", "rollback"]);
    expect(message).toBe("That action could not be completed right now. Try again.");
    unmount();
  });

  it("never rejects, so a caller cannot produce an unhandled rejection", async () => {
    const { guard, unmount } = mountGuard();
    let result: unknown = "unset";
    await act(async () => {
      result = await guard().run("save:1", async () => {
        throw new Error("boom");
      }, { onError: () => undefined });
    });
    expect(result).toBeUndefined();
    unmount();
  });

  it("exposes busy state for the in-flight key only", async () => {
    const { guard, unmount } = mountGuard();
    const slow = deferred<string>();
    let run!: Promise<string | undefined>;
    await act(async () => {
      run = guard().run("like:1", () => slow.promise);
    });
    expect(guard().isBusy("like:1")).toBe(true);
    expect(guard().isBusy("like:2")).toBe(false);
    expect(guard().anyBusy()).toBe(true);
    await act(async () => {
      slow.resolve("ok");
      await run;
    });
    expect(guard().isBusy("like:1")).toBe(false);
    expect(guard().anyBusy()).toBe(false);
    unmount();
  });
});

/**
 * Per-item busy state, which is what a feed card renders. This replaces the
 * `busyPostId === item.id` / `busyId === reel.id` scalars: a scalar can mark at
 * most one item, so acting on one card either greyed out an unrelated card or
 * marked nothing at all.
 */
describe("useSocialActionGuard per-item busy state", () => {
  it("marks only the item being acted on, not every card in the list", async () => {
    const { guard, unmount } = mountGuard();
    const slow = deferred<string>();
    let run!: Promise<string | undefined>;
    await act(async () => {
      run = guard().run(actionKey("like", 1), () => slow.promise);
    });
    expect(guard().isItemBusy(1)).toBe(true);
    expect(guard().isItemBusy(2)).toBe(false);
    await act(async () => {
      slow.resolve("ok");
      await run;
    });
    expect(guard().isItemBusy(1)).toBe(false);
    unmount();
  });

  it("marks an item busy for whichever action is running on it", async () => {
    const { guard, unmount } = mountGuard();
    const slow = deferred<string>();
    let run!: Promise<string | undefined>;
    await act(async () => {
      run = guard().run(actionKey("repost", 42), () => slow.promise);
    });
    expect(guard().isItemBusy(42)).toBe(true);
    // The item is busy without the specific like key being busy, so a card can
    // disable its whole action row while a single action is in flight.
    expect(guard().isBusy(actionKey("like", 42))).toBe(false);
    await act(async () => {
      slow.resolve("ok");
      await run;
    });
    unmount();
  });

  it("does not confuse an item with another whose id merely ends the same way", async () => {
    // The in-flight key is "like:7". Item 17 must not match it — a naive
    // `includes(String(id))` or a prefix test would report card 17 busy while
    // card 7 is loading, and card 7 busy while 70 is loading.
    const { guard, unmount } = mountGuard();
    const slow = deferred<string>();
    let run!: Promise<string | undefined>;
    await act(async () => {
      run = guard().run(actionKey("like", 7), () => slow.promise);
    });
    expect(guard().isItemBusy(7)).toBe(true);
    expect(guard().isItemBusy(17)).toBe(false);
    expect(guard().isItemBusy(70)).toBe(false);
    await act(async () => {
      slow.resolve("ok");
      await run;
    });
    unmount();
  });

  it("keeps two items in flight at once, which a scalar could not represent", async () => {
    const { guard, unmount } = mountGuard();
    const first = deferred<string>();
    const second = deferred<string>();
    let runA!: Promise<string | undefined>;
    let runB!: Promise<string | undefined>;
    await act(async () => {
      runA = guard().run(actionKey("like", 1), () => first.promise);
      runB = guard().run(actionKey("save", 2), () => second.promise);
    });
    expect(guard().isItemBusy(1)).toBe(true);
    expect(guard().isItemBusy(2)).toBe(true);
    await act(async () => {
      first.resolve("ok");
      await runA;
    });
    // Item 1 settled; item 2 is still loading. A scalar would have lost one.
    expect(guard().isItemBusy(1)).toBe(false);
    expect(guard().isItemBusy(2)).toBe(true);
    await act(async () => {
      second.resolve("ok");
      await runB;
    });
    expect(guard().anyBusy()).toBe(false);
    unmount();
  });
});

describe("failure copy", () => {
  it("maps each API status to distinct user-facing copy", () => {
    expect(describeSocialActionError(new PulseApiError("x", 401))).toMatch(/session expired/i);
    expect(describeSocialActionError(new PulseApiError("x", 403))).toMatch(/permission/i);
    expect(describeSocialActionError(new PulseApiError("x", 404))).toMatch(/no longer available/i);
    expect(describeSocialActionError(new PulseApiError("x", 409))).toMatch(/already/i);
    expect(describeSocialActionError(new PulseApiError("x", 429))).toMatch(/too many/i);
    expect(describeSocialActionError(new PulseApiError("x", 503))).toMatch(/try again/i);
  });

  it("names the action in the copy for a server error", () => {
    expect(describeSocialActionError(new PulseApiError("x", 500), "Repost")).toMatch(/^Repost could not/);
  });

  it("prefers the server message for an unmapped 4xx", () => {
    expect(describeSocialActionError(new PulseApiError("Comments are closed", 422))).toBe("Comments are closed");
  });

  it("reports offline for a transport failure", () => {
    expect(describeSocialActionError(new TypeError("Network request failed"))).toMatch(/offline/i);
    expect(describeSocialActionError(new Error("network timeout"))).toMatch(/offline/i);
  });

  it("falls back to generic copy for an unrecognised throw", () => {
    expect(describeSocialActionError("just a string")).toBe("That action could not be completed.");
  });

  it("classifies which failures are worth retrying when connectivity returns", () => {
    expect(isRecoverableSocialError(new PulseApiError("x", 500))).toBe(true);
    expect(isRecoverableSocialError(new PulseApiError("x", 429))).toBe(true);
    expect(isRecoverableSocialError(new TypeError("Network request failed"))).toBe(true);
    expect(isRecoverableSocialError(new PulseApiError("x", 403))).toBe(false);
    expect(isRecoverableSocialError(new PulseApiError("x", 404))).toBe(false);
    expect(isRecoverableSocialError("nope")).toBe(false);
  });
});
