/**
 * Global test setup.
 *
 * AsyncStorage is a native module, so any test whose import graph reaches
 * `src/core/cache.ts` — which now includes every settings screen, via the
 * preference store — throws at require time without a mock. Registering the
 * package's own in-memory mock here rather than per-file means a new test never
 * fails for a reason that has nothing to do with what it is testing.
 */

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

/**
 * No `NativeAnimatedHelper` mock here on purpose. React Native 0.81 no longer
 * exposes that path, and jest-expo's own preset already stubs the animated
 * native module — mocking it again resolves to nothing and fails the suite
 * before a single test runs.
 */

/**
 * The suite does not touch the network.
 *
 * Nothing here was ever meant to, but there is no wall: a screen test that
 * forgets to mock one API module still reaches `pulseApi`, which calls the
 * global `fetch` against the real pulsesoc.com. Those calls do not fail the
 * test — every caller has an offline fallback — so the leak is invisible right
 * up until the host is slow, at which point the suite's result depends on
 * production latency. That is what made the Business OS screens flake: green
 * alone, red under load, and red for everyone the day the host stopped
 * answering.
 *
 * Rejecting immediately is the offline branch those callers already handle, so
 * behaviour is unchanged and now deterministic. A test that wants a *response*
 * must mock at the module boundary (`jest.mock("../../api/…")`) — which states
 * what it expects instead of inheriting whatever the internet says today. A
 * test that genuinely needs to drive `fetch` can still assign its own; this is
 * a default, not a lock.
 */
global.fetch = (input) =>
  Promise.reject(
    new Error(
      `Network access is disabled in tests (${String(input)}). ` +
        "Mock the API module this call goes through rather than letting it reach the wire."
    )
  );

/**
 * A second source of "green alone, red under load", with no wall available —
 * only a habit. Written down here because the fix is invisible and the broken
 * shape is the one everybody reaches for first.
 *
 *   await waitFor(() => expect(queryByText(/gone/)).toBeNull());   // slow
 *   await waitFor(() => expect(queryAllByText(/gone/).length).toBe(0)); // fast
 *
 * `waitFor` retries until the callback stops throwing, so every poll but the
 * last is a deliberate failure — and a failing `expect(element)` sends Jest to
 * `pretty-format` to build a message nobody will ever read. A React Native host
 * element carries `_fiber`, so that message is a serialisation of the render
 * tree: roughly half a second each time, spent with the event loop blocked, so
 * `waitFor` cannot even poll again until it finishes. Three polls exceeds the
 * 1000ms default budget on their own.
 *
 * `PagesHubScreen`'s "drops the card once the server says the invite is gone"
 * was the worked example: 1087ms alone, red the moment the machine had anything
 * else to do, 109ms once the assertion was handed a number instead of a node.
 * Nothing about what it asserts changed.
 *
 * The rule: inside `waitFor`, assert on a primitive — a count, a string, a
 * boolean. Outside `waitFor` a passing `expect(element).toBeNull()` costs
 * nothing, because pretty-format only runs when the assertion fails, so those
 * are fine as they are.
 */
