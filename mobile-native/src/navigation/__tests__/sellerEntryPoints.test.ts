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
