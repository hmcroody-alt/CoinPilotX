/**
 * The client must not re-fabricate the zeros the server just stopped inventing.
 *
 * `calculate_user_portfolio` used to value an unpriced holding at zero and then
 * report its profit as `value - cost` — a fabricated total loss on an asset that
 * had only failed to be quoted. That is fixed server-side, and there is exactly
 * one cheap way to undo it: `Number(value || 0)` on the client. The web
 * portfolio page did precisely that, printing `$0.00` in the value column of a
 * row whose price column already read "Price unavailable".
 *
 * So every case here is about the same distinction in a different place: a
 * number the server declined to state must arrive as `null`, must not join a
 * ranking, and must not let the totals be shown as though they covered it.
 */

const mockPulseApi = jest.fn();

// The real `PulseApiError` is kept deliberately. `isPremiumRequired` is an
// `instanceof` test, so a mock that replaced the class with a stub — or dropped
// it, leaving `instanceof undefined` to throw — would decide these cases for
// reasons that have nothing to do with the code under test.
jest.mock("../pulseApi", () => ({
  ...jest.requireActual("../pulseApi"),
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

import { PulseApiError } from "../pulseApi";
import {
  PREMIUM_REQUIRED,
  addHolding,
  deleteHolding,
  getPortfolio,
  isPremiumRequired,
  normalizeHolding,
  normalizePortfolio,
  rankableHoldings,
  totalsCoverEverything
} from "../portfolio";

/** A holding as the server sends it once every field is known. */
const pricedHolding = {
  id: 1,
  symbol: "btc",
  coin_name: "Bitcoin",
  amount: 2,
  average_buy_price: 50000,
  price: 60000,
  value: 120000,
  cost: 100000,
  pnl_value: 20000,
  pnl_percent: 20,
  change_24h: 1.5,
  priced: true,
  notes: ""
};

/** The same holding when no live price arrived. */
const unpricedHolding = {
  id: 2,
  symbol: "ghost",
  coin_name: "Ghost",
  amount: 5,
  average_buy_price: 100,
  price: null,
  value: null,
  cost: 500,
  pnl_value: null,
  pnl_percent: null,
  change_24h: null,
  priced: false,
  notes: ""
};

beforeEach(() => {
  mockPulseApi.mockReset();
});

describe("A number the server declined to state stays unstated", () => {
  it("keeps a missing price and value as null rather than zero", () => {
    const holding = normalizeHolding(unpricedHolding);
    expect(holding.price).toBeNull();
    expect(holding.value).toBeNull();
    expect(holding.pnlValue).toBeNull();
    expect(holding.pnlPercent).toBeNull();
    expect(holding.priced).toBe(false);
  });

  it("keeps a real zero as a zero", () => {
    // The whole point of null is that it is not zero, which only works if zero
    // still survives the round trip.
    const holding = normalizeHolding({ ...pricedHolding, pnl_value: 0, pnl_percent: 0 });
    expect(holding.pnlValue).toBe(0);
    expect(holding.pnlPercent).toBe(0);
  });

  it("keeps a negative percentage negative", () => {
    expect(normalizeHolding({ ...pricedHolding, pnl_percent: -20 }).pnlPercent).toBe(-20);
  });

  it("reads an empty string as unstated, not as zero", () => {
    // `Number("")` is 0, which is how a blank column becomes a price.
    expect(normalizeHolding({ ...pricedHolding, price: "" }).price).toBeNull();
  });

  it("reads an unparseable value as unstated", () => {
    expect(normalizeHolding({ ...pricedHolding, value: "n/a" }).value).toBeNull();
  });

  it("treats a holding with no buy price as having no basis", () => {
    // Holdings carried over from the original CoinPilotX portfolio have an
    // amount and no basis. Zero would claim they were acquired for nothing.
    const holding = normalizeHolding({ ...unpricedHolding, average_buy_price: null, legacy: true });
    expect(holding.averageBuyPrice).toBeNull();
    expect(holding.legacy).toBe(true);
  });

  it("does not trust a priced flag that the price contradicts", () => {
    expect(normalizeHolding({ ...unpricedHolding, priced: true }).priced).toBe(false);
  });
});

describe("The totals are only shown as whole when they are", () => {
  it("reports an incomplete valuation and names what is missing", () => {
    const portfolio = normalizePortfolio({
      holdings: [pricedHolding, unpricedHolding],
      total_value: 120000,
      total_cost: 100000,
      pnl_value: 20000,
      pnl_percent: 20,
      valuation: {
        complete: false,
        holdings: 2,
        priced: 1,
        unpriced: 1,
        unpriced_symbols: ["GHOST"],
        basis_known: 1
      },
      warning: "Live prices are unavailable for GHOST."
    });
    expect(totalsCoverEverything(portfolio)).toBe(false);
    expect(portfolio.valuation.unpricedSymbols).toEqual(["GHOST"]);
    expect(portfolio.valuation.priced).toBe(1);
    expect(portfolio.warning).toContain("GHOST");
  });

  it("reports a complete valuation when every holding was priced", () => {
    const portfolio = normalizePortfolio({
      holdings: [pricedHolding],
      total_value: 120000,
      valuation: { complete: true, unpriced_symbols: [] }
    });
    expect(totalsCoverEverything(portfolio)).toBe(true);
    expect(portfolio.warning).toBe("");
  });

  it("refuses a claim of completeness that the holdings contradict", () => {
    // If the server's summary and the rows it sent disagree, the caveat wins.
    // An unnecessary note costs a moment of doubt; a missing one costs a member
    // reading a total that is silently short.
    const portfolio = normalizePortfolio({
      holdings: [pricedHolding, unpricedHolding],
      valuation: { complete: true, unpriced_symbols: [] }
    });
    expect(totalsCoverEverything(portfolio)).toBe(false);
  });

  it("derives completeness itself when no valuation block arrived", () => {
    // An older server sends none. Asserting "incomplete" would caveat a
    // portfolio that is fine, so the holdings answer for themselves.
    expect(totalsCoverEverything(normalizePortfolio({ holdings: [pricedHolding] }))).toBe(true);
    expect(
      totalsCoverEverything(normalizePortfolio({ holdings: [pricedHolding, unpricedHolding] }))
    ).toBe(false);
    expect(
      normalizePortfolio({ holdings: [pricedHolding, unpricedHolding] }).valuation.unpricedSymbols
    ).toEqual(["GHOST"]);
  });

  it("reads a response that did not arrive as an empty portfolio, not an error", () => {
    const portfolio = normalizePortfolio(undefined);
    expect(portfolio.holdings).toEqual([]);
    expect(portfolio.totalValue).toBe(0);
    expect(totalsCoverEverything(portfolio)).toBe(true);
  });
});

describe("An absence is not ranked", () => {
  it("leaves an unpriced holding out of the ranking entirely", () => {
    // Formerly it arrived at -100% and would have taken the biggest-loser slot
    // from every real loss in the portfolio.
    const portfolio = normalizePortfolio({ holdings: [pricedHolding, unpricedHolding] });
    expect(rankableHoldings(portfolio).map((holding) => holding.symbol)).toEqual(["BTC"]);
  });

  it("leaves a holding with no cost basis out of the ranking", () => {
    const portfolio = normalizePortfolio({
      holdings: [{ ...pricedHolding, id: 3, symbol: "DOGE", cost: null, pnl_percent: null, legacy: true }]
    });
    expect(rankableHoldings(portfolio)).toEqual([]);
    // It still has a value, so it is not missing from the portfolio itself.
    expect(portfolio.holdings[0].value).toBe(120000);
  });
});

describe("The portfolio is read from the one endpoint that serves it", () => {
  it("normalizes what the endpoint returned", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, portfolio: { holdings: [unpricedHolding] } });
    const portfolio = await getPortfolio();
    expect(mockPulseApi).toHaveBeenCalledWith("/api/portfolio");
    expect(portfolio.holdings[0].value).toBeNull();
  });

  it("survives an ok response carrying no portfolio", async () => {
    mockPulseApi.mockResolvedValue({ ok: true });
    await expect(getPortfolio()).resolves.toMatchObject({ holdings: [] });
  });
});

/* ------------------------------------------------------------------ *
 * Writes
 *
 * The read path's job is not to invent a number the server withheld. The write
 * path has the mirror-image job: not to state a number the *member* withheld.
 * A blank buy price sent as 0 asserts the coin was acquired for nothing, and
 * the server would faithfully report a 100% gain on it — the same fabrication
 * as before, entered from the other end.
 * ------------------------------------------------------------------ */

describe("Adding a holding states only what the member actually entered", () => {
  beforeEach(() => mockPulseApi.mockReset());

  const bodyOf = () => JSON.parse(mockPulseApi.mock.calls[0][1].body as string);

  it("omits the buy price entirely when it was left blank", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, message: "Added" });
    await addHolding({ symbol: "btc", amount: 2 });

    const body = bodyOf();
    expect(body).not.toHaveProperty("average_buy_price");
    expect(mockPulseApi).toHaveBeenCalledWith("/api/portfolio", expect.objectContaining({ method: "POST" }));
  });

  it("omits it for an explicit null too — 'I don't know' is not zero", async () => {
    mockPulseApi.mockResolvedValue({ ok: true });
    await addHolding({ symbol: "eth", amount: 1, averageBuyPrice: null });
    expect(bodyOf()).not.toHaveProperty("average_buy_price");
  });

  it("sends a real zero when the member typed one", async () => {
    // An airdrop genuinely cost nothing. The rule is about silence, not about
    // the digit — dropping a deliberate 0 would be its own small lie.
    mockPulseApi.mockResolvedValue({ ok: true });
    await addHolding({ symbol: "doge", amount: 100, averageBuyPrice: 0 });
    expect(bodyOf().average_buy_price).toBe(0);
  });

  it("normalizes the symbol so the server is not asked to guess", async () => {
    mockPulseApi.mockResolvedValue({ ok: true });
    await addHolding({ symbol: "  sol  ", amount: 3 });
    expect(bodyOf().symbol).toBe("SOL");
  });

  it("leaves out the optional text fields rather than sending empty strings", async () => {
    mockPulseApi.mockResolvedValue({ ok: true });
    await addHolding({ symbol: "BTC", amount: 1, coinName: "", notes: "" });
    const body = bodyOf();
    expect(body).not.toHaveProperty("coin_name");
    expect(body).not.toHaveProperty("notes");
  });

  it("deletes by id through the one endpoint that owns the holding", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, message: "Removed" });
    await expect(deleteHolding(7)).resolves.toEqual({ ok: true, message: "Removed" });
    expect(mockPulseApi).toHaveBeenCalledWith("/api/portfolio/7", { method: "DELETE" });
  });
});

describe("A ceiling and a mistake are different failures", () => {
  /**
   * These two must never be confused. The first should offer to upgrade; the
   * second must not, because showing a paywall to somebody who merely mistyped
   * a ticker charges them for their own typo.
   */
  it("recognises the ceiling by its code", () => {
    expect(isPremiumRequired(new PulseApiError("Free plan limit", 403, PREMIUM_REQUIRED))).toBe(true);
  });

  it("does not recognise a bad symbol as a ceiling", () => {
    expect(isPremiumRequired(new PulseApiError("Unknown symbol", 400, "invalid_symbol"))).toBe(false);
    expect(isPremiumRequired(new PulseApiError("Bad request", 400))).toBe(false);
  });

  it("never matches on the message, so a copy edit cannot switch the upgrade off", () => {
    // Same refusal, translated. The code is what carries the meaning.
    expect(isPremiumRequired(new PulseApiError("Límite del plan gratuito", 403, PREMIUM_REQUIRED))).toBe(true);
    // And an English sentence that merely mentions Premium is not a ceiling.
    expect(isPremiumRequired(new PulseApiError("premium_required", 400, "invalid_symbol"))).toBe(false);
  });

  it("is not fooled by something that is not an API error at all", () => {
    expect(isPremiumRequired(new Error("offline"))).toBe(false);
    expect(isPremiumRequired({ code: PREMIUM_REQUIRED })).toBe(false);
    expect(isPremiumRequired(null)).toBe(false);
    expect(isPremiumRequired(undefined)).toBe(false);
  });
});
