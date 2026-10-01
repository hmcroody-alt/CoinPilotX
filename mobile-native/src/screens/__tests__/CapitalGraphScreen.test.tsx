/**
 * Capital Graph, and the lie this suite exists to make impossible: a portfolio
 * the app could not fetch, drawn as a portfolio that is empty.
 *
 * The regression that motivated it shipped to a device. Production had the
 * graph route but not the portfolio route, so the graph answered READY with
 * zero nodes while the portfolio call failed — and the screen showed "could
 * not be loaded" and "nothing recorded" in the same frame, because the empty
 * claim was derived from the graph alone. The contract pinned here:
 *
 *   1. A failed portfolio fetch renders the failure, with Retry where retrying
 *      can help, and the empty state is ABSENT. Every refusal shape — ERROR,
 *      UNAVAILABLE, DENIED, NOT_ENTITLED, FEATURE_DISABLED, NOT_IMPLEMENTED,
 *      LOCKED — must pass this, because every one of them arrives while the
 *      graph is READY-and-empty.
 *   2. A READY portfolio with zero assets is the only thing allowed to say
 *      "nothing recorded", and it never shows failure copy or a Retry.
 *   3. A READY portfolio with assets renders them, with neither empty nor
 *      failure copy.
 *   4. Before any answer exists the screen says it is loading — not empty.
 *   5. Retry re-asks the server; it does not replay the cached failure.
 *
 * `t` returns the key, per the convention in the other screen tests: these
 * assertions survive a copy edit and fail on a wiring change.
 */

import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

jest.mock("../../i18n", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue || key
  })
}));

const mockGetGraph = jest.fn();
const mockGetPortfolio = jest.fn();
const mockGetOverview = jest.fn();
const mockGetCashFlow = jest.fn();
const mockGetExposure = jest.fn();
const mockGetObligations = jest.fn();
const mockOfficeStatus = jest.fn();
const mockUnlockOffice = jest.fn();

// Only the network reads are replaced; `parseCapitalGraph` and
// `parseCapitalPortfolio` stay real, so the fixtures below are server payloads,
// not hand-built client objects that could drift from the parser.
jest.mock("../../api/capitalGraph", () => ({
  ...jest.requireActual("../../api/capitalGraph"),
  getCapitalGraph: (...args: unknown[]) => mockGetGraph(...args),
  getCapitalPortfolio: (...args: unknown[]) => mockGetPortfolio(...args),
  getCapitalOverview: (...args: unknown[]) => mockGetOverview(...args),
  getCapitalCashFlow: (...args: unknown[]) => mockGetCashFlow(...args),
  getCapitalExposure: (...args: unknown[]) => mockGetExposure(...args),
  getCapitalObligations: (...args: unknown[]) => mockGetObligations(...args)
}));

// The screen sits behind `PrivateOfficeLockGate`; same boundary stubs as the
// Private Facts suite, so the body only renders because a live grant exists.
jest.mock("../../api/privateOffice", () => ({
  ...jest.requireActual("../../api/privateOffice"),
  getOfficeSecurityStatus: (...args: unknown[]) => mockOfficeStatus(...args),
  unlockOffice: (...args: unknown[]) => mockUnlockOffice(...args)
}));

jest.mock("../../session/sessionStore", () => ({
  ...jest.requireActual("../../session/sessionStore"),
  getSessionEnvelope: async () => ({
    version: 1,
    userId: 4021,
    accessToken: "access-token",
    accessTokenExpiresAt: Date.now() + 600_000,
    refreshToken: "refresh-token",
    refreshTokenExpiresAt: Date.now() + 600_000
  })
}));

import {
  parseCapitalCashFlow,
  parseCapitalExposure,
  parseCapitalGraph,
  parseCapitalObligations,
  parseCapitalOverview,
  parseCapitalPortfolio
} from "../../api/capitalGraph";
import {
  __resetOfficeLockForTests,
  isOfficeUnlocked,
  setOfficeUnlocked
} from "../../privateOffice/officeLock";
import { CapitalGraphScreen } from "../CapitalGraphScreen";

const OFFICE_PASSCODE = "846195";

const EMPTY_TITLE = "premium:privateOffice.capital.empty.title";
const FOLIO_EMPTY = "premium:privateOffice.capital.portfolio.empty";
const FOLIO_UNAVAILABLE = "premium:privateOffice.capital.portfolio.unavailable";
const RETRY = "premium:privateOffice.retry";

/** The graph production serves today on the holdings view: READY, no nodes. */
function emptyGraph() {
  return {
    state: "READY",
    graph: parseCapitalGraph(
      { nodes: [], edges: [], facts: [], conflicts: [], stale: [], counted: {}, complete: true },
      "holdings"
    )
  };
}

/** A portfolio exactly as the server emits it. */
function readyPortfolio(assets: Record<string, unknown>[] = []) {
  return {
    state: "READY",
    portfolio: parseCapitalPortfolio({
      assets,
      totals: {
        value: null,
        cost: null,
        pnl_value: null,
        complete: false,
        assets: assets.length,
        priced: 0,
        unpriced_symbols: [],
        basis_known: 0
      },
      prices: { source: "live_board", observed_epoch: null, age_seconds: 12, warning: "" },
      sync: { pending: 0, failed: 0, enabled: true }
    })
  };
}

function btcAsset() {
  return {
    node_id: 71,
    symbol: "BTC",
    name: "Bitcoin",
    quantity: 0.5,
    lot_count: 1,
    cost_basis: 20000,
    price: 60000,
    value: 30000,
    pnl_value: 10000,
    priced: true,
    change_24h: 1.2,
    projected_at: "2026-09-01T00:00:00Z"
  };
}

/**
 * The overview exactly as `/capital-graph/overview` emits it, run through the
 * real parser. Overrides are applied to the `overview` object, so a test can
 * say `readyOverview({ net_position: ... })` and still exercise every field
 * the screen reads.
 */
function readyOverview(overrides: Record<string, unknown> = {}) {
  return {
    state: "READY",
    overview: parseCapitalOverview({
      assets: {
        priced_value: 812450.25,
        currency: "USD",
        count: 9,
        priced: 7,
        unpriced: 2,
        unpriced_symbols: ["XMR"],
        basis_known: 5,
        known_cost: 604000,
        complete: false
      },
      liabilities: {
        known_amount: 240000,
        currency: "USD",
        count: 5,
        quantified: 3,
        unquantified: 2,
        foreign_currency: 1,
        unspecified_currency: 1,
        by_currency: {
          USD: { amount: 240000, count: 3 },
          GBP: { amount: 88000, count: 1 }
        },
        complete: false,
        truncated: false
      },
      net_position: {
        estimated: 572450.25,
        currency: "USD",
        known_assets: 812450.25,
        known_liabilities: 240000,
        complete: false,
        incomplete_reasons: ["unpriced_assets", "unquantified_liabilities"],
        excluded: {
          unpriced_assets: 2,
          unquantified_liabilities: 2,
          foreign_currency_liabilities: 1,
          unspecified_currency_liabilities: 0
        },
        basis: "priced_assets_minus_quantified_liabilities",
        disclaimer: "This is not a net worth figure."
      },
      coverage: {
        dimensions: {
          pricing: { known: 7, countable: 9, ratio: 7 / 9 },
          evidence: { known: 0, countable: 0, ratio: null }
        },
        score: 0.6,
        scored_dimensions: ["pricing"],
        formula: "mean(scored_dimensions)"
      },
      concentrations: {
        assets: [{ key: "BTC", label: "Bitcoin", value: 500000, share: 0.6157 }],
        asset_basis: "priced_asset_value",
        asset_total: 812450.25,
        assets_ranked: 1,
        assets_unranked_tail: 6,
        liabilities: [{ key: "MORTGAGE", label: "Mortgage", value: 240000, share: 1 }],
        liability_basis: "quantified_liability_amount",
        liability_total: 240000,
        currency: "USD"
      },
      needs_review: [
        {
          kind: "UNPRICED_ASSET",
          subject: "XMR",
          detail: "No market price is available.",
          source: "market_data"
        }
      ],
      needs_review_total: 4,
      prices: { source: "live_market_board", observed_epoch: 1788000000, age_seconds: 42, warning: "" },
      generated_at: "2026-09-06T12:00:00+00:00",
      ...overrides
    })
  };
}

/**
 * The obligations payload exactly as `/capital-graph/obligations` emits it,
 * run through the real parser.
 *
 * The default is the interesting case rather than the happy one: five recorded
 * obligations of which three carry an amount, in a single currency, with one
 * more filed under the server's no-currency sentinel. A fixture where
 * everything is known would let a screen that reads `known_amount` as "the
 * total owed" pass.
 */
function readyObligations(overrides: Record<string, unknown> = {}) {
  return {
    state: "READY",
    obligations: parseCapitalObligations({
      liabilities: [
        {
          node_id: 71,
          root_id: 12,
          title: "Mortgage",
          kind: "LIABILITY",
          // Deliberately not the overview fixture's 240000: these tests assert
          // that leaving the tab clears the figure, and a number both tabs
          // happen to print could never prove it.
          amount: 137500,
          currency: "USD",
          quantified: true,
          due_at: "2031-04-01",
          projected_at: "2026-09-06T11:00:00Z",
          freshness: { stale: false, age_days: 1, horizon_days: 30 },
          evidence: { fact_ids: [901, 902], provenance: null }
        },
        {
          node_id: 72,
          root_id: 13,
          title: "Family loan",
          kind: "LIABILITY",
          // No amount was ever entered. Not zero.
          amount: null,
          currency: "",
          quantified: false,
          due_at: null,
          projected_at: "2026-09-06T11:00:00Z",
          freshness: { stale: true, age_days: 400, horizon_days: 30 },
          evidence: { fact_ids: [], provenance: null }
        }
      ],
      totals: {
        known_amount: 137500,
        currency: "USD",
        by_currency: {
          USD: { amount: 137500, count: 1 },
          UNSPECIFIED: { amount: 5000, count: 1 }
        },
        currencies: ["USD"],
        count: 5,
        quantified: 3,
        unquantified: 2,
        unspecified_currency: 1,
        complete: false,
        truncated: false,
        limit: 200
      },
      sync: { projected: true, obligations: 5, retired: 1, skipped: 0 },
      ...overrides
    })
  };
}

/** The server's own wording for the two things this tab is not. */
const INFLOWS_BASIS =
  "Outflows only. PulseSoc records obligations but has no income ledger, so nothing here " +
  "has been netted against earnings, dividends or rent received. This is what is owed and " +
  "when, not what will be left.";
const RECURRENCE_BASIS =
  "Each obligation appears once, on the due date recorded for it. Recurrence is never " +
  "inferred: a monthly commitment recorded as a single dated obligation is scheduled once " +
  "here, not twelve times.";

/** The six windows `cash_flow.BUCKETS` declares, in the server's order. */
const DECLARED_BUCKETS = [
  { name: "overdue", from_days: null, to_days: 0 },
  { name: "due_30", from_days: 0, to_days: 30 },
  { name: "due_90", from_days: 30, to_days: 90 },
  { name: "due_180", from_days: 90, to_days: 180 },
  { name: "due_365", from_days: 180, to_days: 365 },
  { name: "beyond_365", from_days: 365, to_days: null }
];

/**
 * A cash-flow payload exactly as `services/private_office/cash_flow.py` emits it.
 *
 * As with `readyObligations`, the default is the awkward case rather than the
 * tidy one: six obligations were seen, three could be placed on the timeline,
 * and the other three were left out for three different reasons. A fixture
 * where everything was dated and priced would let a screen that reads
 * `scheduled_amount` as "what you owe" pass every assertion.
 *
 * The amounts are deliberately unlike the overview fixture's 240000 and the
 * obligations fixture's 137500 — tests here assert that leaving the tab clears
 * the figure, which a number two tabs both happen to print could never prove.
 */
function readyCashFlow(overrides: Record<string, unknown> = {}) {
  return {
    state: "READY",
    cashFlow: parseCapitalCashFlow({
      generated_at: "2026-09-06T11:00:00+00:00",
      schedule: [
        {
          node_id: 81,
          root_id: 21,
          title: "Council tax",
          kind: "LIABILITY",
          amount: 4200,
          currency: "USD",
          due_at: "2026-08-01T00:00:00+00:00",
          days_until: -36.4,
          overdue: true,
          bucket: "overdue",
          evidence: { fact_ids: [401], provenance: null }
        },
        {
          node_id: 82,
          root_id: 22,
          title: "Insurance premium",
          kind: "LIABILITY",
          amount: 18750,
          currency: "USD",
          due_at: "2026-09-20T00:00:00+00:00",
          days_until: 14.1,
          overdue: false,
          bucket: "due_30",
          evidence: { fact_ids: [], provenance: null }
        },
        {
          node_id: 83,
          root_id: 23,
          title: "Mortgage balloon",
          kind: "LIABILITY",
          amount: 312400,
          currency: "USD",
          due_at: "2031-04-01T00:00:00+00:00",
          days_until: 1668.0,
          overdue: false,
          bucket: "beyond_365",
          evidence: { fact_ids: [402, 403], provenance: null }
        }
      ],
      buckets: {
        overdue: { amount: 4200, count: 1 },
        due_30: { amount: 18750, count: 1 },
        due_90: { amount: 0, count: 0 },
        due_180: { amount: 0, count: 0 },
        due_365: { amount: 0, count: 0 },
        beyond_365: { amount: 312400, count: 1 }
      },
      totals: {
        currency: "USD",
        scheduled_amount: 335350,
        scheduled_count: 3,
        obligations_seen: 6,
        truncated: false,
        // Three obligations could not be placed, so the schedule understates.
        complete: false,
        excluded_count: 3,
        mixed_currency_rows: 0
      },
      excluded: { undated: 1, unquantified: 1, undated_and_unquantified: 1 },
      basis: {
        inflows: INFLOWS_BASIS,
        recurrence: RECURRENCE_BASIS,
        buckets: DECLARED_BUCKETS
      },
      sync: { projected: true, obligations: 6, retired: 0, skipped: 0 },
      ...overrides
    })
  };
}

/**
 * An exposure payload exactly as `/capital-graph/exposure` emits it — which is
 * to say a strict projection of the overview read, run through the real parser.
 *
 * The default is the case that makes the tab worth having: eleven holdings, of
 * which only four have a price. The ranking is therefore over 4/11 of the
 * member's positions, and the top row's 61% is 61% *of that quarter*. A fixture
 * where everything was priced would let a screen that printed the share without
 * its denominator pass every assertion in this block.
 *
 * The figures are deliberately unlike every other fixture here — the overview's
 * 812450.25/240000, the obligations' 137500, the cash flow's 335350 — so a
 * number on screen can only have come from this read. `generated_at` differs
 * from the overview's for the same reason.
 */
function readyExposure(overrides: Record<string, unknown> = {}) {
  return {
    state: "READY",
    exposure: parseCapitalExposure({
      concentrations: {
        // The server's order and the server's shares. They sum to the
        // asset_total below, as the real `_concentrations` guarantees.
        assets: [
          { key: "SOL", label: "Solana", value: 401200, share: 401200 / 655300 },
          { key: "ADA", label: "Cardano", value: 150100, share: 150100 / 655300 },
          { key: "DOT", label: "Polkadot", value: 78000, share: 78000 / 655300 },
          { key: "LINK", label: "Chainlink", value: 26000, share: 26000 / 655300 }
        ],
        asset_basis: "priced_asset_value",
        asset_total: 655300,
        assets_ranked: 4,
        assets_unranked_tail: 0,
        // Kinds, not creditors: this is what the server groups by.
        liabilities: [
          { key: "MORTGAGE", label: "Mortgage", value: 71900, share: 71900 / 96400 },
          { key: "CARD", label: "Card", value: 24500, share: 24500 / 96400 }
        ],
        liability_basis: "quantified_liability_amount",
        liability_total: 96400,
        currency: "USD"
      },
      assets: {
        priced_value: 655300,
        currency: "USD",
        count: 11,
        priced: 4,
        // Seven have no price and the projection could only name three of
        // them, so the screen has to account for four it cannot list.
        unpriced: 7,
        unpriced_symbols: ["XMR", "ZEC", "FIL"],
        basis_known: 2,
        known_cost: 401000,
        complete: false
      },
      liabilities: {
        known_amount: 96400,
        currency: "USD",
        count: 6,
        quantified: 4,
        unquantified: 2,
        foreign_currency: 1,
        unspecified_currency: 1,
        by_currency: {
          USD: { amount: 96400, count: 3 },
          CHF: { amount: 41250, count: 1 },
          // An amount the server holds but cannot label. Printing it in any
          // currency would invent the one fact that is missing.
          UNSPECIFIED: { amount: 8300, count: 1 }
        },
        complete: false,
        truncated: false
      },
      coverage: {
        dimensions: {
          pricing: { known: 4, countable: 11, ratio: 4 / 11 },
          evidence: { known: 0, countable: 0, ratio: null }
        },
        score: 0.31,
        scored_dimensions: ["pricing"],
        formula: "mean(scored_dimensions)"
      },
      prices: {
        source: "live_market_board",
        observed_epoch: 1788000000,
        age_seconds: 30,
        warning: ""
      },
      generated_at: "2026-09-06T12:34:56+00:00",
      ...overrides
    })
  };
}

async function unlockDoor(utils: ReturnType<typeof render>) {
  const { getByLabelText, getByText, queryByText } = utils;
  if (isOfficeUnlocked()) return;
  await waitFor(() => getByText("premium:privateOffice.lock.unlock"));
  fireEvent.changeText(getByLabelText("premium:privateOffice.lock.placeholder"), OFFICE_PASSCODE);
  await waitFor(() =>
    expect(getByLabelText("premium:privateOffice.lock.placeholder").props.value).toBe(
      OFFICE_PASSCODE
    )
  );
  fireEvent.press(getByText("premium:privateOffice.lock.unlock"));
  await waitFor(() => expect(queryByText("premium:privateOffice.lock.unlock")).toBeNull());
}

async function renderScreen(view = "holdings") {
  const navigation = { navigate: jest.fn(), goBack: jest.fn(), setOptions: jest.fn() };
  const utils = render(
    <CapitalGraphScreen
      route={{ key: "c", name: "CapitalGraph", params: { view } } as never}
      navigation={navigation as never}
    />
  );
  await unlockDoor(utils);
  return { ...utils, navigation };
}

/** Locale-stable expectations: the same Intl calls the screen makes. */
const money = (value: number) =>
  new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(value);
const percent = (ratio: number) =>
  new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 1 }).format(ratio);
/** Same, for the currencies that are not the screen's default. */
const moneyIn = (value: number, currency: string) =>
  new Intl.NumberFormat(undefined, { style: "currency", currency }).format(value);

beforeEach(() => {
  jest.clearAllMocks();
  __resetOfficeLockForTests();
  mockGetGraph.mockResolvedValue(emptyGraph());
  mockGetPortfolio.mockResolvedValue(readyPortfolio([btcAsset()]));
  mockGetOverview.mockResolvedValue(readyOverview());
  mockGetCashFlow.mockResolvedValue(readyCashFlow());
  mockGetExposure.mockResolvedValue(readyExposure());
  mockGetObligations.mockResolvedValue(readyObligations());
  // The status probe answers from the grant, as the real endpoint does: the
  // server that just accepted the passcode reports `unlocked: true` on the
  // next status read. A static `false` here would model a server-side
  // revocation between mounts — which the gate treats as authoritative and
  // answers by relocking, a different scenario than these tests stage.
  mockOfficeStatus.mockImplementation(async () => ({
    state: "READY",
    passcodeSet: true,
    setupRequired: false,
    cooldownSeconds: 0,
    biometricPreference: "unset",
    unlocked: isOfficeUnlocked()
  }));
  mockUnlockOffice.mockImplementation(async (passcode: string, userId: number) => {
    if (passcode !== OFFICE_PASSCODE) return { state: "WRONG_PASSCODE" };
    setOfficeUnlocked(
      "office-grant-token",
      new Date(Date.now() + 300_000).toISOString(),
      Number(userId) || 0
    );
    return { state: "UNLOCKED" };
  });
});

describe("CapitalGraphScreen holdings state machine", () => {
  it("says it is loading before either endpoint has answered, and claims nothing else", async () => {
    mockGetGraph.mockReturnValue(new Promise(() => undefined));
    mockGetPortfolio.mockReturnValue(new Promise(() => undefined));
    const { getByText, queryByText } = await renderScreen();
    expect(getByText("premium:privateOffice.capital.loading")).toBeTruthy();
    expect(queryByText(EMPTY_TITLE)).toBeNull();
    expect(queryByText(FOLIO_UNAVAILABLE)).toBeNull();
  });

  it("never draws a portfolio it could not fetch as one that is empty (the shipped regression)", async () => {
    // Production today: old graph route answers READY with zero nodes, the new
    // portfolio route does not exist, so the fetch comes back ERROR.
    mockGetPortfolio.mockResolvedValue({ state: "ERROR", message: "HTTP 404" });
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText(FOLIO_UNAVAILABLE));
    // The two sentences from the device screenshot must never coexist:
    expect(queryByText(EMPTY_TITLE)).toBeNull();
    expect(queryByText(FOLIO_EMPTY)).toBeNull();
    // A failed read is retryable.
    expect(getByText(RETRY)).toBeTruthy();
    // And the raw server message is not shown to the member.
    expect(queryByText("HTTP 404")).toBeNull();
  });

  it("keeps an outage as an outage: UNAVAILABLE gets failure copy and Retry, never the empty state", async () => {
    mockGetPortfolio.mockResolvedValue({ state: "UNAVAILABLE" });
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText(FOLIO_UNAVAILABLE));
    expect(queryByText(EMPTY_TITLE)).toBeNull();
    expect(queryByText(FOLIO_EMPTY)).toBeNull();
    expect(getByText(RETRY)).toBeTruthy();
  });

  it("keeps a refusal as a refusal: DENIED carries the server's reason and offers no Retry", async () => {
    mockGetPortfolio.mockResolvedValue({ state: "DENIED", reason: "actor_is_not_owner" });
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText("premium:privateOffice.capital.denied.body"));
    expect(getByText("actor_is_not_owner")).toBeTruthy();
    expect(queryByText(EMPTY_TITLE)).toBeNull();
    expect(queryByText(FOLIO_EMPTY)).toBeNull();
    // A policy refusal is not a transient failure.
    expect(queryByText(RETRY)).toBeNull();
  });

  it("names the tier wall instead of pretending the portfolio is empty", async () => {
    mockGetPortfolio.mockResolvedValue({ state: "NOT_ENTITLED", minimumTier: "PRIVATE" });
    const withTier = await renderScreen();
    await waitFor(() => withTier.getByText("premium:privateOffice.capital.notEntitled.body"));
    expect(withTier.queryByText(EMPTY_TITLE)).toBeNull();
    expect(withTier.queryByText(RETRY)).toBeNull();

    mockGetPortfolio.mockResolvedValue({ state: "NOT_ENTITLED", minimumTier: "" });
    const generic = await renderScreen();
    await waitFor(() =>
      generic.getByText("premium:privateOffice.capital.notEntitled.bodyGeneric")
    );
    expect(generic.queryByText(EMPTY_TITLE)).toBeNull();
  });

  it("keeps switched-off and never-built as their own sentences, with the empty state absent", async () => {
    mockGetPortfolio.mockResolvedValue({ state: "FEATURE_DISABLED" });
    const disabled = await renderScreen();
    await waitFor(() => disabled.getByText("premium:privateOffice.capital.disabled.body"));
    expect(disabled.queryByText(EMPTY_TITLE)).toBeNull();
    // The kill switch can come back within the session.
    expect(disabled.getByText(RETRY)).toBeTruthy();

    mockGetPortfolio.mockResolvedValue({ state: "NOT_IMPLEMENTED" });
    const unbuilt = await renderScreen();
    await waitFor(() => unbuilt.getByText("premium:privateOffice.capital.notImplemented.body"));
    expect(unbuilt.queryByText(EMPTY_TITLE)).toBeNull();
    expect(unbuilt.queryByText(RETRY)).toBeNull();
  });

  it("relocks the office when the portfolio call says the grant is dead", async () => {
    mockGetPortfolio.mockResolvedValue({ state: "LOCKED", setupRequired: false });
    const { getByText, queryByText } = await renderScreen();
    // `lockOfficeLocally` drops the grant, so the gate's door comes back.
    await waitFor(() => getByText("premium:privateOffice.lock.unlock"));
    expect(queryByText(EMPTY_TITLE)).toBeNull();
    expect(queryByText(FOLIO_EMPTY)).toBeNull();
  });

  it("renders a READY empty portfolio as the rich empty state — and only then", async () => {
    mockGetPortfolio.mockResolvedValue(readyPortfolio([]));
    const { getByText, queryByText, navigation } = await renderScreen();
    await waitFor(() => getByText(EMPTY_TITLE));
    expect(getByText("premium:privateOffice.capital.emptyBody.holdings")).toBeTruthy();
    // An empty portfolio is a real answer, not a failure to get one.
    expect(queryByText(FOLIO_UNAVAILABLE)).toBeNull();
    expect(queryByText(RETRY)).toBeNull();
    // The one governed way to add holdings: the existing Portfolio ledger.
    fireEvent.press(getByText("premium:privateOffice.capital.empty.openPortfolio"));
    expect(navigation.navigate).toHaveBeenCalledWith("Portfolio");
  });

  it("renders the holdings the server sent, with neither empty nor failure copy", async () => {
    const { getByText, getAllByText, queryByText } = await renderScreen();
    await waitFor(() => getAllByText("BTC"));
    expect(getByText("Bitcoin")).toBeTruthy();
    expect(queryByText(EMPTY_TITLE)).toBeNull();
    expect(queryByText(FOLIO_EMPTY)).toBeNull();
    expect(queryByText(FOLIO_UNAVAILABLE)).toBeNull();
  });

  it("re-asks both endpoints on Retry instead of replaying the cached failure", async () => {
    mockGetPortfolio.mockResolvedValue({ state: "ERROR", message: "" });
    const { getByText, getAllByText } = await renderScreen();
    await waitFor(() => getByText(RETRY));
    mockGetPortfolio.mockResolvedValue(readyPortfolio([btcAsset()]));
    fireEvent.press(getByText(RETRY));
    await waitFor(() => getAllByText("BTC"));
    expect(mockGetPortfolio).toHaveBeenCalledTimes(2);
    expect(mockGetGraph).toHaveBeenCalledTimes(2);
  });
});

/* --- Stage-two matrix: the dashboard's honesty and the other three tabs --- */

function ethAsset() {
  return {
    node_id: 72,
    symbol: "ETH",
    name: "Ethereum",
    quantity: 5,
    lot_count: 2,
    cost_basis: 5000,
    price: 2000,
    value: 10000,
    pnl_value: 5000,
    priced: true,
    change_24h: -0.4,
    projected_at: "2026-09-01T00:00:00Z"
  };
}

function xrpUnpriced() {
  return {
    node_id: 73,
    symbol: "XRP",
    name: "Ripple",
    quantity: 100,
    lot_count: 1,
    cost_basis: null,
    price: null,
    value: null,
    pnl_value: null,
    priced: false,
    change_24h: null,
    projected_at: "2026-09-01T00:00:00Z"
  };
}

/** A fully priced, fully based portfolio: the server sends real totals. */
function completePortfolio() {
  return {
    state: "READY",
    portfolio: parseCapitalPortfolio({
      assets: [btcAsset(), ethAsset()],
      totals: {
        value: 40000,
        cost: 25000,
        pnl_value: 15000,
        complete: true,
        assets: 2,
        priced: 2,
        unpriced_symbols: [],
        basis_known: 2
      },
      prices: { source: "live_board", observed_epoch: null, age_seconds: 12, warning: "" },
      sync: { pending: 0, failed: 0, enabled: true }
    })
  };
}

/** One priced holding, one the market feed could not price. */
function partialPortfolio() {
  return {
    state: "READY",
    portfolio: parseCapitalPortfolio({
      assets: [btcAsset(), xrpUnpriced()],
      totals: {
        value: null,
        cost: 20000,
        pnl_value: null,
        complete: false,
        assets: 2,
        priced: 1,
        unpriced_symbols: ["XRP"],
        basis_known: 1
      },
      prices: { source: "live_board", observed_epoch: null, age_seconds: 12, warning: "" },
      sync: { pending: 0, failed: 0, enabled: true }
    })
  };
}

const PT = "premium:privateOffice.capital.portfolio.";

describe("CapitalGraphScreen holdings dashboard", () => {
  it("shows the server's totals plainly when every holding is priced and based", async () => {
    mockGetPortfolio.mockResolvedValue(completePortfolio());
    const { getByText, getAllByText, queryByText } = await renderScreen();
    await waitFor(() => getByText(money(40000)));
    expect(getByText(`${PT}totalValue`)).toBeTruthy();
    expect(queryByText(`${PT}pricedValue`)).toBeNull();
    expect(getByText(`+${money(15000)}`)).toBeTruthy();
    expect(getByText(money(25000))).toBeTruthy();
    expect(getByText(`${PT}fresh.live`)).toBeTruthy();
    expect(getByText(`${PT}qualityPriced`)).toBeTruthy();
    expect(getByText(`${PT}qualityBasis`)).toBeTruthy();
    // Allocation is a ratio of the server's own values: 30k/40k and 10k/40k.
    expect(getAllByText(percent(0.75)).length).toBeGreaterThan(0);
    expect(getAllByText(percent(0.25)).length).toBeGreaterThan(0);
    expect(getByText(`${PT}allocationTitle`)).toBeTruthy();
  });

  it("labels a partial total as priced-only and never invents the missing prices", async () => {
    mockGetPortfolio.mockResolvedValue(partialPortfolio());
    const { getByText, getAllByText, queryByText } = await renderScreen();
    await waitFor(() => getByText(`${PT}pricedValue`));
    // The headline number is the priced subtotal, clearly labeled as such.
    expect(queryByText(`${PT}totalValue`)).toBeNull();
    expect(getAllByText(money(30000)).length).toBeGreaterThan(0);
    expect(getByText(`${PT}excludesUnpriced`)).toBeTruthy();
    expect(getByText(`${PT}unpriced`)).toBeTruthy();
    // The unpriced row confesses instead of showing a fabricated value.
    expect(getByText(`${PT}priceUnavailable`)).toBeTruthy();
    expect(getByText(`${PT}excludedFromTotal`)).toBeTruthy();
    // Unknown basis stays unknown: no zero-basis P&L anywhere.
    expect(getAllByText(`${PT}basisUnknown`).length).toBeGreaterThan(0);
    expect(getAllByText(`${PT}pnlUnavailable`).length).toBeGreaterThan(0);
    expect(getByText(`${PT}basisPartial`)).toBeTruthy();
    // Allocation covers priced holdings only and says so.
    expect(getByText(`${PT}allocationPricedOnly`)).toBeTruthy();
  });

  it("renders a loss as a signed negative, not dressed up and not hidden", async () => {
    mockGetPortfolio.mockResolvedValue({
      state: "READY",
      portfolio: parseCapitalPortfolio({
        assets: [{ ...btcAsset(), cost_basis: 35000, pnl_value: -5000 }],
        totals: {
          value: 30000,
          cost: 35000,
          pnl_value: -5000,
          complete: true,
          assets: 1,
          priced: 1,
          unpriced_symbols: [],
          basis_known: 1
        },
        prices: { source: "live_board", observed_epoch: null, age_seconds: 12, warning: "" },
        sync: { pending: 0, failed: 0, enabled: true }
      })
    });
    const { getByText } = await renderScreen();
    await waitFor(() => getByText(money(-5000)));
  });

  it("only ever calls the price feed's numbers as fresh as the feed admits", async () => {
    const aged = completePortfolio();
    const stale = {
      state: "READY",
      portfolio: {
        ...aged.portfolio,
        prices: { ...aged.portfolio.prices, ageSeconds: 7200 }
      }
    };
    mockGetPortfolio.mockResolvedValue(stale);
    const { getByText } = await renderScreen();
    await waitFor(() => getByText(`${PT}fresh.stale`));
  });

  it("swallows a second Retry while the first is still in flight", async () => {
    mockGetPortfolio.mockResolvedValue({ state: "ERROR", message: "" });
    const { getByText, queryByText } = await renderScreen();
    await waitFor(() => getByText(RETRY));
    mockGetGraph.mockReturnValue(new Promise(() => undefined));
    mockGetPortfolio.mockReturnValue(new Promise(() => undefined));
    fireEvent.press(getByText(RETRY));
    // While the pair is in flight the button is gone — replaced by a spinner —
    // so a second tap has nothing to hit, and the pair fired exactly once.
    await waitFor(() => expect(queryByText(RETRY)).toBeNull());
    expect(mockGetGraph).toHaveBeenCalledTimes(2);
    expect(mockGetPortfolio).toHaveBeenCalledTimes(2);
  });
});

describe("CapitalGraphScreen coverage, structure and documents tabs", () => {
  function coverageGraph() {
    return {
      state: "READY",
      graph: parseCapitalGraph(
        {
          nodes: [
            {
              id: 1,
              node_type: "ASSET",
              external_ref: "BTC",
              lifecycle_state: "ACTIVE",
              truth: "KNOWN",
              fact_count: 2
            }
          ],
          edges: [],
          facts: [
            {
              id: 11,
              fact_type: "portfolio.holding",
              value: "0.75",
              value_type: "decimal",
              domain: "capital",
              sensitivity: "HIGH",
              observed_at: "2026-09-01T00:00:00Z",
              lifecycle_state: "ACTIVE",
              provenance: {
                source_type: "upload",
                source_id: "doc-1",
                has_source_document: true,
                provenance_type: "DOCUMENT_EXTRACTED",
                verification: "SOURCED",
                observed_at: "2026-09-01T00:00:00Z",
                confidence: 0.9
              },
              freshness: { stale: false, age_days: 4, horizon_days: 365 }
            },
            {
              id: 12,
              fact_type: "portfolio.cost_basis",
              value: "20000",
              value_type: "decimal",
              domain: "capital",
              sensitivity: "HIGH",
              observed_at: "2026-09-01T00:00:00Z",
              lifecycle_state: "ACTIVE",
              provenance: {
                source_type: "manual",
                source_id: "user",
                has_source_document: false,
                provenance_type: "USER_ASSERTED",
                verification: "SELF_REPORTED",
                observed_at: "2026-09-01T00:00:00Z",
                confidence: 0.5
              },
              freshness: { stale: false, age_days: 4, horizon_days: 365 }
            }
          ],
          conflicts: [],
          stale: [],
          counted: { ASSET: 1 },
          truth_counts: { KNOWN: 1, ESTIMATED: 1 },
          complete: true
        },
        "coverage"
      )
    };
  }

  it("reports pricing, basis and verification coverage from the real payloads", async () => {
    mockGetGraph.mockResolvedValue(coverageGraph());
    mockGetPortfolio.mockResolvedValue(partialPortfolio());
    const { getByText } = await renderScreen("coverage");
    await waitFor(() => getByText("premium:privateOffice.capital.coverage.portfolioTitle"));
    expect(getByText("premium:privateOffice.capital.coverage.pricing")).toBeTruthy();
    expect(getByText("premium:privateOffice.capital.coverage.basis")).toBeTruthy();
    expect(getByText("premium:privateOffice.capital.coverage.factsTitle")).toBeTruthy();
    expect(getByText("premium:privateOffice.capital.coverage.knownShare")).toBeTruthy();
    expect(getByText("premium:privateOffice.capital.coverage.documentBacked")).toBeTruthy();
    // A self-reported fact is named as such — never presented as verified.
    expect(getByText("SOURCED")).toBeTruthy();
    expect(getByText("SELF_REPORTED")).toBeTruthy();
  });

  it("keeps the coverage tab's failed portfolio a failure, never an empty claim", async () => {
    mockGetPortfolio.mockResolvedValue({ state: "ERROR", message: "HTTP 404" });
    const { getByText, queryByText } = await renderScreen("coverage");
    await waitFor(() => getByText(`${PT}unavailableTitle`));
    expect(getByText(FOLIO_UNAVAILABLE)).toBeTruthy();
    expect(queryByText(EMPTY_TITLE)).toBeNull();
    expect(queryByText(FOLIO_EMPTY)).toBeNull();
    expect(getByText(RETRY)).toBeTruthy();
  });

  it("draws the structure tab from the projected edges, grouped and counted", async () => {
    mockGetGraph.mockResolvedValue({
      state: "READY",
      graph: parseCapitalGraph(
        {
          nodes: [
            {
              id: 1,
              node_type: "PERSON",
              external_ref: "Roody Cherie",
              lifecycle_state: "ACTIVE",
              truth: "KNOWN",
              fact_count: 1
            },
            {
              id: 2,
              node_type: "ASSET",
              external_ref: "BTC",
              lifecycle_state: "ACTIVE",
              truth: "KNOWN",
              fact_count: 2
            }
          ],
          edges: [
            {
              id: 9,
              source_node_id: 1,
              target_node_id: 2,
              relation_type: "OWNS",
              lifecycle_state: "ACTIVE",
              provenance: {
                source_type: "manual",
                source_id: "user",
                has_source_document: false,
                provenance_type: "USER_ASSERTED",
                verification: "SELF_REPORTED"
              }
            }
          ],
          facts: [],
          conflicts: [],
          stale: [],
          counted: { PERSON: 1, ASSET: 1 },
          truth_counts: { KNOWN: 2 },
          complete: true
        },
        "structure"
      )
    });
    const { getByText } = await renderScreen("structure");
    await waitFor(() =>
      getByText("premium:privateOffice.capital.structure.relationshipsTitle")
    );
    expect(getByText("OWNS")).toBeTruthy();
    expect(getByText("Roody Cherie → BTC")).toBeTruthy();
  });

  it("admits when the structure has nodes but no recorded relationships", async () => {
    mockGetGraph.mockResolvedValue({
      state: "READY",
      graph: parseCapitalGraph(
        {
          nodes: [
            {
              id: 2,
              node_type: "ASSET",
              external_ref: "BTC",
              lifecycle_state: "ACTIVE",
              truth: "KNOWN",
              fact_count: 2
            }
          ],
          edges: [],
          facts: [],
          conflicts: [],
          stale: [],
          counted: { ASSET: 1 },
          truth_counts: { KNOWN: 1 },
          complete: true
        },
        "structure"
      )
    });
    const { getByText } = await renderScreen("structure");
    await waitFor(() =>
      getByText("premium:privateOffice.capital.structure.noRelationships")
    );
  });

  it("lists only evidence that actually exists on the documents tab", async () => {
    mockGetGraph.mockResolvedValue(coverageGraph());
    const { getByText, queryByText } = await renderScreen("documents");
    await waitFor(() => getByText("premium:privateOffice.capital.documents.evidenceTitle"));
    expect(getByText("portfolio.holding")).toBeTruthy();
    // Only the document-backed fact is evidence; the self-reported one is not.
    expect(queryByText("portfolio.cost_basis")).toBeNull();
  });

  it("keeps the documents empty state governed: its own copy, no Portfolio door", async () => {
    const { getByText, queryByText } = await renderScreen("documents");
    await waitFor(() => getByText(EMPTY_TITLE));
    expect(getByText("premium:privateOffice.capital.emptyBody.documents")).toBeTruthy();
    expect(queryByText("premium:privateOffice.capital.empty.openPortfolio")).toBeNull();
  });

  it("keeps one unlock good for all four tabs — no re-prompt, no relock", async () => {
    const { getByText, getAllByText, queryByText } = await renderScreen();
    await waitFor(() => getAllByText("BTC"));
    fireEvent.press(getByText("premium:privateOffice.capital.views.coverage"));
    await waitFor(() =>
      getByText("premium:privateOffice.capital.coverage.portfolioTitle")
    );
    expect(queryByText("premium:privateOffice.lock.unlock")).toBeNull();
    fireEvent.press(getByText("premium:privateOffice.capital.views.structure"));
    await waitFor(() => getByText("premium:privateOffice.capital.emptyBody.structure"));
    expect(queryByText("premium:privateOffice.lock.unlock")).toBeNull();
    fireEvent.press(getByText("premium:privateOffice.capital.views.documents"));
    await waitFor(() => getByText("premium:privateOffice.capital.emptyBody.documents"));
    expect(queryByText("premium:privateOffice.lock.unlock")).toBeNull();
    // The portfolio read belongs to holdings and coverage alone.
    expect(mockGetPortfolio).toHaveBeenCalledTimes(2);
  });
});

/**
 * The Overview tab, and the lie it exists to make impossible: `estimated`
 * quoted as if it were net worth.
 *
 * It is priced assets minus quantified liabilities. The server ships
 * `complete` and `incomplete_reasons` alongside it precisely so the number
 * cannot be read without its qualifier, and when the server withholds the
 * figure there must be no number on the screen at all — not a zero, not a
 * bare unlabelled total in some assumed currency.
 *
 * Overview is also a different endpoint from the graph, so it fails
 * separately. A healthy graph must never vouch for an overview that never
 * answered.
 */
describe("CapitalGraphScreen overview tab", () => {
  const OV = "premium:privateOffice.capital.overview";

  it("asks the overview endpoint, and does not ask the graph for a view it has no name for", async () => {
    const { getByText } = await renderScreen("overview");
    await waitFor(() => getByText(`${OV}.title`));

    expect(mockGetOverview).toHaveBeenCalledTimes(1);
    // The graph route rejects an unknown `view`. Sending it "overview" would
    // be a 400 dressed as a tab.
    expect(mockGetGraph).not.toHaveBeenCalled();
    expect(mockGetPortfolio).not.toHaveBeenCalled();
  });

  it("never renders the figure without the qualifier the server sent with it", async () => {
    const { getByText, queryByText } = await renderScreen("overview");
    await waitFor(() => getByText(`${OV}.title`));

    // The number is present...
    getByText(money(572450.25));
    // ...and so is every part of what makes it honest: the partial badge, the
    // standing disclaimer, and each reason the server gave by name.
    getByText(`${OV}.partial`);
    expect(queryByText(`${OV}.complete`)).toBeNull();
    getByText(`${OV}.disclaimerTitle`);
    getByText(`${OV}.disclaimerBody`);
    getByText("unpriced_assets");
    getByText("unquantified_liabilities");
  });

  it("reads `complete` from the wire rather than inferring it from an empty reason list", async () => {
    // Deliberately contradictory: no reasons, but the server still says the
    // figure is partial. A screen that derived the badge from
    // `incompleteReasons.length` would call this complete and be wrong.
    mockGetOverview.mockResolvedValue(
      readyOverview({
        net_position: {
          estimated: 100,
          currency: "USD",
          known_assets: 100,
          known_liabilities: 0,
          complete: false,
          incomplete_reasons: [],
          excluded: {
            unpriced_assets: 0,
            unquantified_liabilities: 0,
            foreign_currency_liabilities: 0,
            unspecified_currency_liabilities: 0
          },
          basis: "priced_assets_minus_quantified_liabilities",
          disclaimer: "This is not a net worth figure."
        }
      })
    );
    const { getByText, queryByText } = await renderScreen("overview");
    await waitFor(() => getByText(`${OV}.title`));

    getByText(`${OV}.partial`);
    expect(queryByText(`${OV}.complete`)).toBeNull();
  });

  it("still refuses to call a COMPLETE figure net worth", async () => {
    // The tempting case. Everything is priced, everything is quantified,
    // nothing was excluded — and it is still assets minus liabilities on
    // record, not a net worth statement. The disclaimer is not a defect
    // notice that disappears when the data is clean.
    mockGetOverview.mockResolvedValue(
      readyOverview({
        net_position: {
          estimated: 572450.25,
          currency: "USD",
          known_assets: 812450.25,
          known_liabilities: 240000,
          complete: true,
          incomplete_reasons: [],
          excluded: {
            unpriced_assets: 0,
            unquantified_liabilities: 0,
            foreign_currency_liabilities: 0,
            unspecified_currency_liabilities: 0
          },
          basis: "priced_assets_minus_quantified_liabilities",
          disclaimer: "This is not a net worth figure."
        }
      })
    );
    const { getByText, queryByText } = await renderScreen("overview");
    await waitFor(() => getByText(`${OV}.title`));

    getByText(`${OV}.complete`);
    expect(queryByText(`${OV}.partial`)).toBeNull();
    // Present regardless of completeness — both our wording and the server's.
    getByText(`${OV}.disclaimerTitle`);
    getByText(`${OV}.disclaimerBody`);
    getByText("This is not a net worth figure.");
    // Nothing was excluded, so that section stays away entirely.
    expect(queryByText(`${OV}.excludedTitle`)).toBeNull();
  });

  it("draws no number at all when the server withheld the total — least of all a zero", async () => {
    mockGetOverview.mockResolvedValue(
      readyOverview({
        net_position: {
          estimated: null,
          currency: "",
          known_assets: null,
          known_liabilities: null,
          complete: false,
          incomplete_reasons: ["uncomparable_currency"],
          excluded: {
            unpriced_assets: 0,
            unquantified_liabilities: 0,
            foreign_currency_liabilities: 3,
            unspecified_currency_liabilities: 0
          },
          basis: "priced_assets_minus_quantified_liabilities",
          disclaimer: "This is not a net worth figure."
        }
      })
    );
    const { getByText, getAllByText, queryByText } = await renderScreen("overview");
    await waitFor(() => getByText(`${OV}.title`));

    // Three cells had nothing to say, and all three say so.
    expect(getAllByText(`${OV}.withheld`).length).toBeGreaterThanOrEqual(3);
    getByText(`${OV}.notSummable`);
    getByText("uncomparable_currency");
    // The specific failure this guards: a null total rendered as money.
    expect(queryByText(money(0))).toBeNull();
  });

  it("names what was left out of the figure, and how much of it there was", async () => {
    const { getByText } = await renderScreen("overview");
    await waitFor(() => getByText(`${OV}.title`));

    getByText(`${OV}.excludedUnpricedAssets`);
    getByText(`${OV}.excludedUnquantifiedLiabilities`);
    getByText(`${OV}.excludedForeignCurrency`);
    // Zero-count exclusions are absent rather than listed as none.
    expect(() => getByText(`${OV}.excludedUnspecifiedCurrency`)).toThrow();

    // A count of excluded foreign-currency debts is only half the fact. The
    // magnitude the server set aside has to appear too, or the screen names a
    // number of liabilities while withholding their size.
    getByText(`${OV}.otherCurrencies`);
    getByText("GBP");
    getByText(
      new Intl.NumberFormat(undefined, { style: "currency", currency: "GBP" }).format(88000)
    );
  });

  it("says that nothing recorded is not the same as nothing owed", async () => {
    mockGetOverview.mockResolvedValue(
      readyOverview({
        liabilities: {
          known_amount: null,
          currency: "USD",
          count: 0,
          quantified: 0,
          unquantified: 0,
          foreign_currency: 0,
          unspecified_currency: 0,
          by_currency: {},
          complete: true,
          truncated: false
        }
      })
    );
    const { getByText } = await renderScreen("overview");
    await waitFor(() => getByText(`${OV}.title`));

    getByText(`${OV}.noLiabilities`);
  });

  it("calls an unscoreable coverage unscoreable, not zero", async () => {
    mockGetOverview.mockResolvedValue(
      readyOverview({
        coverage: {
          dimensions: { evidence: { known: 0, countable: 0, ratio: null } },
          score: null,
          scored_dimensions: [],
          formula: "mean(scored_dimensions)"
        }
      })
    );
    const { getByText, queryByText } = await renderScreen("overview");
    await waitFor(() => getByText(`${OV}.title`));

    getByText(`${OV}.unscoreable`);
    // "0%" would read as "we looked and you have nothing verified".
    expect(queryByText(percent(0))).toBeNull();
  });

  it("renders the server's concentration share instead of recomputing one from the values", async () => {
    // The server's `share` is a share of the *priced* total, which is not the
    // same denominator as the ranked slices this list shows. So the fixture
    // makes them disagree on purpose: value/total would land on a different
    // percentage than the ratio the server published. Only a screen that
    // reads the wire prints the server's number.
    mockGetOverview.mockResolvedValue(
      readyOverview({
        concentrations: {
          assets: [{ key: "BTC", label: "Bitcoin", value: 500000, share: 0.25 }],
          asset_basis: "priced_asset_value",
          asset_total: 1000000,
          assets_ranked: 1,
          assets_unranked_tail: 0,
          liabilities: [],
          liability_basis: "quantified_liability_amount",
          liability_total: null,
          currency: "USD"
        }
      })
    );
    const { getByText, queryByText } = await renderScreen("overview");
    await waitFor(() => getByText(`${OV}.title`));

    getByText(`${OV}.concentrationTitle`);
    getByText("Bitcoin");
    getByText(percent(0.25));
    // 500000/1000000. A client that divided would print this instead.
    expect(queryByText(percent(0.5))).toBeNull();
    // Nothing was truncated here, so the tail note stays away.
    expect(queryByText(`${OV}.concentrationUnranked`)).toBeNull();
  });

  it("says the share is unknown when the server had no total to divide by", async () => {
    mockGetOverview.mockResolvedValue(
      readyOverview({
        concentrations: {
          assets: [{ key: "BTC", label: "Bitcoin", value: null, share: null }],
          asset_basis: "priced_asset_value",
          asset_total: null,
          assets_ranked: 1,
          assets_unranked_tail: 2,
          liabilities: [],
          liability_basis: "quantified_liability_amount",
          liability_total: null,
          currency: "USD"
        }
      })
    );
    const { getByText } = await renderScreen("overview");
    await waitFor(() => getByText(`${OV}.title`));

    getByText(`${OV}.shareUnknown`);
    getByText(`${OV}.concentrationUnranked`);
  });

  it("admits the review list is capped rather than implying it is the whole set", async () => {
    const { getByText } = await renderScreen("overview");
    await waitFor(() => getByText(`${OV}.title`));

    getByText(`${OV}.reviewTitle`);
    getByText("XMR");
    // The server's own prose and the system that owns the fix, verbatim.
    getByText("No market price is available.");
    getByText("market_data");
    getByText(`${OV}.reviewMore`);
  });

  it("keeps an overview refusal a refusal, with no balance sheet and no empty claim", async () => {
    mockGetOverview.mockResolvedValue({ state: "DENIED", reason: "not the owner of record" });
    const { getByText, queryByText } = await renderScreen("overview");
    await waitFor(() => getByText("premium:privateOffice.capital.denied.title"));

    getByText("not the owner of record");
    expect(queryByText(`${OV}.title`)).toBeNull();
    expect(queryByText(EMPTY_TITLE)).toBeNull();
    expect(queryByText(RETRY)).toBeNull();
  });

  it("lets the tab in front own the verdict — a healthy graph does not vouch for a failed overview", async () => {
    mockGetOverview.mockResolvedValue({ state: "UNAVAILABLE" });
    const { getAllByText, getByText, queryAllByText, queryByText } =
      await renderScreen("overview");
    await waitFor(() => getByText("premium:privateOffice.capital.unavailable.title"));
    expect(queryByText(`${OV}.title`)).toBeNull();

    // Holdings answers fine. Its success must not retroactively clear the
    // overview's outage banner — nor must the overview's outage survive onto
    // a tab that did answer.
    fireEvent.press(getByText("premium:privateOffice.capital.views.holdings"));
    // BTC labels both the allocation bar and its holdings row, so the count is
    // two; what matters here is that the tab rendered at all.
    await waitFor(() => expect(getAllByText("BTC").length).toBeGreaterThan(0));
    expect(queryByText("premium:privateOffice.capital.unavailable.title")).toBeNull();

    // And back: the overview is still broken, and says so again.
    fireEvent.press(getByText("premium:privateOffice.capital.views.overview"));
    await waitFor(() => getByText("premium:privateOffice.capital.unavailable.title"));
    expect(queryAllByText("BTC")).toHaveLength(0);
  });

  it("stops drawing the net position once the member leaves the tab", async () => {
    const { getAllByText, getByText, queryByText } = await renderScreen("overview");
    await waitFor(() => getByText(`${OV}.title`));

    // The parsed overview survives the switch by design (returning is
    // instant). Painting it over the holdings tab would not.
    fireEvent.press(getByText("premium:privateOffice.capital.views.holdings"));
    await waitFor(() => expect(getAllByText("BTC").length).toBeGreaterThan(0));
    expect(queryByText(`${OV}.title`)).toBeNull();
    expect(queryByText(money(572450.25))).toBeNull();
  });

  it("relocks the office when the overview says the grant is dead", async () => {
    mockGetOverview.mockResolvedValue({ state: "LOCKED", setupRequired: false });
    const { getByText } = await renderScreen("overview");

    // The gate takes the screen back to the unlock door rather than leaving a
    // dead grant in place.
    await waitFor(() => getByText("premium:privateOffice.lock.unlock"));
    expect(isOfficeUnlocked()).toBe(false);
  });
});

/**
 * Obligations: the tab where a null is most likely to be read as a zero.
 *
 * `known_amount` is null whenever the recorded debts do not share one
 * currency, and null there means "we will not add these up" — not "nothing is
 * owed". The same distinction repeats per row: no amount recorded, an amount
 * recorded with no currency, and a real figure are three different states, and
 * every one of them has a way to be flattened into a comforting zero. These
 * tests exist to make each flattening fail.
 */
describe("CapitalGraphScreen obligations tab", () => {
  const OB = "premium:privateOffice.capital.obligations";
  const OV = "premium:privateOffice.capital.overview";

  it("asks the obligations endpoint, and never the graph, for a view it has no name for", async () => {
    const { getByText } = await renderScreen("obligations");
    await waitFor(() => getByText(`${OB}.title`));

    expect(mockGetObligations).toHaveBeenCalledTimes(1);
    // `view=obligations` is not in the graph's vocabulary — the route would
    // reject it. The tab must never be narrowed into one.
    expect(mockGetGraph).not.toHaveBeenCalled();
    expect(mockGetOverview).not.toHaveBeenCalled();
  });

  it("draws no total when no single currency answers for one — least of all a sum across them", async () => {
    mockGetObligations.mockResolvedValue(
      readyObligations({
        totals: {
          known_amount: null,
          currency: "",
          by_currency: {
            USD: { amount: 240000, count: 1 },
            EUR: { amount: 88000, count: 2 }
          },
          currencies: ["EUR", "USD"],
          count: 5,
          quantified: 3,
          unquantified: 2,
          unspecified_currency: 0,
          complete: false,
          truncated: false,
          limit: 200
        }
      })
    );
    const { getByText, queryByText } = await renderScreen("obligations");
    await waitFor(() => getByText(`${OB}.title`));

    getByText(`${OB}.withheld`);
    getByText(`${OB}.notSummable`);
    // The two figures the server did publish, each under its own code.
    getByText(`${OB}.currenciesTitle`);
    getByText("USD");
    getByText("EUR");
    // What the server refused to compute, and what a helpful client would
    // have computed for it: 240000 + 88000, in a currency nobody named.
    expect(queryByText(money(328000))).toBeNull();
    expect(queryByText(money(0))).toBeNull();
  });

  it("keeps the quantified split in the same card as the figure it qualifies", async () => {
    const { getAllByText, getByText } = await renderScreen("obligations");
    await waitFor(() => getByText(`${OB}.title`));

    // Twice: once as the headline, once on the row that is the whole of it.
    expect(getAllByText(money(137500))).toHaveLength(2);
    // Three of five obligations carry an amount. The headline speaks for
    // three; saying so is what stops it speaking for five.
    getByText(`${OB}.recordedLabel`);
    getByText(`${OB}.quantifiedLabel`);
    getByText(`${OB}.unquantifiedLabel`);
    getByText(`${OB}.partial`);
  });

  it("still qualifies a total that has nothing missing from it", async () => {
    // The tempting case. One obligation, quantified, one currency, nothing
    // truncated, nothing skipped — and it is still the sum of what has been
    // written down, not the sum of what is owed. The disclaimer is not a
    // defect notice that disappears when the data is clean.
    mockGetObligations.mockResolvedValue(
      readyObligations({
        liabilities: [
          {
            node_id: 71,
            root_id: 12,
            title: "Mortgage",
            kind: "LIABILITY",
            amount: 240000,
            currency: "USD",
            quantified: true,
            due_at: "2031-04-01",
            projected_at: "2026-09-06T11:00:00Z",
            freshness: { stale: false, age_days: 1, horizon_days: 30 },
            evidence: { fact_ids: [901], provenance: null }
          }
        ],
        totals: {
          known_amount: 240000,
          currency: "USD",
          by_currency: { USD: { amount: 240000, count: 1 } },
          currencies: ["USD"],
          count: 1,
          quantified: 1,
          unquantified: 0,
          unspecified_currency: 0,
          complete: true,
          truncated: false,
          limit: 200
        },
        sync: { projected: true, obligations: 1, retired: 0, skipped: 0 }
      })
    );
    const { getByText, queryByText } = await renderScreen("obligations");
    await waitFor(() => getByText(`${OB}.title`));

    getByText(`${OB}.complete`);
    getByText(`${OB}.disclaimerTitle`);
    getByText(`${OB}.disclaimerBody`);
    expect(queryByText(`${OB}.partial`)).toBeNull();
  });

  it("says that no obligations recorded is not the same as none owed", async () => {
    mockGetObligations.mockResolvedValue(
      readyObligations({
        liabilities: [],
        totals: {
          known_amount: null,
          currency: "",
          by_currency: {},
          currencies: [],
          count: 0,
          quantified: 0,
          unquantified: 0,
          unspecified_currency: 0,
          complete: true,
          truncated: false,
          limit: 200
        },
        sync: { projected: true, obligations: 0, retired: 0, skipped: 0 }
      })
    );
    const { getByText, queryByText } = await renderScreen("obligations");
    await waitFor(() => getByText(`${OB}.title`));

    getByText(`${OB}.none`);
    // Not the generic empty state, which says "nothing recorded" and stops.
    expect(queryByText(EMPTY_TITLE)).toBeNull();
    expect(queryByText(money(0))).toBeNull();
  });

  it("keeps a row with no amount an absence rather than a zero", async () => {
    const { getByText, queryByText } = await renderScreen("obligations");
    await waitFor(() => getByText(`${OB}.title`));

    getByText("Family loan");
    getByText(`${OB}.amountMissing`);
    getByText(`${OB}.dueMissing`);
    getByText(`${OB}.staleTitle`);
    getByText(`${OB}.evidenceMissing`);
    expect(queryByText(money(0))).toBeNull();
  });

  it("tells an amount with no currency apart from no amount at all", async () => {
    mockGetObligations.mockResolvedValue(
      readyObligations({
        liabilities: [
          {
            node_id: 73,
            root_id: 14,
            title: "Tax bill",
            kind: "LIABILITY",
            // A figure the member entered, against a currency they did not.
            amount: 5000,
            currency: "",
            quantified: true,
            due_at: "2027-01-31",
            projected_at: "2026-09-06T11:00:00Z",
            freshness: null,
            evidence: { fact_ids: [5], provenance: null }
          }
        ],
        totals: {
          known_amount: null,
          currency: "",
          by_currency: { UNSPECIFIED: { amount: 5000, count: 1 } },
          currencies: [],
          count: 1,
          quantified: 1,
          unquantified: 0,
          unspecified_currency: 1,
          complete: false,
          truncated: false,
          limit: 200
        }
      })
    );
    const { getAllByText, getByText, queryByText } = await renderScreen("obligations");
    await waitFor(() => getByText(`${OB}.title`));

    getByText("Tax bill");
    // The row is neither blank nor a dollar figure: "an amount, no currency"
    // is its own sentence, and it appears both on the row and in the totals.
    expect(getAllByText(`${OB}.unspecifiedCurrency`).length).toBeGreaterThan(1);
    expect(queryByText(`${OB}.amountMissing`)).toBeNull();
    expect(queryByText(money(5000))).toBeNull();
  });

  it("never prints the server's no-currency bucket as though it were a currency", async () => {
    const { getAllByText, getByText, queryByText } = await renderScreen("obligations");
    await waitFor(() => getByText(`${OB}.title`));

    // `by_currency` files unpriced-currency amounts under a sentinel key. It
    // is a marker, not an ISO code, and a member reading "UNSPECIFIED" beside
    // USD would reasonably think it was one.
    expect(queryByText("UNSPECIFIED")).toBeNull();
    // Named twice — as the totals' excluded count and as the bucket's label.
    expect(getAllByText(`${OB}.unspecifiedCurrency`)).toHaveLength(2);
  });

  it("admits the list is capped rather than implying it is every obligation", async () => {
    mockGetObligations.mockResolvedValue(
      readyObligations({
        totals: {
          known_amount: 240000,
          currency: "USD",
          by_currency: { USD: { amount: 240000, count: 1 } },
          currencies: ["USD"],
          count: 200,
          quantified: 200,
          unquantified: 0,
          unspecified_currency: 0,
          complete: false,
          truncated: true,
          limit: 200
        }
      })
    );
    const { getByText } = await renderScreen("obligations");
    await waitFor(() => getByText(`${OB}.title`));

    getByText(`${OB}.truncated`);
    // Every row here is quantified and in one currency, and the total is
    // still not complete — because rows were left out. A screen that inferred
    // completeness from the unquantified count would call this whole.
    getByText(`${OB}.partial`);
  });

  it("warns beside the figure when the projection never ran", async () => {
    mockGetObligations.mockResolvedValue(
      readyObligations({ sync: { projected: false, obligations: 0, retired: 0, skipped: 3 } })
    );
    const { getByText } = await renderScreen("obligations");
    await waitFor(() => getByText(`${OB}.title`));

    // Everything on this tab is read from the projection. If the sweep did
    // not run, the caveat belongs with the number, not in a footer.
    getByText(`${OB}.notProjected`);
  });

  it("counts what the sweep projected, retired and skipped", async () => {
    const { getByText } = await renderScreen("obligations");
    await waitFor(() => getByText(`${OB}.title`));

    getByText(`${OB}.syncTitle`);
    getByText(`${OB}.projectedLabel`);
    getByText(`${OB}.retiredLabel`);
    // Skipped records are absent from every figure above; this counter is the
    // only place they are visible at all.
    getByText(`${OB}.skippedLabel`);
  });

  it("keeps an obligations refusal a refusal, with no totals and no empty claim", async () => {
    mockGetObligations.mockResolvedValue({ state: "DENIED", reason: "not the owner of record" });
    const { getByText, queryByText } = await renderScreen("obligations");
    await waitFor(() => getByText("premium:privateOffice.capital.denied.title"));

    getByText("not the owner of record");
    expect(queryByText(`${OB}.title`)).toBeNull();
    expect(queryByText(`${OB}.none`)).toBeNull();
    expect(queryByText(EMPTY_TITLE)).toBeNull();
    expect(queryByText(RETRY)).toBeNull();
  });

  it("lets the tab in front own the verdict — a healthy overview does not vouch for failed obligations", async () => {
    mockGetObligations.mockResolvedValue({ state: "UNAVAILABLE" });
    const { getByText, queryByText } = await renderScreen("obligations");
    await waitFor(() => getByText("premium:privateOffice.capital.unavailable.title"));
    expect(queryByText(`${OB}.title`)).toBeNull();

    // The overview answers fine. Its success must not clear the obligations
    // outage banner, nor must the outage survive onto the tab that answered.
    fireEvent.press(getByText("premium:privateOffice.capital.views.overview"));
    await waitFor(() => getByText(`${OV}.title`));
    expect(queryByText("premium:privateOffice.capital.unavailable.title")).toBeNull();

    fireEvent.press(getByText("premium:privateOffice.capital.views.obligations"));
    await waitFor(() => getByText("premium:privateOffice.capital.unavailable.title"));
    expect(queryByText(`${OV}.title`)).toBeNull();
  });

  it("stops drawing the obligation totals once the member leaves the tab", async () => {
    const { getByText, queryAllByText, queryByText } = await renderScreen("obligations");
    await waitFor(() => getByText(`${OB}.title`));

    // The parsed answer survives the switch by design, so that returning is
    // instant. Painting it under the Overview heading would not be.
    fireEvent.press(getByText("premium:privateOffice.capital.views.overview"));
    await waitFor(() => getByText(`${OV}.title`));
    expect(queryByText(`${OB}.title`)).toBeNull();
    expect(queryAllByText(money(137500))).toHaveLength(0);
    expect(queryByText("Family loan")).toBeNull();
  });

  it("relocks the office when the obligations read says the grant is dead", async () => {
    mockGetObligations.mockResolvedValue({ state: "LOCKED", setupRequired: false });
    const { getByText } = await renderScreen("obligations");

    await waitFor(() => getByText("premium:privateOffice.lock.unlock"));
    expect(isOfficeUnlocked()).toBe(false);
  });
});

describe("CapitalGraphScreen cash flow tab", () => {
  const CF = "premium:privateOffice.capital.cashFlow";
  const OV = "premium:privateOffice.capital.overview";

  it("asks the cash-flow endpoint, and never the graph, for a view it has no name for", async () => {
    const { getByText } = await renderScreen("cash_flow");
    await waitFor(() => getByText(`${CF}.title`));

    expect(mockGetCashFlow).toHaveBeenCalledTimes(1);
    // `view=cash_flow` is not in the graph route's vocabulary, and the
    // obligations endpoint returns a different payload for the same debts.
    // Reaching for either would be a different question answered under this
    // tab's heading.
    expect(mockGetGraph).not.toHaveBeenCalled();
    expect(mockGetOverview).not.toHaveBeenCalled();
    expect(mockGetObligations).not.toHaveBeenCalled();
  });

  it("prints the outflows-only basis verbatim, including when nothing is missing", async () => {
    // The tempting case. Everything dated, everything priced, one currency,
    // nothing excluded — and it is still a list of what leaves, never netted
    // against anything that arrives. If this caveat is a defect notice that
    // disappears when the data is clean, the clean case is the one that lies.
    mockGetCashFlow.mockResolvedValue(
      readyCashFlow({
        excluded: { undated: 0, unquantified: 0, undated_and_unquantified: 0 },
        totals: {
          currency: "USD",
          scheduled_amount: 335350,
          scheduled_count: 3,
          obligations_seen: 3,
          truncated: false,
          complete: true,
          excluded_count: 0,
          mixed_currency_rows: 0
        }
      })
    );
    const { getByText, queryByText } = await renderScreen("cash_flow");
    await waitFor(() => getByText(`${CF}.title`));

    getByText(`${CF}.complete`);
    getByText(`${CF}.outflowsTitle`);
    // The server's own sentences, not a paraphrase: they are the difference
    // between "what leaves" and "what is left".
    getByText(INFLOWS_BASIS);
    getByText(RECURRENCE_BASIS);
    expect(queryByText(`${CF}.partial`)).toBeNull();
  });

  it("withholds the headline when no single currency answers for one", async () => {
    mockGetCashFlow.mockResolvedValue(
      readyCashFlow({
        buckets: {
          overdue: { amount: null, count: 1 },
          due_30: { amount: null, count: 1 },
          due_90: { amount: null, count: 0 },
          due_180: { amount: null, count: 0 },
          due_365: { amount: null, count: 0 },
          beyond_365: { amount: null, count: 1 }
        },
        totals: {
          currency: "",
          // The server refuses to sum across currencies it has no approved
          // rate for. null, never 0.0.
          scheduled_amount: null,
          scheduled_count: 3,
          obligations_seen: 6,
          truncated: false,
          complete: false,
          excluded_count: 3,
          mixed_currency_rows: 0
        }
      })
    );
    const { getAllByText, getByText, queryByText } = await renderScreen("cash_flow");
    await waitFor(() => getByText(`${CF}.title`));

    getByText(`${CF}.withheld`);
    // Once for the headline, once for each of the six buckets that has rows
    // but no summable figure.
    expect(getAllByText(`${CF}.notSummable`).length).toBeGreaterThan(1);
    // What the server declined to compute, and what a helpful client would
    // have computed for it in a currency nobody named.
    expect(queryByText(money(335350))).toBeNull();
    expect(queryByText(money(0))).toBeNull();
  });

  it("keeps a declared window the server did not report an absence, not a zero", async () => {
    mockGetCashFlow.mockResolvedValue(
      readyCashFlow({
        // `basis.buckets` still names all six. The payload reports five.
        buckets: {
          overdue: { amount: 4200, count: 1 },
          due_30: { amount: 18750, count: 1 },
          due_180: { amount: 0, count: 0 },
          due_365: { amount: 0, count: 0 },
          beyond_365: { amount: 312400, count: 1 }
        }
      })
    );
    const { getAllByText, getByText } = await renderScreen("cash_flow");
    await waitFor(() => getByText(`${CF}.title`));

    // The window is still drawn — dropping the row would make the schedule
    // look like it had five windows all along. It is headed by the server's
    // range like every other window, so what proves it is still there is the
    // count: six windows above the schedule and three schedule rows below,
    // not five and three.
    expect(
      getAllByText(new RegExp(`^${CF}\\.(bucketDueNow|bucketBetween|bucketAfter)$`))
    ).toHaveLength(9);
    // Named twice on that row: once where the amount goes, once where the
    // count goes. Neither may quietly become 0.
    expect(getAllByText(`${CF}.bucketMissing`)).toHaveLength(2);
  });

  it("draws a bucket the payload carries but never declared", async () => {
    mockGetCashFlow.mockResolvedValue(
      readyCashFlow({
        buckets: {
          overdue: { amount: 4200, count: 1 },
          due_30: { amount: 18750, count: 1 },
          due_90: { amount: 0, count: 0 },
          due_180: { amount: 0, count: 0 },
          due_365: { amount: 0, count: 0 },
          beyond_365: { amount: 312400, count: 1 },
          // A window a newer server added to `buckets` without this build
          // knowing its name. Rendering only what `basis.buckets` declares
          // would silently hide the money in it.
          due_730: { amount: 91000, count: 2 }
        }
      })
    );
    const { getByText } = await renderScreen("cash_flow");
    await waitFor(() => getByText(`${CF}.title`));

    getByText("due_730");
    getByText(money(91000));
    // Marked as a window this build cannot describe, rather than given an
    // invented range that would misplace it on the member's timeline.
    getByText(`${CF}.bucketUndeclared`);
  });

  it("describes each declared window by the edge the server actually gave it", async () => {
    const { getAllByText, getByText, queryByText } = await renderScreen("cash_flow");
    await waitFor(() => getByText(`${CF}.title`));

    // Overdue is open at the near end and beyond_365 at the far end; the four
    // between them are closed. Three different sentences, because a window
    // with no lower bound and one with no upper bound are not the same fact,
    // and rendering `from_days: null` as 0 would put "already overdue" and
    // "due within a month" on the same footing.
    //
    // The server closes `overdue` at exactly 0, which is a window rather than
    // a boundary worth printing, so it gets words instead of "up to 0 days".
    // `bucketBefore` is therefore reserved for a server that moves that edge
    // off zero, and must not appear against this payload.
    expect(queryByText(`${CF}.bucketBefore`)).toBeNull();
    // Every window here is declared, so none may be labelled as one this
    // build cannot describe.
    expect(queryByText(`${CF}.bucketUndeclared`)).toBeNull();
    // Every window on the tab, in render order: the six the server declared,
    // in the server's order rather than whatever `Object.keys` yielded, then
    // the three schedule rows. A row is labelled with the *window* it was
    // counted in and not the server's internal key, and it is the same
    // sentence in both places, so a member can match a row to its window.
    // Nothing here is a name this build hardcoded: every one of these is
    // derived from `from_days`/`to_days` on the wire.
    expect(
      getAllByText(
        new RegExp(`^${CF}\\.(bucketDueNow|bucketBetween|bucketAfter)$`)
      ).map((node) => node.props.children)
    ).toEqual([
      `${CF}.bucketDueNow`,
      `${CF}.bucketBetween`,
      `${CF}.bucketBetween`,
      `${CF}.bucketBetween`,
      `${CF}.bucketBetween`,
      `${CF}.bucketAfter`,
      `${CF}.bucketDueNow`,
      `${CF}.bucketBetween`,
      `${CF}.bucketAfter`
    ]);
    // And the server's internal keys are not shown as though they were
    // windows: they appear only where there is no window to show.
    expect(queryByText("due_90")).toBeNull();
    expect(queryByText("beyond_365")).toBeNull();
  });

  it("counts what could not be placed instead of dropping it from the tab", async () => {
    const { getByText } = await renderScreen("cash_flow");
    await waitFor(() => getByText(`${CF}.title`));

    getByText(`${CF}.excludedTitle`);
    getByText(`${CF}.excludedBody`);
    // Three separate absences, each named for what is missing. An obligation
    // with an amount and no date is not the same gap as one with neither, and
    // collapsing them would tell the member less than the server knows.
    getByText(`${CF}.undated`);
    getByText(`${CF}.unquantified`);
    getByText(`${CF}.undatedAndUnquantified`);
    getByText(`${CF}.excludedLabel`);
    // Six obligations were seen; three reached a window. Saying so is what
    // stops the headline speaking for all six.
    getByText(`${CF}.scheduledLabel`);
    getByText(`${CF}.seenLabel`);
    getByText(`${CF}.partial`);
  });

  it("names only the exclusions that happened", async () => {
    mockGetCashFlow.mockResolvedValue(
      readyCashFlow({
        excluded: { undated: 2, unquantified: 0, undated_and_unquantified: 0 },
        totals: {
          currency: "USD",
          scheduled_amount: 335350,
          scheduled_count: 3,
          obligations_seen: 5,
          truncated: false,
          complete: false,
          excluded_count: 2,
          mixed_currency_rows: 0
        }
      })
    );
    const { getByText, queryByText } = await renderScreen("cash_flow");
    await waitFor(() => getByText(`${CF}.title`));

    getByText(`${CF}.undated`);
    // A zero counter listed beside a real one reads as a category that was
    // checked and found empty; here it is a category nothing fell into.
    expect(queryByText(`${CF}.unquantified`)).toBeNull();
    expect(queryByText(`${CF}.undatedAndUnquantified`)).toBeNull();
  });

  it("says an empty schedule is about dates it holds, not about debts", async () => {
    mockGetCashFlow.mockResolvedValue(
      readyCashFlow({
        schedule: [],
        buckets: {
          overdue: { amount: 0, count: 0 },
          due_30: { amount: 0, count: 0 },
          due_90: { amount: 0, count: 0 },
          due_180: { amount: 0, count: 0 },
          due_365: { amount: 0, count: 0 },
          beyond_365: { amount: 0, count: 0 }
        },
        totals: {
          currency: "USD",
          scheduled_amount: 0,
          scheduled_count: 0,
          obligations_seen: 4,
          truncated: false,
          complete: false,
          // Four obligations exist. None of them could be dated.
          excluded_count: 4,
          mixed_currency_rows: 0
        },
        excluded: { undated: 4, unquantified: 0, undated_and_unquantified: 0 }
      })
    );
    const { getByText, queryByText } = await renderScreen("cash_flow");
    await waitFor(() => getByText(`${CF}.title`));

    getByText(`${CF}.noneScheduled`);
    getByText(`${CF}.undated`);
    // Not the generic empty state, which says "nothing recorded" and stops —
    // four obligations were recorded and every one of them is undated.
    expect(queryByText(EMPTY_TITLE)).toBeNull();
  });

  it("takes overdue from the server's read instant rather than the device clock", async () => {
    mockGetCashFlow.mockResolvedValue(
      readyCashFlow({
        schedule: [
          {
            node_id: 84,
            root_id: 24,
            title: "Settled early",
            kind: "LIABILITY",
            amount: 900,
            currency: "USD",
            // A date in the past by any clock a test could run under, and the
            // server did not call it overdue. A screen recomputing this from
            // `Date.now()` would contradict the bucket in the same payload.
            due_at: "2020-01-01T00:00:00+00:00",
            days_until: 4.0,
            overdue: false,
            bucket: "due_30",
            evidence: { fact_ids: [], provenance: null }
          }
        ]
      })
    );
    const { getByText, queryByText } = await renderScreen("cash_flow");
    await waitFor(() => getByText(`${CF}.title`));

    getByText("Settled early");
    expect(queryByText(`${CF}.overdue`)).toBeNull();
  });

  it("keeps a scheduled row with no amount an absence rather than a zero", async () => {
    // A contract-break probe, not a payload production emits today: the
    // backend routes an unquantified obligation to `excluded.unquantified` and
    // never into `schedule`. If that ever loosens, the row must arrive as an
    // absence — a dated obligation summed as 0 would make the window it lands
    // in read as settled.
    mockGetCashFlow.mockResolvedValue(
      readyCashFlow({
        schedule: [
          {
            node_id: 85,
            root_id: 25,
            title: "Service charge",
            kind: "LIABILITY",
            amount: null,
            currency: "USD",
            due_at: "2026-11-01T00:00:00+00:00",
            days_until: 56.0,
            overdue: false,
            bucket: "due_90",
            evidence: { fact_ids: [], provenance: null }
          }
        ]
      })
    );
    const { getAllByText, getByText } = await renderScreen("cash_flow");
    await waitFor(() => getByText(`${CF}.title`));

    getByText("Service charge");
    getByText(`${CF}.amountMissing`);
    getByText(`${CF}.evidenceMissing`);
    // Exactly three zeros on the tab, and all three are the server's own: the
    // declared windows it summed and found empty. A fourth would be this row
    // having been given a figure nobody recorded.
    expect(getAllByText(money(0))).toHaveLength(3);
  });

  it("tells an amount with no currency apart from no amount at all", async () => {
    mockGetCashFlow.mockResolvedValue(
      readyCashFlow({
        schedule: [
          {
            node_id: 86,
            root_id: 26,
            title: "Tax bill",
            kind: "LIABILITY",
            // A figure the member entered, against a currency they did not.
            amount: 5000,
            currency: "",
            due_at: "2027-01-31T00:00:00+00:00",
            days_until: 147.0,
            overdue: false,
            bucket: "due_180",
            evidence: { fact_ids: [7], provenance: null }
          }
        ]
      })
    );
    const { getByText, queryByText } = await renderScreen("cash_flow");
    await waitFor(() => getByText(`${CF}.title`));

    getByText("Tax bill");
    getByText(`${CF}.unspecifiedCurrency`);
    // Printing 5000 in the screen's default would name a currency the member
    // never gave; printing nothing would lose a figure they did.
    expect(queryByText(`${CF}.amountMissing`)).toBeNull();
    expect(queryByText(money(5000))).toBeNull();
  });

  it("shouts when the server's always-zero currency invariant breaks", async () => {
    mockGetCashFlow.mockResolvedValue(
      readyCashFlow({
        totals: {
          currency: "USD",
          scheduled_amount: 335350,
          scheduled_count: 3,
          obligations_seen: 6,
          truncated: false,
          complete: false,
          excluded_count: 3,
          // Must always be 0. Non-zero means a row in a currency this total
          // does not name reached a bucket, so the headline above is FX by
          // accident and the member has no way to see it otherwise.
          mixed_currency_rows: 2
        }
      })
    );
    const { getByText } = await renderScreen("cash_flow");
    await waitFor(() => getByText(`${CF}.title`));

    getByText(`${CF}.mixedCurrency`);
  });

  it("says nothing about mixed currencies while the invariant holds", async () => {
    const { getByText, queryByText } = await renderScreen("cash_flow");
    await waitFor(() => getByText(`${CF}.title`));

    // The paired half of the test above: a warning that is always on is not a
    // warning, and would train the member to ignore the one case that matters.
    expect(queryByText(`${CF}.mixedCurrency`)).toBeNull();
  });

  it("admits the schedule is capped rather than implying it is every obligation", async () => {
    mockGetCashFlow.mockResolvedValue(
      readyCashFlow({
        excluded: { undated: 0, unquantified: 0, undated_and_unquantified: 0 },
        totals: {
          currency: "USD",
          scheduled_amount: 335350,
          scheduled_count: 200,
          obligations_seen: 200,
          truncated: true,
          complete: false,
          excluded_count: 0,
          mixed_currency_rows: 0
        }
      })
    );
    const { getByText } = await renderScreen("cash_flow");
    await waitFor(() => getByText(`${CF}.title`));

    getByText(`${CF}.truncated`);
    // Nothing was excluded and every row is in one currency, and the schedule
    // is still not complete — because rows were cut off. A screen inferring
    // completeness from the excluded count alone would call this whole.
    getByText(`${CF}.partial`);
  });

  it("warns beside the figure when the projection never ran", async () => {
    mockGetCashFlow.mockResolvedValue(
      readyCashFlow({ sync: { projected: false, obligations: 0, retired: 0, skipped: 4 } })
    );
    const { getByText } = await renderScreen("cash_flow");
    await waitFor(() => getByText(`${CF}.title`));

    // Every figure on this tab is read from the projection. If the sweep did
    // not run, the caveat belongs with the number, not in a footer.
    getByText(`${CF}.notProjected`);
    getByText(`${CF}.syncTitle`);
    getByText(`${CF}.skippedLabel`);
  });

  it("keeps a cash-flow refusal a refusal, with no schedule and no empty claim", async () => {
    mockGetCashFlow.mockResolvedValue({ state: "DENIED", reason: "not the owner of record" });
    const { getByText, queryByText } = await renderScreen("cash_flow");
    await waitFor(() => getByText("premium:privateOffice.capital.denied.title"));

    getByText("not the owner of record");
    expect(queryByText(`${CF}.title`)).toBeNull();
    expect(queryByText(`${CF}.noneScheduled`)).toBeNull();
    expect(queryByText(EMPTY_TITLE)).toBeNull();
    expect(queryByText(RETRY)).toBeNull();
  });

  it("lets the tab in front own the verdict — a healthy overview does not vouch for a failed schedule", async () => {
    mockGetCashFlow.mockResolvedValue({ state: "UNAVAILABLE" });
    const { getByText, queryByText } = await renderScreen("cash_flow");
    await waitFor(() => getByText("premium:privateOffice.capital.unavailable.title"));
    expect(queryByText(`${CF}.title`)).toBeNull();

    fireEvent.press(getByText("premium:privateOffice.capital.views.overview"));
    await waitFor(() => getByText(`${OV}.title`));
    expect(queryByText("premium:privateOffice.capital.unavailable.title")).toBeNull();

    fireEvent.press(getByText("premium:privateOffice.capital.views.cash_flow"));
    await waitFor(() => getByText("premium:privateOffice.capital.unavailable.title"));
    expect(queryByText(`${OV}.title`)).toBeNull();
  });

  it("stops drawing the schedule once the member leaves the tab", async () => {
    const { getByText, queryByText } = await renderScreen("cash_flow");
    await waitFor(() => getByText(`${CF}.title`));
    getByText(money(335350));

    // The parsed answer survives the switch by design, so that returning is
    // instant. Painting it under the Obligations heading would not be: the two
    // tabs count the same debts to different totals.
    fireEvent.press(getByText("premium:privateOffice.capital.views.obligations"));
    await waitFor(() => getByText("premium:privateOffice.capital.obligations.title"));
    expect(queryByText(`${CF}.title`)).toBeNull();
    expect(queryByText(money(335350))).toBeNull();
    expect(queryByText("Council tax")).toBeNull();
    expect(queryByText(INFLOWS_BASIS)).toBeNull();
  });

  it("relocks the office when the cash-flow read says the grant is dead", async () => {
    mockGetCashFlow.mockResolvedValue({ state: "LOCKED", setupRequired: false });
    const { getByText } = await renderScreen("cash_flow");

    await waitFor(() => getByText("premium:privateOffice.lock.unlock"));
    expect(isOfficeUnlocked()).toBe(false);
  });
});

/* --- Stage-three: the exposure tab, and the denominator under every share --- */

/**
 * A concentration percentage is only as honest as what it is a percentage of.
 *
 * The failure this block exists to make impossible is a ranking that reads as
 * a ranking of everything the member owns. The server ranks only holdings that
 * have a price; on the default fixture that is four of eleven. "61% in Solana"
 * is therefore 61% of a quarter of the positions on file, and the seven
 * unpriced ones are not small — they are *invisible*, and any of them could
 * outweigh the row at the top.
 *
 * So the contract pinned here is: the priced total, its coverage and the
 * holdings excluded from it are drawn above the ranking; the caption naming the
 * basis is unconditional; the server's shares are printed and never recomputed;
 * the two counts of the priced set are cross-checked out loud; and the
 * dimensions this surface has no data for at all are named in every state.
 */
const EX = "premium:privateOffice.capital.exposure";

describe("CapitalGraphScreen exposure tab", () => {
  it("asks the exposure endpoint, and never the overview or the graph", async () => {
    const { getByText } = await renderScreen("exposure");
    await waitFor(() => getByText(`${EX}.basisTitle`));

    expect(mockGetExposure).toHaveBeenCalledTimes(1);
    // The server derives exposure from the same read it answers /overview
    // with. Deriving it *here* from overview state we already hold would let a
    // healthy overview vouch for an exposure read that never happened.
    expect(mockGetOverview).not.toHaveBeenCalled();
    expect(mockGetGraph).not.toHaveBeenCalled();
    expect(mockGetPortfolio).not.toHaveBeenCalled();
  });

  it("leads with the denominator and names what the server called it", async () => {
    const { getByText } = await renderScreen("exposure");
    await waitFor(() => getByText(`${EX}.basisTitle`));

    getByText(money(655300));
    // The server's token for the basis, rendered rather than paraphrased, so a
    // basis this client has no translation for is still shown to the member.
    getByText("priced_asset_value");
    getByText(`${EX}.pricedOf`);
  });

  it("says the shares are of the priced total even when nothing is missing", async () => {
    // The healthiest payload there is: every holding priced, portfolio
    // complete. The caption is not a warning — it is what the number means —
    // so it must survive the case where there is nothing to warn about.
    mockGetExposure.mockResolvedValue(
      readyExposure({
        assets: {
          priced_value: 655300,
          currency: "USD",
          count: 4,
          priced: 4,
          unpriced: 0,
          unpriced_symbols: [],
          basis_known: 4,
          known_cost: 401000,
          complete: true
        }
      })
    );
    const { getByText, queryByText } = await renderScreen("exposure");
    await waitFor(() => getByText(`${EX}.assetsTitle`));

    getByText(`${EX}.sharesOfPriced`);
    getByText(`${EX}.complete`);
    // Nothing was excluded, so the blind-spot card has nothing to say.
    expect(queryByText(`${EX}.blindSpotTitle`)).toBeNull();
  });

  it("names the holdings the ranking cannot see, on the same card as the ranking", async () => {
    const { getByText } = await renderScreen("exposure");
    await waitFor(() => getByText(`${EX}.basisTitle`));

    getByText(`${EX}.blindSpotTitle`);
    getByText(`${EX}.blindSpotBody`);
    // An unpriced holding is absent from the ranking, not last in it, so
    // "which ones" is the question this card exists to answer.
    getByText("XMR");
    getByText("ZEC");
    getByText("FIL");
  });

  it("accounts for the unpriced holdings it could not name", async () => {
    const { getByText } = await renderScreen("exposure");
    await waitFor(() => getByText(`${EX}.blindSpotTitle`));
    // Seven unpriced, three named: the other four are stated rather than
    // letting the list imply it is complete.
    getByText(`${EX}.unpricedUnnamed`);

    mockGetExposure.mockResolvedValue(
      readyExposure({
        assets: {
          priced_value: 655300,
          currency: "USD",
          count: 7,
          priced: 4,
          unpriced: 3,
          unpriced_symbols: ["XMR", "ZEC", "FIL"],
          basis_known: 2,
          known_cost: 401000,
          complete: false
        }
      })
    );
    const named = await renderScreen("exposure");
    await waitFor(() => named.getByText(`${EX}.blindSpotTitle`));
    expect(named.queryByText(`${EX}.unpricedUnnamed`)).toBeNull();
  });

  it("withholds the basis figure rather than printing a currency the server did not name", async () => {
    mockGetExposure.mockResolvedValue(
      readyExposure({
        concentrations: {
          assets: [{ key: "SOL", label: "Solana", value: 401200, share: null }],
          asset_basis: "priced_asset_value",
          asset_total: 655300,
          assets_ranked: 1,
          assets_unranked_tail: 3,
          liabilities: [],
          liability_basis: "quantified_liability_amount",
          liability_total: 96400,
          // The server withholds the code when it refused to reduce a mixed
          // set to one figure. Defaulting to USD here would relabel a
          // withheld total as dollars.
          currency: ""
        }
      })
    );
    const { getByText, getAllByText, queryByText } = await renderScreen("exposure");
    await waitFor(() => getByText(`${EX}.noBasis`));

    expect(getAllByText(`${EX}.withheld`).length).toBeGreaterThan(0);
    expect(queryByText(money(655300))).toBeNull();
    // And no row may print its value either.
    expect(queryByText(money(401200))).toBeNull();
    // A share with no total to divide by is not computed on this side.
    getByText(`${EX}.shareUnknown`);
  });

  it("prints the server's pricing ratio, and says so when the server withheld it", async () => {
    const withRatio = await renderScreen("exposure");
    await waitFor(() => withRatio.getByText(`${EX}.pricingCoverage`));
    withRatio.getByText(percent(4 / 11));
    expect(withRatio.queryByText(`${EX}.pricingUnknown`)).toBeNull();

    mockGetExposure.mockResolvedValue(
      readyExposure({
        coverage: {
          // known and countable still arrive; the ratio does not. Dividing
          // them here would publish a number the server declined to.
          dimensions: { pricing: { known: 4, countable: 11, ratio: null } },
          score: null,
          scored_dimensions: [],
          formula: "mean(scored_dimensions)"
        }
      })
    );
    const withheld = await renderScreen("exposure");
    await waitFor(() => withheld.getByText(`${EX}.pricingUnknown`));
    expect(withheld.queryByText(percent(4 / 11))).toBeNull();
  });

  it("treats an absent pricing dimension as unknown, never as full coverage", async () => {
    mockGetExposure.mockResolvedValue(
      readyExposure({
        coverage: {
          dimensions: { evidence: { known: 0, countable: 0, ratio: null } },
          score: null,
          scored_dimensions: [],
          formula: "mean(scored_dimensions)"
        }
      })
    );
    const { getByText, queryByText } = await renderScreen("exposure");
    await waitFor(() => getByText(`${EX}.pricingUnknown`));
    expect(queryByText(percent(1))).toBeNull();
    expect(queryByText(percent(0))).toBeNull();
  });

  it("draws the ranking in the server's order, with the server's own shares", async () => {
    const { getByText, getAllByText } = await renderScreen("exposure");
    await waitFor(() => getByText(`${EX}.assetsTitle`));

    const labels = getAllByText(/^(Solana|Cardano|Polkadot|Chainlink)$/).map(
      (node) => node.props.children
    );
    expect(labels).toEqual(["Solana", "Cardano", "Polkadot", "Chainlink"]);

    getByText(money(401200));
    getByText(money(150100));
    getByText(money(78000));
    getByText(money(26000));
    getByText(percent(401200 / 655300));
    getByText(percent(26000 / 655300));
  });

  it("leaves a share the server withheld unstated, without touching the others", async () => {
    mockGetExposure.mockResolvedValue(
      readyExposure({
        concentrations: {
          assets: [
            { key: "SOL", label: "Solana", value: 401200, share: 401200 / 655300 },
            // The server had a value but no share for this one.
            { key: "ADA", label: "Cardano", value: 150100, share: null }
          ],
          asset_basis: "priced_asset_value",
          asset_total: 655300,
          assets_ranked: 2,
          assets_unranked_tail: 2,
          liabilities: [],
          liability_basis: "quantified_liability_amount",
          liability_total: 96400,
          currency: "USD"
        }
      })
    );
    const { getByText, queryByText } = await renderScreen("exposure");
    await waitFor(() => getByText(`${EX}.assetsTitle`));

    getByText(percent(401200 / 655300));
    getByText(`${EX}.shareUnknown`);
    // The value is still the server's; only the share was missing.
    getByText(money(150100));
    expect(queryByText(percent(150100 / 655300))).toBeNull();
  });

  it("says how many priced holdings it did not rank, and stays quiet when it ranked them all", async () => {
    const all = await renderScreen("exposure");
    await waitFor(() => all.getByText(`${EX}.assetsTitle`));
    expect(all.queryByText(`${EX}.unrankedTail`)).toBeNull();

    mockGetExposure.mockResolvedValue(
      readyExposure({
        assets: {
          priced_value: 655300,
          currency: "USD",
          count: 17,
          priced: 10,
          unpriced: 7,
          unpriced_symbols: ["XMR", "ZEC", "FIL"],
          basis_known: 2,
          known_cost: 401000,
          complete: false
        },
        concentrations: {
          assets: [{ key: "SOL", label: "Solana", value: 401200, share: 401200 / 655300 }],
          asset_basis: "priced_asset_value",
          asset_total: 655300,
          assets_ranked: 1,
          assets_unranked_tail: 9,
          liabilities: [],
          liability_basis: "quantified_liability_amount",
          liability_total: 96400,
          currency: "USD"
        }
      })
    );
    const capped = await renderScreen("exposure");
    await waitFor(() => capped.getByText(`${EX}.unrankedTail`));
    // 1 ranked + 9 tail = 10 priced: the invariant still holds, so no shout.
    expect(capped.queryByText(`${EX}.basisMismatch`)).toBeNull();
  });

  it("shouts when the ranked and unranked counts do not add up to the priced count", async () => {
    const healthy = await renderScreen("exposure");
    await waitFor(() => healthy.getByText(`${EX}.assetsTitle`));
    expect(healthy.queryByText(`${EX}.basisMismatch`)).toBeNull();

    mockGetExposure.mockResolvedValue(
      readyExposure({
        // 4 ranked + 0 tail, but the assets block says 9 have a price. One of
        // the two is wrong, so the ranking is not over the printed total.
        assets: {
          priced_value: 655300,
          currency: "USD",
          count: 11,
          priced: 9,
          unpriced: 2,
          unpriced_symbols: [],
          basis_known: 2,
          known_cost: 401000,
          complete: false
        }
      })
    );
    const broken = await renderScreen("exposure");
    await waitFor(() => broken.getByText(`${EX}.basisMismatch`));
  });

  it("shouts when the server's ranked count does not match the rows it sent", async () => {
    const healthy = await renderScreen("exposure");
    await waitFor(() => healthy.getByText(`${EX}.assetsTitle`));
    expect(healthy.queryByText(`${EX}.rankedMismatch`)).toBeNull();

    mockGetExposure.mockResolvedValue(
      readyExposure({
        concentrations: {
          assets: [{ key: "SOL", label: "Solana", value: 401200, share: 401200 / 655300 }],
          asset_basis: "priced_asset_value",
          asset_total: 655300,
          // Claims four, sent one.
          assets_ranked: 4,
          assets_unranked_tail: 0,
          liabilities: [],
          liability_basis: "quantified_liability_amount",
          liability_total: 96400,
          currency: "USD"
        }
      })
    );
    const broken = await renderScreen("exposure");
    await waitFor(() => broken.getByText(`${EX}.rankedMismatch`));
  });

  it("keeps three different silences apart when there is no ranking to draw", async () => {
    const nothing = {
      assets: [],
      asset_basis: "priced_asset_value",
      asset_total: 0,
      assets_ranked: 0,
      assets_unranked_tail: 0,
      liabilities: [],
      liability_basis: "quantified_liability_amount",
      liability_total: 0,
      currency: "USD"
    };
    const block = (count: number, priced: number) => ({
      priced_value: 0,
      currency: "USD",
      count,
      priced,
      unpriced: count - priced,
      unpriced_symbols: [],
      basis_known: 0,
      known_cost: null,
      complete: false
    });

    // Nothing on file at all. Not "you own nothing".
    mockGetExposure.mockResolvedValue(
      readyExposure({ concentrations: nothing, assets: block(0, 0) })
    );
    const none = await renderScreen("exposure");
    await waitFor(() => none.getByText(`${EX}.noHoldings`));
    expect(none.queryByText(`${EX}.nonePriced`)).toBeNull();
    expect(none.queryByText(`${EX}.pricedButUnrankable`)).toBeNull();

    // Holdings exist; no price could be found for any of them.
    mockGetExposure.mockResolvedValue(
      readyExposure({ concentrations: nothing, assets: block(3, 0) })
    );
    const unpriced = await renderScreen("exposure");
    await waitFor(() => unpriced.getByText(`${EX}.nonePriced`));
    expect(unpriced.queryByText(`${EX}.noHoldings`)).toBeNull();
    expect(unpriced.queryByText(`${EX}.pricedButUnrankable`)).toBeNull();

    // Priced, but the prices do not add up to anything to rank against.
    mockGetExposure.mockResolvedValue(
      readyExposure({ concentrations: nothing, assets: block(3, 3) })
    );
    const unrankable = await renderScreen("exposure");
    await waitFor(() => unrankable.getByText(`${EX}.pricedButUnrankable`));
    expect(unrankable.queryByText(`${EX}.noHoldings`)).toBeNull();
    expect(unrankable.queryByText(`${EX}.nonePriced`)).toBeNull();
  });

  it("says the debt rows are kinds rather than creditors", async () => {
    const { getByText } = await renderScreen("exposure");
    await waitFor(() => getByText(`${EX}.liabilitiesTitle`));

    getByText(`${EX}.liabilitiesGrouped`);
    getByText("quantified_liability_amount");
    getByText("Mortgage");
    getByText("Card");
    getByText(money(96400));
    getByText(percent(71900 / 96400));
  });

  it("names only the exclusions that happened", async () => {
    const { getByText, queryByText } = await renderScreen("exposure");
    await waitFor(() => getByText(`${EX}.excludedTitle`));
    getByText(`${EX}.excludedUnquantified`);
    getByText(`${EX}.excludedForeignCurrency`);
    getByText(`${EX}.excludedUnspecifiedCurrency`);

    mockGetExposure.mockResolvedValue(
      readyExposure({
        liabilities: {
          known_amount: 96400,
          currency: "USD",
          count: 4,
          quantified: 4,
          unquantified: 0,
          foreign_currency: 0,
          unspecified_currency: 0,
          by_currency: { USD: { amount: 96400, count: 4 } },
          complete: true,
          truncated: false
        }
      })
    );
    const clean = await renderScreen("exposure");
    await waitFor(() => clean.getByText(`${EX}.liabilitiesTitle`));
    // A row reading "no amount recorded: none" is noise; its absence says the
    // same thing quieter. But the *title* going too is the real assertion.
    expect(clean.queryByText(`${EX}.excludedTitle`)).toBeNull();
    expect(clean.queryByText(`${EX}.excludedUnquantified`)).toBeNull();
    expect(clean.queryByText(`${EX}.otherCurrencies`)).toBeNull();
  });

  it("gives the magnitude of what was set aside, not just the count", async () => {
    const { getByText } = await renderScreen("exposure");
    await waitFor(() => getByText(`${EX}.otherCurrencies`));

    // Naming "1 excluded, another currency" while withholding how much is a
    // count masquerading as a disclosure.
    getByText("CHF");
    getByText(moneyIn(41250, "CHF"));
    // The base currency is already the headline; repeating it here would
    // double-count it as something set aside.
    expect(getByText(money(96400))).toBeTruthy();
  });

  it("falls back to a count when the server could not name the currency", async () => {
    const { getByText, queryByText } = await renderScreen("exposure");
    await waitFor(() => getByText(`${EX}.otherCurrencies`));

    getByText("UNSPECIFIED");
    // The amount is real but its currency is not. Printing 8,300 in dollars
    // would invent the single fact that is missing, so the row says how many
    // instead of how much.
    expect(queryByText(money(8300))).toBeNull();
    expect(queryByText("8300")).toBeNull();
    expect(queryByText("8,300")).toBeNull();
  });

  it("keeps an empty liability ranking apart from having no obligations at all", async () => {
    const noRows = {
      assets: [{ key: "SOL", label: "Solana", value: 401200, share: 401200 / 655300 }],
      asset_basis: "priced_asset_value",
      asset_total: 655300,
      assets_ranked: 1,
      assets_unranked_tail: 3,
      liabilities: [],
      liability_basis: "quantified_liability_amount",
      liability_total: 0,
      currency: "USD"
    };

    mockGetExposure.mockResolvedValue(
      readyExposure({
        concentrations: noRows,
        liabilities: {
          known_amount: 0,
          currency: "USD",
          count: 0,
          quantified: 0,
          unquantified: 0,
          foreign_currency: 0,
          unspecified_currency: 0,
          by_currency: {},
          complete: true,
          truncated: false
        }
      })
    );
    const none = await renderScreen("exposure");
    await waitFor(() => none.getByText(`${EX}.noLiabilities`));
    expect(none.queryByText(`${EX}.noneQuantified`)).toBeNull();

    mockGetExposure.mockResolvedValue(
      readyExposure({
        concentrations: noRows,
        liabilities: {
          known_amount: 0,
          currency: "USD",
          // Three obligations are on file; none of them has an amount, so
          // none can be ranked. That is not "no debt".
          count: 3,
          quantified: 0,
          unquantified: 3,
          foreign_currency: 0,
          unspecified_currency: 0,
          by_currency: {},
          complete: false,
          truncated: false
        }
      })
    );
    const unquantified = await renderScreen("exposure");
    await waitFor(() => unquantified.getByText(`${EX}.noneQuantified`));
    expect(unquantified.queryByText(`${EX}.noLiabilities`)).toBeNull();
  });

  it("admits the obligation list was capped", async () => {
    const uncapped = await renderScreen("exposure");
    await waitFor(() => uncapped.getByText(`${EX}.liabilitiesTitle`));
    expect(uncapped.queryByText(`${EX}.liabilitiesTruncated`)).toBeNull();

    mockGetExposure.mockResolvedValue(
      readyExposure({
        liabilities: {
          known_amount: 96400,
          currency: "USD",
          count: 6,
          quantified: 4,
          unquantified: 2,
          foreign_currency: 0,
          unspecified_currency: 0,
          by_currency: { USD: { amount: 96400, count: 4 } },
          complete: false,
          truncated: true
        }
      })
    );
    const capped = await renderScreen("exposure");
    await waitFor(() => capped.getByText(`${EX}.liabilitiesTruncated`));
  });

  it("names what it cannot measure at all, on the healthiest payload there is", async () => {
    mockGetExposure.mockResolvedValue(
      readyExposure({
        assets: {
          priced_value: 655300,
          currency: "USD",
          count: 4,
          priced: 4,
          unpriced: 0,
          unpriced_symbols: [],
          basis_known: 4,
          known_cost: 401000,
          complete: true
        },
        liabilities: {
          known_amount: 96400,
          currency: "USD",
          count: 4,
          quantified: 4,
          unquantified: 0,
          foreign_currency: 0,
          unspecified_currency: 0,
          by_currency: { USD: { amount: 96400, count: 4 } },
          complete: true,
          truncated: false
        }
      })
    );
    const { getByText } = await renderScreen("exposure");
    await waitFor(() => getByText(`${EX}.assetsTitle`));

    // Nothing on file records these, so silence here would read as an
    // all-clear on exactly the risks the word "exposure" promises.
    getByText(`${EX}.unmeasuredTitle`);
    getByText(`${EX}.unmeasuredBody`);
    getByText(`${EX}.unmeasured.counterparty`);
    getByText(`${EX}.unmeasured.custodian`);
    getByText(`${EX}.unmeasured.geography`);
    getByText(`${EX}.unmeasured.sector`);
    getByText(`${EX}.unmeasured.assetCurrency`);
  });

  it("dates the ranking from its own read, not from the overview's", async () => {
    const { getByText, queryByText } = await renderScreen("exposure");
    await waitFor(() => getByText(`${EX}.readAt`));

    getByText("2026-09-06T12:34:56+00:00");
    // The overview fixture's instant. Exposure is a separate request and must
    // never borrow another tab's timestamp.
    expect(queryByText("2026-09-06T12:00:00+00:00")).toBeNull();
  });

  it("flags a price outage beside the ranking, and prints the server's warning verbatim", async () => {
    const live = await renderScreen("exposure");
    await waitFor(() => live.getByText(`${EX}.basisTitle`));
    expect(live.queryByText(`${EX}.pricesUnavailable`)).toBeNull();

    mockGetExposure.mockResolvedValue(
      readyExposure({
        prices: {
          source: "unavailable",
          observed_epoch: null,
          age_seconds: null,
          warning: "The market board did not answer; values are last-known, not current."
        }
      })
    );
    const stale = await renderScreen("exposure");
    await waitFor(() => stale.getByText(`${EX}.pricesUnavailable`));
    // Policy prose written for a person. A paraphrase here would drift.
    stale.getByText("The market board did not answer; values are last-known, not current.");
  });

  it("stops drawing the ranking once the member leaves the tab", async () => {
    const { getByText, queryByText } = await renderScreen("exposure");
    await waitFor(() => getByText(`${EX}.assetsTitle`));
    getByText(money(655300));

    // The parsed answer survives the switch so returning is instant. Painting
    // it under the Obligations heading would not be.
    fireEvent.press(getByText("premium:privateOffice.capital.views.obligations"));
    await waitFor(() => getByText("premium:privateOffice.capital.obligations.title"));
    expect(queryByText(`${EX}.assetsTitle`)).toBeNull();
    expect(queryByText(money(655300))).toBeNull();
    expect(queryByText("Solana")).toBeNull();
    expect(queryByText(`${EX}.unmeasuredTitle`)).toBeNull();
  });

  it("keeps a failed exposure read a failure, never a ranking of nothing", async () => {
    mockGetExposure.mockResolvedValue({ state: "UNAVAILABLE" });
    const outage = await renderScreen("exposure");
    await waitFor(() => outage.getByText("premium:privateOffice.capital.unavailable.body"));
    expect(outage.queryByText(`${EX}.assetsTitle`)).toBeNull();
    expect(outage.queryByText(`${EX}.noHoldings`)).toBeNull();
    expect(outage.queryByText(EMPTY_TITLE)).toBeNull();
    expect(outage.getByText(RETRY)).toBeTruthy();

    mockGetExposure.mockResolvedValue({ state: "DENIED", reason: "actor_is_not_owner" });
    const denied = await renderScreen("exposure");
    await waitFor(() => denied.getByText("premium:privateOffice.capital.denied.body"));
    denied.getByText("actor_is_not_owner");
    expect(denied.queryByText(`${EX}.assetsTitle`)).toBeNull();
    expect(denied.queryByText(RETRY)).toBeNull();
  });

  it("relocks the office when the exposure read says the grant is dead", async () => {
    mockGetExposure.mockResolvedValue({ state: "LOCKED", setupRequired: false });
    const { getByText } = await renderScreen("exposure");

    await waitFor(() => getByText("premium:privateOffice.lock.unlock"));
    expect(isOfficeUnlocked()).toBe(false);
  });
});
