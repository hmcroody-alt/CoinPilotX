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

jest.mock("../../api/marketplaceCommerce", () => ({
  addToCart: jest.fn(async () => ({ lines: [], badgeCount: 0 })),
  actOnOffer: jest.fn(async () => ({})),
  counterOffer: jest.fn(async () => ({ closed: {}, counter: {} })),
  fetchCart: jest.fn(async () => ({ lines: [], badgeCount: 0 })),
  fetchOffers: jest.fn(async () => [])
}));

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(async () => null),
  setItem: jest.fn(async () => undefined)
}));

/**
 * The seller verdict is an external dependency of this screen, and a stubborn
 * one to supply for real: `useSellerAccess` calls `useFocusEffect`, which
 * demands a navigation context this suite has never had — it renders the screen
 * bare with a hand-rolled `navigation` object. Wrapping all 12 existing tests in
 * a NavigationContainer to satisfy one hook would quietly change what they are
 * exercising.
 *
 * So it is mocked, defaulting to an approved seller: the state in which this
 * file's actual subject — the two panes — is on screen at all. The gate is not
 * thereby assumed away; the `selling behind the seller gate` block below drives
 * this mock through the refusing states rather than trusting the default.
 */
const mockUseSellerAccess = jest.fn();
const mockRefreshSellerAccess = jest.fn();
jest.mock("../../marketplace/useSellerAccess", () => ({
  useSellerAccess: () => mockUseSellerAccess()
}));

import {
  MARKETPLACE_BOOST_ENABLED,
  MARKETPLACE_CART_ENABLED,
  MARKETPLACE_OFFERS_ENABLED
} from "../../api/marketplaceOffers";
import { MARKETPLACE_MOCK_DATA_GAPS } from "../../api/marketplaceScreen";
import { DENIED_SELLER_ACCESS, parseSellerAccessState } from "../../api/sellerAccess";
import { MarketplaceManagerScreen } from "../MarketplaceManagerScreen";

const navigation = { navigate: jest.fn(), goBack: jest.fn() };

function sellerAccess(status: string) {
  return {
    state: parseSellerAccessState({ seller_application_status: status }),
    loading: false,
    failed: false,
    stale: false,
    unsupported: false,
    refresh: mockRefreshSellerAccess
  };
}

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
  mockUseSellerAccess.mockReturnValue(sellerAccess("APPROVED"));
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
    // Asserted as constants rather than as absent UI because a flipped flag
    // with no backend behind it is the failure this guards. Offers and cart
    // are now ON — their backends are `services/marketplace_offers_routes.py`
    // and `services/marketplace_cart_routes.py`, registered in bot.py. Boost
    // still has no backend and must stay off.
    expect(MARKETPLACE_OFFERS_ENABLED).toBe(true);
    expect(MARKETPLACE_CART_ENABLED).toBe(true);
    expect(MARKETPLACE_BOOST_ENABLED).toBe(false);
  });

  it("lists every mock-data gap rather than filling one in", () => {
    // If someone fakes one of these, the count changes and this says so.
    // 12 → 10 when offers and cart gained real backends (Aug 2026 mission).
    expect(MARKETPLACE_MOCK_DATA_GAPS.length).toBe(10);
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

  it("shows honest words for every metric, and never a dash", async () => {
    const { findByLabelText, findByText, queryAllByText } = await renderScreen();
    // Offers has a real backend now: none waiting is a zero — a figure, good
    // news, not an unknown.
    expect(await findByLabelText("Offers waiting: 0")).toBeTruthy();
    // Saves still has no source: the unknown is named, not dashed and not
    // passed off as a zero.
    expect(await findByText("Not measured yet")).toBeTruthy();
    expect(queryAllByText("—").length).toBe(0);
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
    const { findByText, queryByText } = await renderScreen();
    expect(await findByText("Nothing listed yet.")).toBeTruthy();
    // No stored city, so the copy may not promise "buyers nearby" — it offers
    // the control that would make the promise true instead.
    expect(queryByText(/Buyers nearby/)).toBeNull();
    expect(await findByText("Set Marketplace location")).toBeTruthy();
  });

  it("files a reserved listing under a Reserved tab that exists because of it", async () => {
    mockSellerListings.mockResolvedValue({
      items: [
        listing({ id: 2, title: "Road bike" }),
        listing({ id: 4, title: "Held chair", status: "reserved" })
      ]
    });
    const { findByLabelText, findByText, queryByText } = await renderScreen();
    const reservedTab = await findByLabelText("Reserved, 1 items");
    await act(async () => {
      fireEvent.press(reservedTab);
    });
    expect(await findByText("Held chair")).toBeTruthy();
    // Tabs whose state never occurs do not render at all — no permanent
    // "Removed 0" advertising moderation trouble the seller has never had.
    expect(queryByText(/Removed/)).toBeNull();
    expect(queryByText(/Archived/)).toBeNull();
  });

  it("states the rating gap as information, not as a locked feature", async () => {
    const { findByText, queryByLabelText } = await renderScreen();
    expect(await findByText("Not enough completed sales yet")).toBeTruthy();
    // Not announced "Unavailable": an absent rating is a normal state for a
    // new seller, not a door the app has locked.
    expect(queryByLabelText(/Seller rating\. Unavailable/)).toBeNull();
  });

  it("routes Meetup spots to safety guidance instead of a dead tile", async () => {
    const { findByLabelText, findByText } = await renderScreen();
    const tile = await findByLabelText("Meetup spots. Safe exchange tips");
    await act(async () => {
      fireEvent.press(tile);
    });
    expect(await findByText("Meet up safely")).toBeTruthy();
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

describe("selling behind the seller gate", () => {
  /**
   * Selling and the Store dashboard used to answer differently for one account:
   * the dashboard consulted the seller application, this pane consulted nothing
   * at all, so a seller with a half-finished application saw a full Selling tab
   * — empty, and reading as a broken store rather than an unfinished form.
   * Both now read the one verdict from the one endpoint.
   */

  it("shows the gate instead of the selling pane for an unapproved seller", async () => {
    for (const status of [
      "NO_APPLICATION",
      "DRAFT",
      "SUBMITTED",
      "UNDER_REVIEW",
      "MORE_INFORMATION_REQUIRED",
      "DECLINED",
      "SUSPENDED"
    ] as const) {
      mockUseSellerAccess.mockReturnValue(sellerAccess(status));
      const view = await renderScreen();
      expect(view.queryByTestId("marketplace-selling-gate")).toBeTruthy();
      expect(view.queryByText("Your items")).toBeNull();
      view.unmount();
    }
  });

  it("does not gate buying, which was never about being a seller", async () => {
    // The two panes are independent surfaces that happen to share a screen.
    // Refusing to let someone *buy* because their seller application is a
    // draft would be a new bug introduced by the fix for the old one.
    mockUseSellerAccess.mockReturnValue(sellerAccess("DRAFT"));
    const { findByText, findAllByLabelText } = await renderScreen();

    // The toggle is switched rather than the hidden pane inspected in place.
    // Both panes stay mounted (that is how each one's scroll position survives
    // the toggle), but the inactive one carries `display: "none"`, which RNTL
    // excludes from queries by default — so reading it while hidden would prove
    // only that it exists in the tree, not that a draft seller can reach it.
    await act(async () => {
      fireEvent.press(await findByText("Buying"));
    });

    // Asserted through the accessible name rather than the visible title: the
    // card composes its title alongside other nodes, so an exact text query
    // misses it and would read here as "the buying feed is gone" — the exact
    // false alarm this test exists to rule out.
    expect((await findAllByLabelText(/Oak dining table/)).length).toBeGreaterThan(0);
  });

  it("gates while the verdict is still loading, rather than after", async () => {
    // An open Selling pane during `loading` is the same bug narrowed to the
    // first few hundred milliseconds of a cold launch — long enough to see.
    mockUseSellerAccess.mockReturnValue({
      state: DENIED_SELLER_ACCESS,
      loading: true,
      failed: false,
      stale: false,
      unsupported: false,
      refresh: mockRefreshSellerAccess
    });
    const view = await renderScreen();
    expect(view.queryByTestId("marketplace-selling-gate-loading")).toBeTruthy();
    expect(view.queryByText("Your items")).toBeNull();
  });

  it("retries rather than denies when the verdict could not be read", async () => {
    mockUseSellerAccess.mockReturnValue({
      state: DENIED_SELLER_ACCESS,
      loading: false,
      failed: true,
      stale: true,
      unsupported: false,
      refresh: mockRefreshSellerAccess
    });
    const view = await renderScreen();
    await act(async () => {
      fireEvent.press(view.getByTestId("marketplace-selling-gate-cta"));
    });
    expect(mockRefreshSellerAccess).toHaveBeenCalled();
    expect(navigation.navigate).not.toHaveBeenCalled();
  });

  it("does not gate at all against a server that has no gate", async () => {
    // A 404 on the access-state route means this deployment predates the route
    // — and therefore predates the server-side enforcement behind it. Gating
    // here would be a client refusing on behalf of a server that never agreed
    // to refuse, which is how "We couldn't check your seller status" ended up
    // in front of every seller on a build that shipped ahead of its backend.
    mockUseSellerAccess.mockReturnValue({
      state: DENIED_SELLER_ACCESS,
      loading: false,
      failed: false,
      stale: false,
      unsupported: true,
      refresh: mockRefreshSellerAccess
    });
    const view = await renderScreen();
    expect(view.queryByTestId("marketplace-selling-gate")).toBeNull();
    expect(view.queryByTestId("marketplace-selling-gate-loading")).toBeNull();
    expect(view.queryByText("Your items")).toBeTruthy();
  });

  it("sends a suspended seller to their orders, not to the application form", async () => {
    mockUseSellerAccess.mockReturnValue(sellerAccess("SUSPENDED"));
    const view = await renderScreen();
    await act(async () => {
      fireEvent.press(view.getByTestId("marketplace-selling-gate-cta"));
    });
    expect(navigation.navigate).toHaveBeenCalledWith("SellerStore", {
      mode: "orders",
      title: "Orders"
    });
  });

  it("sends an applicant to the application form", async () => {
    mockUseSellerAccess.mockReturnValue(sellerAccess("DRAFT"));
    const view = await renderScreen();
    await act(async () => {
      fireEvent.press(view.getByTestId("marketplace-selling-gate-cta"));
    });
    expect(navigation.navigate).toHaveBeenCalledWith("MerchantApply");
  });
});
