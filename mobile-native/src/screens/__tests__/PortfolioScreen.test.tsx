/**
 * What the portfolio screen is allowed to say.
 *
 * Two failures are worth a test suite of their own here, because both are
 * invisible in review and both look like working software.
 *
 * The first is `$0.00`. The server stopped valuing an unpriced holding at zero,
 * but a render is one `Number(value || 0)` away from putting the zero back —
 * and the resulting row is not obviously wrong, it just quietly reports a total
 * loss on a coin that merely failed to be quoted. So the first group asserts the
 * dash, not the absence of a crash.
 *
 * The second is the upgrade prompt appearing for the wrong reason. The free
 * ceiling and a mistyped ticker both arrive as a thrown `PulseApiError`, and if
 * the screen cannot tell them apart it shows a paywall to somebody who fat-
 * fingered "BTCC". So the second group drives both refusals through the real
 * `isPremiumRequired` and checks which one offers to charge money.
 *
 * `t` returns the key, per the convention in the other screen tests: these
 * assertions should survive a copy edit and fail on a wiring change.
 */

import React from "react";
import { Alert } from "react-native";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("../../i18n", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue || key
  }),
  useFormatters: () => ({ date: (value: string) => value, number: (value: number) => String(value) })
}));

const mockGetPortfolio = jest.fn();
const mockAddHolding = jest.fn();
const mockDeleteHolding = jest.fn();

// Only the three network calls are replaced. `normalizePortfolio`,
// `isPremiumRequired`, `rankableHoldings` and `totalsCoverEverything` are the
// real ones on purpose — they are the logic under test, and stubbing them would
// leave a suite that verifies the stubs agree with themselves.
jest.mock("../../api/portfolio", () => ({
  ...jest.requireActual("../../api/portfolio"),
  getPortfolio: (...args: unknown[]) => mockGetPortfolio(...args),
  addHolding: (...args: unknown[]) => mockAddHolding(...args),
  deleteHolding: (...args: unknown[]) => mockDeleteHolding(...args)
}));

import { PulseApiError } from "../../api/pulseApi";
import { PREMIUM_REQUIRED, normalizePortfolio } from "../../api/portfolio";
import { PortfolioScreen } from "../PortfolioScreen";

const priced = {
  id: 1,
  symbol: "BTC",
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

/** Quoted, and down. Gives the movers panel a second rankable row. */
const loser = {
  ...priced,
  id: 2,
  symbol: "ETH",
  coin_name: "Ethereum",
  price: 1800,
  value: 3600,
  cost: 4000,
  pnl_value: -400,
  pnl_percent: -10
};

/** Held, but no live quote arrived. Every number the server withheld is null. */
const unpriced = {
  id: 3,
  symbol: "GHOST",
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

/** Carried over from the original CoinPilotX portfolio: an amount, no basis. */
const noBasis = {
  ...priced,
  id: 4,
  symbol: "DOGE",
  coin_name: "Dogecoin",
  average_buy_price: null,
  cost: null,
  pnl_value: null,
  pnl_percent: null,
  legacy: true
};

const navigation = { navigate: jest.fn(), setOptions: jest.fn() };

/**
 * The two props the screen actually touches, cast to the full navigator type.
 *
 * Building a real `NativeStackScreenProps` would mean stubbing a few dozen
 * navigation methods this screen never calls, and every one of them would be a
 * lie of the same size as the cast — just spread over more lines.
 */
type ScreenProps = React.ComponentProps<typeof PortfolioScreen>;
const props = () =>
  ({
    navigation,
    route: { key: "portfolio", name: "Portfolio", params: undefined }
  }) as unknown as ScreenProps;

const serve = (raw: Record<string, unknown>) =>
  mockGetPortfolio.mockResolvedValue(normalizePortfolio(raw));

/** Renders and waits out the initial load, so no test asserts against a spinner. */
async function show() {
  const utils = render(<PortfolioScreen {...props()} />);
  await waitFor(() => expect(mockGetPortfolio).toHaveBeenCalled());
  await act(async () => undefined);
  return utils;
}

beforeEach(() => {
  jest.clearAllMocks();
  serve({ holdings: [] });
});

describe("A number the server withheld is never rendered as a number", () => {
  it("shows a dash, not $0.00, for an unpriced holding", async () => {
    serve({
      holdings: [priced, unpriced],
      total_value: 120000,
      pnl_value: 20000,
      pnl_percent: 20,
      valuation: { complete: false, holdings: 2, priced: 1, unpriced: 1, unpriced_symbols: ["GHOST"], basis_known: 2 }
    });
    const { queryAllByText, getByText } = await show();

    // The specific string this whole feature exists to prevent.
    expect(queryAllByText("$0.00")).toHaveLength(0);
    // The value column of the unpriced row, and its percentage, are both dashes.
    expect(queryAllByText("--").length).toBeGreaterThanOrEqual(2);
    expect(getByText("premium:crypto.portfolio.row.priceUnavailable")).toBeTruthy();
    // The priced row is unaffected — the dash is about absence, not caution.
    // Twice: once as the row's value and once as the total, which here covers
    // only that row.
    expect(queryAllByText("$120,000.00")).toHaveLength(2);
  });

  it("says a holding has no buy price rather than showing one of $0.00", async () => {
    serve({ holdings: [noBasis], valuation: { complete: true, holdings: 1, priced: 1, unpriced: 0, unpriced_symbols: [], basis_known: 0 } });
    const { getByText, queryAllByText } = await show();

    expect(getByText(/premium:crypto\.portfolio\.row\.noBasis/)).toBeTruthy();
    expect(queryAllByText("$0.00")).toHaveLength(0);
  });

  /**
   * The aggregate P/L sums the holdings whose basis is known. With none known
   * that sum is 0, and 0 renders as "+$0.000000 · +0.00%" — which reads as
   * "you are exactly break-even", a claim the data does not support, sitting
   * directly above the screen's own note that no holding has a buy price.
   *
   * `$0.00` never appears here (six decimals below $1), so the sibling test
   * above cannot catch this. It has to be asserted on its own.
   */
  it("shows no aggregate P/L when no holding has a basis", async () => {
    serve({
      holdings: [noBasis],
      total_value: 120000,
      // What the server really sends for an empty decidable subset.
      pnl_value: 0,
      pnl_percent: 0,
      valuation: { complete: true, holdings: 1, priced: 1, unpriced: 0, unpriced_symbols: [], basis_known: 0 }
    });
    const { getByText, queryByText, queryAllByText } = await show();

    expect(queryByText(/\$0\.000000/)).toBeNull();
    expect(queryByText(/\+0\.00%/)).toBeNull();
    // The undecidable spelling every other unknown on this screen uses.
    expect(getByText("-- · --")).toBeTruthy();
    // The total itself is still known and still shown; only the gain is not.
    // Twice: the single holding's value column, and the total covering it.
    expect(queryAllByText("$120,000.00")).toHaveLength(2);
  });

  it("still shows the aggregate P/L when some holding has a basis", async () => {
    serve({
      holdings: [priced, noBasis],
      total_value: 120000,
      pnl_value: 20000,
      pnl_percent: 20,
      valuation: { complete: true, holdings: 2, priced: 2, unpriced: 0, unpriced_symbols: [], basis_known: 1 }
    });
    const { getByText } = await show();

    expect(getByText("+$20,000.00 · +20.00%")).toBeTruthy();
  });
});

describe("The screen loads the portfolio once", () => {
  /**
   * The i18n mock above returns a fresh `t` on every render, which is exactly
   * the shape the real provider would take if it ever stopped memoizing. If the
   * loader closed over `t`, the initial-load effect would re-run on every render
   * and refetch forever — a green build, a correct-looking screen, and a request
   * per frame. The ref in `PortfolioScreen` exists for this, so it gets a test.
   */
  it("does not refetch on every render", async () => {
    serve({ holdings: [priced] });
    const { rerender } = await show();
    expect(mockGetPortfolio).toHaveBeenCalledTimes(1);

    for (let index = 0; index < 3; index += 1) {
      rerender(<PortfolioScreen {...props()} />);
      await act(async () => undefined);
    }
    expect(mockGetPortfolio).toHaveBeenCalledTimes(1);
  });

  it("keeps an error on screen instead of clearing it with a fresh load", async () => {
    mockAddHolding.mockRejectedValue(new PulseApiError("Unknown symbol", 400, "invalid_symbol"));
    const utils = await show();
    fireEvent.changeText(utils.getByLabelText("premium:crypto.portfolio.form.symbolLabel"), "BTCC");
    fireEvent.changeText(utils.getByLabelText("premium:crypto.portfolio.form.amountLabel"), "1");
    await act(async () => {
      fireEvent.press(utils.getByText("premium:crypto.portfolio.form.submit"));
    });
    utils.rerender(<PortfolioScreen {...props()} />);
    await act(async () => undefined);
    expect(utils.getByText("Unknown symbol")).toBeTruthy();
  });
});

describe("The total says what it covers", () => {
  it("carries the server's warning when some holdings could not be priced", async () => {
    serve({
      holdings: [priced, unpriced],
      total_value: 120000,
      valuation: { complete: false, holdings: 2, priced: 1, unpriced: 1, unpriced_symbols: ["GHOST"], basis_known: 2 },
      warning: "Live prices are unavailable for GHOST."
    });
    const { getByText } = await show();

    expect(getByText("Live prices are unavailable for GHOST.")).toBeTruthy();
    // And the label admits the total is a sum over a subset.
    expect(getByText("premium:crypto.portfolio.total.partialLabel")).toBeTruthy();
  });

  it("adds no caveat at all when every holding was priced", async () => {
    serve({
      holdings: [priced],
      total_value: 120000,
      valuation: { complete: true, holdings: 1, priced: 1, unpriced: 0, unpriced_symbols: [], basis_known: 1 }
    });
    const { getByText, queryByText } = await show();

    expect(getByText("premium:crypto.portfolio.total.label")).toBeTruthy();
    expect(queryByText("premium:crypto.portfolio.total.incomplete")).toBeNull();
    expect(queryByText("premium:crypto.portfolio.total.partialLabel")).toBeNull();
    expect(queryByText("premium:crypto.portfolio.total.basisNote")).toBeNull();
  });

  it("caveats the total when the rows contradict a claim of completeness", async () => {
    // The server said complete and then sent an unpriced row. The caveat wins:
    // an unnecessary note costs a moment of doubt, a missing one costs a member
    // reading a total that is silently short.
    serve({
      holdings: [priced, unpriced],
      valuation: { complete: true, holdings: 2, priced: 2, unpriced: 0, unpriced_symbols: [], basis_known: 2 }
    });
    const { getByText } = await show();
    expect(getByText("premium:crypto.portfolio.total.incomplete")).toBeTruthy();
  });
});

describe("Only a real profit is ranked", () => {
  it("leaves an unpriced holding out of the movers list", async () => {
    serve({
      holdings: [priced, loser, unpriced],
      valuation: { complete: false, holdings: 3, priced: 2, unpriced: 1, unpriced_symbols: ["GHOST"], basis_known: 3 }
    });
    const { getByText, queryByText } = await show();

    expect(getByText("premium:crypto.portfolio.movers.title")).toBeTruthy();
    // GHOST has no P/L at all. Ranking it would rank an absence — which is how
    // a coin that merely failed to quote used to win "biggest loser" at -100%.
    expect(queryByText("mover-3")).toBeNull();
    expect(getByText("premium:crypto.portfolio.movers.excluded")).toBeTruthy();
  });

  it("hides the movers panel entirely rather than rank a single holding", async () => {
    serve({ holdings: [priced, unpriced] });
    const { queryByText } = await show();
    expect(queryByText("premium:crypto.portfolio.movers.title")).toBeNull();
  });
});

describe("The ceiling and a mistake get different offers", () => {
  const fill = (utils: ReturnType<typeof render>, symbol: string, amount: string) => {
    fireEvent.changeText(utils.getByLabelText("premium:crypto.portfolio.form.symbolLabel"), symbol);
    fireEvent.changeText(utils.getByLabelText("premium:crypto.portfolio.form.amountLabel"), amount);
  };

  it("offers an upgrade after the server refuses on the ceiling", async () => {
    mockAddHolding.mockRejectedValue(new PulseApiError("Free plan limit reached", 403, PREMIUM_REQUIRED));
    const utils = await show();
    fill(utils, "sol", "3");

    await act(async () => {
      fireEvent.press(utils.getByText("premium:crypto.portfolio.form.submit"));
    });

    expect(utils.getByText("premium:crypto.portfolio.upgrade.title")).toBeTruthy();
    fireEvent.press(utils.getByText("premium:crypto.portfolio.upgrade.action"));
    // The Premium screen owns the App Store relationship; this one names no
    // price and no purchase path of its own.
    expect(navigation.navigate).toHaveBeenCalledWith("Premium");
  });

  it("does not offer an upgrade for a symbol the server did not recognise", async () => {
    mockAddHolding.mockRejectedValue(new PulseApiError("Unknown symbol BTCC", 400, "invalid_symbol"));
    const utils = await show();
    fill(utils, "BTCC", "1");

    await act(async () => {
      fireEvent.press(utils.getByText("premium:crypto.portfolio.form.submit"));
    });

    // The error is shown; the paywall is not. Charging somebody for their own
    // typo is the failure this case exists to prevent.
    expect(utils.getByText("Unknown symbol BTCC")).toBeTruthy();
    expect(utils.queryByText("premium:crypto.portfolio.upgrade.title")).toBeNull();
    expect(navigation.navigate).not.toHaveBeenCalled();
  });

  it("shows no upgrade prompt before the server has refused anything", async () => {
    serve({ holdings: [priced, loser, noBasis] });
    const { queryByText } = await show();
    // Three holdings is the free ceiling exactly. The screen still does not
    // guess — it counts nothing and waits to be told.
    expect(queryByText("premium:crypto.portfolio.upgrade.title")).toBeNull();
  });

  it("withdraws the upgrade prompt once an add succeeds", async () => {
    mockAddHolding.mockRejectedValueOnce(new PulseApiError("Free plan limit", 403, PREMIUM_REQUIRED));
    const utils = await show();
    fill(utils, "SOL", "3");
    await act(async () => {
      fireEvent.press(utils.getByText("premium:crypto.portfolio.form.submit"));
    });
    expect(utils.getByText("premium:crypto.portfolio.upgrade.title")).toBeTruthy();

    mockAddHolding.mockResolvedValueOnce({ ok: true, message: "Added" });
    fill(utils, "SOL", "3");
    await act(async () => {
      fireEvent.press(utils.getByText("premium:crypto.portfolio.form.submit"));
    });
    expect(utils.queryByText("premium:crypto.portfolio.upgrade.title")).toBeNull();
  });
});

describe("The add form sends only what was typed", () => {
  const fill = (utils: ReturnType<typeof render>, symbol: string, amount: string, basis?: string) => {
    fireEvent.changeText(utils.getByLabelText("premium:crypto.portfolio.form.symbolLabel"), symbol);
    fireEvent.changeText(utils.getByLabelText("premium:crypto.portfolio.form.amountLabel"), amount);
    if (basis !== undefined) {
      fireEvent.changeText(utils.getByLabelText("premium:crypto.portfolio.form.basisLabel"), basis);
    }
  };

  it("sends a null basis when the buy price box was left empty", async () => {
    mockAddHolding.mockResolvedValue({ ok: true, message: "" });
    const utils = await show();
    fill(utils, "sol", "3");
    await act(async () => {
      fireEvent.press(utils.getByText("premium:crypto.portfolio.form.submit"));
    });

    // Null, not 0. Zero would assert the coin was acquired for nothing and the
    // server would report a 100% gain on it.
    expect(mockAddHolding).toHaveBeenCalledWith({ symbol: "SOL", amount: 3, averageBuyPrice: null });
  });

  it("passes a typed buy price through", async () => {
    mockAddHolding.mockResolvedValue({ ok: true, message: "" });
    const utils = await show();
    fill(utils, "SOL", "3", "142.5");
    await act(async () => {
      fireEvent.press(utils.getByText("premium:crypto.portfolio.form.submit"));
    });
    expect(mockAddHolding).toHaveBeenCalledWith({ symbol: "SOL", amount: 3, averageBuyPrice: 142.5 });
  });

  it("refuses locally rather than post an empty symbol or a zero amount", async () => {
    const utils = await show();

    fill(utils, "", "3");
    await act(async () => {
      fireEvent.press(utils.getByText("premium:crypto.portfolio.form.submit"));
    });
    expect(utils.getByText("premium:crypto.portfolio.form.symbolRequired")).toBeTruthy();

    fill(utils, "SOL", "0");
    await act(async () => {
      fireEvent.press(utils.getByText("premium:crypto.portfolio.form.submit"));
    });
    expect(utils.getByText("premium:crypto.portfolio.form.amountRequired")).toBeTruthy();

    expect(mockAddHolding).not.toHaveBeenCalled();
  });
});

describe("Removing a holding is confirmed first", () => {
  it("asks before deleting, and deletes by id once confirmed", async () => {
    serve({ holdings: [priced] });
    mockDeleteHolding.mockResolvedValue({ ok: true, message: "Removed" });
    const alert = jest.spyOn(Alert, "alert").mockImplementation(() => undefined);

    const utils = await show();
    fireEvent.press(utils.getByLabelText("premium:crypto.portfolio.remove.a11y"));

    expect(mockDeleteHolding).not.toHaveBeenCalled();
    const buttons = alert.mock.calls[0][2];
    await act(async () => {
      buttons?.[1]?.onPress?.();
    });
    expect(mockDeleteHolding).toHaveBeenCalledWith(1);

    alert.mockRestore();
  });
});
