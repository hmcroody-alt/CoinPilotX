/**
 * "Open this listing in PulseSoc" must open *that* listing.
 *
 * The promise a product deep link makes is that canonical identity survives the
 * platform boundary: web listing L -> universal link -> app -> listing L. It was
 * broken, and not at the boundary. `/pulse/marketplace/:id` resolved to
 * `MarketplaceDetail`, which renders `MarketplaceScreen` -- the browse grid --
 * and the grid forwarded to the product page only when the id happened to appear
 * in the page of rows its search had just returned:
 *
 *     const target = source.find((item) => item.id === initialListingId);
 *     if (!target) return;
 *
 * So the identity in the URL was resolved by a client-side `find` over a
 * paginated search response. For any listing outside that page the member was
 * silently left on the Marketplace grid -- a CTA reading "Open this listing"
 * opening the catalogue instead. `bot.py` names this gap in as many words and
 * calls it "a shared product gap, not a web one".
 *
 * These tests cover the whole chain on the app side of the boundary, and the
 * third one is the load-bearing case: a listing that search would NOT return
 * still opens. That is the assertion the old behaviour could not pass, so it is
 * what stops this regressing.
 */

import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 62, bottom: 34, left: 0, right: 0 })
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

// Only the read-one call is faked. Everything else in the module is the real
// implementation, including `normalizeMarketplaceListing` -- the serializer the
// screen's rendering depends on -- so this cannot pass against a shape the real
// wire format would not produce.
const mockFetchListing = jest.fn();
jest.mock("../../api/marketplace", () => ({
  ...jest.requireActual("../../api/marketplace"),
  fetchMarketplaceListing: (...args: unknown[]) => mockFetchListing(...args)
}));

import { MarketplaceProductScreen } from "../../screens/MarketplaceProductScreen";
import { nativeObjectDestination } from "../nativeRouteActions";
import { navigationRef, routeNotificationTarget, setNotificationRouteReporter } from "../notificationRouting";
import { linking } from "../linking";

/** The listing under test. Every assertion below is about *this* id. */
const LISTING_ID = 8123;
const LISTING = {
  id: LISTING_ID,
  title: "Hand-thrown stoneware mug",
  price_label: "$34.00",
  currency: "USD",
  seller_user_id: 12,
  category: "Home",
  status: "active",
  approval_status: "approved",
  quantity: 4,
  media: []
};

/** A different listing, to prove the id is carried rather than guessed. */
const OTHER_LISTING = { ...LISTING, id: 9004, title: "Linen apron" };

function makeNavigation(canGoBack = true) {
  return {
    navigate: jest.fn(),
    goBack: jest.fn(),
    canGoBack: jest.fn(() => canGoBack),
    setOptions: jest.fn(),
    addListener: jest.fn(() => () => undefined)
  };
}

beforeEach(() => {
  mockFetchListing.mockReset();
});

describe("a marketplace product deep link", () => {
  it("resolves the canonical web URL to the product page, carrying the id", () => {
    // The identity step. `MarketplaceDetail` here is the old answer and the bug.
    expect(nativeObjectDestination(`/pulse/marketplace/${LISTING_ID}`)).toEqual({
      screen: "MarketplaceProduct",
      params: { listingId: LISTING_ID, title: "Marketplace" }
    });
  });

  it("resolves the https universal link and the custom scheme identically", () => {
    // Safari hands over an https URL; a fallback hand-off hands over
    // pulsesoc://. Two spellings of one listing must not become two answers.
    const web = nativeObjectDestination(`https://pulsesoc.com/pulse/marketplace/${LISTING_ID}`);
    const scheme = nativeObjectDestination(`pulsesoc://pulse/marketplace/${LISTING_ID}`);
    expect(web).toEqual(scheme);
    expect(web?.params).toMatchObject({ listingId: LISTING_ID });
  });

  it("opens the exact listing when it holds only an id and no snapshot", async () => {
    // The case the old routing could not serve. A deep link carries an id and
    // nothing else, and this listing is deliberately not in any search response
    // the test provides -- nothing here searches at all.
    mockFetchListing.mockResolvedValue(LISTING);
    const navigation = makeNavigation();
    const { getByText, queryByText } = render(
      <MarketplaceProductScreen
        navigation={navigation as never}
        route={{ params: { listingId: LISTING_ID, title: "Marketplace" } } as never}
      />
    );

    await waitFor(() => expect(getByText(LISTING.title)).toBeTruthy());
    // Resolved by canonical id, and by nothing else.
    expect(mockFetchListing).toHaveBeenCalledWith(LISTING_ID);
    // Not the unavailable state, and not some other product.
    expect(queryByText("This item is no longer available.")).toBeNull();
    expect(queryByText(OTHER_LISTING.title)).toBeNull();
    // The listing opened in place. Nothing was redirected to Home or to the grid.
    expect(navigation.navigate).not.toHaveBeenCalled();
  });

  it("does not refetch when the caller already holds the listing", async () => {
    // An in-app tap from the grid, a seller store or a commerce card passes the
    // snapshot. Putting a spinner in front of data the caller is holding would
    // be a regression for every one of those surfaces.
    const navigation = makeNavigation();
    const { getByText } = render(
      <MarketplaceProductScreen
        navigation={navigation as never}
        route={{ params: { listingId: LISTING_ID, listing: LISTING } } as never}
      />
    );
    expect(getByText(LISTING.title)).toBeTruthy();
    expect(mockFetchListing).not.toHaveBeenCalled();
  });

  it("shows the unavailable state for a listing that is gone, never Home", async () => {
    // A product withdrawn between the web page and the tap. The requirement is an
    // honest unavailable state on the product route -- not a silent bounce to
    // Home, which is indistinguishable from the bug this file exists to stop.
    mockFetchListing.mockRejectedValue(new Error("This listing is no longer available."));
    const navigation = makeNavigation();
    const { getByText } = render(
      <MarketplaceProductScreen
        navigation={navigation as never}
        route={{ params: { listingId: LISTING_ID } } as never}
      />
    );

    await waitFor(() => expect(getByText("This item is no longer available.")).toBeTruthy());
    expect(getByText("Browse Marketplace")).toBeTruthy();
    // Nothing navigated on its own. The member chooses.
    expect(navigation.navigate).not.toHaveBeenCalled();
  });

  it("gives a cold-start arrival somewhere to go when there is no back stack", async () => {
    // A deep link from Safari is the whole stack, so `goBack()` has nothing to
    // pop: the only control on the unavailable page did nothing at all and the
    // member was trapped on it.
    mockFetchListing.mockRejectedValue(new Error("gone"));
    const navigation = makeNavigation(false);
    const { getByText } = render(
      <MarketplaceProductScreen
        navigation={navigation as never}
        route={{ params: { listingId: LISTING_ID } } as never}
      />
    );

    const browse = await waitFor(() => getByText("Browse Marketplace"));
    fireEvent.press(browse);
    expect(navigation.goBack).not.toHaveBeenCalled();
    expect(navigation.navigate).toHaveBeenCalledWith("Tabs", { screen: "Marketplace" });
  });
});

/**
 * The OS hand-off. `linking.ts` is what answers a universal link that launches
 * the app, and it is a separate table from the in-app resolver -- so the path
 * had to be moved in both or the two would disagree about one URL.
 */
describe("the linking config iOS hands a cold launch to", () => {
  it("resolves the product path to the product screen with the id parsed", () => {
    const state = linking.getStateFromPath?.(`/pulse/marketplace/${LISTING_ID}`);
    expect(state?.routes?.[state.routes.length - 1]).toMatchObject({
      name: "MarketplaceProduct",
      params: { listingId: LISTING_ID }
    });
  });

  it("does not read a sibling static path as a listing id", () => {
    // `pulse/marketplace/:listingId` is a single-segment wildcard, so moving it
    // between screens is exactly when a static sibling starts matching as an id
    // instead. "create" must not open a product page hunting for listing NaN.
    //
    // It does not reach `MarketplaceCreateGateway` either, and that is a
    // separate, pre-existing defect of the *default* matcher -- verified by
    // running this path against the pre-change config, which produces the same
    // segment-named state. In scope here is only that the product route does not
    // claim it.
    const state = linking.getStateFromPath?.("/pulse/marketplace/create");
    const names = (state?.routes || []).map((route) => route.name);
    expect(names).not.toContain("MarketplaceProduct");
  });
});

/**
 * One path, three resolvers.
 *
 * `nativeObjectDestination` answers an in-app tap, `linking.ts` answers an OS
 * hand-off, and `routeNotificationTarget` answers everything that arrives as a
 * bare target string: a push tap, and -- the case that matters here -- the
 * pending-target replay a signed-out arrival goes through. `App.tsx` mounts
 * `linking` only when `signedIn`, so a guest who follows "Open this listing in
 * PulseSoc" does not reach the linking config at all; the URL is held and
 * replayed through *this* resolver after login.
 *
 * It had its own copy of the wrong answer. Fixing two of three resolvers would
 * have left the logged-out path -- the one a first-time visitor from the web
 * takes -- still landing on the catalogue.
 */
describe("the target replayed after a signed-out arrival logs in", () => {
  beforeEach(() => setNotificationRouteReporter(() => undefined));
  afterEach(() => setNotificationRouteReporter(null));

  function withReadyNavigation(run: (navigate: jest.SpyInstance) => Promise<void>) {
    const isReady = jest.spyOn(navigationRef, "isReady").mockReturnValue(true);
    const navigate = jest.spyOn(navigationRef, "navigate").mockImplementation(() => undefined);
    return run(navigate).finally(() => {
      navigate.mockRestore();
      isReady.mockRestore();
    });
  }

  it("opens the product page for the id in the path", () =>
    withReadyNavigation(async (navigate) => {
      const result = await routeNotificationTarget(`/pulse/marketplace/${LISTING_ID}`);
      expect(result).toMatchObject({ handled: true });
      // Not the browse grid, and not the Activity fallback.
      expect(result.reason).toBeUndefined();
      expect(navigate).toHaveBeenCalledWith("MarketplaceProduct", {
        listingId: LISTING_ID,
        title: "Marketplace"
      });
    }));

  it("opens the product page for a listing id carried in the query", () =>
    withReadyNavigation(async (navigate) => {
      await routeNotificationTarget(`/pulse/marketplace?listing=${LISTING_ID}`);
      expect(navigate).toHaveBeenCalledWith("MarketplaceProduct", {
        listingId: LISTING_ID,
        title: "Marketplace"
      });
    }));

  it("still opens the catalogue when the target names no listing", () =>
    withReadyNavigation(async (navigate) => {
      // The guard on the fix above: sending every marketplace target to a
      // product page would break the plain "open the Marketplace" link.
      await routeNotificationTarget("/pulse/marketplace");
      expect(navigate).toHaveBeenCalledWith("Tabs", { screen: "Marketplace" });
    }));

  it("agrees with the resolver an in-app tap uses", () =>
    withReadyNavigation(async (navigate) => {
      // Two resolvers, one path, one answer. This is the assertion that fails if
      // a future change fixes one of them and forgets the other.
      await routeNotificationTarget(`/pulse/marketplace/${LISTING_ID}`);
      const inApp = nativeObjectDestination(`/pulse/marketplace/${LISTING_ID}`);
      expect(navigate).toHaveBeenCalledWith(inApp?.screen, inApp?.params);
    }));
});
