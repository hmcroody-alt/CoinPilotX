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
import { ensureNamespace } from "../../i18n/engine";
import {
  actionKey,
  classifyPulseFailure,
  describeSavedActionError,
  describeSavedLibraryError,
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

/**
 * The classifier both Saved describers share.
 *
 * Tested on its own because the read and write copy are allowed to differ but
 * the *category* is not: a screen that decided a dead socket was a server fault
 * on one path and a connectivity fault on the other would tell the same user two
 * different things about the same failure.
 */
describe("pulse failure classification", () => {
  it("maps API statuses to categories", () => {
    expect(classifyPulseFailure(new PulseApiError("x", 401))).toBe("auth");
    expect(classifyPulseFailure(new PulseApiError("x", 403))).toBe("forbidden");
    expect(classifyPulseFailure(new PulseApiError("x", 404))).toBe("request");
    expect(classifyPulseFailure(new PulseApiError("x", 500))).toBe("server");
    expect(classifyPulseFailure(new PulseApiError("x", 0))).toBe("offline");
  });

  it("treats an unreachable request as offline even though it arrives as a 503", () => {
    // `pulseApi` reports a fetch that never produced a response as 503
    // `request_unreachable`. Read as a plain 5xx it would tell someone with no
    // signal to wait for the server to recover.
    expect(classifyPulseFailure(new PulseApiError("x", 503, "request_unreachable"))).toBe("offline");
    expect(classifyPulseFailure(new PulseApiError("x", 503, "session_refresh_temporary"))).toBe("offline");
    expect(classifyPulseFailure(new PulseApiError("x", 503))).toBe("server");
  });

  it("treats a transport throw as offline", () => {
    expect(classifyPulseFailure(new TypeError("Network request failed"))).toBe("offline");
    expect(classifyPulseFailure("nope")).toBe("unknown");
  });
});

/**
 * Saved-library copy, read and write.
 *
 * The defect these guard against is one string: the backend's JSON error
 * handler answers every failing API path with "Upload failed. Please retry or
 * contact support with this trace ID." Any describer that echoes `err.message`
 * puts that sentence under a rename box. Every assertion below that checks for
 * the *absence* of "Upload" is checking that door is still shut.
 */
describe("saved library copy", () => {
  const uploadCopy = "Upload failed. Please retry or contact support with this trace ID.";

  beforeAll(async () => {
    // The engine resolves keys from catalogs loaded on demand; without this the
    // describers would fall back to humanized key names and every assertion
    // below would pass or fail for the wrong reason.
    await ensureNamespace("en", "errors");
  });

  it("describes a failed read without repeating what the server said", () => {
    const message = describeSavedLibraryError(new PulseApiError(uploadCopy, 500));
    expect(message).not.toMatch(/upload/i);
    expect(message).toMatch(/saved library/i);
  });

  it("names the attempted write, not an upload and not a save", () => {
    const created = describeSavedActionError(new PulseApiError(uploadCopy, 500), "create");
    expect(created).not.toMatch(/upload/i);
    expect(created).toMatch(/collection/i);

    // The Saved screen's Remove button reuses the unsave mutation, whose own
    // copy is worded "Save". The user pressed Remove.
    const removed = describeSavedActionError(new PulseApiError(uploadCopy, 500), "remove");
    expect(removed).toMatch(/removed/i);
    expect(removed).not.toMatch(/^Save could not/);
  });

  it("gives each write action its own subject line", () => {
    const subjects = (["create", "rename", "delete", "remove", "move"] as const).map((action) =>
      describeSavedActionError(new PulseApiError("x", 500), action)
    );
    expect(new Set(subjects).size).toBe(subjects.length);
  });

  it("varies the remedy by category rather than by action", () => {
    const offline = describeSavedActionError(new PulseApiError("x", 503, "request_unreachable"), "move");
    const server = describeSavedActionError(new PulseApiError("x", 503), "move");
    expect(offline).toMatch(/connection/i);
    expect(offline).not.toBe(server);
  });

  it("never echoes the server message on an unmapped 4xx, unlike the social describer", () => {
    // `describeSocialActionError` deliberately prefers the server's own words
    // here, which is why the Saved write path does not route through it.
    expect(describeSocialActionError(new PulseApiError(uploadCopy, 422))).toBe(uploadCopy);
    expect(describeSavedActionError(new PulseApiError(uploadCopy, 422), "rename")).not.toMatch(/upload/i);
  });

  it("keeps a well-formed trace id on both the read and the write path", () => {
    const details = { trace_id: "abc-123_XYZ" };
    expect(describeSavedLibraryError(new PulseApiError(uploadCopy, 500, "server_error", details))).toContain("abc-123_XYZ");
    expect(describeSavedActionError(new PulseApiError(uploadCopy, 500, "server_error", details), "delete")).toContain("abc-123_XYZ");
  });

  it("drops a trace id that is prose rather than an id", () => {
    // The trace field is the one part of the server payload still shown, so it
    // is also the remaining way server text could reach the screen.
    const prose = { trace_id: "Upload failed. Please retry or contact support." };
    const message = describeSavedActionError(new PulseApiError(uploadCopy, 500, "server_error", prose), "create");
    expect(message).not.toMatch(/upload/i);
    expect(message).not.toMatch(/reference/i);
  });

  it("shows no trace reference when the server supplied none", () => {
    expect(describeSavedActionError(new PulseApiError("x", 500), "create")).not.toMatch(/reference/i);
    expect(describeSavedActionError(new TypeError("Network request failed"), "create")).not.toMatch(/reference/i);
  });
});
