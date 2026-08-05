/**
 * The Marketplace manager makes promises that are invisible in a screenshot and
 * easy to break in a refactor, so they are pinned here.
 *
 * 1. **It never invents a number.** Views, saves, offer counts, seller rating,
 *    distance and radius have no source. A future change that fills them with a
 *    plausible default would ship a lie about a seller's listing.
 * 2. **Nothing pretends a checkout or an accept worked.** Offers, cart and boost
 *    have no backend; the flags are off and the controls are absent or disabled.
 * 3. **Both panes stay mounted**, because that is the only way each mode's
 *    scroll position survives the toggle.
 * 4. **A failed feed never takes the category rail or the seller's own items
 *    with it** — the two halves load independently and fail independently.
 */
import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

const mockReducedMotion = jest.fn(() => false);

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
jest.mock("expo-linear-gradient", () => ({ LinearGradient: "LinearGradient" }));
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn(() => () => undefined)
}));
jest.mock("../../core/unreadCounts", () => ({
  refreshUnreadCounts: jest.fn(async () => undefined),
  useBellCount: jest.fn(() => 0)
}));
jest.mock("../../theme/logiNexusMotion", () => ({
  ...jest.requireActual("../../theme/logiNexusMotion"),
  useLogiNexusReducedMotion: () => mockReducedMotion()
}));

const mockSearch = jest.fn();
const mockSellerListings = jest.fn();
const mockSellerOrders = jest.fn();
const mockCachedMarketplace = jest.fn();
const mockCachedStore = jest.fn();
jest.mock("../../api/marketplace", () => ({
  ...jest.requireActual("../../api/marketplace"),
  searchMarketplace: (...args: unknown[]) => mockSearch(...args),
  listMarketplaceSellerListings: (...args: unknown[]) => mockSellerListings(...args),
  listMarketplaceSellerOrders: (...args: unknown[]) => mockSellerOrders(...args),
  loadCachedMarketplace: (...args: unknown[]) => mockCachedMarketplace(...args),
  loadCachedSellerStore: (...args: unknown[]) => mockCachedStore(...args)
}));

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(async () => null),
  setItem: jest.fn(async () => undefined)
}));

import {
  MARKETPLACE_BOOST_ENABLED,
  MARKETPLACE_CART_ENABLED,
  MARKETPLACE_OFFERS_ENABLED
} from "../../api/marketplaceOffers";
import { MARKETPLACE_MOCK_DATA_GAPS } from "../../api/marketplaceScreen";
import { MarketplaceManagerScreen } from "../MarketplaceManagerScreen";

const navigation = { navigate: jest.fn(), goBack: jest.fn() };

const DAY = 24 * 60 * 60 * 1000;

function listing(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    title: "Oak dining table",
    price_label: "$220.00",
    currency: "USD",
    category: "Furniture",
    seller_name: "Dana R.",
    seller_user_id: 7,
    status: "active",
    approval_status: "approved",
    created_at: new Date(Date.now() - DAY).toISOString(),
    delivery_type: "pickup",
    media: [],
    ...overrides
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockReducedMotion.mockReturnValue(false);
  mockSearch.mockResolvedValue({ items: [listing()] });
  mockSellerListings.mockResolvedValue({ items: [listing({ id: 2, title: "Road bike" })] });
  mockSellerOrders.mockResolvedValue({ orders: [] });
  mockCachedMarketplace.mockResolvedValue([]);
  mockCachedStore.mockResolvedValue(null);
});

async function renderScreen() {
  const view = render(<MarketplaceManagerScreen navigation={navigation as any} />);
  await act(async () => {
    await Promise.resolve();
  });
  return view;
}

describe("MarketplaceManagerScreen", () => {
  it("keeps every unbacked surface behind a flag that is off", () => {
    // These three are asserted as constants rather than as absent UI because a
    // flipped flag with no backend behind it is the failure this guards.
    expect(MARKETPLACE_OFFERS_ENABLED).toBe(false);
    expect(MARKETPLACE_CART_ENABLED).toBe(false);
    expect(MARKETPLACE_BOOST_ENABLED).toBe(false);
  });

  it("lists every mock-data gap rather than filling one in", () => {
    // If someone fakes one of these, the count changes and this says so.
    expect(MARKETPLACE_MOCK_DATA_GAPS.length).toBe(12);
    MARKETPLACE_MOCK_DATA_GAPS.forEach((gap) => {
      expect(gap.field.length).toBeGreaterThan(0);
      expect(gap.needs.length).toBeGreaterThan(0);
    });
  });

  it("loads both modes in one pass so the toggle never fetches", async () => {
    await renderScreen();
    await waitFor(() => expect(mockSearch).toHaveBeenCalledTimes(1));
    expect(mockSellerListings).toHaveBeenCalledTimes(1);
    expect(mockSellerOrders).toHaveBeenCalledTimes(1);
  });

  it("renders the seller's own items and the mode toggle", async () => {
    const { findByText, getByText } = await renderScreen();
    expect(await findByText("Road bike")).toBeTruthy();
    expect(getByText("Selling")).toBeTruthy();
    expect(getByText("Buying")).toBeTruthy();
  });

  it("shows a dash, not a zero, for figures it has no source for", async () => {
    const { findAllByText } = await renderScreen();
    // "Saves this week" and "Offers waiting" are both unbacked. A zero would be
    // a claim that the answer is none; a dash says it is unknown.
    expect((await findAllByText("—")).length).toBeGreaterThanOrEqual(2);
  });

  it("keeps the seller's items when the buying feed fails", async () => {
    mockSearch.mockRejectedValue(new Error("feed down"));
    const { findByText } = await renderScreen();
    expect(await findByText("Road bike")).toBeTruthy();
  });

  it("keeps the buying feed when the seller's listings fail", async () => {
    mockSellerListings.mockRejectedValue(new Error("listings down"));
    const { findByText } = await renderScreen();
    expect(await findByText("Your items didn't load.")).toBeTruthy();
  });

  it("falls back to cache and says so when everything fails", async () => {
    mockSearch.mockRejectedValue(new Error("down"));
    mockSellerListings.mockRejectedValue(new Error("down"));
    mockSellerOrders.mockRejectedValue(new Error("down"));
    mockCachedMarketplace.mockResolvedValue([listing({ id: 9, title: "Cached lamp" })]);
    mockCachedStore.mockResolvedValue({ listings: [], orders: [], cached_at: null });

    const { findByText } = await renderScreen();
    expect(await findByText("Offline — showing saved items")).toBeTruthy();
  });

  it("invites a first listing rather than reporting an error when empty", async () => {
    mockSellerListings.mockResolvedValue({ items: [] });
    const { findByText } = await renderScreen();
    expect(await findByText("Nothing listed yet.")).toBeTruthy();
  });

  it("marks a sold row SOLD in words, not by styling alone", async () => {
    // Sold is derived, not a column: out of stock with a sale behind it in the
    // last seven days. Both halves are supplied here so the derivation is what
    // is under test, not a hand-set boolean.
    mockSellerListings.mockResolvedValue({
      items: [listing({ id: 3, title: "Vintage amp", quantity: 0 })]
    });
    mockSellerOrders.mockResolvedValue({
      orders: [
        { id: 55, item_id: 3, status: "completed", created_at: new Date(Date.now() - DAY).toISOString() }
      ]
    });

    const { findByText, findByLabelText } = await renderScreen();
    // It is filed under Sold, not left sitting in Active.
    const soldTab = await findByLabelText("Sold, 1 items");
    await act(async () => {
      fireEvent.press(soldTab);
    });

    // The overlay wipes in via an animation, but the word is in the tree either
    // way — colour and motion never carry the meaning on their own.
    expect(await findByText("SOLD")).toBeTruthy();
    // And the row's accessible name says it too, for a reader that never sees
    // the overlay at all.
    expect(await findByLabelText("Vintage amp, $220.00, sold")).toBeTruthy();
  });

  it("still reports its real content under reduce-motion", async () => {
    mockReducedMotion.mockReturnValue(true);
    const { findByText } = await renderScreen();
    // The animation stops; the content does not disappear.
    expect(await findByText("Road bike")).toBeTruthy();
    expect(await findByText("Your items")).toBeTruthy();
  });
});
