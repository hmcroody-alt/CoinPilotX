/**
 * The client's half of the delivery contract, and the three ways it can lie.
 *
 * This module is a parser that must never become an estimator. The failures
 * worth pinning are therefore all of the form "the client produced a date the
 * server did not give it", or its mirror, "the client hid a date the server
 * did":
 *
 * 1. **A failure rendered as an estimate.** Unreachable server, refused
 *    listing, unknown state, half a window — each has to arrive as
 *    `UNAVAILABLE` with a reason, because a screen that gets `null` fills the
 *    gap with whatever copy was there before.
 * 2. **A promise upgraded in transit.** `guaranteed` and `is_estimate` are what
 *    license the words "Guaranteed" and "Estimated", and no response shape may
 *    flip the first one or clear the second.
 * 3. **The date moving by a day.** The server sends a calendar day at the
 *    destination; `new Date("2026-03-16")` is UTC midnight, which is the 15th in
 *    every timezone west of Greenwich. The formatter must not go through `Date`.
 *
 * The request shape is pinned too, from the other direction: the endpoint
 * refuses a body that names the fulfillment type or carries a postal code, and
 * these tests assert this module never sends either — so a future convenience
 * field cannot be added here and then met with a 400 in production.
 */

import {
  CLIENT_INCOMPLETE,
  CLIENT_MALFORMED,
  CLIENT_UNREACHABLE,
  estimateUnavailable,
  fetchDeliveryEstimate,
  formatDeliveryDate,
  formatDeliveryWindow,
  parseDeliveryEstimate
} from "../delivery";

const mockPulseApi = jest.fn();
jest.mock("../pulseApi", () => ({ pulseApi: (...args: unknown[]) => mockPulseApi(...args) }));

beforeEach(() => mockPulseApi.mockReset());

/** What the server actually sends for a quoted supplier route. */
const WINDOW = {
  state: "ESTIMATED",
  reason: null,
  earliest: "2026-03-16",
  latest: "2026-03-21",
  confidence: "PROVIDER_QUOTED",
  guaranteed: false,
  is_estimate: true,
  shipping_price: "FREE"
};

function ok(delivery: unknown, destination?: unknown) {
  return {
    ok: true,
    delivery,
    destination: destination || { country: "US", precision: "COUNTRY", tier: "SESSION", known: true }
  };
}

function lastBody(): Record<string, unknown> {
  const [, options] = mockPulseApi.mock.calls[0];
  return JSON.parse((options as { body: string }).body);
}

describe("parseDeliveryEstimate", () => {
  it("carries a real window through unchanged", () => {
    const estimate = parseDeliveryEstimate(WINDOW);
    expect(estimate.state).toBe("ESTIMATED");
    expect(estimate.earliest).toBe("2026-03-16");
    expect(estimate.latest).toBe("2026-03-21");
    expect(estimate.confidence).toBe("PROVIDER_QUOTED");
    expect(estimate.shippingPrice).toBe("FREE");
    expect(estimate.reason).toBeNull();
  });

  it("keeps a cached provider quote as a provider quote", () => {
    // One refresh behind is still the supplier's own number. Collapsing it to
    // NONE would hide a window the server was willing to stand behind.
    const estimate = parseDeliveryEstimate({ ...WINDOW, confidence: "PROVIDER_CACHED" });
    expect(estimate.state).toBe("ESTIMATED");
    expect(estimate.confidence).toBe("PROVIDER_CACHED");
  });

  it("refuses an ESTIMATED state that is missing either date", () => {
    // Half a window is a bug, not "arrives from the 16th". Drawing it would put
    // a range around a null.
    for (const partial of [
      { ...WINDOW, latest: null },
      { ...WINDOW, earliest: null },
      { ...WINDOW, earliest: null, latest: null },
      { ...WINDOW, latest: "soon" },
      { ...WINDOW, earliest: "2026-3-16" }
    ]) {
      const estimate = parseDeliveryEstimate(partial);
      expect(estimate.state).toBe("UNAVAILABLE");
      expect(estimate.reason).toBe(CLIENT_INCOMPLETE);
      expect(estimate.earliest).toBeNull();
      expect(estimate.latest).toBeNull();
    }
  });

  it("fails closed on a state this build has never heard of", () => {
    // A newer server can add a state while older binaries are still in the
    // field. An unrecognised state passed through as if it were ESTIMATED is a
    // window drawn around two nulls.
    for (const body of [
      { ...WINDOW, state: "ARRIVED" },
      { ...WINDOW, state: "estimated" },
      { ...WINDOW, state: null },
      {},
      null,
      "ESTIMATED",
      42
    ]) {
      const estimate = parseDeliveryEstimate(body);
      expect(estimate.state).toBe("UNAVAILABLE");
      expect(estimate.reason).toBe(CLIENT_MALFORMED);
    }
  });

  it("carries the server's own refusal reason rather than replacing it", () => {
    // `handling_time_undeclared` reaches an operator who has to set a variable.
    // Rewriting it to a generic client reason sends that query nowhere.
    const estimate = parseDeliveryEstimate({
      state: "UNAVAILABLE",
      reason: "handling_time_undeclared",
      earliest: null,
      latest: null,
      confidence: "NONE",
      guaranteed: false,
      is_estimate: true,
      shipping_price: "FREE"
    });
    expect(estimate.reason).toBe("handling_time_undeclared");
  });

  it("keeps an unserviceable route distinct from a failure", () => {
    // §55: an unsupported destination has to block a checkout, and a screen can
    // only do that if it can tell this apart from "the supplier is down".
    const estimate = parseDeliveryEstimate({
      ...WINDOW,
      state: "UNSUPPORTED_ROUTE",
      reason: "no_eligible_route",
      earliest: null,
      latest: null
    });
    expect(estimate.state).toBe("UNSUPPORTED_ROUTE");
    expect(estimate.reason).toBe("no_eligible_route");
  });

  it("drops the dates and the confidence from anything that is not a window", () => {
    // A server that sent a stale date beside a refusal, or a build that kept the
    // previous answer's fields, would let a screen render a date from a state
    // that says there is none.
    const estimate = parseDeliveryEstimate({
      ...WINDOW,
      state: "UNAVAILABLE",
      reason: "supplier_unreachable"
    });
    expect(estimate.earliest).toBeNull();
    expect(estimate.latest).toBeNull();
    expect(estimate.confidence).toBe("NONE");
  });

  it("never reports a guarantee", () => {
    // §58 and §125. `guaranteed` is read with `=== true` so that no shape short
    // of an explicit boolean can earn the word.
    for (const raw of [undefined, null, "true", 1, "yes", {}]) {
      expect(parseDeliveryEstimate({ ...WINDOW, guaranteed: raw }).guaranteed).toBe(false);
    }
  });

  it("keeps calling an estimate an estimate unless told otherwise explicitly", () => {
    // The opposite reading direction from `guaranteed`, for the same reason: an
    // older server that omits the field is still sending an estimate, and the
    // failure that matters is calling one a certainty.
    for (const raw of [undefined, null, true, "no", 0]) {
      expect(parseDeliveryEstimate({ ...WINDOW, is_estimate: raw }).isEstimate).toBe(true);
    }
    expect(parseDeliveryEstimate({ ...WINDOW, is_estimate: false }).isEstimate).toBe(false);
  });

  it("does not turn a missing shipping price into a free one", () => {
    // Shipping is free to the buyer by policy, but that is the server's sentence
    // to say. A client that defaulted the field would print FREE beside a quote
    // from a deployment that had not agreed to it.
    for (const raw of [undefined, null, "", "   ", 0]) {
      expect(parseDeliveryEstimate({ ...WINDOW, shipping_price: raw }).shippingPrice).toBeNull();
    }
    expect(parseDeliveryEstimate(WINDOW).shippingPrice).toBe("FREE");
  });
});

describe("fetchDeliveryEstimate", () => {
  it("sends only the three fields the endpoint accepts", async () => {
    mockPulseApi.mockResolvedValue(ok(WINDOW));
    await fetchDeliveryEstimate({ variantRef: "v-1", quantity: 3, country: "us" });
    const [path, options] = mockPulseApi.mock.calls[0];
    expect(path).toBe("/api/pulse/delivery/estimate");
    expect((options as { method: string }).method).toBe("POST");
    expect(lastBody()).toEqual({ variant_ref: "v-1", quantity: 3, country: "US" });
  });

  it("never names the fulfillment type or a postal code", async () => {
    // The endpoint refuses both: the fulfillment type is read off the listing so
    // a caller cannot ask for a supplier quote on a listing the supplier does
    // not ship, and a postal code would be promoted into precision the session
    // never established — and into a cache key. A convenience field added here
    // would be met with a 400 in production.
    mockPulseApi.mockResolvedValue(ok(WINDOW));
    await fetchDeliveryEstimate({
      variantRef: "v-1",
      country: "US",
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ...({ fulfillment: "SUPPLIER", postal_code: "94105", postalCode: "94105" } as any)
    });
    const body = lastBody();
    expect(Object.keys(body).sort()).toEqual(["country", "variant_ref"]);
  });

  it("omits a country it was not given rather than guessing one", async () => {
    // The server resolves a destination from the session it can see. A guessed
    // country is a confident date for the wrong continent.
    mockPulseApi.mockResolvedValue(ok(WINDOW));
    await fetchDeliveryEstimate({ variantRef: "v-1" });
    expect(lastBody()).toEqual({ variant_ref: "v-1" });
  });

  it("omits a country that is not a two-letter code", async () => {
    mockPulseApi.mockResolvedValue(ok(WINDOW));
    await fetchDeliveryEstimate({ variantRef: "v-1", country: "United States" });
    expect(lastBody().country).toBeUndefined();
  });

  it("sends a quantity the server will refuse rather than clamping it", async () => {
    // The per-line ceiling belongs to the cart and is enforced at the endpoint.
    // Clamping here would return a window for a parcel the caller did not
    // describe — three of an item quoted as one.
    mockPulseApi.mockResolvedValue(ok(WINDOW));
    await fetchDeliveryEstimate({ variantRef: "v-1", quantity: 9999 });
    expect(lastBody().quantity).toBe(9999);
  });

  it("drops a quantity that is not a whole count", async () => {
    mockPulseApi.mockResolvedValue(ok(WINDOW));
    await fetchDeliveryEstimate({ variantRef: "v-1", quantity: 2.5 });
    expect(lastBody().quantity).toBeUndefined();
  });

  it("refuses an empty variant reference without calling the server", async () => {
    const answer = await fetchDeliveryEstimate({ variantRef: "   " });
    expect(mockPulseApi).not.toHaveBeenCalled();
    expect(answer.delivery.state).toBe("UNAVAILABLE");
    expect(answer.delivery.reason).toBe(CLIENT_MALFORMED);
  });

  it("reports an unreachable server as unavailable, not as a blank", async () => {
    // Every transport failure gets one reason. There is no `null` in the return
    // type precisely so a caller cannot forget this branch: the failure arrives
    // in the same shape as the success.
    mockPulseApi.mockRejectedValue(new Error("timeout"));
    const answer = await fetchDeliveryEstimate({ variantRef: "v-1" });
    expect(answer.delivery.state).toBe("UNAVAILABLE");
    expect(answer.delivery.reason).toBe(CLIENT_UNREACHABLE);
    expect(answer.delivery.earliest).toBeNull();
    expect(answer.destination.known).toBe(false);
  });

  it("carries a boundary refusal's reason and its destination", async () => {
    mockPulseApi.mockResolvedValue({
      ok: false,
      reason: "listing_unavailable",
      destination: { country: "GB", precision: "COUNTRY", tier: "SESSION", known: true }
    });
    const answer = await fetchDeliveryEstimate({ variantRef: "v-1" });
    expect(answer.delivery.reason).toBe("listing_unavailable");
    expect(answer.destination.country).toBe("GB");
  });

  it("treats a response that never said ok as a refusal", async () => {
    // `ok !== true`, not `ok === false`. A body from something that is not this
    // endpoint — a proxy error page parsed as JSON, an older route — has not
    // agreed to anything.
    for (const body of [{}, { delivery: WINDOW }, { ok: "true", delivery: WINDOW }, { ok: 1 }]) {
      mockPulseApi.mockResolvedValue(body);
      const answer = await fetchDeliveryEstimate({ variantRef: "v-1" });
      expect(answer.delivery.state).toBe("UNAVAILABLE");
    }
  });

  it("reads an unknown destination as unknown rather than as absent", async () => {
    // `known: false` means the server had nothing to go on. The honest prompt is
    // "choose a country", which a screen can only offer if this survives.
    mockPulseApi.mockResolvedValue(
      ok({ ...WINDOW, state: "UNAVAILABLE", reason: "destination_unresolved" }, {
        country: null,
        precision: null,
        tier: null,
        known: false
      })
    );
    const answer = await fetchDeliveryEstimate({ variantRef: "v-1" });
    expect(answer.destination.known).toBe(false);
    expect(answer.delivery.reason).toBe("destination_unresolved");
  });
});

describe("formatDeliveryDate", () => {
  it("keeps the day the server chose, in every timezone", () => {
    // The regression this exists for: `new Date("2026-03-16")` is UTC midnight,
    // so a formatter that went through `Date` would print 15 Mar for a buyer in
    // California. The server's day is already a calendar day at the
    // destination — there is nothing to convert.
    expect(formatDeliveryDate("2026-03-16")).toBe("16 Mar");
    expect(formatDeliveryDate("2026-01-01")).toBe("1 Jan");
    expect(formatDeliveryDate("2026-12-31")).toBe("31 Dec");
  });

  it("returns null for anything that is not a calendar day", () => {
    for (const raw of [null, "", "soon", "2026-3-16", "2026-03-16T00:00:00Z", "16/03/2026"]) {
      expect(formatDeliveryDate(raw)).toBeNull();
    }
  });

  it("returns null for a month that does not exist", () => {
    expect(formatDeliveryDate("2026-13-01")).toBeNull();
    expect(formatDeliveryDate("2026-00-01")).toBeNull();
  });
});

describe("formatDeliveryWindow", () => {
  it("says the month once within a month", () => {
    expect(formatDeliveryWindow(parseDeliveryEstimate(WINDOW))).toBe("16 – 21 Mar");
  });

  it("says both months across a boundary", () => {
    const estimate = parseDeliveryEstimate({
      ...WINDOW,
      earliest: "2026-03-28",
      latest: "2026-04-02"
    });
    expect(formatDeliveryWindow(estimate)).toBe("28 Mar – 2 Apr");
  });

  it("says a single day once", () => {
    // The one place this gets less precise than the data, on purpose:
    // "16 – 16 Mar" reads as a formatting bug and invites the reader to distrust
    // the rest of the page.
    const estimate = parseDeliveryEstimate({ ...WINDOW, latest: "2026-03-16" });
    expect(formatDeliveryWindow(estimate)).toBe("16 Mar");
  });

  it("returns null for every state that has no window", () => {
    for (const reason of ["supplier_unreachable", "handling_time_undeclared"]) {
      expect(formatDeliveryWindow(estimateUnavailable(reason))).toBeNull();
    }
    expect(
      formatDeliveryWindow(
        parseDeliveryEstimate({ ...WINDOW, state: "UNSUPPORTED_ROUTE", reason: "no_route" })
      )
    ).toBeNull();
  });
});
