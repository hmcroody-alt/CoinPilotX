/**
 * The seller gate at the one chokepoint, and the card-payment door beside it.
 *
 * `navigate("SellerStore", …)` is called from the Business sections grid, two
 * dashboard-routing paths, a deep-link handler and a notification router. The
 * gate lives in `SellerStoreRoute` rather than at those callers precisely so
 * there is one copy of the rule — and this file is what stops the copy from
 * quietly becoming conditional.
 *
 * The two screens behind the route are mocked to bare markers. The question
 * here is *which* of them renders and *when*, which neither screen can answer
 * about itself, and mounting the real dashboard would drag its whole data layer
 * into a test about a boolean.
 */

jest.mock("../../i18n", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
  useFormatters: () => ({ count: (n: number) => String(n) })
}));

const mockUseSellerAccess = jest.fn();
jest.mock("../../marketplace/useSellerAccess", () => ({
  useSellerAccess: () => mockUseSellerAccess()
}));

jest.mock("../StoreDashboardScreen", () => {
  const { Text } = require("react-native");
  return {
    StoreDashboardScreen: (props: { cardPaymentStatus?: string }) => (
      <Text testID="dashboard">{`dashboard:${props.cardPaymentStatus ?? "none"}`}</Text>
    )
  };
});

jest.mock("../SellerStoreScreen", () => {
  const { Text } = require("react-native");
  return { SellerStoreScreen: () => <Text testID="legacy-store">legacy</Text> };
});

import { fireEvent, render } from "@testing-library/react-native";
import { SellerStoreRoute, requiresSellerApproval } from "../SellerStoreRoute";
import { DENIED_SELLER_ACCESS, parseSellerAccessState } from "../../api/sellerAccess";
import type { RootStackParamList } from "../../navigation/types";

const refresh = jest.fn();

function accessOf(status: string, over: Record<string, unknown> = {}) {
  return {
    state: parseSellerAccessState({ seller_application_status: status, ...over }),
    loading: false,
    failed: false,
    stale: false,
    unsupported: false,
    refresh
  };
}

function navigation() {
  return { navigate: jest.fn(), goBack: jest.fn() };
}

function open(params: RootStackParamList["SellerStore"], nav = navigation()) {
  const view = render(<SellerStoreRoute route={{ params }} navigation={nav} />);
  return { view, nav };
}

beforeEach(() => {
  mockUseSellerAccess.mockReset();
  refresh.mockReset();
  mockUseSellerAccess.mockReturnValue(accessOf("APPROVED"));
});

/* ------------------------------------------------------------------ *
 * Which modes are gated
 * ------------------------------------------------------------------ */

describe("requiresSellerApproval", () => {
  it("gates the seller tools", () => {
    for (const mode of ["dashboard", "create", "payouts"] as const) {
      expect(requiresSellerApproval({ mode })).toBe(true);
    }
  });

  it("leaves open the screens that would strand someone if closed", () => {
    // `apply` is the only screen that can clear the gate; gating it would send
    // a seller to finish their application and then refuse them entry to it.
    // `orders` is how a suspended seller meets obligations to buyers who have
    // already paid — and the gate's own suspended copy routes *to* it, so
    // closing it would shut a door the gate opens.
    for (const mode of ["apply", "orders", "profile", "overview"] as const) {
      expect(requiresSellerApproval({ mode })).toBe(false);
    }
  });

  it("does not gate a route with no mode at all", () => {
    expect(requiresSellerApproval(undefined)).toBe(false);
    expect(requiresSellerApproval({} as RootStackParamList["SellerStore"])).toBe(false);
  });
});

/* ------------------------------------------------------------------ *
 * The gate itself
 * ------------------------------------------------------------------ */

describe("the Store dashboard behind the gate", () => {
  it("opens for an approved seller", () => {
    const { view } = open({ mode: "dashboard" });
    expect(view.queryByTestId("dashboard")).toBeTruthy();
    expect(view.queryByTestId("seller-access-gate")).toBeNull();
  });

  it("stays shut for a draft application", () => {
    // The production bug, exactly: four accounts holding a half-finished
    // application were handed a full seller dashboard, which then loaded empty
    // and read as "your store is broken" rather than "you haven't applied yet".
    mockUseSellerAccess.mockReturnValue(accessOf("DRAFT"));
    const { view } = open({ mode: "dashboard" });
    expect(view.queryByTestId("dashboard")).toBeNull();
    expect(view.queryByTestId("seller-access-gate")).toBeTruthy();
  });

  it("stays shut for every unapproved status", () => {
    for (const status of [
      "NO_APPLICATION",
      "DRAFT",
      "SUBMITTED",
      "UNDER_REVIEW",
      "MORE_INFORMATION_REQUIRED",
      "DECLINED",
      "SUSPENDED"
    ] as const) {
      mockUseSellerAccess.mockReturnValue(accessOf(status));
      const { view } = open({ mode: "dashboard" });
      expect(view.queryByTestId("dashboard")).toBeNull();
      view.unmount();
    }
  });

  it("stays shut while the verdict is still loading", () => {
    // Rendering seller tools during `loading` would reintroduce the bug,
    // narrowed to the first few hundred milliseconds of every cold launch —
    // which is long enough to see.
    mockUseSellerAccess.mockReturnValue({
      state: DENIED_SELLER_ACCESS,
      loading: true,
      failed: false,
      stale: false,
      unsupported: false,
      refresh
    });
    const { view } = open({ mode: "dashboard" });
    expect(view.queryByTestId("dashboard")).toBeNull();
    expect(view.queryByTestId("seller-access-gate-loading")).toBeTruthy();
  });

  it("does not gate an ungated mode even for an account with no application", () => {
    mockUseSellerAccess.mockReturnValue(accessOf("NO_APPLICATION"));
    const { view } = open({ mode: "orders", title: "Orders" });
    expect(view.queryByTestId("legacy-store")).toBeTruthy();
    expect(view.queryByTestId("seller-access-gate")).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * What a blocked seller is offered
 * ------------------------------------------------------------------ */

describe("the gate's way out", () => {
  it("sends an applicant to the application screen", () => {
    mockUseSellerAccess.mockReturnValue(accessOf("DRAFT"));
    const { view, nav } = open({ mode: "dashboard" });
    fireEvent.press(view.getByTestId("seller-access-gate-cta"));
    expect(nav.navigate).toHaveBeenCalledWith("MerchantApply");
  });

  it("sends a suspended seller to their existing orders, not to the form", () => {
    mockUseSellerAccess.mockReturnValue(accessOf("SUSPENDED"));
    const { view, nav } = open({ mode: "dashboard" });
    fireEvent.press(view.getByTestId("seller-access-gate-cta"));
    expect(nav.navigate).toHaveBeenCalledWith("SellerStore", { mode: "orders", title: "Orders" });
    // And the mode it routes to is one the gate does not itself close.
    expect(requiresSellerApproval({ mode: "orders" })).toBe(false);
  });

  it("offers a suspended seller support as well", () => {
    mockUseSellerAccess.mockReturnValue(accessOf("SUSPENDED"));
    const { view, nav } = open({ mode: "dashboard" });
    fireEvent.press(view.getByTestId("seller-access-gate-secondary"));
    expect(nav.navigate).toHaveBeenCalledWith("TrustSafetySupport");
  });

  it("offers a retry rather than a denial when the verdict could not be read", () => {
    // Telling an approved seller they have no store because the network
    // hiccuped is a worse lie than an honest "we couldn't check". The server
    // re-checks every mutation, so the cost of the honest answer is nothing.
    mockUseSellerAccess.mockReturnValue({
      state: DENIED_SELLER_ACCESS,
      loading: false,
      failed: true,
      stale: true,
      unsupported: false,
      refresh
    });
    const { view, nav } = open({ mode: "dashboard" });
    fireEvent.press(view.getByTestId("seller-access-gate-cta"));
    expect(refresh).toHaveBeenCalled();
    expect(nav.navigate).not.toHaveBeenCalled();
  });
});

/* ------------------------------------------------------------------ *
 * A server that has no gate
 * ------------------------------------------------------------------ */

describe("a deployment without the access-state route", () => {
  /** No verdict, no cache, and a 404 that will 404 again on every retry. */
  function unsupportedAccess() {
    return {
      state: DENIED_SELLER_ACCESS,
      loading: false,
      failed: false,
      stale: false,
      unsupported: true,
      refresh
    };
  }

  it("opens the Store instead of walling it off", () => {
    // Observed on a real device: the app shipped ahead of the route, so every
    // fetch 404'd, nothing was ever cached, and the state stayed denied. The
    // result was "We couldn't check your seller status" in front of *every*
    // seller, permanently, with a Try again that could never succeed.
    //
    // Falling open is safe here and only here: the gate is routing, not
    // authorization. A server without this route also lacks the enforcement
    // behind it, so the seller surfaces there are governed by the checks that
    // were already in place, and every mutation is re-checked server-side
    // regardless. A client gate stricter than its server protects nothing
    // while taking a working store away from an approved seller.
    mockUseSellerAccess.mockReturnValue(unsupportedAccess());
    const { view } = open({ mode: "dashboard" });
    expect(view.queryByTestId("seller-access-gate")).toBeNull();
    expect(view.queryByTestId("dashboard")).toBeTruthy();
  });

  it("does not show the retry that would never succeed", () => {
    mockUseSellerAccess.mockReturnValue(unsupportedAccess());
    const { view } = open({ mode: "dashboard" });
    expect(view.queryByTestId("seller-access-gate-cta")).toBeNull();
    expect(view.queryByTestId("seller-access-gate-loading")).toBeNull();
  });

  it("still gates when the route exists and answers no", () => {
    // The negative control for the rule above. `unsupported` must mean "there
    // is no gate on this server", never "the gate said no" — if the two ever
    // collapse, the fix for the wall becomes a hole.
    mockUseSellerAccess.mockReturnValue(accessOf("DRAFT"));
    const { view } = open({ mode: "dashboard" });
    expect(view.queryByTestId("seller-access-gate")).toBeTruthy();
    expect(view.queryByTestId("dashboard")).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * The other axis
 * ------------------------------------------------------------------ */

describe("card-payment status travels with the approved seller", () => {
  it("hands the dashboard the status rather than making it fetch again", () => {
    mockUseSellerAccess.mockReturnValue(accessOf("APPROVED", { card_payment_status: "READY" }));
    const { view } = open({ mode: "dashboard" });
    expect(view.getByTestId("dashboard").props.children).toBe("dashboard:READY");
  });

  it("opens the store for an approved seller who cannot yet take cards", () => {
    // The separation of axes, from the screen's side: no Connect account is a
    // card-payments problem, never a locked storefront.
    mockUseSellerAccess.mockReturnValue(
      accessOf("APPROVED", { card_payment_status: "SETUP_REQUIRED" })
    );
    const { view } = open({ mode: "dashboard" });
    expect(view.queryByTestId("seller-access-gate")).toBeNull();
    expect(view.getByTestId("dashboard").props.children).toBe("dashboard:SETUP_REQUIRED");
  });
});
