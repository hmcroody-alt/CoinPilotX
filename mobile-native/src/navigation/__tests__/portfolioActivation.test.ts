/**
 * Can a user actually reach the Portfolio, and does it open the right screen?
 *
 * A registered `Stack.Screen` is not a reachable one. The portfolio screen, its
 * API writes and eleven catalogs can all be correct while nothing in the app
 * navigates to it — a feature that exists only in the source tree. So the two
 * halves are pinned separately: the catalog advertises it, and the resolver
 * turns the advertised route into this screen.
 *
 * The ordering case at the bottom is the one worth having. `/pulse/premium/
 * portfolio` is a real server route, and the dashboard resolver matched
 * `/premium` several rules before it would ever have looked for `/portfolio` —
 * so a link to a member's own holdings opened the upgrade screen. That failure
 * is invisible in review (both rules are individually correct) and reads to a
 * member as the app trying to sell them something they already have.
 */
import { openNativeRoute } from "../nativeRouteActions";
import { flattenMasterNavigation } from "../masterNavigation";

type NavigateCall = { screen: string; params?: any };

function makeNavigation() {
  const calls: NavigateCall[] = [];
  return { navigation: { navigate: (screen: string, params?: any) => calls.push({ screen, params }) }, calls };
}

const PORTFOLIO_ENTRY = flattenMasterNavigation().find((action) => action.label === "Portfolio");

describe("Portfolio is reachable from navigation", () => {
  it("exists in the Intelligence section as a native destination", () => {
    // Without this the rest of the file would assert about `undefined` and pass.
    expect(PORTFOLIO_ENTRY).toBeDefined();
    expect(PORTFOLIO_ENTRY?.section).toBe("Intelligence");
    expect(PORTFOLIO_ENTRY?.status).toBe("native");
  });

  it("opens the native screen from the route the catalog advertises", () => {
    const { navigation, calls } = makeNavigation();
    openNativeRoute(navigation, PORTFOLIO_ENTRY!.route);
    // No title param: the header then uses `common:screens.portfolio`, so the
    // title is translated rather than pinned to English by the caller.
    expect(calls).toEqual([{ screen: "Portfolio", params: undefined }]);
  });

  it.each(["/portfolio", "/pulse/portfolio", "/pulse/premium/portfolio", "/dashboard/crypto/portfolio"])(
    "lands %s on the portfolio",
    (route) => {
      // Every spelling the server actually serves, plus the dashboard one. A
      // link that worked on the web must not open a different screen here.
      const { navigation, calls } = makeNavigation();
      openNativeRoute(navigation, route);
      expect(calls).toEqual([{ screen: "Portfolio", params: undefined }]);
    }
  );

  it("still sends the premium routes to the Premium screen", () => {
    // The new rule sits above `/premium`, so this is the case that proves it
    // was narrowed by ordering rather than by breaking the rule beneath it.
    for (const route of ["/pulse/premium", "/dashboard/subscriptions", "/dashboard/premium"]) {
      const { navigation, calls } = makeNavigation();
      openNativeRoute(navigation, route);
      expect({ route, calls }).toEqual({ route, calls: [{ screen: "Premium", params: undefined }] });
    }
  });

  it("does not swallow the neighbouring crypto routes", () => {
    for (const route of ["/dashboard/crypto/alerts", "/dashboard/crypto/watchlists"]) {
      const { navigation, calls } = makeNavigation();
      openNativeRoute(navigation, route);
      expect(calls).toHaveLength(1);
      expect(calls[0].screen).not.toBe("Portfolio");
    }
  });
});
