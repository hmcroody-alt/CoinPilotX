/**
 * One URL, one destination.
 *
 * `/pulse/merchant/apply` is reachable three ways: a cold-start deep link
 * (`linking.ts`), a notification tap (`notificationRouting.ts`), and an in-app
 * button (`SellerStoreScreen`). Until this was fixed the notification path
 * resolved the URL to a different screen than the deep link did, so the same
 * reviewer notification opened the application for a user who tapped it from a
 * cold start and opened a panel containing a button to the application for a
 * user whose app was already running.
 *
 * These tests pin the agreement between the two resolvers. They assert on the
 * screen name rather than on rendered output on purpose: the failure being
 * guarded against is a routing table drifting out of step with another routing
 * table, which no screen-level test can see.
 */

import { navigationRef, routeNotificationTarget } from "../notificationRouting";
import { linking } from "../linking";

function screenPathFromLinking(screen: string): string | undefined {
  const screens = (linking.config?.screens || {}) as Record<string, unknown>;
  const entry = screens[screen];
  if (typeof entry === "string") return entry;
  if (entry && typeof entry === "object" && typeof (entry as { path?: string }).path === "string") {
    return (entry as { path: string }).path;
  }
  return undefined;
}

describe("seller application entry points", () => {
  let navigate: jest.SpyInstance;
  let isReady: jest.SpyInstance;

  beforeEach(() => {
    isReady = jest.spyOn(navigationRef, "isReady").mockReturnValue(true);
    navigate = jest.spyOn(navigationRef, "navigate").mockImplementation(() => undefined);
  });

  afterEach(() => {
    navigate.mockRestore();
    isReady.mockRestore();
  });

  it("opens the application screen itself for a notification tap", async () => {
    const result = await routeNotificationTarget("/pulse/merchant/apply");
    expect(result.handled).toBe(true);
    expect(navigate).toHaveBeenCalledWith("MerchantApply", { title: "Merchant Application" });
  });

  it("never detours an applicant through the seller store", async () => {
    await routeNotificationTarget("/pulse/merchant/apply");
    const destinations = navigate.mock.calls.map((call) => call[0]);
    expect(destinations).not.toContain("SellerStore");
  });

  it("resolves the same path the cold-start deep link resolves", () => {
    // The leading slash is the only difference in shape; if these two ever
    // describe different paths, one of the entry points is broken.
    expect(screenPathFromLinking("MerchantApply")).toBe("pulse/merchant/apply");
  });

  it("carries query strings and trailing segments into the application", async () => {
    // Reviewers append tracking parameters to the notification target. A stricter
    // equality check here would send those taps to the unmapped-path fallback.
    await routeNotificationTarget("/pulse/merchant/apply?source=review_email");
    expect(navigate).toHaveBeenCalledWith("MerchantApply", { title: "Merchant Application" });
  });

  it("still sends the other merchant paths to the seller store", async () => {
    await routeNotificationTarget("/pulse/merchant/dashboard");
    expect(navigate).toHaveBeenCalledWith("SellerStore", { title: "Merchant Dashboard", mode: "dashboard" });

    navigate.mockClear();
    await routeNotificationTarget("/pulse/merchant/payouts");
    expect(navigate).toHaveBeenCalledWith("SellerStore", { title: "Merchant Payouts", mode: "payouts" });
  });

  it("does not mistake a merchant profile handle for the application", async () => {
    await routeNotificationTarget("/pulse/merchant/applewatch-store");
    expect(navigate).toHaveBeenCalledWith("SellerStore", {
      title: "Merchant Profile",
      mode: "profile",
      sellerId: "applewatch-store"
    });
  });
});

/**
 * A registered route with nothing pointing at it.
 *
 * `Dropshipping` and its eight siblings were in `AppNavigator` from the start,
 * so every screen-level test of them passed and typechecking was satisfied: the
 * route existed. What did not exist was any way for a merchant to arrive. The
 * single `navigate("Dropshipping")` call sat in `StoreDashboardScreen`, a file
 * registered in no navigator, and `linking.ts` never named the route at all.
 *
 * That is invisible to a test that renders a screen, because the screen renders
 * perfectly once you are on it. It is only visible by asking the two routing
 * tables whether anything leads there — which is what these assert, in the same
 * spirit as the merchant-apply tests above.
 */
describe("dropshipping hub reachability", () => {
  it("is named by a deep link", () => {
    expect(screenPathFromLinking("Dropshipping")).toBe("pulse/dropshipping");
  });

  it("takes no route parameters, so the URL is usable by a human", () => {
    // `DropshippingProducts` and its siblings need a `connectionId` nobody
    // knows. If the hub ever grows a required param this link silently starts
    // resolving to a screen with `undefined` where an id should be.
    const screens = (linking.config?.screens || {}) as Record<string, unknown>;
    const entry = screens.Dropshipping;
    expect(typeof entry).toBe("string");
  });

  it("does not link the sibling routes that require an id", () => {
    // Deliberate, not an oversight: a `connectionId` belongs in a tap from the
    // hub, not in a URL a merchant is expected to produce.
    for (const sibling of ["DropshippingProducts", "DropshippingCatalog", "DropshippingCart", "DropshippingDraft"]) {
      expect(screenPathFromLinking(sibling)).toBeUndefined();
    }
  });
});
