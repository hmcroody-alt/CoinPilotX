/**
 * Performance regression tests for the Business OS hub.
 *
 * Like `ProfileScreen.perf.test.tsx`, these assert ORDERING and REQUEST COUNT
 * rather than timings. A latency regression on this screen does not look like a
 * slow number in CI; it looks like a request that moved back behind another one,
 * a cache read that stopped being consulted, or a re-entry that started asking
 * the network again. Those are all observable without a clock.
 *
 * The properties pinned here:
 *
 *  - the hub shell (every tile) is on screen before any request settles, so the
 *    launcher is usable while its summary is still loading;
 *  - cached values are painted while the canonical requests are still in flight,
 *    rather than only after they fail — the hub used to hold a spinner over data
 *    the device already had;
 *  - a cached read that resolves late never overwrites a canonical response that
 *    already landed (constitution Rule 1: cache is display, never authority);
 *  - re-entry inside the freshness window issues no requests at all, which is
 *    what makes going back from a tile feel free;
 *  - concurrent sync invalidations collapse onto one load instead of racing
 *    several copies of the same four requests;
 *  - one module failing does not stop the others from rendering.
 */
import React from "react";
import { act, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("../../navigation/BottomNavVisibility", () => ({
  BOTTOM_NAV_CONTENT_CLEARANCE: 0,
  useBottomNavScrollVisibility: () => ({
    onScroll: jest.fn(),
    onScrollBeginDrag: jest.fn(),
    scrollEventThrottle: 16
  })
}));

// Captures the invalidation handlers so a test can fire them the way a real
// marketplace write does — several subsystems at once.
const syncHandlers: Record<string, () => void> = {};
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: (subsystem: string, handler: () => void) => {
    syncHandlers[subsystem] = handler;
    return () => delete syncHandlers[subsystem];
  }
}));
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));

const mockListAdAccounts = jest.fn();
const mockGetAdAnalytics = jest.fn();
const mockCachedAccounts = jest.fn();
const mockCachedAnalytics = jest.fn();

jest.mock("../../api/businessOs", () => ({
  ...jest.requireActual("../../api/businessOs"),
  listAdAccounts: (...args: unknown[]) => mockListAdAccounts(...args),
  getAdAnalytics: (...args: unknown[]) => mockGetAdAnalytics(...args),
  loadCachedAdAccounts: (...args: unknown[]) => mockCachedAccounts(...args),
  loadCachedAdAnalytics: (...args: unknown[]) => mockCachedAnalytics(...args)
}));

const mockSellerSnapshot = jest.fn();
const mockCachedSeller = jest.fn();
jest.mock("../../api/marketplace", () => ({
  loadSellerStoreSnapshot: (...args: unknown[]) => mockSellerSnapshot(...args),
  loadCachedSellerStore: (...args: unknown[]) => mockCachedSeller(...args)
}));

import { businessOsLaunchSections } from "../../api/businessOs";
import { preloadNamespaces } from "../../i18n/engine";
import { businessModuleId, isLaunchGated } from "../../launch/readiness";
import { BusinessOsScreen, resetBusinessOsFreshness } from "../BusinessOsScreen";

// Locked tiles read their label out of the lazily-loaded `commerce` catalog.
// Without it every gated tile humanizes to the same "Locked Label", which is
// both wrong copy and an ambiguous lookup. See the note in
// `BusinessOsScreen.test.tsx`.
beforeAll(async () => {
  await preloadNamespaces("en", ["commerce"]);
});

/**
 * The accessibility label a section's tile carries.
 *
 * A launch-gated tile reads "Events. Coming soon." rather than its blurb, so
 * the expectation is derived from the gate instead of hardcoded. That keeps
 * this suite about first-frame completeness — the property it exists to pin —
 * rather than about which modules happen to be gated this week.
 */
function tileLabel(section: { key: string; label: string; blurb: string }) {
  return isLaunchGated(businessModuleId(section.key))
    ? `${section.label}. Coming soon.`
    : `${section.label}. ${section.blurb}`;
}

const EMPTY_ANALYTICS = {
  totals: { impressions: 0, viewable_impressions: 0, clicks: 0, hides: 0, reports: 0, spend_cents: 0, ctr: 0 },
  campaigns: []
};

/** A promise the test resolves by hand, so "still in flight" is observable. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/**
 * A request that does not answer while the test is running.
 *
 * `new Promise(() => undefined)` says the same thing and is what these tests
 * used to pass, but the screen wraps every canonical request in a 12-second
 * deadline (`withBusinessOsDeadline`) and clears that timer only when the
 * request settles. A promise that never settles never clears it, so the timer
 * outlives the test. That was invisible while these suites took longer than the
 * deadline to run; the moment they got fast, Jest started force-exiting the
 * worker and warning about a leak — a warning nobody can act on, sitting on top
 * of the output where the next real failure will appear.
 *
 * Settling them in `afterEach` — after the render is already torn down, so no
 * assertion can observe an answer — lets the deadline be cleared without
 * changing what any test is about.
 */
const unanswered: Array<() => void> = [];

function neverAnswers<T>(): Promise<T> {
  return new Promise<T>((resolve) => {
    unanswered.push(() => resolve(undefined as T));
  });
}

afterEach(() => {
  unanswered.splice(0).forEach((settle) => settle());
});

function navigationSpy() {
  return { navigate: jest.fn() };
}

/** Total canonical network calls the hub made. */
function requestCount() {
  return mockListAdAccounts.mock.calls.length + mockGetAdAnalytics.mock.calls.length + mockSellerSnapshot.mock.calls.length;
}

beforeEach(() => {
  jest.clearAllMocks();
  resetBusinessOsFreshness();
  Object.keys(syncHandlers).forEach((key) => delete syncHandlers[key]);
  mockListAdAccounts.mockResolvedValue({ accounts: [] });
  mockGetAdAnalytics.mockResolvedValue({ analytics: EMPTY_ANALYTICS });
  mockSellerSnapshot.mockResolvedValue({ listings: [], orders: [] });
  mockCachedAccounts.mockResolvedValue([]);
  mockCachedAnalytics.mockResolvedValue(null);
  mockCachedSeller.mockResolvedValue(null);
});

describe("Business OS hub — shell is never blocked", () => {
  it("renders every tile on the first frame, before any request settles", () => {
    // None of the three ever resolve during this test.
    mockListAdAccounts.mockReturnValue(neverAnswers());
    mockGetAdAnalytics.mockReturnValue(neverAnswers());
    mockSellerSnapshot.mockReturnValue(neverAnswers());

    const view = render(<BusinessOsScreen navigation={navigationSpy()} />);

    // No `await`. This is the synchronous first render, which is the whole
    // claim: the launcher is usable before the network is consulted.
    const sections = businessOsLaunchSections();
    expect(sections.length).toBeGreaterThan(0);
    sections.forEach((section) => {
      expect(view.getByLabelText(tileLabel(section))).toBeTruthy();
    });
    expect(view.getByText("At a glance")).toBeTruthy();
  });

  it("keeps the numbers on screen while a refresh runs instead of blanking them", async () => {
    mockSellerSnapshot.mockResolvedValue({ listings: [{ id: 1 }, { id: 2 }], orders: [] });
    const view = render(<BusinessOsScreen navigation={navigationSpy()} />);
    await waitFor(() => expect(view.getByLabelText("Live listings: 2")).toBeTruthy());

    // A refresh that never settles. The previous implementation set a blocking
    // `loading` flag here, which removed At a glance from the tree entirely.
    const pending = deferred<{ listings: unknown[]; orders: unknown[] }>();
    mockSellerSnapshot.mockReturnValue(pending.promise);
    await act(async () => {
      syncHandlers.orders?.();
      await Promise.resolve();
    });

    expect(view.getByLabelText("Live listings: 2")).toBeTruthy();
    await act(async () => {
      pending.resolve({ listings: [], orders: [] });
    });
  });
});

describe("Business OS hub — cache is painted, never authoritative", () => {
  it("paints cached values while the canonical requests are still in flight", async () => {
    const accounts = deferred<{ accounts: unknown[] }>();
    mockListAdAccounts.mockReturnValue(accounts.promise);
    mockGetAdAnalytics.mockReturnValue(neverAnswers());
    mockSellerSnapshot.mockReturnValue(neverAnswers());
    mockCachedAnalytics.mockResolvedValue({
      ...EMPTY_ANALYTICS,
      totals: { ...EMPTY_ANALYTICS.totals, spend_cents: 1234 }
    });
    mockCachedSeller.mockResolvedValue({ listings: [{ id: 1 }, { id: 2 }, { id: 3 }], orders: [] });

    const view = render(<BusinessOsScreen navigation={navigationSpy()} />);
    await waitFor(() => expect(view.getByLabelText("Ad spend: $12.34")).toBeTruthy());

    // Nothing canonical has landed yet — the paint above came from cache alone.
    expect(view.getByLabelText("Live listings: 3")).toBeTruthy();
    await act(async () => {
      accounts.resolve({ accounts: [] });
    });
  });

  it("never lets a slow cache read overwrite a canonical response that already landed", async () => {
    // The canonical store answers immediately with two listings.
    mockSellerSnapshot.mockResolvedValue({ listings: [{ id: 9 }, { id: 10 }], orders: [] });
    // The cache answers late, with a stale count from a previous session.
    const cache = deferred<{ listings: unknown[]; orders: unknown[] }>();
    mockCachedSeller.mockReturnValue(cache.promise);

    const view = render(<BusinessOsScreen navigation={navigationSpy()} />);
    await waitFor(() => expect(view.getByLabelText("Live listings: 2")).toBeTruthy());

    await act(async () => {
      cache.resolve({ listings: [{ id: 1 }, { id: 2 }, { id: 3 }, { id: 4 }, { id: 5 }], orders: [] });
      await Promise.resolve();
    });

    // Still the canonical 2, not the stale 5.
    expect(view.getByLabelText("Live listings: 2")).toBeTruthy();
  });
});

describe("Business OS hub — request count", () => {
  it("issues its three canonical requests together, not as a waterfall", async () => {
    const view = render(<BusinessOsScreen navigation={navigationSpy()} />);
    // All three are in flight before any of them has resolved.
    expect(mockListAdAccounts).toHaveBeenCalledTimes(1);
    expect(mockGetAdAnalytics).toHaveBeenCalledTimes(1);
    expect(mockSellerSnapshot).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(view.getByText("At a glance")).toBeTruthy());
  });

  it("issues no requests at all when the hub is re-entered inside the freshness window", async () => {
    const first = render(<BusinessOsScreen navigation={navigationSpy()} />);
    await waitFor(() => expect(first.queryAllByLabelText("Refreshing your business summary").length).toBe(0));
    const afterFirst = requestCount();
    expect(afterFirst).toBe(3);
    first.unmount();

    // Something must be in cache for the window to be honoured — the window is
    // about not re-asking for values already held, not about showing nothing.
    mockCachedSeller.mockResolvedValue({ listings: [{ id: 1 }], orders: [] });

    const second = render(<BusinessOsScreen navigation={navigationSpy()} />);
    await waitFor(() => expect(second.getByLabelText("Live listings: 1")).toBeTruthy());

    expect(requestCount()).toBe(afterFirst);
  });

  it("collapses concurrent sync invalidations onto a single reload", async () => {
    const view = render(<BusinessOsScreen navigation={navigationSpy()} />);
    await waitFor(() => expect(view.queryAllByLabelText("Refreshing your business summary").length).toBe(0));
    const baseline = requestCount();

    // One marketplace write invalidates inventory, marketplace and orders. Three
    // subscriptions fire in the same tick; the screen must not run three loads.
    const pending = deferred<{ listings: unknown[]; orders: unknown[] }>();
    mockSellerSnapshot.mockReturnValue(pending.promise);
    await act(async () => {
      syncHandlers.seller_inventory?.();
      syncHandlers.marketplace?.();
      syncHandlers.orders?.();
      await Promise.resolve();
    });

    // One extra load — three requests — not three loads and nine requests.
    expect(requestCount()).toBe(baseline + 3);
    await act(async () => {
      pending.resolve({ listings: [], orders: [] });
    });
  });

  it("forces past the freshness window when a real mutation invalidates", async () => {
    const view = render(<BusinessOsScreen navigation={navigationSpy()} />);
    await waitFor(() => expect(view.queryAllByLabelText("Refreshing your business summary").length).toBe(0));
    const baseline = requestCount();

    await act(async () => {
      syncHandlers.orders?.();
      await Promise.resolve();
    });

    // The window is 30s and no time has passed, so this proves `force` wins:
    // a write the user just made is never hidden behind a staleness timer.
    expect(requestCount()).toBe(baseline + 3);
  });
});

describe("Business OS hub — failure isolation", () => {
  it("renders the store count even when advertising fails outright", async () => {
    mockListAdAccounts.mockRejectedValue(new Error("ads down"));
    mockGetAdAnalytics.mockRejectedValue(new Error("ads down"));
    mockSellerSnapshot.mockResolvedValue({ listings: [{ id: 1 }, { id: 2 }, { id: 3 }], orders: [{ id: 5 }] });

    const view = render(<BusinessOsScreen navigation={navigationSpy()} />);
    await waitFor(() => expect(view.getByLabelText("Live listings: 3")).toBeTruthy());
    expect(view.getByLabelText("Orders: 1")).toBeTruthy();
  });
});
