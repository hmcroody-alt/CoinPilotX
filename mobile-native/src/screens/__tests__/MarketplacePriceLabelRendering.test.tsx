/**
 * A listing with no price shows no price line. It does not show a sentence
 * standing in for one.
 *
 * Both marketplace surfaces used to fill an empty `price_label` with prose —
 * the grid card said "Price at checkout", the product page said "Price shown at
 * checkout". Neither is a fallback in the harmless sense. They are claims about
 * a checkout that neither screen can see: a dropship draft carries no price
 * anywhere in the system, so the sentence promised a number that never arrives.
 * Worse, the two screens disagreed with each other about the *same row*, so
 * which lie you got depended on how you reached the product.
 *
 * The server stopped inventing a price on the way out and the web grid stopped
 * inventing one on the way in (`tests/web_parity/` and
 * `tests/marketplace/test_seller_listing_edit.py` pin those two). This file is
 * the same rule on the third and fourth surface, and it exists because when the
 * screens were fixed all 31 existing native marketplace tests passed unchanged
 * — nothing in the suite could see the defect.
 *
 * Both halves are asserted deliberately:
 *
 *   - absent price renders nothing, AND
 *   - a price the seller actually set still renders.
 *
 * Without the second half, deleting the price line entirely would satisfy this
 * file. Each screen is also checked to have rendered *something* real, so
 * "invents no price" cannot be won by a screen that failed to mount.
 *
 * The phrases are written out as literals rather than imported from the screens.
 * A shared constant would let a future edit rename the phrase and keep this
 * suite green while the app still says it.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));

const mockSearchMarketplace = jest.fn();
const mockLoadCachedMarketplace = jest.fn();
jest.mock("../../api/marketplace", () => ({
  ...jest.requireActual("../../api/marketplace"),
  searchMarketplace: (...args: unknown[]) => mockSearchMarketplace(...args),
  loadCachedMarketplace: (...args: unknown[]) => mockLoadCachedMarketplace(...args)
}));
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
// Native playback has nothing to do with what a card prints.
jest.mock("../../components/NativeMediaViewer", () => ({
  mediaViewerItemFromPulseMedia: jest.fn(() => ({})),
  NativeMediaViewer: () => null
}));
jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(async () => null),
  setItem: jest.fn(async () => undefined)
}));

import { MarketplaceProductScreen } from "../MarketplaceProductScreen";
import { MarketplaceScreen } from "../MarketplaceScreen";

/**
 * Every sentence either screen has ever used in place of a price, plus the one
 * the web surfaces used. A screen that invents a *new* phrase is a new bug and
 * will need a new line here; that is the point of listing them rather than
 * pattern-matching, since a regex loose enough to catch the next invention
 * would also catch a legitimately priced row.
 */
const INVENTED = [
  "Price at checkout",
  "Price shown at checkout",
  "Request access",
  "Contact for price",
  "See price at checkout"
];

/**
 * Collapse the hits to plain strings before asserting.
 *
 * `expect(queryByText(phrase)).toBeNull()` is the obvious spelling and it is a
 * trap: on failure Jest serializes the matched host element, and a React Native
 * element tree is deep enough that building that diff exhausts the V8 heap —
 * the run dies with a stack dump instead of naming the phrase it found. A test
 * whose failure mode is a crash cannot tell anyone what broke, so what gets
 * compared here is an array of strings.
 */
function expectInventsNoPrice(queryByText: (text: string) => unknown, where: string) {
  const found = INVENTED.filter((phrase) => queryByText(phrase) !== null);
  expect({ where, invented: found }).toEqual({ where, invented: [] });
}

const UNPRICED = {
  id: 501,
  title: "Imported ceramic mug",
  price_label: "",
  currency: "USD",
  seller_user_id: 12,
  seller_name: "Dana R.",
  category: "Home",
  status: "active",
  approval_status: "approved",
  media: []
};

const PRICED = { ...UNPRICED, id: 502, title: "Oak dining table", price_label: "$220.00" };

const navigation = { navigate: jest.fn(), goBack: jest.fn(), setOptions: jest.fn(), addListener: jest.fn(() => () => undefined) };

function renderProduct(listing: Record<string, unknown>) {
  return render(
    <MarketplaceProductScreen
      navigation={navigation as never}
      route={{ params: { listingId: listing.id, listing, title: listing.title } } as never}
    />
  );
}

async function renderGrid(listing: Record<string, unknown>) {
  mockSearchMarketplace.mockResolvedValue({ items: [listing] });
  const screen = render(<MarketplaceScreen navigation={navigation as never} route={{ params: {} } as never} />);
  // The card only exists once the search settles; asserting an absence before
  // then would pass against an empty grid.
  await waitFor(() => expect(screen.getByText(listing.title as string)).toBeTruthy());
  return screen;
}

beforeEach(() => {
  mockSearchMarketplace.mockReset();
  mockLoadCachedMarketplace.mockReset();
  mockLoadCachedMarketplace.mockResolvedValue([]);
  navigation.navigate.mockReset();
});

describe("the grid card", () => {
  it("prints no price line at all for a listing the seller never priced", async () => {
    const { queryByText, getByText } = await renderGrid(UNPRICED);
    // The card mounted — the absence below is a real absence, not an empty grid.
    expect(getByText("Imported ceramic mug")).toBeTruthy();
    expectInventsNoPrice(queryByText, "marketplace grid card");
  });

  it("still prints a price the seller did set", async () => {
    const { getByText } = await renderGrid(PRICED);
    expect(getByText("$220.00")).toBeTruthy();
  });
});

describe("the product page", () => {
  it("prints no price line at all for a listing the seller never priced", async () => {
    const { queryByText, getByText } = renderProduct(UNPRICED);
    expect(getByText("Imported ceramic mug")).toBeTruthy();
    expectInventsNoPrice(queryByText, "marketplace product page");
  });

  it("still prints a price the seller did set", async () => {
    const { getByText } = renderProduct(PRICED);
    expect(getByText("$220.00")).toBeTruthy();
  });
});

describe("the two surfaces agree about one product", () => {
  /**
   * The specific regression: the grid and the product page rendered *different*
   * invented sentences for the identical row, so tapping a card changed what
   * the app claimed about the price. Pinning both against the same listing
   * object is what makes a one-sided fix fail here.
   */
  it("shows the same price text on the card and on the page", async () => {
    const grid = await renderGrid(PRICED);
    const product = renderProduct(PRICED);
    expect(grid.getByText("$220.00")).toBeTruthy();
    expect(product.getByText("$220.00")).toBeTruthy();
  });

  it("shows no price text on either when there is none to show", async () => {
    const grid = await renderGrid(UNPRICED);
    const product = renderProduct(UNPRICED);
    expectInventsNoPrice(grid.queryByText, "marketplace grid card");
    expectInventsNoPrice(product.queryByText, "marketplace product page");
  });
});
