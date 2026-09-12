/**
 * What the checkout screen sends is what it showed.
 *
 * `quantity` travelled the whole way from the product screen's stepper to this
 * screen, was multiplied into `subtotalMinor` for the amount, and was printed on
 * the summary as "×3" — and then `openMarketplaceCheckout` sent a body with no
 * quantity field in it at all. The server read the absence as one. The buyer saw
 * $75.00, was charged $25.00, and the order row said one unit.
 *
 * Nothing caught it because every existing assertion about quantity is about
 * *rendering*. The screen displayed the right number and transmitted a different
 * one, and no test compared the two. These do: each case reads the argument
 * handed to the API and, where the number is visible, checks the screen agrees
 * with what it sent.
 *
 * The number's position in the argument list is checked by its value, not by
 * index alone — `openMarketplaceCheckout` takes five other positional arguments
 * and a quantity landing in the wrong slot would arrive as a fulfilment lane.
 */

import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
const mockOpenCheckout = jest.fn();
const mockCheckoutCartGroup = jest.fn();
const mockValidateCart = jest.fn();
jest.mock("../../api/marketplace", () => ({
  openMarketplaceCheckout: (...args: unknown[]) => mockOpenCheckout(...args)
}));
jest.mock("../../api/marketplaceCommerce", () => ({
  checkoutCartGroup: (...args: unknown[]) => mockCheckoutCartGroup(...args),
  getMarketplacePaymentOrder: jest.fn(),
  validateCart: (...args: unknown[]) => mockValidateCart(...args)
}));
jest.mock("../../api/checkoutCountries", () => ({ fetchShippingCountries: jest.fn(async () => []) }));
// Only the cash lane is reachable: `MARKETPLACE_CARD_PAYMENTS_PAUSED` returns
// before the card lane's call site, so no test that drives this screen can get
// there. That lane is covered structurally instead, by the call-site count in
// `MarketplaceCheckoutInformationOrder.test.ts`.
jest.mock("../../api/stripePaymentSheet", () => ({
  isPaymentSheetAvailable: () => false,
  presentPaymentSheet: jest.fn()
}));
jest.mock("../../payments/PaymentController", () => ({
  PaymentController: { begin: jest.fn(), release: jest.fn() }
}));

import { MarketplaceCheckoutScreen } from "../MarketplaceCheckoutScreen";

type Params = React.ComponentProps<typeof MarketplaceCheckoutScreen>["route"]["params"];

/** A digital buy-now order, so the screen opens straight on review. */
const BASE: Params = {
  mode: "buy_now",
  listingId: 41,
  sellerUserId: 7,
  sellerName: "Test Seller",
  itemTitle: "A thing",
  quantity: 3,
  subtotalMinor: 7500,
  currency: "USD",
  fulfillmentKind: "digital"
};

/** The argument slot `quantity` occupies, named so a reorder reads as a change. */
const QUANTITY_ARG = 5;

function renderCheckout(params: Params) {
  const navigation = { setOptions: jest.fn(), navigate: jest.fn(), replace: jest.fn(), goBack: jest.fn() };
  return render(
    <MarketplaceCheckoutScreen
      route={{ key: "checkout", name: "MarketplaceCheckout", params } as never}
      navigation={navigation as never}
    />
  );
}

async function confirm(params: Params) {
  const view = renderCheckout(params);
  fireEvent.press(view.getByLabelText(/^Confirm cash order/));
  await waitFor(() => expect(mockOpenCheckout).toHaveBeenCalled());
  return view;
}

beforeEach(() => {
  mockOpenCheckout.mockReset();
  mockOpenCheckout.mockResolvedValue({ handoff: { checkoutUrl: "", transactionIds: [99] } });
  mockCheckoutCartGroup.mockReset();
});

describe("the buy-now checkout handoff", () => {
  it("sends the quantity the buyer chose", async () => {
    await confirm(BASE);

    expect(mockOpenCheckout.mock.calls[0][QUANTITY_ARG]).toBe(3);
  });

  it("sends the same number it printed on the summary", async () => {
    // The failure this replaces was precisely a screen that displayed one number
    // and transmitted another, so the two are compared rather than asserted
    // separately.
    const view = await confirm(BASE);

    expect(view.getAllByText("×3").length).toBeGreaterThan(0);
    expect(mockOpenCheckout.mock.calls[0][QUANTITY_ARG]).toBe(3);
  });

  it("does not put the quantity in the fulfilment or payment slot", async () => {
    await confirm(BASE);
    const call = mockOpenCheckout.mock.calls[0];

    expect(call[0]).toBe(41); // listing id
    expect(call[2]).toBe(""); // lane — not chosen, this listing offers one
    expect(call[3]).toBe("cash"); // payment mode
    expect(call[3]).not.toBe(3);
  });

  it("sends one for an order the buyer never stepped up", async () => {
    // Absence must keep meaning one. Every build in the field today sends no
    // quantity at all, and a fix that made "unspecified" mean zero would break
    // every single-unit purchase on the way to fixing multi-unit ones.
    const params = { ...BASE };
    delete (params as Record<string, unknown>).quantity;
    await confirm(params as Params);

    expect(mockOpenCheckout.mock.calls[0][QUANTITY_ARG]).toBe(1);
  });

  it("never sends a quantity below one", async () => {
    await confirm({ ...BASE, quantity: 0 });

    expect(mockOpenCheckout.mock.calls[0][QUANTITY_ARG]).toBe(1);
  });
});

describe("the cart checkout handoff", () => {
  it("still carries its quantities in the cart, not in this argument", async () => {
    // The cart lane was never wrong: each line's quantity lives on the line, and
    // the server reads it from the cart rows. This asserts the fix did not reach
    // across and start sending a screen-level quantity the cart lane would have
    // to reconcile against its own.
    // The cart lane re-validates before it pays; an unanswered validation throws
    // before the call under test is ever made.
    mockValidateCart.mockResolvedValue({
      lines: [{ line_id: 1, seller_user_id: 7, quantity: 3 }],
      blockingLineIds: [],
      priceChangedLineIds: []
    });
    mockCheckoutCartGroup.mockResolvedValue({ checkoutUrl: "", transactionIds: [7], sheet: null });
    const view = renderCheckout({
      mode: "cart", sellerUserId: 7, sellerName: "Test Seller",
      itemTitle: "Marketplace items", quantity: 3, subtotalMinor: 7500,
      currency: "USD", fulfillmentKind: "digital"
    } as Params);
    fireEvent.press(view.getByLabelText(/^Confirm cash order/));

    await waitFor(() => expect(mockCheckoutCartGroup).toHaveBeenCalled());
    expect(mockOpenCheckout).not.toHaveBeenCalled();
    expect(mockCheckoutCartGroup.mock.calls[0].length).toBe(5);
  });
});
