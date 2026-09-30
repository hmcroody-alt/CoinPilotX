/**
 * A listing id handed to the browse grid must reach that listing's product page
 * even when the id is not in the page of rows the grid just loaded.
 *
 * `MarketplaceScreen` accepts `route.params.listingId` and forwards to
 * `MarketplaceProduct`. It used to do that only when the id appeared in
 * `source` -- one page of 32 rows from its own unfiltered search -- and to
 * `return` silently otherwise, leaving the caller on the browse grid with no
 * error and no sign that a particular product had been asked for. Production
 * publishes 196 listings, so the page held about a sixth of the catalogue and
 * the miss was the normal case, not the edge one.
 *
 * The two tests are deliberately a pair. Only asserting the miss would pass
 * against a screen that had stopped reading the page at all and thrown away
 * the free snapshot with it, which would cost every hit an extra round trip.
 *
 * `MarketplaceProductScreen` fetches by id when no snapshot arrives
 * (`MarketplaceProductByIdLoad.test`), so the miss path is a round trip, not a
 * dead end -- but that is a property of the *destination*, and it only helps if
 * this screen actually navigates there. That is what is proved here.
 */
import { render, waitFor } from "@testing-library/react-native";
import { loadCachedMarketplace, searchMarketplace } from "../../api/marketplace";
import { MarketplaceScreen } from "../MarketplaceScreen";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

// Keys echoed: every claim here is about navigation, never copy, and the real
// engine warns on each key it cannot find without an `I18nProvider`.
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
  fetchCommerceModules: jest.fn(() => Promise.resolve([])),
  recordCommerceFeedback: jest.fn(() => Promise.resolve(true)),
  recordCommerceImpression: jest.fn(() => Promise.resolve(true)),
  recordCommerceEngagement: jest.fn(() => Promise.resolve(true)),
  explainCommercePlacement: jest.fn(() => Promise.resolve(null))
}));

const search = searchMarketplace as jest.MockedFunction<typeof searchMarketplace>;
const cached = loadCachedMarketplace as jest.MockedFunction<typeof loadCachedMarketplace>;

function listing(id: number) {
  return {
    id,
    title: `Listing ${id}`,
    description: "",
    price: 1999,
    currency: "USD",
    category: "misc",
    image_url: "https://cdn.example/l.jpg",
    seller_user_id: 42,
    created_at: "2026-09-01T00:00:00Z",
    saved: false
  } as any;
}

/** One page of the grid's own search: ids 1..32, mirroring the real limit. */
const PAGE = Array.from({ length: 32 }, (_, i) => listing(i + 1));

function renderWith(listingId: number) {
  const navigate = jest.fn();
  const navigation = { navigate, setOptions: jest.fn(), goBack: jest.fn() } as any;
  const route = { params: { listingId, title: "Marketplace" } } as any;
  render(<MarketplaceScreen navigation={navigation} route={route} />);
  return navigate;
}

beforeEach(() => {
  jest.clearAllMocks();
  cached.mockResolvedValue([]);
  search.mockResolvedValue({ items: PAGE } as any);
});

describe("a listing id handed to the browse grid", () => {
  it("opens the product page when the id is in the page the grid loaded", async () => {
    const navigate = renderWith(7);

    await waitFor(() => expect(navigate).toHaveBeenCalled());

    // The snapshot rides along, so the product opens without a second fetch.
    expect(navigate).toHaveBeenCalledWith("MarketplaceProduct", {
      listingId: 7,
      listing: expect.objectContaining({ id: 7 }),
      title: "Listing 7"
    });
  });

  it("still opens the product page when the id is outside that page", async () => {
    // 199 is published in production and is not in any 32-row first page.
    const navigate = renderWith(199);

    await waitFor(() => expect(navigate).toHaveBeenCalled());

    expect(navigate).toHaveBeenCalledWith("MarketplaceProduct", { listingId: 199 });

    // The failure this file exists for: landing the caller on the grid by
    // navigating nowhere at all. `toHaveBeenCalled` above would also catch a
    // navigation to some *other* screen, so name the browse grid explicitly --
    // it is the destination a regression would fall back to.
    expect(navigate).not.toHaveBeenCalledWith("MarketplaceDetail", expect.anything());
  });
});
