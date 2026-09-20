/**
 * The card lane, actually selected.
 *
 * `MarketplaceCheckoutCardCopy.test.tsx` pins the card row's unavailable copy
 * across two server verdicts and passes — but every one of its cases leaves the
 * lane on cash, the default. The broken sentence only rendered on the card
 * lane, so the contradiction shipped *under* a green suite:
 *
 *   Card / Stripe  — "Pay by card now. The seller is paid after the order completes."
 *   Total to pay   — $38.00
 *                    "Marketplace card payments are temporarily unavailable."
 *   [ Pay securely · $38.00 ]
 *
 * These tests do the one thing that suite never did: press the card row. The
 * decision itself is covered exhaustively in
 * `marketplace/__tests__/checkoutPaymentCopy.test.ts`; what is covered *here* is
 * only that the screen is wired to it — that the sentence under the total comes
 * from `checkoutSettlementCopy` and the CTA from the same call, rather than from
 * a literal that happens to read the same today.
 *
 * Which is why the press is asserted before the copy. `fireEvent.press` no-ops
 * silently behind Pressability, and a press that never landed leaves the screen
 * on cash — where the old literal was also absent. The test would go green for
 * the wrong reason.
 */

import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

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
const OPEN = {
  countries: [],
  cardPaymentsAvailable: true,
  cardBadge: "",
  cardUnavailableMessage: ""
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

const READY_COPY = "Your order will be confirmed after your card payment is successfully processed.";
const OLD_LITERAL = "Marketplace card payments are temporarily unavailable.";

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

/**
 * Select the card lane and prove the selection landed.
 *
 * The row is `disabled` until the options fetch resolves, and RNTL's press goes
 * through Pressability's responder, which trails `accessibilityState.disabled`
 * by an effect flush. Waiting on the row's *open* detail text before pressing is
 * what makes the press real; the CTA label flipping to "Pay securely" is what
 * proves it afterwards.
 */
async function selectCard(view: ReturnType<typeof renderWith>) {
  await waitFor(() =>
    expect(view.getByText("Pay by card now. The seller is paid after the order completes.")).toBeTruthy()
  );
  fireEvent.press(view.getByLabelText("Card / Stripe"));
  await waitFor(() => expect(view.getByLabelText("Pay securely · $38.00")).toBeTruthy());
}

beforeEach(() => mockFetch.mockReset());

describe("card ready and the card lane selected", () => {
  it("says the order is confirmed after payment, and not the old literal", async () => {
    const view = renderWith(OPEN);
    await selectCard(view);
    expect(view.getByText(READY_COPY)).toBeTruthy();
    expect(view.queryByText(OLD_LITERAL)).toBeNull();
    // Not merely that one literal: nothing on the card lane claims the rail is
    // shut, whatever wording a regression might reach for.
    expect(view.queryByText(/unavailable/i)).toBeNull();
  });

  it("leaves the CTA live and keeps the helper line the mission asked to keep", async () => {
    const view = renderWith(OPEN);
    await selectCard(view);
    const cta = view.getByLabelText("Pay securely · $38.00");
    expect(cta.props.accessibilityState?.disabled).toBe(false);
    expect(view.getByText("Your order isn't confirmed until your payment clears.")).toBeTruthy();
  });

  it("does not show the cash sentence once card is chosen", async () => {
    // The two lanes' settlement sentences are mutually exclusive by
    // construction; a regression that rendered both would read as two different
    // accounts of where the money goes, inches apart.
    const view = renderWith(OPEN);
    await selectCard(view);
    expect(view.queryByText(/Pay the seller directly when you pick up/)).toBeNull();
  });
});

describe("card ready but cash still selected", () => {
  it("keeps the cash settlement sentence and says nothing about card", async () => {
    // The default lane. This is the state the existing suite covers wholesale —
    // pinned here too because the fix moved the sentence, and "unchanged" is a
    // claim worth an assertion rather than an assumption.
    const view = renderWith(OPEN);
    await waitFor(() => expect(view.getByText("Card / Stripe")).toBeTruthy());
    expect(
      view.getByText(
        "No card or Stripe charge will start. Pay the seller directly when you pick up or meet in person."
      )
    ).toBeTruthy();
    expect(view.queryByText(READY_COPY)).toBeNull();
    expect(view.getByLabelText("Confirm cash order · $38.00").props.accessibilityState?.disabled).toBe(false);
  });
});

describe("the card row cannot be selected while the rail is shut", () => {
  it("stays on cash when the disabled card row is pressed", async () => {
    // The reason the invalid triad is not reachable through this screen, stated
    // as a test rather than as a comment: the row is disabled whenever the
    // server says the lane is shut, and options are fetched once, so there is no
    // window where a selected card lane meets an unavailable rail.
    //
    // The screen's CTA gate is therefore defense-in-depth, not the fix for an
    // observed state — the observed bug was the literal, on an *open* rail. The
    // shut-lane half of the invariant is proven exhaustively against the pure
    // function, where the state is one line away instead of unreachable.
    const view = renderWith(CLOSED);
    await waitFor(() => expect(view.getByText("Card / Stripe")).toBeTruthy());
    fireEvent.press(view.getByLabelText("Card / Stripe"));
    await waitFor(() => expect(view.getByLabelText("Confirm cash order · $38.00")).toBeTruthy());
    expect(view.queryByLabelText("Pay securely · $38.00")).toBeNull();
  });
});
