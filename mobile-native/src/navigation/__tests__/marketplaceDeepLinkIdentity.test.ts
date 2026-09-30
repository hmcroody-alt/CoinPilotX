/**
 * A product link opens that product — through every door into the app.
 *
 * The website's "Open in the PulseSoc app" button on a Marketplace product page
 * sends an iPhone to `/open/product/<id>`, which hands the app
 * `pulsesoc://pulse/marketplace/<id>`. That one path is resolved by THREE
 * separate functions depending on how the app was opened, and they have to agree:
 *
 *   - `nativeObjectDestination` — an in-app tap on a link, and (because
 *     `linking.ts` calls it first) the cold-start hand-off from iOS as well.
 *   - `linking.getStateFromPath` — the route table React Navigation builds the
 *     initial state from, and the one `getPathFromState` reads when the app
 *     generates a link.
 *   - `routeNotificationTarget` — a push tap, and the pending-target replay a
 *     signed-out arrival goes through after logging in.
 *
 * All three used to answer `MarketplaceDetail`, which renders the browse grid.
 * The grid opened the product only when that id happened to appear in the page
 * of rows its own search had just returned, and otherwise stayed on the
 * catalogue — so a shared product link landed on a list, or on nothing, and the
 * id was discarded. `MarketplaceProduct` resolves the id against the listing
 * read endpoint and can open any listing.
 *
 * Three resolvers is the real hazard here, not any one of them: fixing one and
 * shipping is how "it works from a push but not from Safari" happens. These
 * tests assert each one, and then assert they agree.
 */

import { linking } from "../linking";
import { nativeObjectDestination, openNativeRoute } from "../nativeRouteActions";
import { navigationRef, routeNotificationTarget, setNotificationRouteReporter } from "../notificationRouting";

const LISTING_ID = 163;
const PRODUCT_PATH = `/pulse/marketplace/${LISTING_ID}`;
const PRODUCT_SCREEN = "MarketplaceProduct";

/** The screen the deepest route in a navigation state names. */
function deepestRoute(state: any): { name?: string; params?: any } {
  let route = state?.routes?.[state.routes.length - 1];
  while (route?.state?.routes?.length) {
    route = route.state.routes[route.state.routes.length - 1];
  }
  return route || {};
}

function withReadyNavigation(run: (navigate: jest.SpyInstance) => Promise<void>) {
  const isReady = jest.spyOn(navigationRef, "isReady").mockReturnValue(true);
  const navigate = jest.spyOn(navigationRef, "navigate").mockImplementation(() => undefined);
  return run(navigate).finally(() => {
    navigate.mockRestore();
    isReady.mockRestore();
  });
}

describe("the in-app resolver", () => {
  it("opens the product page for a product path, not the browse grid", () => {
    expect(nativeObjectDestination(PRODUCT_PATH)).toEqual({
      screen: PRODUCT_SCREEN,
      params: { listingId: LISTING_ID, title: "Marketplace" }
    });
  });

  it("resolves the pulsesoc:// form the /open handoff hands over identically", () => {
    // `app_links.app_scheme_url` builds exactly this from the interstitial, so a
    // mismatch here is the website's button opening the app to the wrong place.
    expect(nativeObjectDestination(`pulsesoc://pulse/marketplace/${LISTING_ID}`)).toEqual(
      nativeObjectDestination(PRODUCT_PATH)
    );
  });

  it("carries the id through, so two links are two different products", () => {
    const first = nativeObjectDestination("/pulse/marketplace/163");
    const second = nativeObjectDestination("/pulse/marketplace/164");
    expect(first?.params?.listingId).toBe(163);
    expect(second?.params?.listingId).toBe(164);
  });

  it("leaves the bare marketplace path on the catalogue", () => {
    // The grid is the right answer for a link that names no product. What it is
    // not is the answer for a link that names one.
    const { navigation, calls } = { navigation: { navigate: (screen: string, params?: any) => calls.push({ screen, params }) }, calls: [] as any[] };
    openNativeRoute(navigation, "/pulse/marketplace");
    expect(calls).toEqual([{ screen: "Tabs", params: { screen: "Marketplace" } }]);
  });
});

describe("the route table iOS hands a cold launch to", () => {
  it("resolves the product path to the product screen with the id parsed", () => {
    const state = linking.getStateFromPath?.(PRODUCT_PATH, undefined as any);
    expect(deepestRoute(state)).toMatchObject({
      name: PRODUCT_SCREEN,
      params: { listingId: LISTING_ID }
    });
  });

  it("parses the id as a number, so the screen can query with it", () => {
    const state = linking.getStateFromPath?.(PRODUCT_PATH, undefined as any);
    expect(typeof deepestRoute(state).params?.listingId).toBe("number");
  });

  it("does not claim a sibling static path as a listing id", () => {
    const state = linking.getStateFromPath?.("/pulse/marketplace/create", undefined as any);
    expect(deepestRoute(state).name).not.toBe(PRODUCT_SCREEN);
  });
});

describe("the target replayed after a signed-out arrival logs in", () => {
  beforeEach(() => setNotificationRouteReporter(() => undefined));
  afterEach(() => setNotificationRouteReporter(null));

  it("opens the product the visitor was reading on the website", () =>
    withReadyNavigation(async (navigate) => {
      const result = await routeNotificationTarget(PRODUCT_PATH);
      expect(result.handled).toBe(true);
      expect(navigate).toHaveBeenCalledWith(PRODUCT_SCREEN, {
        listingId: LISTING_ID,
        title: "Marketplace"
      });
    }));

  it("agrees with the resolver an in-app tap uses", () =>
    withReadyNavigation(async (navigate) => {
      await routeNotificationTarget(PRODUCT_PATH);
      const inApp = nativeObjectDestination(PRODUCT_PATH);
      expect(navigate).toHaveBeenCalledWith(inApp?.screen, inApp?.params);
    }));

  it("still sends a product-less marketplace link to the catalogue", () =>
    withReadyNavigation(async (navigate) => {
      await routeNotificationTarget("/pulse/marketplace");
      expect(navigate).toHaveBeenCalledWith("Tabs", { screen: "Marketplace" });
    }));
});

describe("all three doors agree", () => {
  it("names one screen for one path", () =>
    withReadyNavigation(async (navigate) => {
      const inApp = nativeObjectDestination(PRODUCT_PATH);
      const coldStart = deepestRoute(linking.getStateFromPath?.(PRODUCT_PATH, undefined as any));
      await routeNotificationTarget(PRODUCT_PATH);
      const replayed = navigate.mock.calls[0];

      expect(new Set([inApp?.screen, coldStart.name, replayed[0]])).toEqual(
        new Set([PRODUCT_SCREEN])
      );
      // And one product. A resolver that agreed on the screen but dropped the id
      // would open the product page on whatever listing it was last showing.
      expect(new Set([
        inApp?.params?.listingId,
        coldStart.params?.listingId,
        replayed[1]?.listingId
      ])).toEqual(new Set([LISTING_ID]));
    }));
});
