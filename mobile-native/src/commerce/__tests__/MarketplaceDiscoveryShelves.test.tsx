/**
 * The rails above the browse grid.
 *
 * `useMarketplaceCommerce.test.ts` proves the state — what is fetched, what is
 * dropped, what a hide does to the shelf it came from. What is left is the
 * rendering, and four of its properties do not show up in a diff:
 *
 *   - **The heading is a key, not prose.** A shelf's title is the claim it
 *     makes: "Because you viewed" is an assertion about this user's history and
 *     "Trending on PulseSoc" is an assertion about the catalogue. Both come off
 *     the wire as i18n keys and are resolved here. A server that one day sends a
 *     display string instead would render untranslated English to eleven
 *     locales, and it would look fine in review.
 *
 *   - **Organic never wears an ad disclosure.** Marketplace serves the largest
 *     mix of promotion classes in the system, which makes it the easiest place
 *     to leak "Sponsored" onto a placement nobody paid for. That is the one
 *     failure here with a legal shape.
 *
 *   - **The ••• menu is inside a `Modal`.** The thing directly under a rail is
 *     the browse grid. An inline expansion would push the products the user is
 *     actually looking at down half a screen to show six options. `Modal` costs
 *     no layout; an expanding `View` costs exactly as much as it occupies, and
 *     the two are indistinguishable in a screenshot of the closed state.
 *
 *   - **Served and seen stay separate.** A rail is mounted the moment the
 *     Marketplace header renders, and every card in it fires a served beacon
 *     immediately. None of that is someone looking at a product. The visible
 *     impression is gated on the rail *reporting* the card viewable and on the
 *     dwell being met, and this is the one surface where the dwell is the
 *     client's own constant, so it is the one that most needs holding down.
 */
import { act, fireEvent, render } from "@testing-library/react-native";
import { FlatList, Modal } from "react-native";
import type { ReactTestInstance } from "react-test-renderer";
import { MarketplaceDiscoveryShelves } from "../MarketplaceDiscoveryShelves";
import {
  recordCommerceEngagement,
  recordCommerceImpression,
  type CommerceFeedbackAction,
  type CommerceModule,
  type CommercePlacement
} from "../../api/commerceDiscovery";
import { saveMarketplaceListing } from "../../api/marketplace";

jest.mock("../../api/commerceDiscovery", () => ({
  recordCommerceImpression: jest.fn(() => Promise.resolve(true)),
  recordCommerceEngagement: jest.fn(() => Promise.resolve(true)),
  explainCommercePlacement: jest.fn(() => Promise.resolve(null))
}));

jest.mock("../../api/marketplace", () => ({
  saveMarketplaceListing: jest.fn(() => Promise.resolve(true))
}));

// Keys echoed back. The catalog is `npm run i18n:validate`'s job; echoing keeps
// every assertion below about *which key was chosen* rather than about English.
jest.mock("../../i18n/I18nContext", () => ({
  useTranslation: () => ({ t: (key: string) => key })
}));

const impression = recordCommerceImpression as jest.MockedFunction<typeof recordCommerceImpression>;
const engagement = recordCommerceEngagement as jest.MockedFunction<typeof recordCommerceEngagement>;
const save = saveMarketplaceListing as jest.MockedFunction<typeof saveMarketplaceListing>;

function placement(overrides: Partial<CommercePlacement> = {}): CommercePlacement {
  const { product: productOverrides, ...rest } = overrides as any;
  return {
    placementId: "p1",
    impressionToken: "tok-1",
    surface: "marketplace",
    slot: 0,
    expiresAt: "",
    promotionClass: "organic",
    labelKey: "commerce:discovery.label.recommended",
    reason: "because_you_viewed",
    rankingVersion: "commerce-discovery-v1",
    priceMinor: 4999,
    priceCurrency: "USD",
    ...rest,
    product: {
      listingId: 501,
      title: "Women's Casual Sneakers",
      priceLabel: "$49.99",
      coverImageUrl: "https://cdn.example/p.jpg",
      sellerUserId: 77,
      sellerStoreName: "M&W Store",
      category: "shoes",
      rating: 4.8,
      ratingCount: 120,
      ...(productOverrides || {})
    }
  };
}

function module_(overrides: Partial<CommerceModule> = {}): CommerceModule {
  return {
    key: "because_you_viewed",
    reason: "because_you_viewed",
    titleKey: "commerce:discovery.module.because_you_viewed",
    placements: [placement()],
    ...overrides
  };
}

const navigate = jest.fn();
const onFeedback = jest.fn();

function renderShelves(modules: CommerceModule[] = [module_()]) {
  return render(
    <MarketplaceDiscoveryShelves modules={modules} navigation={{ navigate }} onFeedback={onFeedback} />
  );
}

/** Every ancestor of a node, innermost first. */
function ancestors(node: ReactTestInstance): ReactTestInstance[] {
  const chain: ReactTestInstance[] = [];
  let current: ReactTestInstance | null = node.parent;
  while (current) {
    chain.push(current);
    current = current.parent;
  }
  return chain;
}

/**
 * Drive the rail's viewability callback by hand.
 *
 * `FlatList` measures nothing under jest, so `onViewableItemsChanged` never
 * fires on its own and every card would sit at `isViewable: false` forever —
 * which would make the visible-impression assertions below vacuously green. So
 * the rail is told what it would have reported. Calling the list's own prop
 * (rather than reaching into the card) keeps the wiring under test: a card that
 * stopped consulting the rail's viewable set would fail here.
 */
function reportViewable(list: ReactTestInstance, items: CommercePlacement[]) {
  act(() => {
    list.props.onViewableItemsChanged({
      viewableItems: items.map((item) => ({ key: item.placementId, item }))
    });
  });
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe("what a shelf says about itself", () => {
  it("renders the heading the server asked for", () => {
    const { getByTestId } = renderShelves();
    expect(getByTestId("marketplace-shelf-heading-because_you_viewed").props.children).toBe(
      "commerce:discovery.module.because_you_viewed"
    );
  });

  it("falls back to the reason code when a shelf arrives with no title key", () => {
    // Still a key. A shelf with no heading, or a heading assembled from the raw
    // reason code, both end up showing the user a snake_case identifier.
    const { getByTestId } = renderShelves([module_({ titleKey: "", reason: "trending", key: "trending" })]);
    expect(getByTestId("marketplace-shelf-heading-trending").props.children).toBe(
      "commerce:discovery.module.trending"
    );
  });

  it("never labels an organic placement as an ad", () => {
    const { queryByText } = renderShelves();
    // The largest promotion-class mix in the system sits on this surface, so
    // this is where a "Sponsored" string is most likely to end up over a
    // placement nobody paid for.
    expect(queryByText("commerce:discovery.label.sponsored")).toBeNull();
    expect(queryByText("commerce:discovery.label.ad")).toBeNull();
  });

  it("renders one card per placement, in the order it was served", () => {
    const { getByTestId } = renderShelves([
      module_({ placements: [placement({ placementId: "a" }), placement({ placementId: "b" })] })
    ]);
    const data = getByTestId("marketplace-shelf-because_you_viewed").findByType(FlatList).props.data;
    expect(data.map((item: CommercePlacement) => item.placementId)).toEqual(["a", "b"]);
  });

  it("shows the product, the store, the price and the rating", () => {
    const { getByText } = renderShelves();
    expect(getByText("Women's Casual Sneakers")).toBeTruthy();
    expect(getByText("M&W Store")).toBeTruthy();
    expect(getByText("$49.99")).toBeTruthy();
    expect(getByText("★ 4.8")).toBeTruthy();
  });

  it("prefers the server's formatted price over re-deriving one", () => {
    // The label was produced where the listing's own currency is known. A client
    // that re-formats is how a listing priced in one currency gets shown in
    // another.
    const { getByText, queryByText } = renderShelves([
      module_({ placements: [placement({ priceMinor: 100, priceCurrency: "EUR" })] })
    ]);
    expect(getByText("$49.99")).toBeTruthy();
    expect(queryByText("€1.00")).toBeNull();
  });
});

describe("no shelves is the normal answer", () => {
  it("renders nothing at all rather than an empty container", () => {
    // Not an empty `View` with the shelf padding on it, which would leave a gap
    // above the grid on every narrowed browse — and narrowed is most of them.
    const { toJSON } = renderShelves([]);
    expect(toJSON()).toBeNull();
  });

  it("renders no card for a placement with no title", () => {
    const { queryByTestId } = renderShelves([
      module_({ placements: [placement({ placementId: "bad", product: { title: "" } } as any)] })
    ]);
    expect(queryByTestId("marketplace-shelf-card-bad")).toBeNull();
  });

  it("renders no card for a placement with nothing to open", () => {
    const { queryByTestId } = renderShelves([
      module_({ placements: [placement({ placementId: "bad", product: { listingId: 0 } } as any)] })
    ]);
    expect(queryByTestId("marketplace-shelf-card-bad")).toBeNull();
  });
});

describe("the ••• menu does not move the grid", () => {
  it("is closed until it is asked for", () => {
    const { queryByTestId } = renderShelves();
    expect(queryByTestId("marketplace-shelf-card-menu")).toBeNull();
  });

  it("opens inside a Modal rather than expanding the rail", () => {
    const { getByTestId, UNSAFE_getAllByType } = renderShelves();
    fireEvent.press(getByTestId("marketplace-shelf-card-menu-button"));

    const menu = getByTestId("marketplace-shelf-card-menu");
    const modals = new Set(UNSAFE_getAllByType(Modal));
    // Containment, not a testID on the Modal: what matters is that the menu sits
    // *inside* one, because that is what makes it cost zero layout in the grid
    // underneath. A menu moved into a sibling `View` keeps its testID and pushes
    // every product down.
    expect(ancestors(menu).some((node) => modals.has(node))).toBe(true);
  });

  it("offers every control the mission requires", () => {
    const { getByTestId } = renderShelves();
    fireEvent.press(getByTestId("marketplace-shelf-card-menu-button"));
    for (const id of [
      "marketplace-shelf-card-menu-hide",
      "marketplace-shelf-card-menu-not_interested",
      "marketplace-shelf-card-menu-see_fewer",
      "marketplace-shelf-card-menu-hide_seller",
      "marketplace-shelf-card-menu-why",
      "marketplace-shelf-card-menu-snooze"
    ]) {
      expect(getByTestId(id)).toBeTruthy();
    }
  });

  it.each<[string, CommerceFeedbackAction]>([
    ["marketplace-shelf-card-menu-hide", "hide"],
    ["marketplace-shelf-card-menu-not_interested", "not_interested"],
    ["marketplace-shelf-card-menu-see_fewer", "see_fewer"],
    ["marketplace-shelf-card-menu-hide_seller", "hide_seller"],
    ["marketplace-shelf-card-menu-snooze", "snooze"]
  ])("%s reports its own verb", (testID, action) => {
    // Five items that all post `hide` would clear the card correctly and throw
    // away the signal the ranking loop runs on. Assert the argument, never
    // `toHaveBeenCalled()`.
    const { getByTestId } = renderShelves();
    fireEvent.press(getByTestId("marketplace-shelf-card-menu-button"));
    fireEvent.press(getByTestId(testID));

    expect(onFeedback).toHaveBeenCalledTimes(1);
    expect(onFeedback.mock.calls[0][1]).toBe(action);
    expect(onFeedback.mock.calls[0][0].placementId).toBe("p1");
  });

  it("closes itself when an action is taken", () => {
    const { getByTestId, queryByTestId } = renderShelves();
    fireEvent.press(getByTestId("marketplace-shelf-card-menu-button"));
    fireEvent.press(getByTestId("marketplace-shelf-card-menu-hide"));
    expect(queryByTestId("marketplace-shelf-card-menu")).toBeNull();
  });

  it("explains itself without dismissing anything", async () => {
    // "Why am I seeing this?" is the one menu item that is not a preference.
    // Hiding the thing being asked about is a strange answer to a question.
    const { getByTestId, queryByTestId } = renderShelves();
    fireEvent.press(getByTestId("marketplace-shelf-card-menu-button"));
    await act(async () => {
      fireEvent.press(getByTestId("marketplace-shelf-card-menu-why"));
    });

    expect(onFeedback).not.toHaveBeenCalled();
    expect(getByTestId("marketplace-shelf-card-why")).toBeTruthy();
    expect(queryByTestId("marketplace-shelf-card-menu")).toBeNull();
  });
});

describe("opening and saving a product", () => {
  it("records the click before navigating away", async () => {
    // The transition unmounts the card, so a beacon started on the way out
    // races its own teardown.
    const { getByTestId } = renderShelves();
    await act(async () => {
      fireEvent.press(getByTestId("marketplace-shelf-card-body"));
    });

    expect(engagement).toHaveBeenCalledTimes(1);
    expect(engagement.mock.calls[0][1]).toBe("click");
    expect(navigate).toHaveBeenCalledWith("MarketplaceProduct", {
      listingId: 501,
      title: "Women's Casual Sneakers"
    });
  });

  it("does not fire twice on a double tap", async () => {
    const { getByTestId } = renderShelves();
    await act(async () => {
      fireEvent.press(getByTestId("marketplace-shelf-card-body"));
      fireEvent.press(getByTestId("marketplace-shelf-card-body"));
    });
    expect(navigate).toHaveBeenCalledTimes(1);
  });

  it("saves the listing and reports the save as engagement", () => {
    const { getByTestId } = renderShelves();
    fireEvent.press(getByTestId("marketplace-shelf-card-save"));

    expect(save).toHaveBeenCalledWith(501);
    expect(engagement).toHaveBeenCalledTimes(1);
    expect(engagement.mock.calls[0][1]).toBe("save");
  });
});

describe("served and seen are two different events", () => {
  it("reports served on mount for every card in the rail", () => {
    renderShelves([
      module_({ placements: [placement({ placementId: "a" }), placement({ placementId: "b" })] })
    ]);
    expect(impression).toHaveBeenCalledTimes(2);
    expect(impression.mock.calls.every((call) => call[1]?.visible === false)).toBe(true);
  });

  it("reports no visible impression for a rail nobody looked at", () => {
    // Every shelf is mounted the moment the Marketplace header renders. If
    // mounting counted, the only number anyone will use to judge this feature
    // would be a count of app launches.
    jest.useFakeTimers();
    try {
      const { unmount } = renderShelves();
      act(() => {
        jest.advanceTimersByTime(5000);
      });
      unmount();
      expect(impression.mock.calls.filter((call) => call[1]?.visible)).toHaveLength(0);
    } finally {
      jest.useRealTimers();
    }
  });

  it("reports no visible impression for a card scrolled past inside the dwell", () => {
    jest.useFakeTimers();
    try {
      const only = placement({ placementId: "a" });
      const { UNSAFE_getByType, unmount } = renderShelves([module_({ placements: [only] })]);
      reportViewable(UNSAFE_getByType(FlatList), [only]);
      act(() => {
        jest.advanceTimersByTime(300);
      });
      reportViewable(UNSAFE_getByType(FlatList), []);
      act(() => {
        jest.advanceTimersByTime(5000);
      });
      unmount();
      expect(impression.mock.calls.filter((call) => call[1]?.visible)).toHaveLength(0);
    } finally {
      jest.useRealTimers();
    }
  });

  it("reports a visible impression once the dwell is met, and only once", () => {
    jest.useFakeTimers();
    try {
      const only = placement({ placementId: "a" });
      const { UNSAFE_getByType, unmount } = renderShelves([module_({ placements: [only] })]);
      reportViewable(UNSAFE_getByType(FlatList), [only]);
      act(() => {
        // The rails' dwell is the one number in this system the client owns —
        // `/marketplace/modules` carries no `visible_dwell_ms`. Crossing it here
        // rather than at some other figure is what pins that constant down.
        jest.advanceTimersByTime(1200);
      });
      act(() => {
        jest.advanceTimersByTime(4000);
      });
      unmount();
      const visible = impression.mock.calls.filter((call) => call[1]?.visible);
      expect(visible).toHaveLength(1);
      expect(visible[0][0].placementId).toBe("a");
    } finally {
      jest.useRealTimers();
    }
  });

  it("counts only the card the rail said was on screen", () => {
    // A horizontal rail holds more cards than fit. Counting the off-screen ones
    // would report impressions for products that were never rendered into the
    // viewport, which is the specific way a carousel inflates its own numbers.
    jest.useFakeTimers();
    try {
      const a = placement({ placementId: "a" });
      const b = placement({ placementId: "b" });
      const { UNSAFE_getByType, unmount } = renderShelves([module_({ placements: [a, b] })]);
      reportViewable(UNSAFE_getByType(FlatList), [a]);
      act(() => {
        jest.advanceTimersByTime(1200);
      });
      unmount();
      const visible = impression.mock.calls.filter((call) => call[1]?.visible);
      expect(visible).toHaveLength(1);
      expect(visible[0][0].placementId).toBe("a");
    } finally {
      jest.useRealTimers();
    }
  });
});
