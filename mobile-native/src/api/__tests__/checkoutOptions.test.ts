/**
 * The checkout's two deployment facts, and which way each one fails.
 *
 * This module is the client's only source for whether Marketplace card checkout
 * is open. The screen used to answer that itself with a hard-coded constant,
 * which was safe only while the server's pause was also hard-coded. It is now
 * `MARKETPLACE_CARD_PAYMENTS_ENABLED`, so the interesting cases here are all the
 * ways the client can fail to get an answer — and every one of them has to come
 * out closed.
 */

import { CHECKOUT_OPTIONS_FALLBACK, fetchCheckoutOptions } from "../checkoutCountries";

const mockPulseApi = jest.fn();
jest.mock("../pulseApi", () => ({ pulseApi: (...args: unknown[]) => mockPulseApi(...args) }));

beforeEach(() => mockPulseApi.mockReset());

describe("fetchCheckoutOptions", () => {
  it("reads an open rail from the server", async () => {
    mockPulseApi.mockResolvedValue({
      shipping_countries: ["US", "CA"],
      card_payments_available: true,
      payment_badge: "",
      payment_unavailable_message: ""
    });
    const options = await fetchCheckoutOptions();
    expect(options.cardPaymentsAvailable).toBe(true);
    expect(options.countries.map((c) => c.code)).toEqual(["CA", "US"]);
  });

  it("carries the server's own badge and message when the rail is closed", async () => {
    // Not the client's copies. The operator who closed the rail may have a
    // reason the shipped binary has never heard of.
    mockPulseApi.mockResolvedValue({
      shipping_countries: ["US"],
      card_payments_available: false,
      payment_badge: "Paused",
      payment_unavailable_message: "Card checkout is off while we finish setup."
    });
    const options = await fetchCheckoutOptions();
    expect(options.cardPaymentsAvailable).toBe(false);
    expect(options.cardBadge).toBe("Paused");
    expect(options.cardUnavailableMessage).toBe("Card checkout is off while we finish setup.");
  });

  it("closes the rail on every answer that is not an explicit yes", async () => {
    // Including the shapes an *older* server returns. This endpoint predates the
    // field, and a build that read a missing field as permission would open card
    // checkout against a deployment that never agreed to it.
    for (const body of [
      {},
      { shipping_countries: ["US"] },
      { card_payments_available: "true" },
      { card_payments_available: 1 },
      { card_payments_available: null },
      { card_payments_available: false }
    ]) {
      mockPulseApi.mockResolvedValue(body);
      expect((await fetchCheckoutOptions()).cardPaymentsAvailable).toBe(false);
    }
  });

  it("closes the rail when the server cannot be reached at all", async () => {
    mockPulseApi.mockRejectedValue(new Error("offline"));
    const options = await fetchCheckoutOptions();
    expect(options.cardPaymentsAvailable).toBe(false);
    expect(options.cardBadge).toBe(CHECKOUT_OPTIONS_FALLBACK.cardBadge);
  });

  it("still offers the default country when the server cannot be reached", async () => {
    // The two halves fail in opposite directions and that is deliberate: an
    // empty picker blocks an order the deployment would have accepted, so the
    // countries fail soft while the card rail fails closed.
    mockPulseApi.mockRejectedValue(new Error("offline"));
    expect((await fetchCheckoutOptions()).countries.map((c) => c.code)).toEqual(["US"]);

    mockPulseApi.mockResolvedValue({ shipping_countries: [] });
    expect((await fetchCheckoutOptions()).countries.map((c) => c.code)).toEqual(["US"]);
  });

  it("ships closed", () => {
    expect(CHECKOUT_OPTIONS_FALLBACK.cardPaymentsAvailable).toBe(false);
  });
});
