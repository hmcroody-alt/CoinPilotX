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
  CAPITAL_ENTITY_PATH,
  CAPITAL_GRAPH_PATH,
  CAPITAL_PORTFOLIO_PATH,
  getCapitalEntity,
  getCapitalGraph,
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
