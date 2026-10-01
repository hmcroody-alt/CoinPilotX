/**
 * Capital Graph carries the server's answers — and refusals — untranslated.
 *
 * Same discipline as the Private Records suite: a tagged result per call,
 * never a thrown Error that collapses "we could not look" into "there is
 * nothing here". Each case pins one translation the server decided in
 * `services/private_office_routes.py`:
 *
 *   - a 423 is LOCKED whatever else the body claims, carrying setup_required;
 *   - a 403 whose state word is DENIED is a refused question with a reason,
 *     not an empty graph;
 *   - a 404 with the NOT_FOUND state word keeps "not yours" and "never
 *     existed" identical, on purpose;
 *   - there is no aggregate anywhere. `counted` and `complete` are read from
 *     the wire, never derived — "3 properties" may only be said while
 *     `complete` is true, and this client must not compute a total the server
 *     refused to.
 */

const mockPulseApi = jest.fn();

// The real `PulseApiError` is kept deliberately: `refusal` narrows with an
// `instanceof` test, and a stubbed class would decide these cases for reasons
// unrelated to the code under test.
jest.mock("../pulseApi", () => ({
  ...jest.requireActual("../pulseApi"),
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => undefined),
  deleteItemAsync: jest.fn(async () => undefined),
  AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY: "afterFirstUnlockThisDeviceOnly"
}));

import { PulseApiError } from "../pulseApi";
import {
  CAPITAL_CASH_FLOW_PATH,
  CAPITAL_ENTITY_PATH,
  CAPITAL_EXPOSURE_PATH,
  CAPITAL_GRAPH_PATH,
  CAPITAL_INTEGRITY_PATH,
  CAPITAL_OBLIGATIONS_PATH,
  CAPITAL_OVERVIEW_PATH,
  CAPITAL_PORTFOLIO_PATH,
  getCapitalCashFlow,
  getCapitalEntity,
  getCapitalExposure,
  getCapitalGraph,
  getCapitalIntegrity,
  getCapitalObligations,
  getCapitalOverview,
  getCapitalPortfolio,
  getCapitalRelationships,
  parseCapitalEdge,
  parseCapitalNode
} from "../capitalGraph";
import { PRIVATE_OFFICE_FACTS_PATH, createPrivateFact, parseFact } from "../privateOffice";
import {
  OFFICE_DEVICE_HEADER,
  OFFICE_GRANT_HEADER,
  __resetOfficeLockForTests,
  setOfficeUnlocked
} from "../../privateOffice/officeLock";

const TOKEN = "grant-token-a1b2c3d4e5f6";

function apiError(
  status: number,
  details?: Record<string, unknown>,
  message = "refused"
): PulseApiError {
  return new PulseApiError(message, status, undefined, details);
}

function lastRequest(): { path: string; options: Record<string, any> } {
  const call = mockPulseApi.mock.calls[mockPulseApi.mock.calls.length - 1];
  return { path: call[0] as string, options: (call[1] ?? {}) as Record<string, any> };
}

/** A node exactly as the capital-graph serializer emits it. */
function rawNode(overrides: Record<string, unknown> = {}) {
  return {
    id: 11,
    node_type: "PROPERTY",
    external_ref: "prop-11",
    lifecycle_state: "ACTIVE",
    sensitivity: "NORMAL",
    domain: "FINANCIAL",
    created_at: "2026-08-01T09:00:00Z",
    updated_at: "2026-08-30T09:00:00Z",
    truth: "KNOWN",
    fact_count: 4,
    ...overrides
  };
}

function rawEdge(overrides: Record<string, unknown> = {}) {
  return {
    id: 71,
    source_node_id: 11,
    target_node_id: 12,
    relation_type: "INSURED_BY",
    lifecycle_state: "ACTIVE",
    created_at: "2026-08-02T09:00:00Z",
    updated_at: "2026-08-02T09:00:00Z",
    provenance: {
      source_type: "DOCUMENT",
      source_id: "doc-9",
      has_source_document: true,
      provenance_type: "DOCUMENT_EXTRACTED",
      verification: "VERIFIED"
    },
    ...overrides
  };
}

function rawFact(overrides: Record<string, unknown> = {}) {
  return {
    id: 501,
    fact_type: "purchase_price",
    value: "420000",
    value_type: "MONEY",
    domain: "FINANCIAL",
    sensitivity: "NORMAL",
    observed_at: "2026-07-01T00:00:00Z",
    lifecycle_state: "ACTIVE",
    provenance: {
      source_type: "USER",
      source_id: "",
      has_source_document: false,
      provenance_type: "USER_ASSERTED",
      verification: "UNVERIFIED",
      observed_at: "2026-07-01T00:00:00Z",
      confidence: 0.6
    },
    freshness: { stale: false, age_days: 66, horizon_days: 365 },
    ...overrides
  };
}

function rawConflict() {
  return {
    conflict_id: "c-1",
    subject_id: "node:11",
    fact_type: "valuation",
    reason: "two sources disagree",
    competing: [
      {
        fact_id: 501,
        value: "420000",
        value_type: "MONEY",
        provenance_type: "USER_ASSERTED",
        verification: "UNVERIFIED",
        observed_at: "2026-07-01T00:00:00Z",
        stale: false
      },
      {
        fact_id: 502,
        value: "455000",
        value_type: "MONEY",
        provenance_type: "DOCUMENT_EXTRACTED",
        verification: "VERIFIED",
        observed_at: "2026-08-01T00:00:00Z",
        stale: true
      }
    ]
  };
}

function rawStale() {
  return { fact_id: 502, fact_type: "valuation", age_days: 400, horizon_days: 365 };
}

/** A wire body exactly as the graph route answers it, for one view. */
function rawGraphBody(overrides: Record<string, unknown> = {}) {
  return {
    capital_graph: {
      view: "holdings",
      nodes: [rawNode()],
      edges: [rawEdge()],
      facts: [rawFact()],
      conflicts: [rawConflict()],
      stale: [rawStale()],
      counted: { PROPERTY: 2, ACCOUNT: 1 },
      truth_counts: { KNOWN: 2, STALE: 1 },
      complete: true,
      ...overrides
    }
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  __resetOfficeLockForTests();
});

describe("getCapitalGraph", () => {
  it("parses a READY envelope — every snake_case wire key lands on its camel field", async () => {
    mockPulseApi.mockResolvedValueOnce(rawGraphBody());
    const result = await getCapitalGraph("holdings");
    expect(result).toEqual({
      state: "READY",
      graph: {
        view: "holdings",
        nodes: [
          {
            id: 11,
            nodeType: "PROPERTY",
            externalRef: "prop-11",
            lifecycleState: "ACTIVE",
            sensitivity: "NORMAL",
            domain: "FINANCIAL",
            createdAt: "2026-08-01T09:00:00Z",
            updatedAt: "2026-08-30T09:00:00Z",
            truth: "KNOWN",
            factCount: 4
          }
        ],
        edges: [parseCapitalEdge(rawEdge())],
        facts: [parseFact(rawFact())],
        conflicts: [
          {
            conflictId: "c-1",
            subjectId: "node:11",
            factType: "valuation",
            reason: "two sources disagree",
            competing: [
              {
                factId: 501,
                value: "420000",
                valueType: "MONEY",
                provenanceType: "USER_ASSERTED",
                verification: "UNVERIFIED",
                observedAt: "2026-07-01T00:00:00Z",
                stale: false
              },
              {
                factId: 502,
                value: "455000",
                valueType: "MONEY",
                provenanceType: "DOCUMENT_EXTRACTED",
                verification: "VERIFIED",
                observedAt: "2026-08-01T00:00:00Z",
                stale: true
              }
            ]
          }
        ],
        stale: [{ factId: 502, factType: "valuation", ageDays: 400, horizonDays: 365 }],
        counted: { PROPERTY: 2, ACCOUNT: 1 },
        truthCounts: { KNOWN: 2, STALE: 1 },
        complete: true
      }
    });
    expect(lastRequest().path).toBe(`${CAPITAL_GRAPH_PATH}?view=holdings`);
  });

  it("sends the office headers on every read, and the grant only once unlocked", async () => {
    mockPulseApi.mockResolvedValue(rawGraphBody());
    await getCapitalGraph("holdings");
    const locked = lastRequest().options.headers as Record<string, string>;
    expect(locked[OFFICE_DEVICE_HEADER]).toBeTruthy();
    expect(locked[OFFICE_GRANT_HEADER]).toBeUndefined();

    setOfficeUnlocked(TOKEN, new Date(Date.now() + 900_000).toISOString(), 4021);
    await getCapitalGraph("holdings");
    const unlocked = lastRequest().options.headers as Record<string, string>;
    expect(unlocked[OFFICE_GRANT_HEADER]).toBe(TOKEN);
  });

  it("keeps a 403 DENIED as a refused question with the server's reason, whatever the casing", async () => {
    mockPulseApi.mockRejectedValueOnce(
      apiError(403, { state: "denied", reason: "sensitivity ceiling" })
    );
    expect(await getCapitalGraph("holdings")).toEqual({
      state: "DENIED",
      reason: "sensitivity ceiling"
    });

    mockPulseApi.mockRejectedValueOnce(apiError(403, { state: "DENIED", reason: "out of view" }));
    expect(await getCapitalGraph("coverage")).toEqual({ state: "DENIED", reason: "out of view" });
  });

  it("maps a 423 to LOCKED and carries setup_required from the details", async () => {
    mockPulseApi.mockRejectedValueOnce(apiError(423, { setup_required: true }));
    expect(await getCapitalGraph("holdings")).toEqual({ state: "LOCKED", setupRequired: true });

    mockPulseApi.mockRejectedValueOnce(apiError(423, {}));
    expect(await getCapitalGraph("holdings")).toEqual({ state: "LOCKED", setupRequired: false });
  });

  it("keeps the outage and the entitlement refusal as their own words", async () => {
    mockPulseApi.mockRejectedValueOnce(apiError(503, {}));
    expect(await getCapitalGraph("structure")).toEqual({ state: "UNAVAILABLE" });

    mockPulseApi.mockRejectedValueOnce(
      apiError(403, { state: "NOT_ENTITLED", minimum_tier: "PRIVATE" })
    );
    expect(await getCapitalGraph("structure")).toEqual({
      state: "NOT_ENTITLED",
      minimumTier: "PRIVATE"
    });
  });

  it("never derives an aggregate — counted and complete arrive exactly as sent", async () => {
    mockPulseApi.mockResolvedValueOnce(rawGraphBody({ complete: false }));
    const result = await getCapitalGraph("holdings");
    if (result.state !== "READY") throw new Error(`expected READY, got ${result.state}`);
    // A graph with rows present but complete:false keeps complete false; the
    // client must not upgrade "so far" to "all of it" because it saw data.
    expect(result.graph.complete).toBe(false);
    expect(result.graph.counted).toEqual({ PROPERTY: 2, ACCOUNT: 1 });
    expect(result.graph.truthCounts).toEqual({ KNOWN: 2, STALE: 1 });
    // No total, no sum, no aggregate value anywhere on the parsed shape.
    expect(result.graph).not.toHaveProperty("total");
    expect(result.graph).not.toHaveProperty("aggregate");
    expect(result.graph).not.toHaveProperty("totalValue");
  });
});

describe("getCapitalEntity", () => {
  it("parses the entity, its neighbourhood without the subject, and the graph", async () => {
    const related = rawNode({ id: 12, node_type: "POLICY", external_ref: "pol-12" });
    const body = rawGraphBody();
    (body.capital_graph as Record<string, unknown>).related = [related];
    mockPulseApi.mockResolvedValueOnce({ entity: rawNode(), ...body });

    const result = await getCapitalEntity(11, "holdings");
    if (result.state !== "READY") throw new Error(`expected READY, got ${result.state}`);
    expect(result.entity).toEqual(parseCapitalNode(rawNode()));
    expect(result.related).toEqual([parseCapitalNode(related)]);
    expect(result.graph.nodes).toEqual([parseCapitalNode(rawNode())]);
    expect(result.graph.complete).toBe(true);
    expect(lastRequest().path).toBe(`${CAPITAL_ENTITY_PATH}/11?view=holdings`);
    expect((lastRequest().options.headers as Record<string, string>)[OFFICE_DEVICE_HEADER]).toBeTruthy();
  });

  it("keeps 'not yours' and 'never existed' as one NOT_FOUND, by state word", async () => {
    mockPulseApi.mockRejectedValueOnce(apiError(404, { state: "not_found" }));
    expect(await getCapitalEntity(999, "holdings")).toEqual({ state: "NOT_FOUND" });
  });

  it("does not read NOT_FOUND into a 404 that never said it", async () => {
    mockPulseApi.mockRejectedValueOnce(apiError(404, {}, "no route"));
    expect(await getCapitalEntity(999, "holdings")).toEqual({ state: "ERROR", message: "no route" });
  });

  it("maps the shared refusals like the graph read does", async () => {
    mockPulseApi.mockRejectedValueOnce(apiError(423, { setup_required: true }));
    expect(await getCapitalEntity(11, "coverage")).toEqual({ state: "LOCKED", setupRequired: true });

    mockPulseApi.mockRejectedValueOnce(apiError(503, {}));
    expect(await getCapitalEntity(11, "coverage")).toEqual({ state: "UNAVAILABLE" });
  });
});

describe("getCapitalRelationships", () => {
  it("parses each edge from the subject's point of view, with the far end named", async () => {
    const other = rawNode({ id: 12, node_type: "POLICY" });
    mockPulseApi.mockResolvedValueOnce({
      entity: rawNode(),
      relationships: [{ ...rawEdge(), direction: "in", other }],
      complete: true
    });

    const result = await getCapitalRelationships(11, "coverage");
    expect(result).toEqual({
      state: "READY",
      entity: parseCapitalNode(rawNode()),
      relationships: [
        { ...parseCapitalEdge(rawEdge()), direction: "in", other: parseCapitalNode(other) }
      ],
      complete: true
    });
    expect(lastRequest().path).toBe(`${CAPITAL_ENTITY_PATH}/11/relationships?view=coverage`);
  });

  it("carries complete:false exactly as sent — never derived from the rows", async () => {
    mockPulseApi.mockResolvedValueOnce({
      entity: rawNode(),
      relationships: [{ ...rawEdge(), direction: "out", other: rawNode({ id: 12 }) }],
      complete: false
    });
    const result = await getCapitalRelationships(11, "holdings");
    if (result.state !== "READY") throw new Error(`expected READY, got ${result.state}`);
    expect(result.complete).toBe(false);
    expect(result.relationships).toHaveLength(1);
  });

  it("keeps the 404 state word as NOT_FOUND", async () => {
    mockPulseApi.mockRejectedValueOnce(apiError(404, { state: "NOT_FOUND" }));
    expect(await getCapitalRelationships(999, "holdings")).toEqual({ state: "NOT_FOUND" });
  });
});

/** A wire body exactly as the portfolio route answers it. */
function rawPortfolioBody(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    portfolio: {
      ok: true,
      assets: [
        {
          node_id: 41,
          symbol: "BTC",
          name: "Bitcoin",
          quantity: 0.75,
          lot_count: 2,
          cost_basis: 25000,
          price: 60000,
          value: 45000,
          pnl_value: 20000,
          priced: true,
          change_24h: 1.2,
          projected_at: "2026-09-01T00:00:00Z",
          freshness: { stale: false, age_days: 4, horizon_days: 365 },
          evidence: { fact_ids: [901, 902], provenance: { provenance_type: "USER_ASSERTED" } }
        },
        {
          node_id: 42,
          symbol: "XYZ",
          name: "XYZ",
          quantity: 10,
          lot_count: 1,
          cost_basis: null,
          price: null,
          value: null,
          pnl_value: null,
          priced: false,
          change_24h: null,
          projected_at: "2026-09-02T00:00:00Z",
          freshness: null,
          evidence: { fact_ids: [903], provenance: null }
        }
      ],
      totals: {
        value: null,
        cost: 25000,
        pnl_value: null,
        complete: false,
        assets: 2,
        priced: 1,
        unpriced_symbols: ["XYZ"],
        basis_known: 1
      },
      prices: { source: "coingecko", observed_epoch: 1756700000, age_seconds: 42, warning: "" },
      sync: { pending: 0, failed: 0, enabled: true, swept: 0 },
      ...overrides
    }
  };
}

describe("getCapitalPortfolio", () => {
  it("parses a READY envelope — unpriced holdings carry null, never zero", async () => {
    mockPulseApi.mockResolvedValueOnce(rawPortfolioBody());
    const result = await getCapitalPortfolio();
    if (result.state !== "READY") throw new Error(`expected READY, got ${result.state}`);

    expect(result.portfolio.assets).toEqual([
      {
        nodeId: 41,
        symbol: "BTC",
        name: "Bitcoin",
        quantity: 0.75,
        lotCount: 2,
        costBasis: 25000,
        price: 60000,
        value: 45000,
        pnlValue: 20000,
        priced: true,
        change24h: 1.2,
        projectedAt: "2026-09-01T00:00:00Z"
      },
      {
        nodeId: 42,
        symbol: "XYZ",
        name: "XYZ",
        quantity: 10,
        lotCount: 1,
        // A holding without a live quote has no value — null, not 0.
        costBasis: null,
        price: null,
        value: null,
        pnlValue: null,
        priced: false,
        change24h: null,
        projectedAt: "2026-09-02T00:00:00Z"
      }
    ]);
    expect(result.portfolio.prices).toEqual({
      source: "coingecko",
      observedEpoch: 1756700000,
      ageSeconds: 42,
      warning: ""
    });
    expect(result.portfolio.sync).toEqual({ pending: 0, failed: 0, enabled: true });
    expect(lastRequest().path).toBe(CAPITAL_PORTFOLIO_PATH);
    expect((lastRequest().options.headers as Record<string, string>)[OFFICE_DEVICE_HEADER]).toBeTruthy();
  });

  it("never derives a total — an incomplete set keeps value null even though rows exist", async () => {
    mockPulseApi.mockResolvedValueOnce(rawPortfolioBody());
    const result = await getCapitalPortfolio();
    if (result.state !== "READY") throw new Error(`expected READY, got ${result.state}`);
    // One asset is priced at 45000, but the server refused to total a set with
    // an unpriced member — the client must not sum what the server would not.
    expect(result.portfolio.totals).toEqual({
      value: null,
      cost: 25000,
      pnlValue: null,
      complete: false,
      assets: 2,
      priced: 1,
      unpricedSymbols: ["XYZ"],
      basisKnown: 1
    });
  });

  it("keeps the owner-only refusal as DENIED with the server's reason word", async () => {
    mockPulseApi.mockRejectedValueOnce(
      apiError(403, { state: "denied", reason: { reason: "actor_is_not_owner" } })
    );
    expect(await getCapitalPortfolio()).toEqual({
      state: "DENIED",
      reason: "actor_is_not_owner"
    });
  });

  it("maps the shared refusals like every other Office call", async () => {
    mockPulseApi.mockRejectedValueOnce(apiError(423, { setup_required: true }));
    expect(await getCapitalPortfolio()).toEqual({ state: "LOCKED", setupRequired: true });

    mockPulseApi.mockRejectedValueOnce(apiError(503, {}));
    expect(await getCapitalPortfolio()).toEqual({ state: "UNAVAILABLE" });

    mockPulseApi.mockRejectedValueOnce(apiError(403, { state: "FEATURE_DISABLED" }));
    expect(await getCapitalPortfolio()).toEqual({ state: "FEATURE_DISABLED" });
  });

  it("keeps every failed fetch a failure — never READY, never an empty portfolio", async () => {
    // The deployment-gap shape: the route does not exist yet, so production
    // answers a bare 404 with no state word. That is a failed read, not an
    // empty portfolio.
    mockPulseApi.mockRejectedValueOnce(apiError(404, {}, "no route"));
    expect(await getCapitalPortfolio()).toEqual({ state: "ERROR", message: "no route" });

    mockPulseApi.mockRejectedValueOnce(apiError(401, {}, "unauthenticated"));
    expect(await getCapitalPortfolio()).toEqual({ state: "ERROR", message: "unauthenticated" });

    mockPulseApi.mockRejectedValueOnce(apiError(500, {}, "boom"));
    expect(await getCapitalPortfolio()).toEqual({ state: "ERROR", message: "boom" });

    // A dead network throws something that is not a PulseApiError at all.
    mockPulseApi.mockRejectedValueOnce(new TypeError("Network request failed"));
    expect(await getCapitalPortfolio()).toEqual({ state: "ERROR", message: "" });
  });
});

describe("createPrivateFact", () => {
  it("posts snake_case keys, omits sensitivity when not provided, and returns SAVED", async () => {
    mockPulseApi.mockResolvedValueOnce({
      ok: true,
      status: "RECORDED",
      fact_id: "fact-991",
      domain: "FINANCIAL",
      sensitivity: "NORMAL"
    });
    const result = await createPrivateFact({
      domain: "FINANCIAL",
      factType: "purchase_price",
      value: "420000",
      valueType: "MONEY"
    });
    expect(result).toEqual({ state: "SAVED", status: "RECORDED", factId: "fact-991" });
    expect(lastRequest().path).toBe(PRIVATE_OFFICE_FACTS_PATH);
    expect(lastRequest().options.method).toBe("POST");
    expect((lastRequest().options.headers as Record<string, string>)[OFFICE_DEVICE_HEADER]).toBeTruthy();
    expect(JSON.parse(lastRequest().options.body)).toEqual({
      domain: "FINANCIAL",
      fact_type: "purchase_price",
      value: "420000",
      value_type: "MONEY"
    });
  });

  it("includes sensitivity in the body only when the draft carries one", async () => {
    mockPulseApi.mockResolvedValueOnce({ ok: true, status: "RECORDED", fact_id: "fact-992" });
    await createPrivateFact({
      domain: "HEALTH",
      factType: "allergy",
      value: "penicillin",
      valueType: "STRING",
      sensitivity: "HIGH"
    });
    expect(JSON.parse(lastRequest().options.body)).toEqual({
      domain: "HEALTH",
      fact_type: "allergy",
      value: "penicillin",
      value_type: "STRING",
      sensitivity: "HIGH"
    });
  });

  it("relays the writer's 400 verbatim — it was written for a person", async () => {
    mockPulseApi.mockRejectedValueOnce(
      apiError(400, { message: "value_type MONEY needs a numeric value" })
    );
    expect(
      await createPrivateFact({ domain: "FINANCIAL", factType: "x", value: "?", valueType: "MONEY" })
    ).toEqual({ state: "REJECTED", message: "value_type MONEY needs a numeric value" });
  });

  it("maps the shared refusals like every other Office call", async () => {
    const draft = { domain: "GENERAL", factType: "x", value: "y", valueType: "STRING" };

    mockPulseApi.mockRejectedValueOnce(apiError(423, { setup_required: false }));
    expect(await createPrivateFact(draft)).toEqual({ state: "LOCKED", setupRequired: false });

    mockPulseApi.mockRejectedValueOnce(
      apiError(403, { state: "NOT_ENTITLED", minimum_tier: "PRIVATE" })
    );
    expect(await createPrivateFact(draft)).toEqual({
      state: "NOT_ENTITLED",
      minimumTier: "PRIVATE"
    });

    mockPulseApi.mockRejectedValueOnce(apiError(503, {}));
    expect(await createPrivateFact(draft)).toEqual({ state: "UNAVAILABLE" });
  });
});

/**
 * The Capital Command Center — overview and exposure.
 *
 * These two routes are one server computation (`capital_overview.overview`)
 * projected twice, so the suite checks that the client keeps them agreeing
 * rather than re-deriving either. The bodies below are shaped from
 * `services/private_office/capital_overview.py` directly; if a key here is
 * wrong the parser silently yields null and a screen quietly under-reports,
 * which no type can catch.
 *
 * The load-bearing cases are the honesty ones. `estimated` is priced assets
 * minus quantified liabilities, NOT net worth, and the server says so through
 * `complete` / `incomplete_reasons`. A parser that defaulted a missing money
 * key to 0 would turn "we would not say" into "you have nothing" — so every
 * money field is asserted `null`, never `0`, when the server omits it.
 */

function rawAssets(overrides: Record<string, unknown> = {}) {
  return {
    priced_value: 812450.25,
    currency: "USD",
    count: 9,
    priced: 7,
    unpriced: 2,
    unpriced_symbols: ["XMR", "PRIVATECO"],
    basis_known: 5,
    known_cost: 604000,
    complete: false,
    ...overrides
  };
}

function rawLiabilities(overrides: Record<string, unknown> = {}) {
  return {
    known_amount: 240000,
    currency: "USD",
    count: 5,
    quantified: 3,
    unquantified: 2,
    foreign_currency: 1,
    unspecified_currency: 1,
    by_currency: {
      USD: { amount: 240000, count: 3 },
      GBP: { amount: 88000, count: 1 },
      UNSPECIFIED: { amount: null, count: 1 }
    },
    complete: false,
    truncated: false,
    ...overrides
  };
}

function rawNetPosition(overrides: Record<string, unknown> = {}) {
  return {
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
      unspecified_currency_liabilities: 1
    },
    basis: "priced_assets_minus_quantified_liabilities",
    disclaimer: "This is not a net worth figure.",
    ...overrides
  };
}

function rawCoverage(overrides: Record<string, unknown> = {}) {
  return {
    dimensions: {
      pricing: { known: 7, countable: 9, ratio: 7 / 9 },
      cost_basis: { known: 5, countable: 9, ratio: 5 / 9 },
      liability_amounts: { known: 3, countable: 5, ratio: 0.6 },
      evidence: { known: 0, countable: 0, ratio: null }
    },
    score: 0.6,
    scored_dimensions: ["pricing", "cost_basis", "liability_amounts"],
    formula: "mean(scored_dimensions)",
    ...overrides
  };
}

function rawConcentrations(overrides: Record<string, unknown> = {}) {
  return {
    assets: [{ key: "BTC", label: "Bitcoin", value: 500000, share: 0.6157 }],
    asset_basis: "priced_asset_value",
    asset_total: 812450.25,
    assets_ranked: 1,
    assets_unranked_tail: 6,
    liabilities: [{ key: "MORTGAGE", label: "MORTGAGE", value: 240000, share: 1 }],
    liability_basis: "quantified_liability_amount",
    liability_total: 240000,
    currency: "USD",
    ...overrides
  };
}

function rawPrices(overrides: Record<string, unknown> = {}) {
  return {
    source: "live_market_board",
    observed_epoch: 1788000000,
    age_seconds: 42,
    warning: "",
    ...overrides
  };
}

function rawOverviewBody(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    overview: {
      assets: rawAssets(),
      liabilities: rawLiabilities(),
      net_position: rawNetPosition(),
      coverage: rawCoverage(),
      concentrations: rawConcentrations(),
      needs_review: [
        {
          kind: "UNPRICED_ASSET",
          subject: "XMR",
          detail: "no price on the board",
          source: "portfolio_projection"
        }
      ],
      needs_review_total: 4,
      prices: rawPrices(),
      generated_at: "2026-09-06T12:00:00+00:00",
      ...overrides
    }
  };
}

describe("getCapitalOverview", () => {
  it("parses a READY envelope — every wire key lands on its camel field", async () => {
    mockPulseApi.mockResolvedValueOnce(rawOverviewBody());
    const result = await getCapitalOverview();

    expect(result).toEqual({
      state: "READY",
      overview: {
        assets: {
          pricedValue: 812450.25,
          currency: "USD",
          count: 9,
          priced: 7,
          unpriced: 2,
          unpricedSymbols: ["XMR", "PRIVATECO"],
          basisKnown: 5,
          knownCost: 604000,
          complete: false
        },
        liabilities: {
          knownAmount: 240000,
          currency: "USD",
          count: 5,
          quantified: 3,
          unquantified: 2,
          foreignCurrency: 1,
          unspecifiedCurrency: 1,
          byCurrency: {
            USD: { amount: 240000, count: 3 },
            GBP: { amount: 88000, count: 1 },
            UNSPECIFIED: { amount: null, count: 1 }
          },
          complete: false,
          truncated: false
        },
        netPosition: {
          estimated: 572450.25,
          currency: "USD",
          knownAssets: 812450.25,
          knownLiabilities: 240000,
          complete: false,
          incompleteReasons: ["unpriced_assets", "unquantified_liabilities"],
          excluded: {
            unpricedAssets: 2,
            unquantifiedLiabilities: 2,
            foreignCurrencyLiabilities: 1,
            unspecifiedCurrencyLiabilities: 1
          },
          basis: "priced_assets_minus_quantified_liabilities",
          disclaimer: "This is not a net worth figure."
        },
        coverage: {
          dimensions: {
            pricing: { known: 7, countable: 9, ratio: 7 / 9 },
            cost_basis: { known: 5, countable: 9, ratio: 5 / 9 },
            liability_amounts: { known: 3, countable: 5, ratio: 0.6 },
            evidence: { known: 0, countable: 0, ratio: null }
          },
          score: 0.6,
          scoredDimensions: ["pricing", "cost_basis", "liability_amounts"],
          formula: "mean(scored_dimensions)"
        },
        concentrations: {
          assets: [{ key: "BTC", label: "Bitcoin", value: 500000, share: 0.6157 }],
          assetBasis: "priced_asset_value",
          assetTotal: 812450.25,
          assetsRanked: 1,
          assetsUnrankedTail: 6,
          liabilities: [{ key: "MORTGAGE", label: "MORTGAGE", value: 240000, share: 1 }],
          liabilityBasis: "quantified_liability_amount",
          liabilityTotal: 240000,
          currency: "USD"
        },
        needsReview: [
          {
            kind: "UNPRICED_ASSET",
            subject: "XMR",
            detail: "no price on the board",
            source: "portfolio_projection"
          }
        ],
        needsReviewTotal: 4,
        prices: {
          source: "live_market_board",
          observedEpoch: 1788000000,
          ageSeconds: 42,
          warning: ""
        },
        generatedAt: "2026-09-06T12:00:00+00:00"
      }
    });

    expect(lastRequest().path).toBe(CAPITAL_OVERVIEW_PATH);
    expect(
      (lastRequest().options.headers as Record<string, string>)[OFFICE_DEVICE_HEADER]
    ).toBeTruthy();
  });

  it("yields null, never 0, for money the server withheld", async () => {
    // The shape `_denied()` builds and the shape a partially-degraded read
    // returns: present keys, empty objects. Zero here would be a lie.
    mockPulseApi.mockResolvedValueOnce({
      ok: true,
      overview: {
        assets: {},
        liabilities: {},
        net_position: {},
        coverage: {},
        concentrations: {},
        needs_review: [],
        prices: {}
      }
    });
    const result = await getCapitalOverview();
    if (result.state !== "READY") throw new Error(`expected READY, got ${result.state}`);

    const { netPosition, assets, liabilities, coverage, concentrations } = result.overview;

    expect(netPosition.estimated).toBeNull();
    expect(netPosition.estimated).not.toBe(0);
    expect(netPosition.knownAssets).toBeNull();
    expect(netPosition.knownLiabilities).toBeNull();
    expect(assets.pricedValue).toBeNull();
    expect(assets.knownCost).toBeNull();
    expect(liabilities.knownAmount).toBeNull();
    expect(coverage.score).toBeNull();
    expect(concentrations.assetTotal).toBeNull();
    expect(concentrations.liabilityTotal).toBeNull();

    // An absent net position is never "complete" — the UI must not print a
    // bare figure because a boolean defaulted true.
    expect(netPosition.complete).toBe(false);
  });

  it("never treats an absent `complete` as true", async () => {
    mockPulseApi.mockResolvedValueOnce(
      rawOverviewBody({ net_position: rawNetPosition({ complete: "yes" }) })
    );
    const result = await getCapitalOverview();
    if (result.state !== "READY") throw new Error("expected READY");
    // A truthy non-boolean must not be promoted; only literal true counts.
    expect(result.overview.netPosition.complete).toBe(false);
  });

  it("keeps a zero ratio distinct from an unscoreable one", async () => {
    mockPulseApi.mockResolvedValueOnce(
      rawOverviewBody({
        coverage: rawCoverage({
          dimensions: {
            pricing: { known: 0, countable: 4, ratio: 0 },
            evidence: { known: 0, countable: 0, ratio: null }
          }
        })
      })
    );
    const result = await getCapitalOverview();
    if (result.state !== "READY") throw new Error("expected READY");

    // "nothing priced out of 4" and "nothing to price" are different claims.
    expect(result.overview.coverage.dimensions.pricing.ratio).toBe(0);
    expect(result.overview.coverage.dimensions.evidence.ratio).toBeNull();
  });

  it("carries the excluded-currency magnitudes, not just their counts", async () => {
    mockPulseApi.mockResolvedValueOnce(rawOverviewBody());
    const result = await getCapitalOverview();
    if (result.state !== "READY") throw new Error("expected READY");

    const { byCurrency, knownAmount } = result.overview.liabilities;
    // knownAmount is the base bucket alone; GBP is excluded but must remain
    // visible, and its amount must not have been folded into the total.
    expect(knownAmount).toBe(240000);
    expect(byCurrency.GBP).toEqual({ amount: 88000, count: 1 });
    expect(byCurrency.UNSPECIFIED.amount).toBeNull();
    expect(Object.keys(byCurrency).sort()).toEqual(["GBP", "UNSPECIFIED", "USD"]);
  });

  it("reports needsReviewTotal from the wire, not from the truncated list", async () => {
    mockPulseApi.mockResolvedValueOnce(rawOverviewBody());
    const result = await getCapitalOverview();
    if (result.state !== "READY") throw new Error("expected READY");
    expect(result.overview.needsReview).toHaveLength(1);
    expect(result.overview.needsReviewTotal).toBe(4);
  });

  it("maps the shared refusals — a refusal is never an empty balance sheet", async () => {
    mockPulseApi.mockRejectedValueOnce(
      apiError(403, { state: "DENIED", reason: { reason: "not_owner" } })
    );
    expect(await getCapitalOverview()).toEqual({ state: "DENIED", reason: "not_owner" });

    mockPulseApi.mockRejectedValueOnce(apiError(423, { setup_required: true }));
    expect(await getCapitalOverview()).toEqual({ state: "LOCKED", setupRequired: true });

    mockPulseApi.mockRejectedValueOnce(
      apiError(403, { state: "NOT_ENTITLED", minimum_tier: "PRIVATE" })
    );
    expect(await getCapitalOverview()).toEqual({
      state: "NOT_ENTITLED",
      minimumTier: "PRIVATE"
    });

    mockPulseApi.mockRejectedValueOnce(apiError(503, {}));
    expect(await getCapitalOverview()).toEqual({ state: "UNAVAILABLE" });
  });
});

describe("getCapitalExposure", () => {
  function rawExposureBody(overrides: Record<string, unknown> = {}) {
    return {
      ok: true,
      exposure: {
        concentrations: rawConcentrations(),
        assets: rawAssets(),
        liabilities: rawLiabilities(),
        coverage: rawCoverage(),
        prices: rawPrices(),
        generated_at: "2026-09-06T12:00:00+00:00",
        ...overrides
      }
    };
  }

  it("parses the route's strict subset and asserts no net position rides along", async () => {
    mockPulseApi.mockResolvedValueOnce(rawExposureBody());
    const result = await getCapitalExposure();
    if (result.state !== "READY") throw new Error("expected READY");

    expect(Object.keys(result.exposure).sort()).toEqual([
      "assets",
      "concentrations",
      "coverage",
      "generatedAt",
      "liabilities",
      "prices"
    ]);
    expect(result.exposure).not.toHaveProperty("netPosition");
    expect(lastRequest().path).toBe(CAPITAL_EXPOSURE_PATH);
  });

  it("agrees with the overview, because both read one server computation", async () => {
    mockPulseApi.mockResolvedValueOnce(rawOverviewBody());
    const overview = await getCapitalOverview();
    mockPulseApi.mockResolvedValueOnce(rawExposureBody());
    const exposure = await getCapitalExposure();
    if (overview.state !== "READY" || exposure.state !== "READY") {
      throw new Error("expected both READY");
    }

    // The largest holding must be the same object on both screens. If either
    // side ever starts deriving instead of reading, this diverges.
    expect(exposure.exposure.concentrations).toEqual(overview.overview.concentrations);
    expect(exposure.exposure.assets).toEqual(overview.overview.assets);
    expect(exposure.exposure.liabilities).toEqual(overview.overview.liabilities);
    expect(exposure.exposure.coverage).toEqual(overview.overview.coverage);
  });

  it("maps the shared refusals", async () => {
    mockPulseApi.mockRejectedValueOnce(
      apiError(403, { state: "DENIED", reason: { reason: "second_lock" } })
    );
    expect(await getCapitalExposure()).toEqual({ state: "DENIED", reason: "second_lock" });

    mockPulseApi.mockRejectedValueOnce(apiError(423, { setup_required: false }));
    expect(await getCapitalExposure()).toEqual({ state: "LOCKED", setupRequired: false });

    mockPulseApi.mockRejectedValueOnce(apiError(503, {}));
    expect(await getCapitalExposure()).toEqual({ state: "UNAVAILABLE" });
  });
});

/**
 * Obligations, cash flow and integrity.
 *
 * Each of these three routes publishes a figure next to the reason it may be
 * partial, and in each the partiality field is the one that can lie by
 * omission. The cases below are built from the server modules directly
 * (`obligation_projection.py`, `cash_flow.py`, `integrity.py`) so a renamed
 * key fails here rather than silently nulling a screen.
 */

function rawLiabilityRow(overrides: Record<string, unknown> = {}) {
  return {
    node_id: 31,
    root_id: 7,
    title: "Mortgage — 14 Elm Row",
    kind: "MORTGAGE",
    amount: 240000,
    currency: "USD",
    quantified: true,
    due_at: "2031-06-01T00:00:00Z",
    projected_at: "2026-08-01T00:00:00Z",
    freshness: { stale: false, age_days: 36, horizon_days: 365 },
    evidence: {
      fact_ids: [901, 902],
      provenance: {
        source_type: "DOCUMENT",
        source_id: "doc-4",
        has_source_document: true,
        provenance_type: "DOCUMENT_EXTRACTED",
        verification: "VERIFIED"
      }
    },
    ...overrides
  };
}

function rawObligationsBody(totals: Record<string, unknown> = {}, rows?: unknown[]) {
  return {
    ok: true,
    obligations: {
      liabilities: rows ?? [rawLiabilityRow()],
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
        limit: 500,
        ...totals
      },
      sync: { projected: true, obligations: 1, retired: 0, skipped: 0 }
    }
  };
}

describe("getCapitalObligations", () => {
  it("parses a READY envelope, evidence and freshness included", async () => {
    mockPulseApi.mockResolvedValueOnce(rawObligationsBody());
    const result = await getCapitalObligations();
    if (result.state !== "READY") throw new Error(`expected READY, got ${result.state}`);

    expect(result.obligations.liabilities).toEqual([
      {
        nodeId: 31,
        rootId: 7,
        title: "Mortgage — 14 Elm Row",
        kind: "MORTGAGE",
        amount: 240000,
        currency: "USD",
        quantified: true,
        dueAt: "2031-06-01T00:00:00Z",
        projectedAt: "2026-08-01T00:00:00Z",
        freshness: { stale: false, ageDays: 36, horizonDays: 365 },
        evidence: {
          factIds: [901, 902],
          provenance: {
            sourceType: "DOCUMENT",
            sourceId: "doc-4",
            hasSourceDocument: true,
            provenanceType: "DOCUMENT_EXTRACTED",
            verification: "VERIFIED"
          }
        }
      }
    ]);
    expect(result.obligations.totals.knownAmount).toBe(240000);
    expect(result.obligations.totals.limit).toBe(500);
    expect(result.obligations.sync).toEqual({
      projected: true,
      obligations: 1,
      retired: 0,
      skipped: 0
    });
    expect(lastRequest().path).toBe(CAPITAL_OBLIGATIONS_PATH);
  });

  it("keeps an unsummable multi-currency total null, never zero", async () => {
    // `liabilities_view` publishes known_amount only when one currency answers
    // for every row. Two currencies means "no rate, no sum" — not "owes 0".
    mockPulseApi.mockResolvedValueOnce(
      rawObligationsBody({
        known_amount: null,
        currency: "",
        by_currency: {
          USD: { amount: 240000, count: 1 },
          GBP: { amount: 88000, count: 1 }
        },
        currencies: ["GBP", "USD"],
        count: 2,
        quantified: 2,
        complete: false
      })
    );
    const result = await getCapitalObligations();
    if (result.state !== "READY") throw new Error("expected READY");

    expect(result.obligations.totals.knownAmount).toBeNull();
    expect(result.obligations.totals.knownAmount).not.toBe(0);
    expect(result.obligations.totals.currency).toBe("");
    expect(result.obligations.totals.currencies).toEqual(["GBP", "USD"]);
    expect(result.obligations.totals.complete).toBe(false);
    // Both magnitudes remain legible even though neither may be summed.
    expect(result.obligations.totals.byCurrency.GBP.amount).toBe(88000);
  });

  it("keeps an unquantified obligation null and does not infer `quantified`", async () => {
    mockPulseApi.mockResolvedValueOnce(
      rawObligationsBody({ known_amount: null, unquantified: 1, quantified: 0, complete: false }, [
        rawLiabilityRow({ amount: null, quantified: false, currency: "" })
      ])
    );
    const result = await getCapitalObligations();
    if (result.state !== "READY") throw new Error("expected READY");

    const row = result.obligations.liabilities[0];
    expect(row.amount).toBeNull();
    expect(row.amount).not.toBe(0);
    expect(row.quantified).toBe(false);
    expect(result.obligations.totals.unquantified).toBe(1);
  });

  it("reads `quantified` from the wire instead of inferring it from `amount`", async () => {
    // The two agree today. Reading rather than deriving is what makes a future
    // divergence — a contract change, a serializer bug — visible here instead
    // of being papered over by a client that recomputes the server's answer.
    mockPulseApi.mockResolvedValueOnce(
      rawObligationsBody({}, [rawLiabilityRow({ amount: 5000, quantified: false })])
    );
    const disagreeing = await getCapitalObligations();
    if (disagreeing.state !== "READY") throw new Error("expected READY");
    expect(disagreeing.obligations.liabilities[0].amount).toBe(5000);
    expect(disagreeing.obligations.liabilities[0].quantified).toBe(false);

    mockPulseApi.mockResolvedValueOnce(
      rawObligationsBody({}, [rawLiabilityRow({ amount: null, quantified: true })])
    );
    const other = await getCapitalObligations();
    if (other.state !== "READY") throw new Error("expected READY");
    expect(other.obligations.liabilities[0].amount).toBeNull();
    expect(other.obligations.liabilities[0].quantified).toBe(true);
  });

  it("keeps a missing due date null rather than an empty string date", async () => {
    mockPulseApi.mockResolvedValueOnce(
      rawObligationsBody({}, [rawLiabilityRow({ due_at: null, freshness: null })])
    );
    const result = await getCapitalObligations();
    if (result.state !== "READY") throw new Error("expected READY");
    expect(result.obligations.liabilities[0].dueAt).toBeNull();
    expect(result.obligations.liabilities[0].freshness).toBeNull();
  });

  it("maps the shared refusals", async () => {
    mockPulseApi.mockRejectedValueOnce(
      apiError(403, { state: "DENIED", reason: { reason: "actor_is_not_owner" } })
    );
    expect(await getCapitalObligations()).toEqual({
      state: "DENIED",
      reason: "actor_is_not_owner"
    });

    mockPulseApi.mockRejectedValueOnce(apiError(423, { setup_required: true }));
    expect(await getCapitalObligations()).toEqual({ state: "LOCKED", setupRequired: true });

    mockPulseApi.mockRejectedValueOnce(apiError(503, {}));
    expect(await getCapitalObligations()).toEqual({ state: "UNAVAILABLE" });
  });
});

function rawCashFlowBody(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    cash_flow: {
      generated_at: "2026-09-06T12:00:00+00:00",
      schedule: [
        {
          node_id: 31,
          root_id: 7,
          title: "Mortgage — 14 Elm Row",
          kind: "MORTGAGE",
          amount: 240000,
          currency: "USD",
          due_at: "2026-10-01T00:00:00Z",
          days_until: 25,
          overdue: false,
          bucket: "next_90_days",
          evidence: { fact_ids: [901], provenance: null }
        }
      ],
      buckets: {
        overdue: { amount: 0, count: 0 },
        next_30_days: { amount: 0, count: 0 },
        next_90_days: { amount: 240000, count: 1 }
      },
      totals: {
        currency: "USD",
        scheduled_amount: 240000,
        scheduled_count: 1,
        obligations_seen: 3,
        truncated: false,
        complete: false,
        excluded_count: 2,
        mixed_currency_rows: 0
      },
      excluded: { undated: 1, unquantified: 1, undated_and_unquantified: 0 },
      basis: {
        inflows: "Outflows only. PulseSoc records no income, so nothing here is netted.",
        recurrence: "No recurrence is inferred.",
        buckets: [
          { name: "overdue", from_days: null, to_days: 0 },
          { name: "next_30_days", from_days: 0, to_days: 30 },
          { name: "next_90_days", from_days: 30, to_days: 90 }
        ]
      },
      sync: { projected: true, obligations: 3, retired: 0, skipped: 0 },
      ...overrides
    }
  };
}

describe("getCapitalCashFlow", () => {
  it("parses a READY envelope with buckets, exclusions and basis", async () => {
    mockPulseApi.mockResolvedValueOnce(rawCashFlowBody());
    const result = await getCapitalCashFlow();
    if (result.state !== "READY") throw new Error(`expected READY, got ${result.state}`);

    expect(result.cashFlow.buckets.next_90_days).toEqual({ amount: 240000, count: 1 });
    expect(result.cashFlow.schedule[0]).toEqual({
      nodeId: 31,
      rootId: 7,
      title: "Mortgage — 14 Elm Row",
      kind: "MORTGAGE",
      amount: 240000,
      currency: "USD",
      dueAt: "2026-10-01T00:00:00Z",
      daysUntil: 25,
      overdue: false,
      bucket: "next_90_days",
      evidence: { factIds: [901], provenance: null }
    });
    expect(result.cashFlow.excluded).toEqual({
      undated: 1,
      unquantified: 1,
      undatedAndUnquantified: 0
    });
    expect(result.cashFlow.totals.excludedCount).toBe(2);
    expect(result.cashFlow.totals.complete).toBe(false);
    expect(lastRequest().path).toBe(CAPITAL_CASH_FLOW_PATH);
  });

  it("carries the outflows-only basis verbatim — the screen must not imply a balance", async () => {
    mockPulseApi.mockResolvedValueOnce(rawCashFlowBody());
    const result = await getCapitalCashFlow();
    if (result.state !== "READY") throw new Error("expected READY");

    // PulseSoc has no income ledger. A "cash flow" screen that drops this
    // sentence is describing outflows while implying a net figure.
    expect(result.cashFlow.basis.inflows).toBe(
      "Outflows only. PulseSoc records no income, so nothing here is netted."
    );
    expect(result.cashFlow.basis.recurrence).toBe("No recurrence is inferred.");
    expect(result.cashFlow.basis.buckets[0]).toEqual({
      name: "overdue",
      fromDays: null,
      toDays: 0
    });
  });

  it("keeps an unsummable bucket null, so no chart draws it as a zero bar", async () => {
    mockPulseApi.mockResolvedValueOnce(
      rawCashFlowBody({
        buckets: {
          overdue: { amount: null, count: 0 },
          next_30_days: { amount: null, count: 2 }
        },
        totals: {
          currency: "",
          scheduled_amount: null,
          scheduled_count: 2,
          obligations_seen: 2,
          truncated: false,
          complete: false,
          excluded_count: 0,
          mixed_currency_rows: 0
        }
      })
    );
    const result = await getCapitalCashFlow();
    if (result.state !== "READY") throw new Error("expected READY");

    expect(result.cashFlow.totals.scheduledAmount).toBeNull();
    expect(result.cashFlow.buckets.next_30_days.amount).toBeNull();
    // The count is still real — 2 things fall due, we just cannot total them.
    expect(result.cashFlow.buckets.next_30_days.count).toBe(2);
    expect(result.cashFlow.buckets.overdue.amount).toBeNull();
  });

  it("distinguishes 'due today' from 'no due date'", async () => {
    mockPulseApi.mockResolvedValueOnce(
      rawCashFlowBody({
        schedule: [
          {
            node_id: 31,
            root_id: 7,
            title: "Due today",
            kind: "TAX",
            amount: 100,
            currency: "USD",
            due_at: "2026-09-06T00:00:00Z",
            days_until: 0,
            overdue: true,
            bucket: "overdue",
            evidence: { fact_ids: [], provenance: null }
          },
          {
            node_id: 32,
            root_id: 8,
            title: "No date on file",
            kind: "LOAN",
            amount: 100,
            currency: "USD",
            due_at: null,
            days_until: null,
            overdue: false,
            bucket: "",
            evidence: { fact_ids: [], provenance: null }
          }
        ]
      })
    );
    const result = await getCapitalCashFlow();
    if (result.state !== "READY") throw new Error("expected READY");

    expect(result.cashFlow.schedule[0].daysUntil).toBe(0);
    expect(result.cashFlow.schedule[0].overdue).toBe(true);
    expect(result.cashFlow.schedule[1].daysUntil).toBeNull();
    expect(result.cashFlow.schedule[1].dueAt).toBeNull();
  });

  it("surfaces mixedCurrencyRows so a broken invariant is visible, not swallowed", async () => {
    mockPulseApi.mockResolvedValueOnce(rawCashFlowBody());
    const clean = await getCapitalCashFlow();
    if (clean.state !== "READY") throw new Error("expected READY");
    expect(clean.cashFlow.totals.mixedCurrencyRows).toBe(0);

    mockPulseApi.mockResolvedValueOnce(
      rawCashFlowBody({
        totals: {
          currency: "USD",
          scheduled_amount: 240000,
          scheduled_count: 1,
          obligations_seen: 3,
          truncated: false,
          complete: false,
          excluded_count: 2,
          mixed_currency_rows: 3
        }
      })
    );
    const broken = await getCapitalCashFlow();
    if (broken.state !== "READY") throw new Error("expected READY");
    expect(broken.cashFlow.totals.mixedCurrencyRows).toBe(3);
  });

  it("maps the shared refusals", async () => {
    mockPulseApi.mockRejectedValueOnce(
      apiError(403, { state: "DENIED", reason: { reason: "actor_is_not_owner" } })
    );
    expect(await getCapitalCashFlow()).toEqual({
      state: "DENIED",
      reason: "actor_is_not_owner"
    });

    mockPulseApi.mockRejectedValueOnce(apiError(423, { setup_required: false }));
    expect(await getCapitalCashFlow()).toEqual({ state: "LOCKED", setupRequired: false });

    mockPulseApi.mockRejectedValueOnce(apiError(503, {}));
    expect(await getCapitalCashFlow()).toEqual({ state: "UNAVAILABLE" });
  });
});

function rawIntegrityBody(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    integrity: {
      healthy: true,
      findings: [],
      checks: {
        cross_owner_edges: "clean",
        orphan_edges: "clean",
        duplicate_node_identity: "clean",
        unknown_vocabulary: "clean",
        edges_into_retired_nodes: "clean",
        portfolio_projection_drift: "clean"
      },
      examined: { edges: 120, nodes: 64 },
      totals: {
        findings: 0,
        invariant_violations: 0,
        checks_run: 6,
        checks_total: 6,
        inconclusive: [],
        truncated: false,
        complete: true
      },
      basis: {
        repair: "This endpoint repairs nothing.",
        scope: "The caller's own rows only.",
        checks: [
          "cross_owner_edges",
          "orphan_edges",
          "duplicate_node_identity",
          "unknown_vocabulary",
          "edges_into_retired_nodes",
          "portfolio_projection_drift"
        ]
      },
      ...overrides
    }
  };
}

describe("getCapitalIntegrity", () => {
  it("parses a clean READY envelope", async () => {
    mockPulseApi.mockResolvedValueOnce(rawIntegrityBody());
    const result = await getCapitalIntegrity();
    if (result.state !== "READY") throw new Error(`expected READY, got ${result.state}`);

    expect(result.integrity.healthy).toBe(true);
    expect(result.integrity.findings).toEqual([]);
    expect(result.integrity.examined).toEqual({ edges: 120, nodes: 64 });
    expect(result.integrity.totals.checksRun).toBe(6);
    expect(result.integrity.basis.repair).toBe("This endpoint repairs nothing.");
    expect(lastRequest().path).toBe(CAPITAL_INTEGRITY_PATH);
  });

  it("does NOT call a store healthy when a check could not run", async () => {
    // The load-bearing case. Zero findings plus one skipped check is
    // unexamined, not healthy, and the server already decided that — the
    // client must read the flag rather than count the findings array.
    mockPulseApi.mockResolvedValueOnce(
      rawIntegrityBody({
        healthy: false,
        findings: [],
        checks: {
          cross_owner_edges: "clean",
          portfolio_projection_drift: "inconclusive"
        },
        totals: {
          findings: 0,
          invariant_violations: 0,
          checks_run: 5,
          checks_total: 6,
          inconclusive: ["portfolio_projection_drift"],
          truncated: false,
          complete: false
        }
      })
    );
    const result = await getCapitalIntegrity();
    if (result.state !== "READY") throw new Error("expected READY");

    expect(result.integrity.findings).toHaveLength(0);
    expect(result.integrity.healthy).toBe(false);
    expect(result.integrity.totals.inconclusive).toEqual(["portfolio_projection_drift"]);
    expect(result.integrity.totals.complete).toBe(false);
    expect(result.integrity.checks.portfolio_projection_drift).toBe("inconclusive");
  });

  it("keeps `healthy` tri-state — an unsaid answer is null, not false", async () => {
    mockPulseApi.mockResolvedValueOnce(rawIntegrityBody({ healthy: null }));
    const withheld = await getCapitalIntegrity();
    if (withheld.state !== "READY") throw new Error("expected READY");
    expect(withheld.integrity.healthy).toBeNull();

    // A truthy non-boolean must not be promoted into a clean bill of health.
    mockPulseApi.mockResolvedValueOnce(rawIntegrityBody({ healthy: "yes" }));
    const bogus = await getCapitalIntegrity();
    if (bogus.state !== "READY") throw new Error("expected READY");
    expect(bogus.integrity.healthy).toBeNull();
    expect(bogus.integrity.healthy).not.toBe(true);
  });

  it("carries findings with their severity, and counts invariants separately", async () => {
    mockPulseApi.mockResolvedValueOnce(
      rawIntegrityBody({
        healthy: false,
        findings: [
          {
            check: "cross_owner_edges",
            subject: "edge",
            subject_id: "edge:44",
            detail: "edge joins a node owned by another member",
            severity: "invariant"
          },
          {
            check: "portfolio_projection_drift",
            subject: "node",
            subject_id: "node:12",
            detail: "projected amount differs from the ledger",
            severity: "drift"
          }
        ],
        totals: {
          findings: 2,
          invariant_violations: 1,
          checks_run: 6,
          checks_total: 6,
          inconclusive: [],
          truncated: false,
          complete: true
        }
      })
    );
    const result = await getCapitalIntegrity();
    if (result.state !== "READY") throw new Error("expected READY");

    expect(result.integrity.healthy).toBe(false);
    expect(result.integrity.findings.map((f) => f.severity)).toEqual(["invariant", "drift"]);
    expect(result.integrity.findings[0].subjectId).toBe("edge:44");
    expect(result.integrity.totals.invariantViolations).toBe(1);
  });

  it("reports totals.findings from the wire even when the list was truncated", async () => {
    mockPulseApi.mockResolvedValueOnce(
      rawIntegrityBody({
        healthy: false,
        findings: [
          {
            check: "orphan_edges",
            subject: "edge",
            subject_id: "edge:1",
            detail: "dangling",
            severity: "invariant"
          }
        ],
        totals: {
          findings: 500,
          invariant_violations: 500,
          checks_run: 6,
          checks_total: 6,
          inconclusive: [],
          truncated: true,
          complete: false
        }
      })
    );
    const result = await getCapitalIntegrity();
    if (result.state !== "READY") throw new Error("expected READY");

    expect(result.integrity.findings).toHaveLength(1);
    expect(result.integrity.totals.findings).toBe(500);
    expect(result.integrity.totals.truncated).toBe(true);
    expect(result.integrity.totals.complete).toBe(false);
  });

  it("maps the shared refusals", async () => {
    mockPulseApi.mockRejectedValueOnce(
      apiError(403, { state: "DENIED", reason: { reason: "actor_is_not_owner" } })
    );
    expect(await getCapitalIntegrity()).toEqual({
      state: "DENIED",
      reason: "actor_is_not_owner"
    });

    mockPulseApi.mockRejectedValueOnce(apiError(423, { setup_required: true }));
    expect(await getCapitalIntegrity()).toEqual({ state: "LOCKED", setupRequired: true });

    mockPulseApi.mockRejectedValueOnce(apiError(503, {}));
    expect(await getCapitalIntegrity()).toEqual({ state: "UNAVAILABLE" });
  });
});
