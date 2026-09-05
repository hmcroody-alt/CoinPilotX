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
const mockOfficeStatus = jest.fn();
const mockUnlockOffice = jest.fn();

// Only the network reads are replaced; `parseCapitalGraph` and
// `parseCapitalPortfolio` stay real, so the fixtures below are server payloads,
// not hand-built client objects that could drift from the parser.
jest.mock("../../api/capitalGraph", () => ({
  ...jest.requireActual("../../api/capitalGraph"),
  getCapitalGraph: (...args: unknown[]) => mockGetGraph(...args),
  getCapitalPortfolio: (...args: unknown[]) => mockGetPortfolio(...args)
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

import { parseCapitalGraph, parseCapitalPortfolio } from "../../api/capitalGraph";
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

beforeEach(() => {
  jest.clearAllMocks();
  __resetOfficeLockForTests();
  mockGetGraph.mockResolvedValue(emptyGraph());
  mockGetPortfolio.mockResolvedValue(readyPortfolio([btcAsset()]));
  mockOfficeStatus.mockResolvedValue({
    state: "READY",
    passcodeSet: true,
    setupRequired: false,
    cooldownSeconds: 0,
    biometricPreference: "unset",
    unlocked: false
  });
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
