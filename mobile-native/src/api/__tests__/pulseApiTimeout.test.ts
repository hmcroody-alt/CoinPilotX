/**
 * Regression tests for the request timeout budget.
 *
 * A stalled socket is indistinguishable from a slow one to the user: both are a
 * spinner that never resolves. These pin the three properties that make the
 * budget safe rather than merely present:
 *
 *  1. an ordinary request is abandoned at the budget and reported as a normal
 *     reachability failure, so screens fall back to cache and offer retry;
 *  2. an upload gets a much larger budget, because cancelling a legitimate
 *     video upload on a slow connection is a data-loss bug wearing a
 *     performance costume;
 *  3. a caller's own `signal` still wins, so screen teardown and superseded
 *     search queries keep cancelling.
 */

const mockGetSessionCookie = jest.fn(async () => "");
const mockGetSessionEnvelope = jest.fn(async () => null);

jest.mock("../../session/sessionStore", () => ({
  clearNativeSessionCredentials: jest.fn(),
  getSessionCookie: () => mockGetSessionCookie(),
  getSessionEnvelope: () => mockGetSessionEnvelope(),
  setCachedSessionUser: jest.fn(),
  setSessionCookie: jest.fn(),
  setSessionEnvelope: jest.fn()
}));
jest.mock("../../session/qaTemporaryAccount", () => ({ shouldRejectTemporaryQaUser: jest.fn(() => false) }));
jest.mock("../../core/perfTrace", () => ({ startSpan: jest.fn(() => ({ end: jest.fn() })) }));
jest.mock("react-native", () => ({ Platform: { OS: "ios" } }));
jest.mock("../config", () => ({ PULSE_API_BASE_URL: "https://pulse.test" }));

import { pulseApi, PulseApiError } from "../pulseApi";

/** A fetch that never settles on its own, and reports the signal it was given. */
function stalledFetch() {
  const seen: AbortSignal[] = [];
  const fetchMock = jest.fn((_url: string, init: RequestInit) => {
    const signal = init.signal as AbortSignal;
    seen.push(signal);
    return new Promise<Response>((_resolve, reject) => {
      const fail = () => {
        const error = new Error("Aborted");
        error.name = "AbortError";
        reject(error);
      };
      // Real `fetch` rejects immediately when handed an already-aborted signal
      // rather than waiting for an event that will never fire again. Without
      // this branch the mock hangs and the test lies about the production path.
      if (signal.aborted) fail();
      else signal.addEventListener("abort", fail);
    });
  });
  global.fetch = fetchMock as unknown as typeof fetch;
  return { fetchMock, seen };
}

beforeEach(() => {
  jest.clearAllMocks();
  jest.useFakeTimers();
  mockGetSessionCookie.mockResolvedValue("");
  mockGetSessionEnvelope.mockResolvedValue(null);
});

afterEach(() => {
  jest.useRealTimers();
});

/** Lets pending microtasks run while fake timers are installed. */
async function flush() {
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
}

describe("pulseApi request timeout", () => {
  it("abandons a stalled JSON request at the budget and reports it as unreachable", async () => {
    stalledFetch();
    const request = pulseApi("/api/pulse/feed").catch((error: unknown) => error);
    await flush();

    // Still in flight one tick before the budget: the budget must not be so
    // tight that it cancels merely-slow requests.
    jest.advanceTimersByTime(19_000);
    await flush();

    jest.advanceTimersByTime(1_500);
    const error = (await request) as PulseApiError;

    expect(error).toBeInstanceOf(PulseApiError);
    expect(error.code).toBe("request_timeout");
    expect(error.status).toBe(503);
    expect(error.message).toMatch(/took too long/i);
  });

  it("gives a FormData upload a much larger budget than a JSON request", async () => {
    stalledFetch();
    const body = new FormData();
    const request = pulseApi("/api/pulse/media/upload", { method: "POST", body }).catch((error: unknown) => error);
    await flush();

    // Well past the JSON budget, an upload must still be running.
    jest.advanceTimersByTime(60_000);
    await flush();
    let settled = false;
    request.then(() => { settled = true; });
    await flush();
    expect(settled).toBe(false);

    jest.advanceTimersByTime(130_000);
    const error = (await request) as PulseApiError;
    expect(error.code).toBe("request_timeout");
  });

  it("forwards a caller's abort instead of replacing it with the budget", async () => {
    stalledFetch();
    const controller = new AbortController();
    const request = pulseApi("/api/pulse/search?q=a", { signal: controller.signal }).catch((error: unknown) => error);
    await flush();

    controller.abort();
    const error = (await request) as PulseApiError;

    // Cancelled well inside the budget, so this is a caller cancellation, not a
    // timeout — the distinction is what lets screens stay silent about a search
    // they themselves superseded.
    expect(error).toBeInstanceOf(PulseApiError);
    expect(error.code).toBe("request_unreachable");
  });

  it("honours a signal that was already aborted before the call", async () => {
    stalledFetch();
    const controller = new AbortController();
    controller.abort();

    const error = (await pulseApi("/api/pulse/search?q=b", { signal: controller.signal })
      .catch((thrown: unknown) => thrown)) as PulseApiError;

    expect(error).toBeInstanceOf(PulseApiError);
    expect(error.code).toBe("request_unreachable");
  });

  it("clears the timer once a request succeeds, leaving no pending work", async () => {
    global.fetch = jest.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 })) as jest.Mock;
    await expect(pulseApi("/api/pulse/ping")).resolves.toEqual({ ok: true });
    expect(jest.getTimerCount()).toBe(0);
  });
});
