/**
 * Why the card row is off, said once, by whoever actually knows.
 *
 * This screen used to carry its own account of the card rail: a constant
 * `MARKETPLACE_CARD_PAYMENTS_PAUSED = true`, a row detail reading "Card
 * checkout is temporarily paused", and a footnote under the cash button
 * announcing "Marketplace card payments are paused." That was coherent while
 * the server's pause was also a hard-coded `true` — there was exactly one
 * reason and the binary knew it.
 *
 * There are now two reasons and the binary cannot tell them apart. The
 * platform rail is a deployment flag the operator flips; seller eligibility is
 * a Connect onboarding state that differs per seller and changes without a
 * release. "Temporarily paused" said about a seller who has never onboarded
 * tells the buyer to come back for something that will not change, and blames
 * the platform for the seller's state.
 *
 * So the rule pinned here is narrow and mechanical: every sentence this screen
 * shows about card unavailability is the server's sentence, verbatim. The
 * tests below feed the screen two *different* server verdicts and require the
 * copy to change with them — a screen that hard-coded either sentence would
 * pass one case and fail the other, and a screen that hard-coded some third
 * sentence fails both.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
jest.mock("../../api/marketplace", () => ({ openMarketplaceCheckout: jest.fn() }));
jest.mock("../../api/marketplaceCommerce", () => ({
  checkoutCartGroup: jest.fn(),
  getMarketplacePaymentOrder: jest.fn(),
  validateCart: jest.fn()
}));
jest.mock("../../api/stripePaymentSheet", () => ({
  isPaymentSheetAvailable: () => false,
  presentPaymentSheet: jest.fn()
}));
jest.mock("../../payments/PaymentController", () => ({
  PaymentController: { begin: jest.fn(), release: jest.fn() }
}));

const CLOSED = {
  countries: [],
  cardPaymentsAvailable: false,
  cardBadge: "Temporarily Unavailable",
  cardUnavailableMessage:
    "Marketplace card payments are temporarily unavailable. Choose cash, local pickup, or in-person payment."
};
const mockFetch = jest.fn(async () => CLOSED as unknown);
jest.mock("../../api/checkoutCountries", () => ({
  CHECKOUT_OPTIONS_FALLBACK: CLOSED,
  fetchCheckoutOptions: (...args: unknown[]) => mockFetch(...(args as []))
}));

import { MarketplaceCheckoutScreen } from "../MarketplaceCheckoutScreen";

type Params = React.ComponentProps<typeof MarketplaceCheckoutScreen>["route"]["params"];

/** Digital, so the screen opens straight on review with the payment rows. */
const BASE: Params = {
  mode: "buy_now",
  listingId: 41,
  sellerUserId: 7,
  sellerName: "Test Seller",
  itemTitle: "A thing",
  quantity: 1,
  currency: "USD",
  fulfillmentKind: "digital",
  subtotalMinor: 3800
};

/** The two verdicts the server actually distinguishes, plus the open rail. */
const PLATFORM_OFF = {
  ...CLOSED,
  cardUnavailableMessage:
    "Marketplace card payments are temporarily unavailable. Choose cash, local pickup, or in-person payment."
};
const SELLER_NOT_READY = {
  ...CLOSED,
  cardUnavailableMessage: "Seller has not enabled card payments yet."
};
const OPEN = {
  countries: [],
  cardPaymentsAvailable: true,
  cardBadge: "",
  cardUnavailableMessage: ""
};

function renderWith(options: unknown, params: Params = BASE) {
  mockFetch.mockResolvedValue(options);
  const navigation = { setOptions: jest.fn(), navigate: jest.fn(), replace: jest.fn(), goBack: jest.fn() };
  return render(
    <MarketplaceCheckoutScreen
      route={{ key: "checkout", name: "MarketplaceCheckout", params } as never}
      navigation={navigation as never}
    />
  );
}

beforeEach(() => mockFetch.mockReset());

describe("the card row's unavailable copy", () => {
  it("shows the server's seller-specific sentence when the seller is the blocker", async () => {
    const view = renderWith(SELLER_NOT_READY);
    await waitFor(() => expect(view.getByText("Seller has not enabled card payments yet.")).toBeTruthy());
  });

  it("shows the server's platform sentence when the rail is the blocker", async () => {
    // Two matches, not one: the row and the footnote, agreeing. Asserted as a
    // count because `getByText` throws on multiple, and a bare `getAllByText`
    // would pass just as happily if some later change dropped one of them.
    const view = renderWith(PLATFORM_OFF);
    await waitFor(() => expect(view.getAllByText(/temporarily unavailable\. Choose cash/i).length).toBe(2));
  });

  it("never says 'paused' — the word the screen used to own", async () => {
    // The old hard-coded sentence. It is not merely unused: it is a claim the
    // client is no longer in a position to make, in either verdict.
    for (const options of [SELLER_NOT_READY, PLATFORM_OFF]) {
      const view = renderWith(options);
      await waitFor(() => expect(view.getByText("Card / Stripe")).toBeTruthy());
      expect(view.queryByText(/paused/i)).toBeNull();
      view.unmount();
    }
  });

  it("carries the server's badge rather than a literal", async () => {
    const view = renderWith({ ...SELLER_NOT_READY, cardBadge: "Not Accepted Here" });
    await waitFor(() => expect(view.getByText("Not Accepted Here")).toBeTruthy());
    expect(view.queryByText("Temporarily Unavailable")).toBeNull();
  });
});

describe("the cash footnote", () => {
  it("repeats the seller sentence rather than contradicting it", async () => {
    // Both surfaces are visible at once, so two different explanations of the
    // same closed row would be readable side by side.
    const view = renderWith(SELLER_NOT_READY);
    await waitFor(() => expect(view.getAllByText(/Seller has not enabled card payments yet\./).length).toBe(2));
  });

  it("says nothing about card at all once the rail is open", async () => {
    // A buyer who picks cash on an open rail is not being refused anything.
    // The old footnote told them card was paused, which was false the moment
    // the flag went on and false for every buyer who simply preferred cash.
    const view = renderWith(OPEN);
    await waitFor(() => expect(view.getByText("Card / Stripe")).toBeTruthy());
    expect(view.queryByText(/card payments are paused/i)).toBeNull();
    expect(view.queryByText(/unavailable/i)).toBeNull();
    // The fee fact it exists to carry survives.
    expect(view.getByText(/\$0\.00 PulseSoc platform fee/)).toBeTruthy();
  });

  it("still states the cash fee when card is closed", async () => {
    const view = renderWith(SELLER_NOT_READY);
    await waitFor(() => expect(view.getByText(/\$0\.00 PulseSoc platform fee/)).toBeTruthy());
  });
});
