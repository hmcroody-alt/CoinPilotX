/**
 * A refusal the server called unretryable must take the payment CTA with it.
 *
 * The October 2026 incident: a buyer saw "Payments are temporarily unavailable.
 * No card was charged." and, in the same view, a live "Pay securely · $6.25".
 * They tapped it ten times across eight hours. Every tap was refused for the
 * same reason, and nothing on the screen could say so, because the screen had
 * no way to tell a refusal that a retry clears from one it never will.
 *
 * `buyerCanRetry` reads that verdict off the wire, and
 * `api/__tests__/marketplaceCheckoutIntegrity.test.ts` covers it exhaustively.
 * What is covered *here* is the thing those tests cannot see: that this screen
 * actually calls it. The helper shipped with full unit coverage and zero screen
 * callers, which is the same shape of gap as the incident itself — a correct
 * decision made nowhere near the pixels.
 *
 * So the assertions are on the CTA's own disabled state after a real press, not
 * on the helper's return value. Both verdicts are asserted: deleting the wiring
 * fails the blocked case, and hard-coding `disabled` fails the retryable one.
 */

import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));

const mockOpenCheckout = jest.fn();
jest.mock("../../api/marketplace", () => ({
  openMarketplaceCheckout: (...args: unknown[]) => mockOpenCheckout(...(args as []))
}));
jest.mock("../../api/marketplaceCommerce", () => ({
  checkoutCartGroup: jest.fn(),
  getMarketplacePaymentOrder: jest.fn(),
  validateCart: jest.fn()
}));
jest.mock("../../api/stripePaymentSheet", () => ({
  isPaymentSheetAvailable: () => true,
  presentPaymentSheet: jest.fn()
}));
jest.mock("../../payments/PaymentController", () => ({
  PaymentController: {
    begin: jest.fn(),
    release: jest.fn(),
    instruction: jest.fn(async () => ({ ok: true, provider: "stripe", flow: "payment_sheet" }))
  }
}));

const OPEN = {
  countries: [],
  cardPaymentsAvailable: true,
  cardBadge: "",
  cardUnavailableMessage: ""
};

const mockFetch = jest.fn(async () => OPEN as unknown);
jest.mock("../../api/checkoutCountries", () => ({
  CHECKOUT_OPTIONS_FALLBACK: OPEN,
  fetchCheckoutOptions: (...args: unknown[]) => mockFetch(...(args as []))
}));

import { PulseApiError } from "../../api/pulseApi";
import { MarketplaceCheckoutScreen } from "../MarketplaceCheckoutScreen";

type Params = React.ComponentProps<typeof MarketplaceCheckoutScreen>["route"]["params"];

/** Digital, so the screen opens straight on review with the payment rows. */
const BASE: Params = {
  mode: "buy_now",
  listingId: 37,
  sellerUserId: 1,
  sellerName: "Test Seller",
  itemTitle: "Horse eye colorful ring",
  quantity: 1,
  currency: "USD",
  fulfillmentKind: "digital",
  subtotalMinor: 625
};

const CTA = "Pay securely · $6.25";

/**
 * `_error` in the three checkout lanes spreads its keyword arguments into the
 * top level of the JSON body, and `pulseApi` hands that whole body to
 * `PulseApiError.details`. So `retryable` and `cta` sit where this says.
 */
function refusal(extra: Record<string, unknown>) {
  return new PulseApiError(
    "We couldn't open secure payment for this order, and trying again will not help. " +
      "No card was charged. We've been notified and are looking into it.",
    503,
    "PAYMENT_CONFIGURATION_ERROR",
    { ok: false, error_code: "PAYMENT_CONFIGURATION_ERROR", ...extra }
  );
}

function renderScreen(params: Params = BASE) {
  mockFetch.mockResolvedValue(OPEN);
  const navigation = { setOptions: jest.fn(), navigate: jest.fn(), replace: jest.fn(), goBack: jest.fn() };
  return render(
    <MarketplaceCheckoutScreen
      route={{ key: "checkout", name: "MarketplaceCheckout", params } as never}
      navigation={navigation as never}
    />
  );
}

/**
 * Select the card lane and prove the selection landed, because `fireEvent.press`
 * no-ops silently behind Pressability and a press that never happened leaves the
 * screen on cash — where no card CTA exists and these assertions would pass for
 * the wrong reason.
 */
async function selectCardAndTapPay(view: ReturnType<typeof renderScreen>) {
  await waitFor(() =>
    expect(view.getByText("Pay by card now. The seller is paid after the order completes.")).toBeTruthy()
  );
  fireEvent.press(view.getByLabelText("Card / Stripe"));
  const cta = await waitFor(() => view.getByLabelText(CTA));
  expect(cta.props.accessibilityState?.disabled).toBe(false);
  fireEvent.press(cta);
}

beforeEach(() => {
  mockFetch.mockReset();
  mockOpenCheckout.mockReset();
});

describe("a refusal carrying the server's verdict", () => {
  it("disables the payment CTA when the server says a retry cannot work", async () => {
    mockOpenCheckout.mockRejectedValue(refusal({ retryable: false, cta: "blocked" }));
    const view = renderScreen();
    await selectCardAndTapPay(view);
    await waitFor(() =>
      expect(view.getByLabelText(CTA).props.accessibilityState?.disabled).toBe(true)
    );
  });

  it("honours a bare cta:'blocked' when no retryable flag is sent", async () => {
    // The two fields are independent on the wire and an older lane may send only
    // one. Neither may be the sole thing standing between a buyer and a dead end.
    mockOpenCheckout.mockRejectedValue(refusal({ cta: "blocked" }));
    const view = renderScreen();
    await selectCardAndTapPay(view);
    await waitFor(() =>
      expect(view.getByLabelText(CTA).props.accessibilityState?.disabled).toBe(true)
    );
  });

  it("leaves the CTA live when the server says a retry can work", async () => {
    // The burned-idempotency-key case: genuinely retryable, because the next
    // attempt is built against a fresh transaction id and so presents a key
    // Stripe has never seen. Removing the CTA here would strand a buyer one tap
    // from success — the opposite failure, and the one this screen had before.
    mockOpenCheckout.mockRejectedValue(refusal({ retryable: true, cta: "retry" }));
    const view = renderScreen();
    await selectCardAndTapPay(view);
    await waitFor(() => expect(view.getByText(/No card was charged/)).toBeTruthy());
    expect(view.getByLabelText(CTA).props.accessibilityState?.disabled).toBe(false);
  });

  it("leaves the CTA live when the server sends no verdict at all", async () => {
    // An older deployment. The screen must keep the behaviour it already had
    // rather than inventing a block, so a server that has not shipped the field
    // can only ever leave a CTA offered, never withhold one.
    mockOpenCheckout.mockRejectedValue(refusal({}));
    const view = renderScreen();
    await selectCardAndTapPay(view);
    await waitFor(() => expect(view.getByText(/No card was charged/)).toBeTruthy());
    expect(view.getByLabelText(CTA).props.accessibilityState?.disabled).toBe(false);
  });

  it("clears a blocked card refusal when the buyer switches to cash", async () => {
    // The verdict was about the card lane. Cash settles in person and reaches no
    // provider, so a Stripe setup failure says nothing about it — and a buyer
    // left unable to confirm a cash order by a card error has been handed a
    // second dead end by the fix for the first.
    mockOpenCheckout.mockRejectedValue(refusal({ retryable: false, cta: "blocked" }));
    const view = renderScreen();
    await selectCardAndTapPay(view);
    await waitFor(() =>
      expect(view.getByLabelText(CTA).props.accessibilityState?.disabled).toBe(true)
    );
    fireEvent.press(view.getByLabelText("Cash / in-person payment"));
    const cash = await waitFor(() => view.getByLabelText("Confirm cash order · $6.25"));
    expect(cash.props.accessibilityState?.disabled).toBe(false);
  });
});
