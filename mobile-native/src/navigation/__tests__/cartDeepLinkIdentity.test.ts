/**
 * "Open cart in the app" opens the cart — through every door into the app.
 *
 * The reported defect: on pulsesoc.com, "Open cart in the app" -> "Open PulseSoc"
 * opened the app on NOTIFICATIONS. The web chain was never wrong. The button
 * builds `open_interstitial_url("cart")`, the interstitial's own button carries
 * `app_links.app_scheme_url("/pulse/cart")`, and that is exactly
 * `pulsesoc://pulse/cart` — the destination was preserved the whole way to iOS.
 *
 * It was lost inside the app, by all three of the resolvers that own this path:
 *
 *   - `nativeObjectDestination` — returned null, so an in-app tap fell through
 *     `openNativeRoute` to `openDashboardRoute` and opened `DashboardLegacyModule`
 *     titled "Dashboard Module".
 *   - `linking.getStateFromPath` — `config.screens` declared no path for
 *     `MarketplaceCart`, so a cold launch produced `undefined` and React
 *     Navigation dropped the link without a trace.
 *   - `routeNotificationTarget` — had no cart branch, so `/pulse/cart` fell past
 *     every check to `navigateToNotifications()`. This is the one a member sees:
 *     `App.tsx` pipes every OS-delivered URL through it, so it answered first and
 *     answered the Activity Inbox. CART became NOTIFICATIONS here, on line one of
 *     the fallback.
 *
 * `MarketplaceCart` was registered in `AppNavigator` the whole time, and
 * `services/app_links.py` had declared the destination `native_supported` and
 * pointed it at that screen. A Python gate even recorded the gap — as *accepted
 * debt*, on the stated grounds that no on-site button reached it. Shipping the web
 * cart made that false without anyone revisiting the note.
 *
 * So these tests assert each resolver, and then assert they agree. Fixing one and
 * shipping is how "it works from a push but not from Safari" happens.
 */

import { getPathFromState } from "@react-navigation/native";

import { linking } from "../linking";
import { nativeObjectDestination, openNativeRoute } from "../nativeRouteActions";
import { navigationRef, routeNotificationTarget, setNotificationRouteReporter } from "../notificationRouting";

const CART_PATH = "/pulse/cart";
const CART_SCREEN = "MarketplaceCart";
/** The literal string `app_links.app_scheme_url("/pulse/cart")` builds. */
const CART_SCHEME_URL = "pulsesoc://pulse/cart";

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

function recordingNavigation() {
  const calls: Array<{ screen: string; params?: unknown }> = [];
  return {
    calls,
    navigation: { navigate: (screen: string, params?: unknown) => calls.push({ screen, params }) }
  };
}

describe("the in-app resolver", () => {
  it("names the cart screen for the cart path", () => {
    expect(nativeObjectDestination(CART_PATH)?.screen).toBe(CART_SCREEN);
  });

  it("resolves the pulsesoc:// form the interstitial hands over identically", () => {
    // A mismatch here is the website's button opening the app to the wrong place,
    // which is the whole defect. `canonicalNativeRoute` has to fold the scheme's
    // host (`pulse`) back into the path for these to be the same request.
    expect(nativeObjectDestination(CART_SCHEME_URL)).toEqual(nativeObjectDestination(CART_PATH));
  });

  it("carries no cart identity in the link", () => {
    // `marketplace_cart_items` is keyed on the buyer, server-side. The screen asks
    // who is signed in, so the URL needs no token and must not grow one: a cart id
    // in a shareable link is another member's cart one guess away.
    expect(nativeObjectDestination(CART_PATH)?.params).toBeUndefined();
  });

  it("opens the cart on an in-app tap instead of a legacy dashboard module", () => {
    const { navigation, calls } = recordingNavigation();
    openNativeRoute(navigation, CART_PATH);
    expect(calls).toEqual([{ screen: CART_SCREEN, params: undefined }]);
  });

  it("does not claim a neighbouring path as the cart", () => {
    expect(nativeObjectDestination("/pulse/carts")).toBeNull();
    expect(nativeObjectDestination("/pulse/cart/9")).toBeNull();
  });
});

describe("the route table iOS hands a cold launch to", () => {
  it("resolves the cart path instead of returning nothing", () => {
    const state = linking.getStateFromPath?.(CART_PATH, undefined as any);
    // The pre-fix value was literally `undefined` — React Navigation was handed no
    // state at all and silently kept whatever screen it already had.
    expect(state).toBeDefined();
    expect(deepestRoute(state).name).toBe(CART_SCREEN);
  });

  it("still sends the marketplace tab path to the catalogue", () => {
    const state = linking.getStateFromPath?.("/pulse/marketplace", undefined as any);
    expect(deepestRoute(state).name).not.toBe(CART_SCREEN);
  });

  it("generates the cart path back out of the route table", () => {
    // `getStateFromPath` asks `nativeObjectDestination` before it ever reaches
    // `config.screens`, so the inbound tests above pass whether or not the config
    // declares a cart path. This is the direction that reads the declaration: the
    // app generating its own link, and — via `declared_native_paths()` — the only
    // thing `tests/web_surface/test_scheme_urls_match_the_native_route_table.py`
    // can see when it checks that `app_links.py`'s `native_supported` cart has a
    // screen behind it. Without this assertion the `linking.ts` entry has no
    // JS-side guard at all.
    const path = getPathFromState(
      { routes: [{ name: CART_SCREEN }] } as any,
      linking.config as any
    );
    expect(path.split("?")[0]).toBe(CART_PATH);
  });
});

describe("the target replayed after a signed-out arrival logs in", () => {
  beforeEach(() => setNotificationRouteReporter(() => undefined));
  afterEach(() => setNotificationRouteReporter(null));

  it("opens the cart rather than the Activity Inbox", () =>
    withReadyNavigation(async (navigate) => {
      const result = await routeNotificationTarget(CART_PATH);
      expect(result.target).toBe(CART_PATH);
      expect(navigate).toHaveBeenCalledWith(CART_SCREEN, undefined);
      expect(navigate).not.toHaveBeenCalledWith("ActivityInbox", expect.anything());
    }));

  it("no longer reports the cart as an unroutable fallback", () =>
    withReadyNavigation(async () => {
      const result = await routeNotificationTarget(CART_PATH);
      expect(result.reason).not.toBe("native_fallback");
      expect(result.fallbackFrom).toBeUndefined();
    }));

  it("resolves the scheme URL the interstitial emits", () =>
    withReadyNavigation(async (navigate) => {
      await routeNotificationTarget(CART_SCHEME_URL);
      expect(navigate).toHaveBeenCalledWith(CART_SCREEN, undefined);
    }));

  it("sends the app's front door to Home, not to notifications", () =>
    withReadyNavigation(async (navigate) => {
      // `/pulse` is the most generic "open the app" target there is, and it was
      // reaching the same Notifications fallback the cart did.
      await routeNotificationTarget("/pulse");
      expect(navigate).toHaveBeenCalledWith("Tabs", { screen: "Home" });
    }));

  it("still sends a genuine notifications target to the Activity Inbox", () =>
    withReadyNavigation(async (navigate) => {
      // The fallback screen is not wrong; claiming a different request was for it
      // is. This is the assertion that keeps the fix from becoming a regression.
      await routeNotificationTarget("/pulse/notifications");
      expect(navigate).toHaveBeenCalledWith("ActivityInbox", { title: "Activity Inbox" });
    }));

  it("still reports a genuinely unknown path as a fallback", () =>
    withReadyNavigation(async () => {
      const result = await routeNotificationTarget("/pulse/unmapped-analytics-page");
      expect(result.reason).toBe("native_fallback");
    }));
});

describe("all three doors agree", () => {
  beforeEach(() => setNotificationRouteReporter(() => undefined));
  afterEach(() => setNotificationRouteReporter(null));

  it("names one screen for one path", () =>
    withReadyNavigation(async (navigate) => {
      const inApp = nativeObjectDestination(CART_PATH);
      const coldStart = deepestRoute(linking.getStateFromPath?.(CART_PATH, undefined as any));
      await routeNotificationTarget(CART_PATH);
      const replayed = navigate.mock.calls[0];

      expect(new Set([inApp?.screen, coldStart.name, replayed[0]])).toEqual(
        new Set([CART_SCREEN])
      );
    }));
});
