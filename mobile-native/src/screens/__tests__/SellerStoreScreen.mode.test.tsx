/**
 * `SellerStore` accepted a `mode` param that was read once and never used, so
 * Business OS's Store, Orders, Payments and Business Profile links all rendered
 * the same undifferentiated wall of panels. Four links to one screen is not four
 * sections — it is one section advertised four times.
 *
 * `sellerStoreMode.ts` holds the policy and is unit-tested on its own. This file
 * asserts the screen honours it: that the panels a mode excludes are genuinely
 * absent from the tree, not merely styled away, and that the heading changes so
 * the user can tell where they landed.
 */
import React from "react";
import { render, waitFor } from "@testing-library/react-native";

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
jest.mock("../../components/NativeMediaViewer", () => ({
  NativeMediaViewer: () => null,
  mediaViewerItemFromPulseMedia: jest.fn(() => null)
}));

const mockSnapshot = jest.fn();
const mockCommercialTerms = jest.fn();
jest.mock("../../api/marketplace", () => ({
  ...jest.requireActual("../../api/marketplace"),
  loadSellerStoreSnapshot: (...args: unknown[]) => mockSnapshot(...args),
  getMarketplaceCommercialTerms: (...args: unknown[]) => mockCommercialTerms(...args),
  loadCachedSellerStore: jest.fn().mockResolvedValue(null)
}));

import { sellerStoreHeading } from "../../navigation/sellerStoreMode";
import { SellerStoreScreen } from "../SellerStoreScreen";

const LISTING = {
  id: 11,
  title: "Test listing",
  description: "A listing",
  category: "Education",
  price_label: "$10",
  quantity: 2,
  status: "active"
};

const ORDER = {
  id: 77,
  status: "paid",
  total_label: "$10",
  buyer_name: "Buyer One"
};

beforeEach(() => {
  jest.clearAllMocks();
  mockSnapshot.mockResolvedValue({ listings: [LISTING], orders: [ORDER] });
  mockCommercialTerms.mockResolvedValue({ terms: { acceptance: null } });
});

async function renderMode(mode?: string) {
  const navigation = { navigate: jest.fn() };
  const view = render(<SellerStoreScreen route={{ params: { mode } as never }} navigation={navigation} />);
  await waitFor(() => expect(mockSnapshot).toHaveBeenCalled());
  await waitFor(() => expect(mockCommercialTerms).toHaveBeenCalled());
  return { ...view, navigation };
}

/** The section title each panel renders, used to read the tree back. */
const PANEL_TITLES = {
  application: "Merchant application",
  listings: "Listing management",
  inventory: "Seller inventory",
  media: "Product media gallery",
  orders: "Orders and payouts",
  trust: "Trust and eligibility"
};

describe("SellerStore mode wiring", () => {
  it("gives each Business OS entry point its own heading", async () => {
    const seen: string[] = [];
    for (const mode of ["dashboard", "orders", "payouts", "profile"]) {
      const view = await renderMode(mode);
      const title = sellerStoreHeading(mode).title;
      expect(view.getAllByText(title).length).toBeGreaterThan(0);
      seen.push(title);
      view.unmount();
    }
    // Four links that render four identical headings would be four links to one
    // screen, which is the defect this wiring exists to fix.
    expect(new Set(seen).size).toBe(seen.length);
  });

  it("shows orders in Orders mode and hides them in Store mode", async () => {
    const orders = await renderMode("orders");
    expect(orders.getByText(PANEL_TITLES.orders)).toBeTruthy();
    orders.unmount();

    const store = await renderMode("dashboard");
    expect(store.queryByText(PANEL_TITLES.orders)).toBeNull();
    expect(store.queryByText("Buyer One")).toBeNull();
    store.unmount();
  });

  it("shows inventory editing only where the owner manages the store", async () => {
    const store = await renderMode("dashboard");
    expect(store.getByText(PANEL_TITLES.inventory)).toBeTruthy();
    store.unmount();

    const payments = await renderMode("payouts");
    expect(payments.queryByText(PANEL_TITLES.inventory)).toBeNull();
    payments.unmount();
  });

  it("keeps the original everything-at-once view for callers with no mode", async () => {
    const overview = await renderMode(undefined);
    Object.values(PANEL_TITLES).forEach((title) => {
      expect(overview.getAllByText(title).length).toBeGreaterThan(0);
    });
    overview.unmount();
  });

  it("renders a usable screen for every mode rather than an empty one", async () => {
    for (const mode of ["overview", "dashboard", "apply", "profile", "create", "payouts", "orders", "not-a-mode"]) {
      const view = await renderMode(mode);
      // Every mode's panel set includes the hero, so a mode that fell through
      // the policy and rendered nothing would surface here as a blank screen
      // rather than shipping to users.
      const rendered = Object.values(PANEL_TITLES).filter((title) => view.queryAllByText(title).length > 0);
      expect(rendered.length).toBeGreaterThan(0);
      view.unmount();
    }
  });
});
