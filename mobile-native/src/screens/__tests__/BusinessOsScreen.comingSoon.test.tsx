/**
 * Coming Soon experience on the Business OS hub.
 *
 * The registry-level truth (customers + team are the only locked modules) is
 * pinned in `core/__tests__/launchReadiness.test.ts`. What only the rendered
 * screen can prove is tested here: the locked modules are VISIBLE as cards,
 * a tap opens the Coming Soon message instead of navigating anywhere (no
 * dead buttons, no back-door), "Got it" dismisses it, every live tile still
 * navigates exactly as before, and the copy is launch language — never
 * developer language.
 */
import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

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

let mockReducedMotion = false;
const mockPulseStart = jest.fn();
const mockPulseStop = jest.fn();
jest.mock("../../theme/logiNexusMotion", () => ({
  ...jest.requireActual("../../theme/logiNexusMotion"),
  useLogiNexusReducedMotion: () => mockReducedMotion,
  createLogiNexusAmbientPulse: () => ({ start: mockPulseStart, stop: mockPulseStop })
}));

jest.mock("../../api/businessOs", () => ({
  ...jest.requireActual("../../api/businessOs"),
  listAdAccounts: jest.fn().mockResolvedValue({ accounts: [] }),
  getAdAnalytics: jest.fn().mockResolvedValue({
    analytics: {
      totals: { impressions: 0, viewable_impressions: 0, clicks: 0, hides: 0, reports: 0, spend_cents: 0, ctr: 0 },
      campaigns: []
    }
  }),
  loadCachedAdAccounts: jest.fn().mockResolvedValue([]),
  loadCachedAdAnalytics: jest.fn().mockResolvedValue(null)
}));
jest.mock("../../api/marketplace", () => ({
  loadSellerStoreSnapshot: jest.fn().mockResolvedValue({ listings: [], orders: [] }),
  loadCachedSellerStore: jest.fn().mockResolvedValue(null)
}));

import { businessOsHubSections, businessOsNavigationArgs } from "../../api/businessOs";
import { activateLocale } from "../../i18n/engine";
import { BusinessOsScreen, resetBusinessOsFreshness } from "../BusinessOsScreen";

const COMING_SOON_BODY =
  "We're building this part of the PulseSoc universe. This feature is preparing for launch.";

// Real catalogs, not a t() stub: the assertions below pin the exact launch
// copy the user sees, so the English bundle must actually resolve.
beforeAll(() => activateLocale("en"));

beforeEach(() => {
  jest.clearAllMocks();
  jest.useFakeTimers();
  mockReducedMotion = false;
  resetBusinessOsFreshness();
});

afterEach(() => {
  jest.useRealTimers();
});

async function renderHub() {
  const navigation = { navigate: jest.fn() };
  const view = render(<BusinessOsScreen navigation={navigation} />);
  await waitFor(() => view.getByTestId("business-os-coming-soon-customers"));
  return { ...view, navigation };
}

describe("locked modules stay visible", () => {
  it("renders Customers and Team as Coming Soon cards in the grid", async () => {
    const view = await renderHub();
    expect(view.getByTestId("business-os-coming-soon-customers")).toBeTruthy();
    expect(view.getByTestId("business-os-coming-soon-team")).toBeTruthy();
    expect(view.getByLabelText("Customers. Customer records and segments.")).toBeTruthy();
    expect(view.getByLabelText("Team. People who help run the business.")).toBeTruthy();
  });

  it("shows the Coming Soon message on tap and never navigates", async () => {
    const view = await renderHub();
    fireEvent.press(view.getByTestId("business-os-coming-soon-customers"));
    expect(view.getByText(COMING_SOON_BODY)).toBeTruthy();
    expect(view.getByText("Got it")).toBeTruthy();
    expect(view.navigation.navigate).not.toHaveBeenCalled();
  });

  it("dismisses the message with Got it", async () => {
    const view = await renderHub();
    fireEvent.press(view.getByTestId("business-os-coming-soon-team"));
    expect(view.getByText(COMING_SOON_BODY)).toBeTruthy();
    fireEvent.press(view.getByTestId("business-os-coming-soon-team-got-it"));
    expect(view.queryByText(COMING_SOON_BODY)).toBeNull();
    expect(view.navigation.navigate).not.toHaveBeenCalled();
  });

  it("uses launch language, never developer language", async () => {
    const view = await renderHub();
    fireEvent.press(view.getByTestId("business-os-coming-soon-customers"));
    for (const forbidden of [/not implemented/i, /disabled/i, /unavailable/i, /error/i, /TODO/]) {
      expect(view.queryByText(forbidden)).toBeNull();
    }
  });
});

describe("live modules keep working", () => {
  it("every routable section still dispatches its exact registry navigation", async () => {
    const view = await renderHub();
    for (const section of businessOsHubSections()) {
      view.navigation.navigate.mockClear();
      fireEvent.press(view.getByLabelText(`${section.label}. ${section.blurb}`));
      const [route, params] = businessOsNavigationArgs(section);
      expect(view.navigation.navigate).toHaveBeenCalledWith(route, params);
    }
  });
});

describe("motion", () => {
  it("starts the ambient pulse after the stagger delay when motion is allowed", async () => {
    await renderHub();
    expect(mockPulseStart).not.toHaveBeenCalled();
    jest.advanceTimersByTime(6 * 340);
    expect(mockPulseStart).toHaveBeenCalled();
  });

  it("Reduce Motion keeps the locked cards fully functional with no decorative animation", async () => {
    mockReducedMotion = true;
    const view = await renderHub();
    jest.advanceTimersByTime(10_000);
    expect(mockPulseStart).not.toHaveBeenCalled();
    fireEvent.press(view.getByTestId("business-os-coming-soon-customers"));
    expect(view.getByText(COMING_SOON_BODY)).toBeTruthy();
    fireEvent.press(view.getByTestId("business-os-coming-soon-customers-got-it"));
    expect(view.queryByText(COMING_SOON_BODY)).toBeNull();
  });
});
