/**
 * A seller could reach the listing editor but not actually finish an edit: the
 * panel opened blank, `description` was seeded from `short_description`, and the
 * save omitted `short_description` entirely — which the full-replace backend
 * then wiped. This file pins the three properties that make the editor usable:
 * it opens on the requested listing, it opens pre-filled with that listing's
 * real values (price included), and Save sends what the seller typed.
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
const mockInvalidate = jest.fn().mockResolvedValue(undefined);
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn(() => () => undefined),
  invalidateNativeSync: (...args: unknown[]) => mockInvalidate(...args)
}));
jest.mock("../../components/NativeMediaViewer", () => ({
  NativeMediaViewer: () => null,
  mediaViewerItemFromPulseMedia: jest.fn(() => null)
}));

const mockSnapshot = jest.fn();
const mockCommercialTerms = jest.fn();
const mockUpdate = jest.fn();
jest.mock("../../api/marketplace", () => ({
  ...jest.requireActual("../../api/marketplace"),
  loadSellerStoreSnapshot: (...args: unknown[]) => mockSnapshot(...args),
  getMarketplaceCommercialTerms: (...args: unknown[]) => mockCommercialTerms(...args),
  loadCachedSellerStore: jest.fn().mockResolvedValue(null),
  updateMarketplaceSellerListing: (...args: unknown[]) => mockUpdate(...args)
}));

import { SellerStoreScreen } from "../SellerStoreScreen";

const LISTING = {
  id: 42,
  title: "Roasted Beans",
  short_description: "One kilo bag",
  description: "Single origin, roasted weekly.",
  category: "Food",
  price_label: "$40.00",
  quantity: 5,
  status: "active"
};

const OTHER_LISTING = { ...LISTING, id: 43, title: "Cold Brew Kit", price_label: "$12.00" };

beforeEach(() => {
  jest.clearAllMocks();
  // `live` is what makes the screen treat the payload as authoritative; without
  // it the screen falls back to cache and renders an empty inventory.
  mockSnapshot.mockResolvedValue({ live: true, listings: [LISTING, OTHER_LISTING], orders: [] });
  mockCommercialTerms.mockResolvedValue({ terms: { acceptance: null } });
  mockUpdate.mockResolvedValue({ listing: { ...LISTING, price_label: "$55.00" }, message: "Listing updated." });
});

async function renderEditor(params: Record<string, unknown>) {
  const navigation = { navigate: jest.fn() };
  const view = render(<SellerStoreScreen route={{ params: params as never }} navigation={navigation} />);
  await waitFor(() => expect(mockSnapshot).toHaveBeenCalled());
  await waitFor(() => expect(mockCommercialTerms).toHaveBeenCalled());
  return { ...view, navigation };
}

describe("seller listing editor", () => {
  it("opens the listing the caller asked for, pre-filled with its stored values", async () => {
    const view = await renderEditor({ mode: "create", listingId: 42 });

    await waitFor(() => expect(view.getByText("Edit listing #42")).toBeTruthy());
    expect(view.getByDisplayValue("Roasted Beans")).toBeTruthy();
    expect(view.getByDisplayValue("One kilo bag")).toBeTruthy();
    // Seeded from `description`, not from `short_description` — the old
    // fallback made the two fields identical the moment the editor opened.
    expect(view.getByDisplayValue("Single origin, roasted weekly.")).toBeTruthy();
    expect(view.getByDisplayValue("$40.00")).toBeTruthy();
    expect(view.getByDisplayValue("5")).toBeTruthy();
  });

  it("sends the edited price and keeps the fields the seller did not touch", async () => {
    const view = await renderEditor({ mode: "create", listingId: 42 });
    await waitFor(() => expect(view.getByText("Edit listing #42")).toBeTruthy());

    fireEvent.changeText(view.getByDisplayValue("$40.00"), "$55.00");
    await act(async () => {
      fireEvent.press(view.getByText("Save and Review"));
    });

    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    expect(mockUpdate).toHaveBeenCalledWith(42, {
      title: "Roasted Beans",
      short_description: "One kilo bag",
      description: "Single origin, roasted weekly.",
      category: "Food",
      price_label: "$55.00",
      quantity: 5
    });
  });

  it("tells the buyer-facing surfaces the listing moved", async () => {
    const view = await renderEditor({ mode: "create", listingId: 42 });
    await waitFor(() => expect(view.getByText("Edit listing #42")).toBeTruthy());

    await act(async () => {
      fireEvent.press(view.getByText("Save and Review"));
    });

    // A price the Marketplace tab never refetches is a price only the seller
    // can see, which is the failure this invalidation exists to prevent.
    await waitFor(() => expect(mockInvalidate).toHaveBeenCalled());
    expect(mockInvalidate.mock.calls[0][0]).toEqual(expect.arrayContaining(["marketplace", "seller_inventory"]));
  });

  it("opens a later listing rather than defaulting to the first one", async () => {
    const view = await renderEditor({ mode: "create", listingId: 43 });

    await waitFor(() => expect(view.getByText("Edit listing #43")).toBeTruthy());
    expect(view.getByDisplayValue("Cold Brew Kit")).toBeTruthy();
    expect(view.getByDisplayValue("$12.00")).toBeTruthy();
  });
});
