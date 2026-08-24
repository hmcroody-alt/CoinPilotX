/**
 * The crypto portfolio, read the way the server now reports it.
 *
 * The server used to value an unpriced holding at zero and then compute its
 * profit as `value - cost`, reporting an asset that had merely failed to be
 * quoted as a total loss. That is fixed: a missing price, a missing cost basis
 * and a missing profit are each `null`, and the totals sum only over the
 * holdings that could actually be decided.
 *
 * Which puts the whole burden on this file, because the easiest way to undo
 * that fix is one `Number(value || 0)` on the client — the web portfolio page
 * did exactly that, rendering a null value as `$0.00` next to a "price
 * unavailable" label it had already printed. So `numberOrNull` never falls back
 * to zero, and nothing here invents a number the server declined to state.
 *
 * On completeness the client is deliberately *more* cautious than the server:
 * `valuation.complete` is true only when the server says so AND no holding in
 * the list is unpriced. If those two ever disagree the caveat wins, because the
 * cost of an unnecessary "some prices are unavailable" note is a moment of
 * doubt, and the cost of omitting a necessary one is a member reading a total
 * that is silently short.
 */

import { pulseApi } from "./pulseApi";

export type PortfolioHolding = {
  id: number;
  symbol: string;
  coinName: string;
  amount: number;
  /** null for holdings imported from the original CoinPilotX portfolio, which
   *  carry an amount and no buy price. Zero would invent a basis. */
  averageBuyPrice: number | null;
  price: number | null;
  value: number | null;
  cost: number | null;
  pnlValue: number | null;
  pnlPercent: number | null;
  change24h: number | null;
  /** Whether a live price arrived. Saves every caller a null test. */
  priced: boolean;
  legacy: boolean;
  notes: string;
};

export type PortfolioValuation = {
  /** Every holding was priced, so the totals cover the whole portfolio. */
  complete: boolean;
  holdings: number;
  priced: number;
  unpriced: number;
  unpricedSymbols: string[];
  /** How many holdings have a real cost basis, and therefore a real P/L. */
  basisKnown: number;
};

export type Portfolio = {
  holdings: PortfolioHolding[];
  /** Aggregates are always numeric. They sum the decidable subset only, which
   *  is what `valuation` describes — a total rendered without it is a sum over
   *  an unstated set. */
  totalValue: number;
  totalCost: number;
  pnlValue: number;
  pnlPercent: number;
  valuation: PortfolioValuation;
  /** The server's own sentence naming the assets the totals leave out. Empty
   *  when the valuation is complete. */
  warning: string;
};

type RawHolding = Record<string, unknown>;
type RawPortfolio = Record<string, unknown>;

/**
 * A number, or `null` when the server did not state one.
 *
 * The one function in this file that matters. `Number(null)` is 0 and
 * `Number("")` is 0, so the obvious spellings of this all quietly restore the
 * exact fabrication the server stopped producing.
 */
function numberOrNull(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** A number for the fields the server guarantees are numeric (the aggregates). */
function numberOrZero(value: unknown): number {
  return numberOrNull(value) ?? 0;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function normalizeHolding(raw: RawHolding): PortfolioHolding {
  const symbol = text(raw.symbol).toUpperCase();
  const price = numberOrNull(raw.price);
  return {
    id: numberOrZero(raw.id),
    symbol,
    coinName: text(raw.coin_name) || symbol,
    amount: numberOrZero(raw.amount),
    averageBuyPrice: numberOrNull(raw.average_buy_price),
    price,
    value: numberOrNull(raw.value),
    cost: numberOrNull(raw.cost),
    pnlValue: numberOrNull(raw.pnl_value),
    pnlPercent: numberOrNull(raw.pnl_percent),
    change24h: numberOrNull(raw.change_24h),
    // `priced` is stated by the server, but a holding with no price is not
    // priced whatever the flag says, so the two have to agree before it is true.
    priced: raw.priced === true && price !== null,
    legacy: raw.legacy === true,
    notes: text(raw.notes)
  };
}

export function normalizePortfolio(raw: RawPortfolio | null | undefined): Portfolio {
  const source = raw && typeof raw === "object" ? raw : {};
  const holdings = Array.isArray(source.holdings)
    ? (source.holdings as RawHolding[]).map(normalizeHolding)
    : [];
  const rawValuation =
    source.valuation && typeof source.valuation === "object"
      ? (source.valuation as Record<string, unknown>)
      : {};
  const unpricedSymbols = Array.isArray(rawValuation.unpriced_symbols)
    ? (rawValuation.unpriced_symbols as unknown[]).map((entry) => text(entry).toUpperCase()).filter(Boolean)
    : holdings.filter((holding) => holding.value === null).map((holding) => holding.symbol);
  // An older server sends no valuation block at all. Deriving completeness from
  // the holdings themselves is honest in that case; asserting "incomplete"
  // would caveat a portfolio that is fine.
  const everyHoldingPriced = holdings.every((holding) => holding.value !== null);
  const statedComplete =
    rawValuation.complete === undefined ? everyHoldingPriced : rawValuation.complete === true;
  return {
    holdings,
    totalValue: numberOrZero(source.total_value),
    totalCost: numberOrZero(source.total_cost),
    pnlValue: numberOrZero(source.pnl_value),
    pnlPercent: numberOrZero(source.pnl_percent),
    valuation: {
      complete: statedComplete && everyHoldingPriced,
      holdings: holdings.length,
      priced: holdings.filter((holding) => holding.value !== null).length,
      unpriced: holdings.filter((holding) => holding.value === null).length,
      unpricedSymbols,
      basisKnown: holdings.filter((holding) => holding.pnlPercent !== null).length
    },
    warning: text(source.warning)
  };
}

export async function getPortfolio(): Promise<Portfolio> {
  const response = await pulseApi<{ ok?: boolean; portfolio?: RawPortfolio }>("/api/portfolio");
  return normalizePortfolio(response?.portfolio);
}

/**
 * Holdings that can be ranked by profit.
 *
 * Anything unpriced or without a cost basis has no P/L at all, and including it
 * in a "biggest mover" list would rank an absence.
 */
export function rankableHoldings(portfolio: Portfolio): PortfolioHolding[] {
  return portfolio.holdings.filter((holding) => holding.pnlPercent !== null);
}

/**
 * Whether the portfolio total may be shown on its own.
 *
 * A false answer does not mean the total is wrong — it means it covers fewer
 * holdings than are on screen, so it needs the warning next to it.
 */
export function totalsCoverEverything(portfolio: Portfolio): boolean {
  return portfolio.valuation.complete;
}
