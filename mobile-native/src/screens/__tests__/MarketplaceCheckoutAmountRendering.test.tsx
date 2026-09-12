/**
 * The checkout screen shows an amount only when it knows one.
 *
 * This is the same rule the grid card and the product page already follow
 * (`MarketplacePriceLabelRendering.test.tsx`), asserted on the surface that had
 * quietly broken it. When no `subtotalMinor` was passed, this screen filled the
 * amount slot with `params.priceLabel || "Shown at checkout"`, and both halves
 * were wrong in the same way:
 *
 *   - the label is the *unit* price, so an order for two printed one item's
 *     price under "Item total", "Total" and "Amount paid";
 *   - "Shown at checkout" is a promise naming the screen the buyer is already
 *     on, and on the confirmation view it rendered as the value of **Amount
 *     paid** — after the money had moved.
 *
 * Both halves are pinned here deliberately. A screen that renders no amount
 * ever would satisfy "invents nothing", so every case below also checks that
 * the known amount is still printed. And the rows are checked as *absent*
 * rather than blank: a `SummaryRow` with an empty value still prints its label,
 * so "Amount paid" over nothing is its own small lie.
 *
 * The phrases are literals, not imports. Sharing a constant with the screen
 * would let a rename keep this file green while the app went on saying it.
 */

import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
const mockOpenCheckout = jest.fn();
jest.mock("../../api/marketplace", () => ({
  openMarketplaceCheckout: (...args: unknown[]) => mockOpenCheckout(...args)
}));
jest.mock("../../api/marketplaceCommerce", () => ({
  checkoutCartGroup: jest.fn(),
  getMarketplacePaymentOrder: jest.fn(),
  validateCart: jest.fn()
}));
jest.mock("../../api/checkoutCountries", () => ({ fetchShippingCountries: jest.fn(async () => []) }));
jest.mock("../../api/stripePaymentSheet", () => ({
  isPaymentSheetAvailable: () => false,
  presentPaymentSheet: jest.fn()
}));
jest.mock("../../payments/PaymentController", () => ({
  PaymentController: { begin: jest.fn(), release: jest.fn() }
}));

import { MarketplaceCheckoutScreen } from "../MarketplaceCheckoutScreen";

type Params = React.ComponentProps<typeof MarketplaceCheckoutScreen>["route"]["params"];

/** A digital order: no fulfilment fields, so the screen opens on review. */
const BASE: Params = {
  mode: "buy_now",
  listingId: 41,
  sellerUserId: 7,
  sellerName: "Test Seller",
  itemTitle: "A thing",
  quantity: 2,
  currency: "USD",
  fulfillmentKind: "digital"
};

function renderCheckout(params: Params) {
  const navigation = {
    setOptions: jest.fn(),
    navigate: jest.fn(),
    replace: jest.fn(),
    goBack: jest.fn()
  };
  return render(
    <MarketplaceCheckoutScreen
      route={{ key: "checkout", name: "MarketplaceCheckout", params } as never}
      navigation={navigation as never}
    />
  );
}

describe("the checkout screen's order summary", () => {
  it("prints no amount at all when it was handed no subtotal", () => {
    const view = renderCheckout(BASE);

    // It mounted and rendered the summary it *can* stand behind.
    expect(view.getByText("Order summary")).toBeTruthy();
    expect(view.getByText("Seller")).toBeTruthy();

    // The rows that would have carried an invented figure are gone, label and
    // all — not present with an empty value.
    expect(view.queryByText("Item total")).toBeNull();
    expect(view.queryByText("Total to pay")).toBeNull();
    expect(view.queryByText("Total due to seller")).toBeNull();
    expect(view.queryByText("Total")).toBeNull();

    // And no sentence stands in for the number.
    expect(view.queryByText(/Shown at checkout/i)).toBeNull();
    expect(view.queryByText(/Price at checkout/i)).toBeNull();
  });

  it("still prints the amount it does know", () => {
    const view = renderCheckout({ ...BASE, subtotalMinor: 1000 });

    expect(view.getByText("Item total")).toBeTruthy();
    expect(view.getAllByText("$10.00").length).toBeGreaterThan(0);
    expect(view.getByText("Total due to seller")).toBeTruthy();
  });

  it("does not print one item's price as the total of an order for two", () => {
    // `subtotalMinor` is already multiplied out by the caller. The deleted
    // `priceLabel` param carried "$5.00" for this same order, and the screen
    // printed it here.
    const view = renderCheckout({ ...BASE, subtotalMinor: 1000 });

    expect(view.queryByText("$5.00")).toBeNull();
  });

  it("tells the buyer where the figure comes from instead of inventing one", () => {
    // Asserted on the default payment method — cash — because card is paused,
    // and the sentence covering an unknown amount used to live only on the card
    // branch. Removing the total row without this leaves the buyer with no
    // account of the amount whatsoever, which is a quieter kind of dishonest.
    const view = renderCheckout(BASE);

    expect(view.getByText(/agree it with the seller/i)).toBeTruthy();
  });

  it("does not say that to a buyer whose total it already showed", () => {
    const view = renderCheckout({ ...BASE, subtotalMinor: 1000 });

    expect(view.queryByText(/agree it with the seller/i)).toBeNull();
    // The cash radio row carries its own "pay the seller directly", so this
    // matches the muted sentence's whole known-amount form rather than a
    // fragment two elements share.
    expect(
      view.getByText("No card or Stripe charge will start. Pay the seller directly when you pick up or meet in person.")
    ).toBeTruthy();
  });
});

describe("the checkout screen's call to action", () => {
  it("promises no amount when it knows none", () => {
    const view = renderCheckout(BASE);

    expect(view.getByText("Confirm cash order")).toBeTruthy();
    expect(view.queryByText(/Confirm cash order · /)).toBeNull();
  });

  it("states the amount when it knows it", () => {
    const view = renderCheckout({ ...BASE, subtotalMinor: 1000 });

    expect(view.getByText("Confirm cash order · $10.00")).toBeTruthy();
  });
});

/**
 * The confirmation view, reached the way a buyer reaches it.
 *
 * This is the surface the old fallback did the most damage on: "Amount paid" is
 * a statement about money that has already moved, and it was rendering the
 * sentence "Shown at checkout" as its value — on the screen after checkout.
 *
 * Driven through the cash lane rather than by setting state directly, because
 * the row is only worth guarding on the path that actually reaches it.
 */
describe("the confirmation receipt", () => {
  beforeEach(() => {
    mockOpenCheckout.mockReset();
    mockOpenCheckout.mockResolvedValue({ handoff: { checkoutUrl: "", transactionIds: [99] } });
  });

  async function confirm(params: Params) {
    const view = renderCheckout(params);
    fireEvent.press(view.getByLabelText(/^Confirm cash order/));
    await waitFor(() => expect(view.getByText("Order confirmed")).toBeTruthy());
    return view;
  }

  it("prints no Amount paid row when no amount was ever known", async () => {
    const view = await confirm(BASE);

    expect(view.getByText("Order")).toBeTruthy();
    expect(view.queryByText("Amount paid")).toBeNull();
    expect(view.queryByText("Amount due to seller")).toBeNull();
    expect(view.queryByText(/Shown at checkout/i)).toBeNull();
  });

  it("still prints the amount on a receipt that has one", async () => {
    const view = await confirm({ ...BASE, subtotalMinor: 1000 });

    expect(view.getByText("Amount due to seller")).toBeTruthy();
    expect(view.getAllByText("$10.00").length).toBeGreaterThan(0);
  });
});

describe("the checkout screen's product card", () => {
  it("omits the price line rather than rendering an empty one", () => {
    const withAmount = renderCheckout({ ...BASE, subtotalMinor: 1000 });
    const withoutAmount = renderCheckout(BASE);

    // Asserted on the *element*, not on its text. Searching for "$10.00" cannot
    // tell an omitted price from one rendered as an empty string — both find
    // nothing — and an empty `<Text>` still occupies the column with the price
    // font's weight and margins. A mutation removing this guard survived a
    // text-based version of this test, which is why the testID is here.
    expect(withAmount.queryByTestId("checkout-summary-price")).not.toBeNull();
    expect(withoutAmount.queryByTestId("checkout-summary-price")).toBeNull();

    // And the card itself still rendered, so "no price element" cannot be won
    // by a card that failed to mount.
    expect(withoutAmount.getAllByText("A thing").length).toBeGreaterThan(0);
  });
});
