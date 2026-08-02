/**
 * The `SellerStore` route now has two screens behind it. This pins the split.
 *
 * The risk the split creates is silent: if the predicate widened, `mode:
 * "orders"` — which the Business "Orders" card uses — would stop rendering the
 * screen that has an orders panel, and nothing would fail to compile. Deep
 * links (`pulse/merchant/...`) and the `MerchantDashboard` / `MerchantProfile`
 * aliases have the same exposure.
 *
 * Only `mode: "dashboard"` diverts. Everything else, including no params at
 * all, keeps reaching `SellerStoreScreen`.
 */

// The predicate is the unit under test. Stubbing the two screens keeps their
// import graphs — `expo-av` among them — out of a test about routing.
jest.mock("../SellerStoreScreen", () => ({ SellerStoreScreen: () => null }));
jest.mock("../StoreDashboardScreen", () => ({ StoreDashboardScreen: () => null }));

import { isStoreDashboardRoute } from "../SellerStoreRoute";
import { sellerStoreShowsPanel } from "../../navigation/sellerStoreMode";

describe("isStoreDashboardRoute", () => {
  it("diverts only the dashboard mode", () => {
    expect(isStoreDashboardRoute({ mode: "dashboard" })).toBe(true);
  });

  it("leaves every other registered mode on the existing screen", () => {
    (["overview", "apply", "profile", "create", "payouts", "orders"] as const).forEach((mode) => {
      expect(isStoreDashboardRoute({ mode })).toBe(false);
    });
  });

  it("leaves a route with no params on the existing screen", () => {
    // Deep links and the Merchant aliases arrive without a mode.
    expect(isStoreDashboardRoute(undefined)).toBe(false);
    expect(isStoreDashboardRoute({})).toBe(false);
    expect(isStoreDashboardRoute({ title: "Store" })).toBe(false);
  });

  it("keeps a mode that still renders the listing editor", () => {
    // The editor lives in the `listings` panel. The dashboard took over
    // `mode: "dashboard"`, so at least one other mode must still carry it —
    // that is where row taps and Edit are routed.
    const withEditor = (["overview", "apply", "profile", "create", "payouts", "orders"] as const)
      .filter((mode) => sellerStoreShowsPanel(mode, "listings"))
      .filter((mode) => !isStoreDashboardRoute({ mode }));

    expect(withEditor).toContain("create");
    // And the dashboard mode is precisely the one that no longer reaches it,
    // which is why row taps go to `create` rather than staying put.
    expect(sellerStoreShowsPanel("create", "listings")).toBe(true);
  });
});
