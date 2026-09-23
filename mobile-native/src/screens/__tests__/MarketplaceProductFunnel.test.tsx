/**
 * §18: the half of the funnel that happens after the shopper leaves the card.
 *
 * Impression and click are easy — they fire on the commerce card itself, which
 * is holding the placement. Everything past the tap happens here, on a screen
 * reached by four commerce surfaces *and* by search, deep links, the profile and
 * the marketplace grid, handed nothing but a listing id. The whole design rests
 * on one asymmetry that these tests exist to pin:
 *
 *   an attributed arrival reports, and an organic arrival reports nothing.
 *
 * The second half is the one that can rot silently. If `commerceAttributionFor`
 * ever started answering with the last placement anyone tapped — or if someone
 * "simplified" the null guard away — every organic sale in the catalogue would
 * be credited to discovery, the dashboards would look *better*, and no test
 * that only checked the happy path would notice. So each emit below is asserted
 * twice: once attributed, once organic.
 *
 * `purchase` is deliberately absent. It needs the payment-success path, which is
 * a §21-locked surface this work must not touch; it is reported as unwired
 * rather than faked here.
 */

import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
jest.mock("../../api/marketplaceCommerce", () => ({
  addToCart: jest.fn(async () => ({ lines: [], badgeCount: 1 })),
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
 * Only the beacon is mocked, not the attribution store. The store is the part
 * with the interesting logic, and a test that mocked it would be asserting its
 * own fixture rather than the screen's use of the real thing.
 */
const mockRecordCommerceEngagement = jest.fn(async (..._args: unknown[]) => true);
jest.mock("../../api/commerceDiscovery", () => ({
  __esModule: true,
  recordCommerceEngagement: (...args: unknown[]) => mockRecordCommerceEngagement(...args)
}));
const recordCommerceEngagement = mockRecordCommerceEngagement;

import { addToCart } from "../../api/marketplaceCommerce";
import { __resetCommerceAttribution, attributeCommerceClick } from "../../commerce/attribution";
import { MarketplaceProductScreen } from "../MarketplaceProductScreen";

const LISTING_ID = 701;

const LISTING = {
  id: LISTING_ID,
  title: "Walnut side table",
  price_label: "$27.00",
  price_minor: 2700,
  currency: "USD",
  seller_user_id: 12,
  seller_name: "Dana R.",
  category: "Home",
  status: "active",
  approval_status: "approved",
  quantity: 8,
  media: []
};

const navigation = {
  navigate: jest.fn(),
  goBack: jest.fn(),
  setOptions: jest.fn(),
  addListener: jest.fn(() => () => undefined)
};

/** A click on a commerce card, exactly as the four surfaces record one. */
function arriveFromACommerceCard(listingId = LISTING_ID) {
  attributeCommerceClick({
    placementId: "plc-9",
    impressionToken: "tok-9",
    surface: "feed",
    slot: 0,
    expiresAt: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
    promotionClass: "organic",
    labelKey: "commerce.label.recommended",
    reason: "trending",
    rankingVersion: "v1",
    priceMinor: 2700,
    priceCurrency: "USD",
    product: {
      listingId,
      title: LISTING.title,
      priceLabel: "$27.00",
      coverImageUrl: "",
      sellerUserId: 12,
      sellerStoreName: "Dana R.",
      category: "Home",
      rating: 0,
      ratingCount: 0
    }
  } as never);
}

function renderProduct() {
  return render(
    <MarketplaceProductScreen
      navigation={navigation as never}
      route={{ params: { listingId: LISTING_ID, listing: LISTING, title: LISTING.title } } as never}
    />
  );
}

/** Every emit for one action, as `[identity, action, extra]` tuples. */
function emitsOf(action: string): unknown[][] {
  return recordCommerceEngagement.mock.calls.filter((call) => call[1] === action);
}

beforeEach(() => {
  __resetCommerceAttribution();
  recordCommerceEngagement.mockClear();
  navigation.navigate.mockClear();
  (addToCart as jest.Mock).mockClear();
});

describe("product_view", () => {
  it("reports the view against the placement that sent the shopper here", async () => {
    arriveFromACommerceCard();
    renderProduct();
    await waitFor(() => expect(emitsOf("product_view")).toHaveLength(1));
    expect(emitsOf("product_view")[0][0]).toEqual({
      placementId: "plc-9",
      impressionToken: "tok-9"
    });
  });

  it("reports nothing at all for an organic arrival", async () => {
    // Search, a deep link, the grid, a share. Crediting these to discovery is
    // the failure that makes an attribution system worse than none, and it is
    // invisible in the data it produces — the numbers just look good.
    renderProduct();
    await waitFor(() => expect(recordCommerceEngagement).not.toHaveBeenCalled());
  });

  it("does not report a view for a different product's attribution", async () => {
    // The store is keyed on the listing precisely so that a click on one product
    // cannot claim the next product the shopper happens to open.
    arriveFromACommerceCard(LISTING_ID + 1);
    renderProduct();
    await waitFor(() => expect(recordCommerceEngagement).not.toHaveBeenCalled());
  });

  it("does not restate the view when the screen re-renders", async () => {
    arriveFromACommerceCard();
    const { getByLabelText } = renderProduct();
    await waitFor(() => expect(emitsOf("product_view")).toHaveLength(1));
    fireEvent.press(getByLabelText("Increase quantity"));
    fireEvent.press(getByLabelText("Increase quantity"));
    await waitFor(() => expect(emitsOf("product_view")).toHaveLength(1));
  });
});

describe("add_to_cart", () => {
  it("reports the line total, not the unit price", async () => {
    // A discovery-driven basket of three must not be reported as a basket of
    // one: §18's value is what the funnel is measured in.
    arriveFromACommerceCard();
    const { getByLabelText } = renderProduct();
    fireEvent.press(getByLabelText("Increase quantity"));
    fireEvent.press(getByLabelText("Add 2 to cart"));
    await waitFor(() => expect(emitsOf("add_to_cart")).toHaveLength(1));
    expect(emitsOf("add_to_cart")[0][2]).toEqual({ valueMinor: 5400, currency: "USD" });
  });

  it("reports nothing for an organic add to cart", async () => {
    const { getByLabelText } = renderProduct();
    fireEvent.press(getByLabelText("Add 1 to cart"));
    await waitFor(() => expect(addToCart).toHaveBeenCalled());
    expect(emitsOf("add_to_cart")).toHaveLength(0);
  });

  it("does not report an add to cart that failed", async () => {
    // An emit before the await would count every network error as a conversion.
    arriveFromACommerceCard();
    (addToCart as jest.Mock).mockRejectedValueOnce(new Error("nope"));
    const { getByLabelText, findByText } = renderProduct();
    fireEvent.press(getByLabelText("Add 1 to cart"));
    await findByText(/could not be added/i);
    expect(emitsOf("add_to_cart")).toHaveLength(0);
  });

  it("does not let a failed beacon break the buyer's action", async () => {
    // The screen must keep working when the analytics endpoint does not. This
    // is the rule that makes a fire-and-forget emit safe to put in a buy flow.
    arriveFromACommerceCard();
    recordCommerceEngagement.mockRejectedValue(new Error("beacon down"));
    try {
      const { getByLabelText, findByText } = renderProduct();
      fireEvent.press(getByLabelText("Add 1 to cart"));
      await findByText(/Added to cart/i);
    } finally {
      recordCommerceEngagement.mockResolvedValue(true);
    }
  });
});

describe("checkout_started", () => {
  it("reports the checkout the buyer is entering, with the line total", async () => {
    arriveFromACommerceCard();
    const { getByLabelText } = renderProduct();
    fireEvent.press(getByLabelText("Increase quantity"));
    fireEvent.press(getByLabelText("Buy now"));
    await waitFor(() => expect(emitsOf("checkout_started")).toHaveLength(1));
    expect(emitsOf("checkout_started")[0][2]).toEqual({ valueMinor: 5400, currency: "USD" });
  });

  it("still navigates to checkout", async () => {
    // The emit sits on the path *into* a §21-locked screen. If it could ever
    // stand between the buyer and checkout, it would be the wrong design no
    // matter how good the analytics were.
    arriveFromACommerceCard();
    const { getByLabelText } = renderProduct();
    fireEvent.press(getByLabelText("Buy now"));
    await waitFor(() => expect(navigation.navigate).toHaveBeenCalledWith(
      "MarketplaceCheckout",
      expect.objectContaining({ mode: "buy_now", listingId: LISTING_ID })
    ));
  });

  it("navigates for an organic buyer too, reporting nothing", async () => {
    const { getByLabelText } = renderProduct();
    fireEvent.press(getByLabelText("Buy now"));
    await waitFor(() => expect(navigation.navigate).toHaveBeenCalled());
    expect(emitsOf("checkout_started")).toHaveLength(0);
  });
});

describe("what is deliberately not emitted", () => {
  it("never reports a purchase from this screen", async () => {
    // Pinned so the omission stays a decision rather than becoming an oversight
    // someone closes by guessing. A purchase is only true after payment
    // succeeds, and that path is locked; a `purchase` fired on the way into
    // checkout would report abandoned carts as revenue.
    arriveFromACommerceCard();
    const { getByLabelText } = renderProduct();
    fireEvent.press(getByLabelText("Buy now"));
    await waitFor(() => expect(navigation.navigate).toHaveBeenCalled());
    expect(emitsOf("purchase")).toHaveLength(0);
  });
});
