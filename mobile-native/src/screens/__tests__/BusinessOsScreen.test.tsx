/**
 * Business OS is now the single entry point for running a business, so the hub
 * carries the whole product claim: every tile must lead somewhere real, and the
 * numbers on it must come from the backend rather than from a hopeful default.
 *
 * The registry itself is unit-tested in `src/api/__tests__/businessOs.test.ts`
 * and the route names are pinned against the navigator in
 * `src/navigation/__tests__/businessOsRoutes.test.ts`. What neither of those can
 * see is the screen: whether a tap actually dispatches the navigation the
 * registry describes, and whether a failed load degrades to cached data instead
 * of rendering zeros that read as "you have no business".
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
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn(() => () => undefined)
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

import { businessOsHubSections, businessOsNavigationArgs } from "../../api/businessOs";
import { BUSINESS_OS_LOAD_TIMEOUT_MS, BusinessOsScreen, resetBusinessOsFreshness } from "../BusinessOsScreen";

const EMPTY_ANALYTICS = {
  totals: { impressions: 0, viewable_impressions: 0, clicks: 0, hides: 0, reports: 0, spend_cents: 0, ctr: 0 },
  campaigns: []
};

function navigationSpy() {
  return { navigate: jest.fn() };
}

beforeEach(() => {
  jest.clearAllMocks();
  // The freshness window lives at module scope so it can survive the unmount
  // that navigating to a tile causes. That makes it leak between tests, where
  // one test's successful load would satisfy the next test's window and skip
  // the fetch it is trying to observe.
  resetBusinessOsFreshness();
  mockListAdAccounts.mockResolvedValue({ accounts: [] });
  mockGetAdAnalytics.mockResolvedValue({ analytics: EMPTY_ANALYTICS });
  mockSellerSnapshot.mockResolvedValue({ listings: [], orders: [] });
  mockCachedAccounts.mockResolvedValue([]);
  mockCachedAnalytics.mockResolvedValue(null);
  mockCachedSeller.mockResolvedValue(null);
});

async function renderHub(navigation = navigationSpy()) {
  const view = render(<BusinessOsScreen navigation={navigation} />);
  // "Settled" is now the disappearance of the inline revalidation spinner. The
  // hub no longer has a blocking "Loading your business…" panel to wait on —
  // the shell and At a glance are on screen from the first frame, which is the
  // property `BusinessOsScreen.perf.test.tsx` pins.
  await waitFor(() => expect(view.queryByLabelText("Refreshing your business summary")).toBeNull());
  return { ...view, navigation };
}

describe("Business OS hub", () => {
  it("renders a tile for every routable section and no live tile for anything unroutable", async () => {
    const view = await renderHub();
    const sections = businessOsHubSections();
    expect(sections.length).toBeGreaterThan(0);
    // Tiles are matched on their accessibility label rather than their text:
    // "Orders" is both a tile and an at-a-glance metric, so a text lookup is
    // ambiguous in a way that says nothing about the product.
    sections.forEach((section) => {
      expect(view.getByLabelText(`${section.label}. ${section.blurb}`)).toBeTruthy();
    });
    // Customers and Team have no backing contract yet, so they must never be
    // presented as live, navigable tiles. They ARE on screen — as launch-gated
    // Coming Soon cards that show a message instead of navigating, which
    // `BusinessOsScreen.comingSoon.test.tsx` pins — but the routable registry
    // this test walks must not contain them.
    const labels = sections.map((section) => section.label);
    expect(labels).not.toContain("Customers");
    expect(labels).not.toContain("Team");
  });

  it("dispatches the navigation the registry describes when a tile is tapped", async () => {
    const view = await renderHub();
    for (const section of businessOsHubSections()) {
      view.navigation.navigate.mockClear();
      fireEvent.press(view.getByLabelText(`${section.label}. ${section.blurb}`));
      const [route, params] = businessOsNavigationArgs(section);
      expect(view.navigation.navigate).toHaveBeenCalledWith(route, params);
    }
  });

  it("reports real counts from the backend rather than placeholder metrics", async () => {
    mockSellerSnapshot.mockResolvedValue({
      listings: [{ id: 1 }, { id: 2 }, { id: 3 }],
      orders: [{ id: 9 }]
    });
    mockGetAdAnalytics.mockResolvedValue({
      analytics: {
        ...EMPTY_ANALYTICS,
        totals: { ...EMPTY_ANALYTICS.totals, spend_cents: 12550 },
        campaigns: [
          { campaign_id: 1, status: "active" },
          { campaign_id: 2, status: "paused" },
          { campaign_id: 3, status: "active" }
        ]
      }
    });

    const view = await renderHub();
    expect(view.getByLabelText("Live listings: 3")).toBeTruthy();
    expect(view.getByLabelText("Orders: 1")).toBeTruthy();
    expect(view.getByLabelText("Active campaigns: 2")).toBeTruthy();
    expect(view.getByLabelText("Ad spend: $125.50")).toBeTruthy();
  });

  it("tells an unverified advertiser why campaigns cannot deliver", async () => {
    mockListAdAccounts.mockResolvedValue({ accounts: [{ id: 1, status: "pending", verified: false }] });
    const view = await renderHub();
    expect(view.getByText(/awaiting verification/i)).toBeTruthy();
  });

  it("falls back to cached data and says so when PulseSoc cannot be reached", async () => {
    mockListAdAccounts.mockRejectedValue(new Error("Network request failed"));
    mockGetAdAnalytics.mockRejectedValue(new Error("Network request failed"));
    mockCachedAccounts.mockResolvedValue([{ id: 4, status: "active", verified: true }]);
    mockCachedAnalytics.mockResolvedValue({
      ...EMPTY_ANALYTICS,
      totals: { ...EMPTY_ANALYTICS.totals, spend_cents: 500 }
    });

    const view = await renderHub();
    expect(view.getByText("Showing saved data")).toBeTruthy();
    expect(view.getByText("Network request failed")).toBeTruthy();
    // The cached spend is what is shown — not a zero that would misreport the
    // account as having never spent anything.
    expect(view.getByLabelText("Ad spend: $5.00")).toBeTruthy();
  });

  it("shows cached data immediately and does not wait out the deadline to paint it", async () => {
    jest.useFakeTimers();
    mockListAdAccounts.mockReturnValue(new Promise(() => undefined));
    mockGetAdAnalytics.mockReturnValue(new Promise(() => undefined));
    mockSellerSnapshot.mockReturnValue(new Promise(() => undefined));
    mockCachedAccounts.mockResolvedValue([{ id: 4, status: "active", verified: true }]);
    mockCachedAnalytics.mockResolvedValue({
      ...EMPTY_ANALYTICS,
      totals: { ...EMPTY_ANALYTICS.totals, spend_cents: 500 }
    });

    const view = render(<BusinessOsScreen navigation={navigationSpy()} />);

    // The behavioural change this mission made. Previously the cached spend was
    // unreachable until all three requests settled or the 12s deadline fired,
    // so a seller on a bad connection stared at a spinner holding data the
    // device already had. It is on screen before any timer is advanced now.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(view.getByLabelText("Ad spend: $5.00")).toBeTruthy();
    expect(view.getByText("Last synced view — refreshing now.")).toBeTruthy();

    await act(async () => {
      jest.advanceTimersByTime(BUSINESS_OS_LOAD_TIMEOUT_MS);
      await Promise.resolve();
    });

    expect(view.getByText("Showing saved data")).toBeTruthy();
    expect(view.getByText("PulseSoc took too long to load your business.")).toBeTruthy();
    expect(view.getByLabelText("Ad spend: $5.00")).toBeTruthy();
    jest.useRealTimers();
  });

  it("retries the live load when the offline panel's retry is pressed", async () => {
    mockListAdAccounts.mockRejectedValueOnce(new Error("offline"));
    mockGetAdAnalytics.mockRejectedValueOnce(new Error("offline"));
    const view = await renderHub();
    expect(view.getByText("Showing saved data")).toBeTruthy();

    mockListAdAccounts.mockResolvedValue({ accounts: [] });
    mockGetAdAnalytics.mockResolvedValue({ analytics: EMPTY_ANALYTICS });
    await act(async () => {
      fireEvent.press(view.getByLabelText("Retry loading Business OS"));
    });
    await waitFor(() => expect(view.queryByText("Showing saved data")).toBeNull());
  });

  it("does not show the offline notice when only the seller snapshot is empty", async () => {
    // `loadSellerStoreSnapshot` swallows its own failures and always resolves,
    // so treating it as a liveness signal would either never or always fire.
    mockSellerSnapshot.mockResolvedValue({ listings: [], orders: [] });
    const view = await renderHub();
    expect(view.queryByText("Showing saved data")).toBeNull();
  });
});
