/**
 * Where a seller lands when Stripe hands them back.
 *
 * The server's Connect return page opens `pulsesoc://pulse/merchant/payouts`.
 * That path is the one the onboarding link has always carried, and until now
 * nothing in `config.screens` claimed it — so the seller stayed in the browser
 * looking at a web page about an approval they already had.
 *
 * Claiming it is not simply a matter of adding a line, because `MerchantProfile`
 * is declared as `pulse/merchant/:sellerId` and that template matches the
 * literal segment `payouts` perfectly well. The failure mode of getting the
 * ranking wrong is not a dead link; it is a *merchant profile for a seller
 * called "payouts"* — a plausible-looking wrong screen, which is exactly the
 * kind of thing a manual pass reports as "it opened the app, looks fine".
 *
 * So this resolves the real path through the real config rather than asserting
 * that a string appears in the file. React Navigation's ranking is the thing
 * under test; a source grep would pass whichever way it ranked.
 */

import { getStateFromPath } from "@react-navigation/native";

import { linking } from "../linking";

type Resolved = { name?: string; params?: Record<string, unknown> } | undefined;

/** The leaf route a path resolves to, with its params. */
function resolve(path: string): Resolved {
  const state = linking.getStateFromPath
    ? linking.getStateFromPath(path, linking.config as never)
    : getStateFromPath(path, linking.config as never);
  let route = state?.routes?.[state.routes.length - 1] as Resolved;
  // Descend into nested navigators (Tabs) so a leaf is always compared.
  let guard = 0;
  while (route && (route.params as { screen?: string })?.screen && guard < 5) {
    const params = route.params as { screen?: string; params?: Record<string, unknown> };
    route = { name: params.screen, params: params.params };
    guard += 1;
  }
  return route;
}

describe("the Stripe Connect return path", () => {
  it("opens the money layer, not a merchant profile", () => {
    const route = resolve("/pulse/merchant/payouts");
    expect(route?.name).toBe("MoneyLayer");
  });

  it("does not resolve to a seller whose id is the word payouts", () => {
    // Stated separately from the assertion above because this is the specific
    // wrong answer, and a future refactor could break the ranking in a way that
    // produces some third route while still failing the first test.
    const route = resolve("/pulse/merchant/payouts");
    expect(route?.name).not.toBe("MerchantProfile");
    expect(route?.params?.sellerId).toBeUndefined();
  });

  it("carries the layer the server chose", () => {
    // The server picks the landing by return state: a seller who is live wants
    // the overview, a seller with steps left wants the setup surface.
    const route = resolve("/pulse/merchant/payouts?layer=payout_onboarding");
    expect(route?.name).toBe("MoneyLayer");
    expect(route?.params?.layer).toBe("payout_onboarding");

    const overview = resolve("/pulse/merchant/payouts?layer=payout_overview");
    expect(overview?.params?.layer).toBe("payout_overview");
  });

  it("still lands somewhere truthful when no layer is given", () => {
    // An older link, or one a seller saved. The screen falls back to
    // `payout_overview` for an absent or unrecognised layer, so the absence of
    // a param must not stop the route resolving.
    const route = resolve("/pulse/merchant/payouts");
    expect(route?.name).toBe("MoneyLayer");
    expect(route?.params?.layer).toBeUndefined();
  });

  it("leaves real merchant profiles alone", () => {
    // Negative control for the ranking: if the new entry were greedy, or if it
    // were declared with a param segment by mistake, this would start resolving
    // to MoneyLayer and the whole storefront family would quietly break.
    const route = resolve("/pulse/merchant/acme-supplies");
    expect(route?.name).toBe("MerchantProfile");
    expect(route?.params?.sellerId).toBe("acme-supplies");
  });

  it("leaves the sibling merchant routes alone", () => {
    expect(resolve("/pulse/merchant/apply")?.name).toBe("MerchantApply");
    expect(resolve("/pulse/merchant/dashboard")?.name).toBe("MerchantDashboard");
  });

  it("resolves the custom scheme the return page actually uses", () => {
    // Same-domain universal links do not open the app — iOS treats a tap on a
    // pulsesoc.com link from a pulsesoc.com page as in-site navigation and never
    // consults the association. The return page therefore uses `pulsesoc://`,
    // so that is the form worth proving resolves.
    expect(linking.prefixes).toContain("pulsesoc://");
  });
});
