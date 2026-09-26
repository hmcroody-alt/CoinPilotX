/**
 * Arriving at the product page with an id and nothing else.
 *
 * Four call sites navigate to `MarketplaceProduct` carrying only a `listingId`:
 * the commerce discovery surfaces — feed strip, reels chip, messenger strip and
 * marketplace shelves. (The Page product block and the seller store both hand
 * over the whole listing, so neither was ever affected.) The screen used to
 * render only from a `listing` snapshot in the route params, so all four showed
 * "This item is no longer available" for listings that were on sale. That
 * is the defect these cases pin: a tap on a product has to reach the product.
 *
 * The four states are asserted separately because the interesting property is
 * that they are mutually exclusive. In particular a *failed request* must not
 * render as an empty shelf — "the seller withdrew this" and "your connection
 * dropped" are different facts, and only one of them is true when a fetch
 * rejects. A screen that collapsed them would pass a test that only looked for
 * "some non-product screen rendered", so each case also asserts the absence of
 * the other's marker.
 */

import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

const INSETS = { top: 0, bottom: 0, left: 0, right: 0 };

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => INSETS
}));
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
jest.mock("../../api/marketplaceCommerce", () => ({
  addToCart: jest.fn(async () => ({ lines: [], badgeCount: 0 })),
  fetchCart: jest.fn(async () => ({ lines: [], badgeCount: 0 }))
}));
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn(() => () => undefined)
}));
jest.mock("../../navigation/BottomNavVisibility", () => ({
  useBottomNavSurface: () => ({ handlers: {}, contentPadding: {} })
}));
jest.mock("../../components/NativeMediaViewer", () => ({
  mediaViewerItemFromPulseMedia: jest.fn(() => ({})),
  NativeMediaViewer: () => null
}));
jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(async () => null),
  setItem: jest.fn(async () => undefined)
}));

/**
 * Only the read-one call is faked. Everything else in the module is real, so
 * the payload still travels through `normalizeMarketplaceListings` and the
 * presentation helpers the screen actually uses — a hand-shaped mock of the
 * whole module would let a field-name drift pass.
 */
const mockFetchListing = jest.fn();
jest.mock("../../api/marketplace", () => ({
  ...jest.requireActual("../../api/marketplace"),
  fetchMarketplaceListing: (...args: unknown[]) => mockFetchListing(...args),
  reportMarketplaceListing: jest.fn(async () => ({ ok: true })),
  startMarketplaceSellerChat: jest.fn(async () => ({ ok: true }))
}));

import { MarketplaceProductScreen } from "../MarketplaceProductScreen";

const LISTING = {
  id: 8801,
  listing_id: 8801,
  title: "Walnut side table",
  price_label: "$27.00",
  currency: "USD",
  seller_user_id: 12,
  seller_store_name: "Dana's Workshop",
  category: "Home",
  status: "published",
  approval_status: "approved",
  quantity: 4,
  media: []
};

const navigation = {
  navigate: jest.fn(),
  goBack: jest.fn(),
  setOptions: jest.fn(),
  addListener: jest.fn(() => () => undefined)
};

function renderProduct(params: Record<string, unknown>) {
  return render(<MarketplaceProductScreen navigation={navigation as never} route={{ params } as never} />);
}

beforeEach(() => {
  mockFetchListing.mockReset();
  navigation.goBack.mockClear();
});

describe("a product opened with only an id", () => {
  it("fetches the listing and renders the product", async () => {
    mockFetchListing.mockResolvedValue(LISTING);
    const { getByText, queryByTestId } = renderProduct({ listingId: LISTING.id, title: LISTING.title });

    await waitFor(() => expect(getByText("Walnut side table")).toBeTruthy());
    expect(mockFetchListing).toHaveBeenCalledWith(LISTING.id);
    // The headstone this whole change exists to stop rendering.
    expect(queryByTestId("marketplace-product-unavailable")).toBeNull();
    expect(getByText("$27.00")).toBeTruthy();
  });

  it("shows a loading state while the read is in flight, not the headstone", async () => {
    let release: (value: unknown) => void = () => undefined;
    mockFetchListing.mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      })
    );
    const { getByTestId, queryByTestId } = renderProduct({ listingId: LISTING.id });

    expect(getByTestId("marketplace-product-loading")).toBeTruthy();
    // A pending request is the state most likely to be mistaken for an empty
    // one, because both have no listing in hand.
    expect(queryByTestId("marketplace-product-unavailable")).toBeNull();
    expect(queryByTestId("marketplace-product-error")).toBeNull();

    release(LISTING);
    await waitFor(() => expect(queryByTestId("marketplace-product-loading")).toBeNull());
  });

  it("does not fetch when the caller already handed over the listing", () => {
    const { getByText } = renderProduct({ listingId: LISTING.id, listing: LISTING, title: LISTING.title });
    // The grid's instant open. A spinner here would be a regression in front of
    // data the caller already holds.
    expect(getByText("Walnut side table")).toBeTruthy();
    expect(mockFetchListing).not.toHaveBeenCalled();
  });
});

describe("the two ways a product page can have no product", () => {
  it("renders the unavailable state when the server says the listing is not visible", async () => {
    // `fetchMarketplaceListing` resolves null for exactly this — a missing
    // listing, a draft, a paused one, a suspended seller — and throws for
    // anything else.
    mockFetchListing.mockResolvedValue(null);
    const { getByTestId, queryByTestId } = renderProduct({ listingId: 9999 });

    await waitFor(() => expect(getByTestId("marketplace-product-unavailable")).toBeTruthy());
    expect(queryByTestId("marketplace-product-error")).toBeNull();
    expect(queryByTestId("marketplace-product-loading")).toBeNull();
  });

  it("renders a retryable error, never the headstone, when the request fails", async () => {
    mockFetchListing.mockRejectedValue(new Error("network down"));
    const { getByTestId, queryByTestId } = renderProduct({ listingId: LISTING.id });

    await waitFor(() => expect(getByTestId("marketplace-product-error")).toBeTruthy());
    // The assertion that matters: a dropped connection must not tell the buyer
    // the seller removed the item.
    expect(queryByTestId("marketplace-product-unavailable")).toBeNull();
    expect(getByTestId("marketplace-product-retry")).toBeTruthy();
  });

  it("re-reads the listing when the buyer retries", async () => {
    mockFetchListing.mockRejectedValueOnce(new Error("network down")).mockResolvedValueOnce(LISTING);
    const { getByTestId, getByText, queryByTestId } = renderProduct({ listingId: LISTING.id });

    await waitFor(() => expect(getByTestId("marketplace-product-retry")).toBeTruthy());
    fireEvent.press(getByTestId("marketplace-product-retry"));

    await waitFor(() => expect(getByText("Walnut side table")).toBeTruthy());
    expect(mockFetchListing).toHaveBeenCalledTimes(2);
    expect(queryByTestId("marketplace-product-error")).toBeNull();
  });

  it("does not attempt a read for a route carrying no id at all", () => {
    const { getByTestId } = renderProduct({});
    expect(getByTestId("marketplace-product-unavailable")).toBeTruthy();
    expect(mockFetchListing).not.toHaveBeenCalled();
  });
});
