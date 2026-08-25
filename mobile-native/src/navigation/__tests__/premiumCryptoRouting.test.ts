/**
 * The four Premium crypto destinations, and the `/premium` rule they sit above.
 *
 * `portfolioActivation.test.ts` pins the same ordering for the portfolio, and
 * pins it because it regressed: `/pulse/premium/portfolio` is a real server
 * route, `includes("/premium")` matched it several rules early, and a link to a
 * member's own holdings opened the upgrade screen. The other three crypto rules
 * had the identical shape and were still sitting below `/premium`, so this file
 * covers the family rather than the one member that happened to be reported.
 *
 * `/pulse/premium/intelligence` is the case with teeth — it is a real route on
 * the server today, so that one was live. The watchlists and alerts assertions
 * are narrower: no premium-scoped spelling of them is served right now, and
 * they are pinned so that adding one later cannot quietly reintroduce the bug.
 *
 * What makes this failure expensive is that it is invisible in review. Both
 * rules are individually correct; only their order is wrong, and the symptom —
 * the app offering to sell you something you already pay for — reads as a
 * billing bug rather than a routing one.
 */
import { openNativeRoute } from "../nativeRouteActions";

type NavigateCall = { screen: string; params?: any };

function makeNavigation() {
  const calls: NavigateCall[] = [];
  return { navigation: { navigate: (screen: string, params?: any) => calls.push({ screen, params }) }, calls };
}

function resolve(route: string) {
  const { navigation, calls } = makeNavigation();
  openNativeRoute(navigation, route);
  return calls;
}

describe("Premium crypto routes resolve to the crypto screens", () => {
  it.each([
    ["/pulse/premium/intelligence", "IntelligenceCenter"],
    ["/pulse/premium/watchlists", "Watchlists"],
    ["/pulse/premium/crypto/alerts", "AlertManagement"],
    ["/pulse/premium/portfolio", "Portfolio"]
  ])("sends %s to %s rather than to the upgrade screen", (route, screen) => {
    const calls = resolve(route);
    expect({ route, screen: calls[0]?.screen }).toEqual({ route, screen });
    expect(calls).toHaveLength(1);
  });

  it("still sends the actual premium routes to Premium", () => {
    // The half of the fix that proves the rules were reordered rather than the
    // `/premium` rule being broken. Without this, deleting `/premium` entirely
    // would pass every other test in this file.
    for (const route of ["/pulse/premium", "/dashboard/premium", "/dashboard/subscriptions"]) {
      expect({ route, calls: resolve(route) }).toEqual({
        route,
        calls: [{ screen: "Premium", params: undefined }]
      });
    }
  });

  it("prefers the portfolio when a route names both it and intelligence", () => {
    // `/pulse/premium/intelligence/portfolio` is a real server route and matches
    // both rules. The holdings view is the right destination, which is why
    // portfolio is ordered first among the four.
    expect(resolve("/pulse/premium/intelligence/portfolio")[0]?.screen).toBe("Portfolio");
  });

  it("keeps the bare crypto spellings working", () => {
    // The reorder moved these rules past `/premium`; it must not have changed
    // what they match. These are the spellings the dashboard links to.
    expect(resolve("/dashboard/crypto/watchlists")[0]?.screen).toBe("Watchlists");
    expect(resolve("/dashboard/crypto/alerts")[0]?.screen).toBe("AlertManagement");
    expect(resolve("/dashboard/intelligence")[0]?.screen).toBe("IntelligenceCenter");
    expect(resolve("/dashboard/crypto/portfolio")[0]?.screen).toBe("Portfolio");
  });

  /**
   * No crypto spelling may reach the legacy webview.
   *
   * `openDashboardRoute` ends in a `DashboardLegacyModule` catch-all that opens
   * the unmatched path in a webview. When the path is one the server does not
   * serve, that webview is what renders "The requested PulseSoc service was not
   * found." — the error this mission was opened to remove. So the failure mode
   * is not "a rule is missing", it is "a rule is missing *and* the fallback
   * makes it look like a backend outage."
   *
   * Bare `/alerts` was the live instance: its three siblings all accepted their
   * bare spelling and it did not.
   */
  it.each([
    ["/watchlists", "Watchlists"],
    ["/pulse/watchlists", "Watchlists"],
    ["/crypto/watchlists", "Watchlists"],
    ["/alerts", "AlertManagement"],
    ["/crypto/alerts", "AlertManagement"],
    ["/pulse/crypto/alerts", "AlertManagement"],
    ["/portfolio", "Portfolio"],
    ["/pulse/portfolio", "Portfolio"],
    ["/intelligence", "IntelligenceCenter"],
    ["/pulse/intelligence", "IntelligenceCenter"]
  ])("resolves %s natively instead of dropping it in the webview", (route, screen) => {
    const calls = resolve(route);
    expect({ route, screen: calls[0]?.screen }).toEqual({ route, screen });
  });

  it("leaves whale alerts to its own module", () => {
    // The alerts rule matches `/alerts`, and `/dashboard/crypto/whale-alerts`
    // is separated by a hyphen rather than a slash, so it is not swept up. This
    // is the one neighbour a broader alerts rule could plausibly have stolen.
    expect(resolve("/dashboard/crypto/whale-alerts")[0]?.screen).not.toBe("AlertManagement");
  });

  it("resolves every destination the Premium crypto section navigates to", () => {
    // PremiumCenterScreen navigates natively rather than through this resolver,
    // so this is a consistency check: a link to any of its four destinations,
    // arriving from the web or a notification, must land on the same screen the
    // tile opens. If the two ever disagree, one of them is showing the wrong UI.
    const destinations = ["Watchlists", "AlertManagement", "Portfolio", "IntelligenceCenter"];
    const resolved = [
      "/pulse/premium/watchlists",
      "/pulse/premium/crypto/alerts",
      "/pulse/premium/portfolio",
      "/pulse/premium/intelligence"
    ].map((route) => resolve(route)[0]?.screen);
    expect(resolved).toEqual(destinations);
  });
});
