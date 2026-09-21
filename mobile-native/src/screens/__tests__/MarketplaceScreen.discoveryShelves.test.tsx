/**
 * Where the shelves sit on the Marketplace screen, and the four times they are
 * not there at all — proved against the rendered tree rather than the source.
 *
 * `MarketplaceDiscoveryShelves.test` proves the rails render. `useMarketplace
 * Commerce.test` proves the state. Neither can see the two things this file is
 * for, because both are properties of the *screen*:
 *
 *   - **They are not products.** The rails live in `ListHeaderComponent`, never
 *     in the grid's `data`. A recommended product mixed into the grid would make
 *     the grid's ordering partly ours and partly the user's with no seam they
 *     can see, and that is the version of this feature that quietly stops being
 *     trustworthy. Asserted against the `FlatList`'s actual `data` prop.
 *
 *   - **They are below every control the user owns.** Search, sort tabs,
 *     category chips — all above the shelves. Our ordering does not get to sit
 *     on top of the tools for changing it. Asserted by comparing *indices* in
 *     one pre-order walk; comparing `ReactTestInstance`s directly makes Jest
 *     serialise two React trees to build a failure diff and exhausts the heap.
 *
 * And the suppression states, one test each, because each is a different claim:
 * a seller's storefront is about whose surface it is, a search and a category
 * are about a question the user just asked, and offline is about whether a
 * freshly-ranked rail beside an honestly-stale grid is telling the truth.
 */
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";
import { FlatList } from "react-native";
import type { ReactTestInstance } from "react-test-renderer";
import { fetchCommerceModules } from "../../api/commerceDiscovery";
import type { CommerceModule, CommercePlacement } from "../../api/commerceDiscovery";
import { loadCachedMarketplace, searchMarketplace } from "../../api/marketplace";
import { __resetCommerceSessionId } from "../../commerce/session";
import { MarketplaceScreen } from "../MarketplaceScreen";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

// Keys echoed. Every claim here is about position and presence, never copy, and
// without an `I18nProvider` the real engine warns on each key it cannot find —
// a suite that warns on green is a suite whose warnings stop being read.
jest.mock("../../i18n/I18nContext", () => ({
  useTranslation: () => ({ t: (key: string) => key })
}));
jest.mock("../../i18n", () => ({
  useTranslation: () => ({ t: (key: string) => key })
}));

jest.mock("../../navigation/BottomNavVisibility", () => ({
  useBottomNavSurface: () => ({
    handlers: { onScroll: jest.fn(), onScrollBeginDrag: jest.fn(), scrollEventThrottle: 16 },
    contentPadding: { paddingBottom: 0 },
    paddingBottom: 0
  })
}));

jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: () => () => undefined
}));

jest.mock("../../api/marketplace", () => ({
  searchMarketplace: jest.fn(),
  loadCachedMarketplace: jest.fn(() => Promise.resolve([])),
  saveMarketplaceListing: jest.fn(() => Promise.resolve(true))
}));

jest.mock("../../api/marketplaceCommerce", () => ({
  fetchCart: jest.fn(() => Promise.resolve({ badgeCount: 0, items: [] })),
  addToCart: jest.fn(() => Promise.resolve({ badgeCount: 1, items: [] }))
}));

jest.mock("../../api/commerceDiscovery", () => ({
  fetchCommerceModules: jest.fn(),
  recordCommerceFeedback: jest.fn(() => Promise.resolve(true)),
  recordCommerceImpression: jest.fn(() => Promise.resolve(true)),
  recordCommerceEngagement: jest.fn(() => Promise.resolve(true)),
  explainCommercePlacement: jest.fn(() => Promise.resolve(null))
}));

const fetchModules = fetchCommerceModules as jest.MockedFunction<typeof fetchCommerceModules>;
const search = searchMarketplace as jest.MockedFunction<typeof searchMarketplace>;
const cached = loadCachedMarketplace as jest.MockedFunction<typeof loadCachedMarketplace>;

function listing(id: number, category: string) {
  return {
    id,
    title: `Listing ${id}`,
    description: "",
    price: 1999,
    currency: "USD",
    category,
    image_url: "https://cdn.example/l.jpg",
    seller_user_id: 42,
    created_at: "2026-09-01T00:00:00Z",
    saved: false
  } as any;
}

function placement(id: string): CommercePlacement {
  return {
    placementId: id,
    impressionToken: `tok_${id}`,
    surface: "marketplace",
    slot: 0,
    expiresAt: "",
    promotionClass: "organic",
    labelKey: "commerce:discovery.label.recommended",
    reason: "because_you_viewed",
    rankingVersion: "commerce-discovery-v1",
    priceMinor: 4999,
    priceCurrency: "USD",
    product: {
      listingId: 501,
      title: "Women's Casual Sneakers",
      priceLabel: "$49.99",
      coverImageUrl: "https://cdn.example/p.jpg",
      sellerUserId: 77,
      sellerStoreName: "M&W Store",
      category: "shoes",
      rating: 4.8,
      ratingCount: 120
    }
  };
}

const MODULES: CommerceModule[] = [
  {
    key: "because_you_viewed",
    reason: "because_you_viewed",
    titleKey: "commerce:discovery.module.because_you_viewed",
    placements: [placement("p1")]
  }
];

const LISTINGS = [listing(1, "shoes"), listing(2, "bags")];

/** The whole rendered tree in render order. */
function inRenderOrder(node: ReactTestInstance): ReactTestInstance[] {
  return node.findAll(() => true, { deep: true });
}

beforeEach(() => {
  jest.clearAllMocks();
  __resetCommerceSessionId();
  search.mockResolvedValue({ items: LISTINGS } as any);
  cached.mockResolvedValue([]);
  fetchModules.mockResolvedValue(MODULES);
});

/** Render and let both the grid load and the serve request settle. */
async function renderMarketplace(params: Record<string, any> = {}) {
  const utils = render(<MarketplaceScreen route={{ params } as any} navigation={{ navigate: jest.fn() } as any} />);
  await waitFor(() => expect(utils.queryAllByText("Listing 1").length).toBeGreaterThan(0));
  return utils;
}

describe("the shelves' place on the screen", () => {
  it("is not a product — the grid's data is only listings", async () => {
    const { UNSAFE_getByType, getByTestId } = await renderMarketplace();
    await waitFor(() => expect(getByTestId("marketplace-discovery-shelves")).toBeTruthy());

    const data = (UNSAFE_getByType(FlatList).props.data || []) as any[];
    // Every entry is a listing the search returned. Nothing placement-shaped is
    // in here, so the grid's ordering is still entirely the user's.
    expect(data.map((item) => item.id)).toEqual([1, 2]);
    expect(data.some((item) => typeof item.placementId !== "undefined")).toBe(false);
  });

  it("renders below the search field, the sort tabs and the category chips", async () => {
    const { getByTestId, getByPlaceholderText, getByText, UNSAFE_getByType } = await renderMarketplace();
    await waitFor(() => expect(getByTestId("marketplace-discovery-shelves")).toBeTruthy());

    const order = inRenderOrder(UNSAFE_getByType(FlatList));
    const shelves = order.indexOf(getByTestId("marketplace-discovery-shelves"));
    const searchField = order.indexOf(getByPlaceholderText("Search products, categories, sellers"));
    const sortTab = order.indexOf(getByText("New arrivals"));
    const categoryChip = order.indexOf(getByText("shoes"));

    for (const control of [searchField, sortTab, categoryChip]) {
      expect(control).toBeGreaterThanOrEqual(0);
      // Our ordering never sits above the controls for changing it.
      expect(control).toBeLessThan(shelves);
    }
  });

  it("renders above the grid", async () => {
    const { getByTestId, getByText, UNSAFE_getByType } = await renderMarketplace();
    await waitFor(() => expect(getByTestId("marketplace-discovery-shelves")).toBeTruthy());

    const order = inRenderOrder(UNSAFE_getByType(FlatList));
    expect(order.indexOf(getByTestId("marketplace-discovery-shelves"))).toBeLessThan(
      order.indexOf(getByText("Listing 1"))
    );
  });

  it("is not pinned to the top of the list", async () => {
    // One prop away from becoming a permanent banner over somebody's browse.
    const { UNSAFE_getByType } = await renderMarketplace();
    expect(UNSAFE_getByType(FlatList).props.stickyHeaderIndices).toBeUndefined();
  });
});

describe("standing down when the user narrows", () => {
  it("shows nothing, and asks for nothing, on a seller's own storefront", async () => {
    // Not a matter of taste: taking the top of a seller's storefront to show
    // other sellers' products is a thing a marketplace can do and should not.
    const { queryByTestId } = await renderMarketplace({ sellerUserId: 77 });
    expect(queryByTestId("marketplace-discovery-shelves")).toBeNull();
    expect(fetchModules).not.toHaveBeenCalled();
  });

  it("drops the shelves when the user types a search", async () => {
    const { getByTestId, getByPlaceholderText, queryByTestId } = await renderMarketplace();
    await waitFor(() => expect(getByTestId("marketplace-discovery-shelves")).toBeTruthy());

    await act(async () => {
      fireEvent.changeText(getByPlaceholderText("Search products, categories, sellers"), "sneakers");
    });

    expect(queryByTestId("marketplace-discovery-shelves")).toBeNull();
    // And it does not go and fetch a fresh pool for the narrowed state — the
    // whole point is that there is no shelf to rank.
    expect(fetchModules).toHaveBeenCalledTimes(1);
  });

  it("drops the shelves when the user picks a category", async () => {
    const { getByTestId, getByText, queryByTestId } = await renderMarketplace();
    await waitFor(() => expect(getByTestId("marketplace-discovery-shelves")).toBeTruthy());

    await act(async () => {
      fireEvent.press(getByText("shoes"));
    });

    // They narrowed to one category; a rail that ignores the narrowing
    // contradicts the control they just used.
    expect(queryByTestId("marketplace-discovery-shelves")).toBeNull();
  });

  it("brings them back — freshly asked for — when the search is cleared", async () => {
    const { getByTestId, getByPlaceholderText, queryByTestId } = await renderMarketplace();
    const field = getByPlaceholderText("Search products, categories, sellers");

    await act(async () => {
      fireEvent.changeText(field, "sneakers");
    });
    expect(queryByTestId("marketplace-discovery-shelves")).toBeNull();

    await act(async () => {
      fireEvent.changeText(field, "");
    });
    await waitFor(() => expect(getByTestId("marketplace-discovery-shelves")).toBeTruthy());

    // Asked again rather than restored from memory: the pool held before the
    // search was ranked for a user who has since told us something.
    expect(fetchModules).toHaveBeenCalledTimes(2);
  });

  it("shows no shelves beside an offline grid", async () => {
    // The grid is honestly labelled as cached. A freshly-ranked "Trending" rail
    // next to it would not be, and "Trending" from an unknown time ago is a
    // claim rather than a fact.
    search.mockRejectedValue(new Error("offline"));
    cached.mockResolvedValue(LISTINGS);

    const { queryByTestId, queryAllByText } = render(
      <MarketplaceScreen route={{ params: {} } as any} navigation={{ navigate: jest.fn() } as any} />
    );
    await waitFor(() => expect(queryAllByText("Showing saved results").length).toBeGreaterThan(0));
    await waitFor(() => expect(queryByTestId("marketplace-discovery-shelves")).toBeNull());
  });
});

describe("a recommendation failure never breaks browsing", () => {
  it("renders the grid exactly as before when the engine has nothing", async () => {
    fetchModules.mockResolvedValue([]);
    const { queryByTestId, queryAllByText } = await renderMarketplace();

    expect(queryByTestId("marketplace-discovery-shelves")).toBeNull();
    expect(queryAllByText("Listing 1").length).toBeGreaterThan(0);
    expect(queryAllByText("Listing 2").length).toBeGreaterThan(0);
  });

  it("renders the grid when the serve request fails outright", async () => {
    // Browsing is the feature. This is not.
    fetchModules.mockRejectedValue(new Error("engine down"));
    const { queryByTestId, queryAllByText } = await renderMarketplace();

    expect(queryByTestId("marketplace-discovery-shelves")).toBeNull();
    expect(queryAllByText("Listing 1").length).toBeGreaterThan(0);
  });
});

describe("a hide leaves no hole above the grid", () => {
  it("removes the whole shelf when its last card is hidden", async () => {
    const { getByTestId, queryByTestId, queryAllByText } = await renderMarketplace();
    await waitFor(() => expect(getByTestId("marketplace-discovery-shelves")).toBeTruthy());

    // Two `act`s: the menu is a `Modal` that is not in the tree until the first
    // press has flushed, so querying inside the same callback finds nothing.
    await act(async () => {
      fireEvent.press(getByTestId("marketplace-shelf-card-menu-button"));
    });
    await act(async () => {
      fireEvent.press(getByTestId("marketplace-shelf-card-menu-hide"));
    });

    // The container goes too, not just the card — an empty wrapper left in the
    // header is the blank hole the mission calls out by name.
    expect(queryByTestId("marketplace-discovery-shelves")).toBeNull();
    expect(queryByTestId("marketplace-shelf-because_you_viewed")).toBeNull();
    expect(queryAllByText("Listing 1").length).toBeGreaterThan(0);
  });
});
