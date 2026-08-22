/**
 * Performance regression tests for the Business OS *tiles*.
 *
 * `BusinessOsScreen.perf.test.tsx` pins the hub. This file pins the screens the
 * hub opens onto, against the same defect classes the hub had — because they
 * had them too, and a hub that paints in one frame onto a tile that blanks for
 * two seconds has not made the workspace feel fast.
 *
 * As with the hub tests, these assert ORDERING, REQUEST COUNT and PRESENCE, not
 * timings. The regressions being guarded against are structural: a render gate
 * that keys on "a request is running" instead of "there is nothing to show", a
 * callback whose identity churns and drags an effect's subscriptions with it, a
 * catch block that owns more requests than it should.
 *
 * The properties pinned here:
 *
 *  - Advertising does not remove the page in response to the user touching a
 *    control on it (every mutation ends with `await load()`);
 *  - a failure in the seller store's *terms* request cannot discard listings
 *    that already arrived, nor claim the seller is offline when they are not;
 *  - the store's two independent requests are issued together;
 *  - concurrent sync invalidations collapse onto one load on every tile that
 *    subscribes to more than one channel.
 */
import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

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
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
// Pulls in expo-av, which has no native module under Jest.
jest.mock("../../components/NativeMediaViewer", () => ({
  NativeMediaViewer: () => null,
  mediaViewerItemFromPulseMedia: jest.fn(() => null)
}));

// Captures invalidation handlers so a test can fire them the way a real
// marketplace write does — several subsystems in the same tick.
const syncHandlers: Record<string, Array<() => void>> = {};
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: (subsystem: string, handler: () => void) => {
    syncHandlers[subsystem] = syncHandlers[subsystem] || [];
    syncHandlers[subsystem].push(handler);
    return () => {
      syncHandlers[subsystem] = (syncHandlers[subsystem] || []).filter((entry) => entry !== handler);
    };
  }
}));

/** Fire every handler registered on a channel, as eventSync would. */
function fireSync(subsystem: string) {
  (syncHandlers[subsystem] || []).forEach((handler) => handler());
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/* ------------------------------------------------------------ advertising */

const mockListAccounts = jest.fn();
const mockListCampaigns = jest.fn();
const mockRunAction = jest.fn();
const mockCachedAccounts = jest.fn();
const mockCachedCampaigns = jest.fn();
const mockGetAnalytics = jest.fn();
const mockGetWallet = jest.fn();
const mockGetBilling = jest.fn();
const mockCachedAnalytics = jest.fn();

jest.mock("../../api/businessOs", () => ({
  ...jest.requireActual("../../api/businessOs"),
  listAdAccounts: (...args: unknown[]) => mockListAccounts(...args),
  listAdCampaigns: (...args: unknown[]) => mockListCampaigns(...args),
  runAdCampaignAction: (...args: unknown[]) => mockRunAction(...args),
  loadCachedAdAccounts: (...args: unknown[]) => mockCachedAccounts(...args),
  loadCachedAdCampaigns: (...args: unknown[]) => mockCachedCampaigns(...args),
  getAdAnalytics: (...args: unknown[]) => mockGetAnalytics(...args),
  getAdWallet: (...args: unknown[]) => mockGetWallet(...args),
  getAdBillingSummary: (...args: unknown[]) => mockGetBilling(...args),
  loadCachedAdAnalytics: (...args: unknown[]) => mockCachedAnalytics(...args)
}));

// The portal is the one-request happy path; every mock above describes the
// fan-out fallback, so the portal is rejected to keep the fan-out under test.
const mockPortal = jest.fn();
jest.mock("../../api/adsPortal", () => ({
  ...jest.requireActual("../../api/adsPortal"),
  getAdsPortal: () => mockPortal()
}));

/* ------------------------------------------------------------ seller store */

const mockSellerSnapshot = jest.fn();
const mockCachedSeller = jest.fn();
const mockCommercialTerms = jest.fn();

jest.mock("../../api/marketplace", () => ({
  ...jest.requireActual("../../api/marketplace"),
  loadSellerStoreSnapshot: (...args: unknown[]) => mockSellerSnapshot(...args),
  loadCachedSellerStore: (...args: unknown[]) => mockCachedSeller(...args),
  getMarketplaceCommercialTerms: (...args: unknown[]) => mockCommercialTerms(...args)
}));

import { BusinessOsAdvertisingScreen } from "../BusinessOsAdvertisingScreen";
import { SellerStoreScreen } from "../SellerStoreScreen";

const ACCOUNT = { id: 7, business_name: "Roody Goods", status: "active", verified: true };

function campaign(overrides: Record<string, unknown> = {}) {
  return {
    id: 21,
    campaign_name: "Launch",
    objective: "awareness",
    status: "active",
    budget_type: "daily",
    daily_budget_cents: 2500,
    lifetime_budget_cents: 0,
    spent_cents: 0,
    ...overrides
  };
}

function listing(id: number) {
  return { id, title: `Item ${id}`, buyer_visible: true, status: "active", price_cents: 1000 };
}

beforeEach(() => {
  jest.clearAllMocks();
  Object.keys(syncHandlers).forEach((key) => delete syncHandlers[key]);

  mockPortal.mockRejectedValue(new Error("portal unavailable"));
  mockListAccounts.mockResolvedValue({ accounts: [ACCOUNT] });
  mockListCampaigns.mockResolvedValue({ campaigns: [campaign()] });
  mockRunAction.mockResolvedValue({ message: "Done." });
  mockCachedAccounts.mockResolvedValue([]);
  mockCachedCampaigns.mockResolvedValue([]);
  mockGetAnalytics.mockResolvedValue({ analytics: null });
  mockGetWallet.mockResolvedValue({ wallet: null });
  mockGetBilling.mockResolvedValue({ billing: null });
  mockCachedAnalytics.mockResolvedValue(null);

  mockSellerSnapshot.mockResolvedValue({ listings: [], orders: [], live: true });
  mockCachedSeller.mockResolvedValue(null);
  mockCommercialTerms.mockResolvedValue({ terms: { acceptance: null } });
});

describe("Advertising tile — a refresh is additive, never subtractive", () => {
  it("keeps the campaign list mounted while the post-mutation reload runs", async () => {
    const view = render(<BusinessOsAdvertisingScreen />);
    await waitFor(() => expect(view.queryByText("Loading advertising…")).toBeNull(), { timeout: 4000 });
    expect(view.getByText("Launch")).toBeTruthy();

    // Pausing a campaign ends with `await load()`. The render gates used to read
    // `!loading && model`, so this reload unmounted the campaign list, the
    // account form and the new-campaign form: the page went away because the
    // user touched a switch on it.
    const reload = deferred<{ campaigns: unknown[] }>();
    mockListCampaigns.mockReturnValue(reload.promise);

    await act(async () => {
      // The delivery switch. `campaign()` is active, so it reads "Delivering".
      fireEvent.press(view.getByLabelText("Delivering"));
      await Promise.resolve();
    });

    // Mid-reload: the campaign the user just acted on is still on screen.
    expect(view.getByText("Launch")).toBeTruthy();

    await act(async () => {
      reload.resolve({ campaigns: [campaign({ status: "paused" })] });
    });
  });
});

describe("Store tile — one request's failure cannot void another's success", () => {
  it("keeps canonical listings when the independent terms request fails", async () => {
    mockSellerSnapshot.mockResolvedValue({
      listings: [listing(1), listing(2), listing(3)],
      orders: [],
      live: true
    });
    // Terms is a separate contract. It used to share a `try` with the snapshot,
    // so its failure ran a catch that overwrote listings with cache and set the
    // offline flag — a terms hiccup made a healthy store look disconnected.
    mockCommercialTerms.mockRejectedValue(new Error("terms endpoint down"));
    // A stale cached count that is unmistakably not the canonical one.
    mockCachedSeller.mockResolvedValue({
      listings: [listing(9), listing(10), listing(11), listing(12), listing(13), listing(14), listing(15)],
      orders: [],
      live: true
    });

    const view = render(<SellerStoreScreen navigation={{ navigate: jest.fn() }} />);
    await waitFor(() => expect(view.queryByText("Loading seller controls")).toBeNull());

    // Three canonical listings, not the seven stale cached ones.
    expect(view.getByText("Listings loaded")).toBeTruthy();
    expect(view.queryByText("7")).toBeNull();
    expect(view.getAllByText("3").length).toBeGreaterThan(0);
    // And the seller is not told they are offline because a *terms* call failed.
    expect(view.queryByText("Showing saved data")).toBeNull();
  });

  it("issues the snapshot and the terms request together, not as a waterfall", async () => {
    render(<SellerStoreScreen navigation={{ navigate: jest.fn() }} />);
    // Both are in flight synchronously; neither waits on the other, and neither
    // waits on the AsyncStorage cache read.
    expect(mockSellerSnapshot).toHaveBeenCalledTimes(1);
    expect(mockCommercialTerms).toHaveBeenCalledTimes(1);
    await act(async () => {
      await Promise.resolve();
    });
  });

  it("collapses concurrent sync invalidations onto a single reload", async () => {
    const view = render(<SellerStoreScreen navigation={{ navigate: jest.fn() }} />);
    await waitFor(() => expect(view.queryByText("Loading seller controls")).toBeNull());
    const baseline = mockSellerSnapshot.mock.calls.length;

    // One marketplace write invalidates inventory, marketplace and orders. All
    // three fire in the same tick; the screen must not run three loads.
    const pending = deferred<{ listings: unknown[]; orders: unknown[]; live: boolean }>();
    mockSellerSnapshot.mockReturnValue(pending.promise);
    await act(async () => {
      fireSync("seller_inventory");
      fireSync("marketplace");
      fireSync("orders");
      await Promise.resolve();
    });

    expect(mockSellerSnapshot.mock.calls.length).toBe(baseline + 1);
    await act(async () => {
      pending.resolve({ listings: [], orders: [], live: true });
    });
  });

  it("registers each invalidation channel exactly once", async () => {
    const view = render(<SellerStoreScreen navigation={{ navigate: jest.fn() }} />);
    await waitFor(() => expect(view.queryByText("Loading seller controls")).toBeNull());
    // A `load` whose identity churns drags these subscriptions with it, so a
    // duplicate here is the visible symptom of an unstable callback.
    expect(syncHandlers.seller_inventory).toHaveLength(1);
    expect(syncHandlers.marketplace).toHaveLength(1);
    expect(syncHandlers.orders).toHaveLength(1);
  });
});
