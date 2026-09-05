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

import { businessOsHubSections, businessOsLaunchSections, businessOsNavigationArgs } from "../../api/businessOs";
import { preloadNamespaces } from "../../i18n/engine";
import { businessModuleId, isLaunchGated } from "../../launch/readiness";
import { businessOsSectionHasLanding } from "../../launch/sectionCapabilities";
import { BUSINESS_OS_LOAD_TIMEOUT_MS, BusinessOsScreen, resetBusinessOsFreshness } from "../BusinessOsScreen";

/**
 * The accessibility label a section's tile carries.
 *
 * A locked tile does not read "Events. Events you host and promote." — it reads
 * "Events. Coming soon.", because the state has to be in the label rather than
 * only in a hint (iOS hints are off by default) and only in the teal border
 * (colour cannot be the only channel). Deriving the expectation from the gate
 * rather than hardcoding it means these tests keep passing, and keep meaning the
 * same thing, on the day a module's row is deleted from `readiness.ts`.
 */
/**
 * Catalogs are lazy, so an unloaded key degrades to its humanized leaf:
 * `commerce:launch.lockedLabel` renders as "Locked Label" for every gated tile
 * at once. That is not merely wrong copy — it is the *same* copy on three
 * tiles, so a lookup by label becomes ambiguous and the suite would be
 * asserting against a shape the product never ships. Loading the one namespace
 * this screen's launch copy lives in makes these assertions about the real
 * strings a user reads. The rest of this screen is hardcoded English, so
 * nothing else in the suite changes.
 */
beforeAll(async () => {
  await preloadNamespaces("en", ["commerce"]);
});

function tileLabel(section: { key: string; label: string; blurb: string }) {
  return isLaunchGated(businessModuleId(section.key))
    ? `${section.label}. Coming soon.`
    : `${section.label}. ${section.blurb}`;
}

const EMPTY_ANALYTICS = {
  totals: { impressions: 0, viewable_impressions: 0, clicks: 0, hides: 0, reports: 0, spend_cents: 0, ctr: 0 },
  campaigns: []
};

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
  await waitFor(() => expect(view.queryAllByLabelText("Refreshing your business summary").length).toBe(0));
  return { ...view, navigation };
}

describe("Business OS hub", () => {
  it("renders a tile for every section the landing presents, routable or gated", async () => {
    const view = await renderHub();
    const sections = businessOsLaunchSections();
    expect(sections.length).toBeGreaterThan(0);
    // Tiles are matched on their accessibility label rather than their text:
    // "Orders" is both a tile and an at-a-glance metric, so a text lookup is
    // ambiguous in a way that says nothing about the product.
    sections.forEach((section) => {
      expect(view.getByLabelText(tileLabel(section))).toBeTruthy();
    });
    // Every routable section is still on screen. Gating is additive — it must
    // never be the reason a working module disappeared.
    businessOsHubSections().forEach((section) => {
      expect(view.getByLabelText(tileLabel(section))).toBeTruthy();
    });
    // A section with no route and no gate holding it would be a tile that
    // throws from `businessOsNavigationArgs` the moment it is tapped. The
    // resolver is supposed to make that unrepresentable; this is the assertion
    // that says so out loud.
    sections.forEach((section) => {
      expect(Boolean(section.route) || isLaunchGated(businessModuleId(section.key))).toBe(true);
    });
  });

  /**
   * A section with nothing missing keeps the behaviour it always had. This is
   * the assertion that stops the landing layer from becoming a toll booth in
   * front of finished work — if it ever starts appearing for a complete
   * section, this test is what says so.
   */
  it("dispatches the navigation the registry describes when a finished tile is tapped", async () => {
    const view = await renderHub();
    const direct = businessOsLaunchSections().filter(
      (section) => !isLaunchGated(businessModuleId(section.key)) && !businessOsSectionHasLanding(section.key)
    );
    expect(direct.length).toBeGreaterThan(0);
    for (const section of direct) {
      view.navigation.navigate.mockClear();
      fireEvent.press(view.getByLabelText(tileLabel(section)));
      const [route, params] = businessOsNavigationArgs(section);
      expect(view.navigation.navigate).toHaveBeenCalledWith(route, params);
    }
  });

  it("opens the section's landing layer when part of it is still being built", async () => {
    const view = await renderHub();
    const withLanding = businessOsLaunchSections().filter((section) => businessOsSectionHasLanding(section.key));
    expect(withLanding.length).toBeGreaterThan(0);
    for (const section of withLanding) {
      view.navigation.navigate.mockClear();
      fireEvent.press(view.getByLabelText(tileLabel(section)));
      // The landing is pushed by section key, so it stays correct if the
      // section is ever repointed at a different destination.
      expect(view.navigation.navigate).toHaveBeenCalledWith("BusinessOsSection", { section: section.key });
    }
  });

  /**
   * The half of the gate that actually protects anything.
   *
   * A locked tile that still navigated into its module would be worse than no
   * gate at all: the badge would promise the module is not ready while the tap
   * dropped the user into it anyway. What the tap opens instead — the landing
   * that explains the section, or the Coming Soon message when there is nothing
   * written down to explain — is secondary; that it never opens the module is
   * the property.
   */
  it("never navigates into a gated section", async () => {
    const gated = businessOsLaunchSections().filter((section) => isLaunchGated(businessModuleId(section.key)));
    expect(gated.length).toBeGreaterThan(0);

    for (const section of gated) {
      const view = await renderHub();
      fireEvent.press(view.getByLabelText(tileLabel(section)));

      const dispatched = view.navigation.navigate.mock.calls.map(([route]) => route);
      expect(dispatched).not.toContain(section.route);

      if (businessOsSectionHasLanding(section.key)) {
        expect(dispatched).toContain("BusinessOsSection");
      } else {
        // The fallback for a section gated without its capabilities written
        // down. Nothing is in that state today, so this branch is the one that
        // keeps working when something arrives in it.
        expect(view.getByTestId(`coming-soon-${businessModuleId(section.key)}`)).toBeTruthy();
        // Dismissing returns the user to the hub rather than stranding them in
        // a modal whose only other exit is force-quitting the app.
        fireEvent.press(view.getByTestId("coming-soon-dismiss"));
        await waitFor(() => expect(view.queryByTestId(`coming-soon-${businessModuleId(section.key)}`)).toBeNull());
      }
      view.unmount();
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
    mockListAdAccounts.mockReturnValue(neverAnswers());
    mockGetAdAnalytics.mockReturnValue(neverAnswers());
    mockSellerSnapshot.mockReturnValue(neverAnswers());
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
    await waitFor(() => expect(view.queryAllByText("Showing saved data").length).toBe(0));
  });

  it("does not show the offline notice when only the seller snapshot is empty", async () => {
    // `loadSellerStoreSnapshot` swallows its own failures and always resolves,
    // so treating it as a liveness signal would either never or always fire.
    mockSellerSnapshot.mockResolvedValue({ listings: [], orders: [] });
    const view = await renderHub();
    expect(view.queryByText("Showing saved data")).toBeNull();
  });
});
