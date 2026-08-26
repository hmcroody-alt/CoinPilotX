/**
 * The client is not an authority on what an alert may say.
 *
 * Two failure modes are worth a test file. The first is the client deciding for
 * itself that a window, a comparator or a Premium capability is available — the
 * whole point of `/api/crypto/alerts/options` is that those answers move, so a
 * malformed or missing response must read as "nothing on offer", never as
 * "everything on offer". The second is the request saying two different things
 * at once: a compound rule that also carries a basic threshold, or a watchlist
 * rule that also carries a ticker, leaves the server holding two answers to
 * "what is this alert about" and free to pick the wrong one.
 */

const mockPulseApi = jest.fn();
const mockReadJsonCache = jest.fn();
const mockWriteJsonCache = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

jest.mock("../../core/cache", () => ({
  readJsonCache: (...args: unknown[]) => mockReadJsonCache(...args),
  writeJsonCache: (...args: unknown[]) => mockWriteJsonCache(...args)
}));

import {
  alertConditionLabel,
  alertSubjectLabel,
  createCryptoAlert,
  getCryptoAlertOptions,
  normalizeAlertOptions,
  type AlertFormPayload
} from "../alerts";

const baseForm: AlertFormPayload = {
  assetSymbol: "BTC",
  targetValue: "",
  condition: "above",
  notifyInApp: true,
  notifyEmail: false,
  notifyPush: false,
  notifySMS: false,
  notifyTelegram: false,
  mode: "basic",
  logic: "and",
  clauses: [],
  watchlistId: null
};

/** The body the client actually posted, parsed back out of the request. */
function postedBody() {
  const [, init] = mockPulseApi.mock.calls[0];
  return JSON.parse(String((init as { body?: string }).body || "{}"));
}

beforeEach(() => {
  mockPulseApi.mockReset();
  mockReadJsonCache.mockReset();
  mockWriteJsonCache.mockReset();
  mockPulseApi.mockResolvedValue({ ok: true, alert_id: 7, message: "Alert created." });
});

describe("An options response that did not arrive means nothing is on offer", () => {
  it("reads an empty response as locked, not as unlimited", () => {
    // The dangerous default. `locked: false` has to be stated; anything else —
    // absent, null, a string, a failed parse — is a locked account.
    const options = normalizeAlertOptions({});
    expect(options.advanced.locked).toBe(true);
    expect(options.premium).toBe(false);
    expect(options.windows).toEqual([]);
    expect(options.watchlists).toEqual([]);
  });

  it("only unlocks on an explicit false", () => {
    expect(normalizeAlertOptions({ advanced: { locked: false } as never }).advanced.locked).toBe(false);
    expect(normalizeAlertOptions({ advanced: {} as never }).advanced.locked).toBe(true);
  });

  it("discards a window with no real duration rather than rendering it", () => {
    // A zero-minute window is how "no window" is spelled inside a clause. One
    // arriving in the offered list would put a button on screen that selects
    // the absence of the thing the button claims to select.
    const options = normalizeAlertOptions({
      windows: [{ minutes: 0, label: "0m" }, { minutes: 60, label: "1h" }] as never
    });
    expect(options.windows).toEqual([{ minutes: 60, label: "1h" }]);
  });

  it("keeps the server's window order instead of sorting it again", () => {
    // Coverage decides which windows are answerable and in what order they are
    // offered. Re-sorting here would be a second opinion about a list the
    // server already ordered.
    const options = normalizeAlertOptions({
      windows: [{ minutes: 60, label: "1h" }, { minutes: 15, label: "15m" }] as never
    });
    expect(options.windows.map((window) => window.minutes)).toEqual([60, 15]);
  });

  it("treats a watchlist with no eligibility stated as ineligible", () => {
    const options = normalizeAlertOptions({
      watchlists: [{ id: 4, name: "Majors" }] as never
    });
    expect(options.watchlists[0].eligible).toBe(false);
  });
});

describe("The options request asks about one subject", () => {
  it("asks about the watchlist and drops the symbol when a list is chosen", async () => {
    // A list rule is about no single asset. Sending both would ask the coverage
    // check two questions and let it answer whichever it read last.
    mockPulseApi.mockResolvedValue({ ok: true });
    await getCryptoAlertOptions("BTC", 12);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/crypto/alerts/options?watchlistId=12");
  });

  it("asks about the symbol when no list is chosen", async () => {
    mockPulseApi.mockResolvedValue({ ok: true });
    await getCryptoAlertOptions("sol", null);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/crypto/alerts/options?symbol=SOL");
  });

  it("asks about nothing at all rather than sending an empty symbol", async () => {
    // No asset named is a real question with a real answer ("choose an asset"),
    // and it must not arrive looking like a question about the empty ticker.
    mockPulseApi.mockResolvedValue({ ok: true });
    await getCryptoAlertOptions("", null);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/crypto/alerts/options");
  });
});

describe("A compound rule reaches the engine as one rule", () => {
  it("sends the clauses and the logic mode", async () => {
    await createCryptoAlert({
      ...baseForm,
      mode: "advanced",
      logic: "or",
      clauses: [
        { metric: "price", comparator: "above", value: "61000", windowMinutes: 0 },
        { metric: "change_24h", comparator: "below", value: "-5", windowMinutes: 60 }
      ]
    });
    const body = postedBody();
    expect(body.logic).toBe("or");
    expect(body.conditions).toEqual([
      { metric: "price", comparator: "above", value: 61000 },
      { metric: "change_24h", comparator: "below", value: -5, window_minutes: 60 }
    ]);
  });

  it("omits the window key entirely rather than sending a zero", async () => {
    // The server treats a window as part of a clause's identity, so an explicit
    // zero and an absent key must not become two spellings of the same clause.
    await createCryptoAlert({
      ...baseForm,
      mode: "advanced",
      clauses: [{ metric: "price", comparator: "above", value: "61000", windowMinutes: 0 }]
    });
    expect(postedBody().conditions[0]).not.toHaveProperty("window_minutes");
  });

  it("keeps a negative percentage negative", async () => {
    // A 24h change is genuinely negative half the time. Coercing it positive
    // would silently invert the rule the member wrote.
    await createCryptoAlert({
      ...baseForm,
      mode: "advanced",
      clauses: [{ metric: "change_24h", comparator: "below", value: "-12.5", windowMinutes: 0 }]
    });
    expect(postedBody().conditions[0].value).toBe(-12.5);
  });

  it("sends no clauses at all for a basic rule", async () => {
    // The free single-threshold path has to keep posting exactly what it always
    // posted, or every existing basic alert becomes a compound one.
    await createCryptoAlert({ ...baseForm, targetValue: "61000", condition: "above" });
    const body = postedBody();
    expect(body.conditions).toBeUndefined();
    expect(body.logic).toBeUndefined();
    expect(body.condition).toBe("above");
    expect(body.threshold).toBe("61000");
  });

  it("sends no clauses when advanced was selected but nothing was written", async () => {
    // A mode with an empty clause list is not a compound rule; sending
    // `conditions: []` would ask the server to evaluate a rule with no
    // conditions in it.
    await createCryptoAlert({ ...baseForm, mode: "advanced", clauses: [], targetValue: "61000" });
    expect(postedBody().conditions).toBeUndefined();
  });
});

describe("A watchlist rule carries no ticker", () => {
  it("posts the list and blanks every symbol field", async () => {
    await createCryptoAlert({ ...baseForm, watchlistId: 12, targetValue: "61000" });
    const body = postedBody();
    expect(body.watchlistId).toBe(12);
    expect(body.symbol).toBe("");
    expect(body.asset_symbol).toBe("");
    expect(body.assetSymbol).toBe("");
  });

  it("still posts the symbol when no list is chosen", async () => {
    await createCryptoAlert({ ...baseForm, assetSymbol: "sol", targetValue: "200" });
    expect(postedBody().symbol).toBe("SOL");
  });
});

describe("A rule is described the way the engine describes it", () => {
  it("prefers the server's summary over the first clause", () => {
    // A compound rule keeps its first clause in condition/threshold so older
    // readers see something true. Rendering only that would name one condition
    // as though it were the whole rule.
    expect(alertConditionLabel({
      id: 1,
      is_advanced: true,
      condition: "above",
      threshold: 61000,
      condition_summary: "price above 61,000 and 24h change below -5%"
    })).toBe("price above 61,000 and 24h change below -5%");
  });

  it("falls back to the single condition when there is no summary", () => {
    expect(alertConditionLabel({ id: 1, condition: "above", threshold: 61000 })).toBe("Above 61000");
  });

  it("names a watchlist rule by its list, not by an empty symbol", () => {
    expect(alertSubjectLabel({ id: 1, is_watchlist_rule: true, watchlist_id: 4, watchlist_name: "Majors" }))
      .toBe("Majors watchlist");
  });

  it("still names a list rule whose name did not arrive", () => {
    // Better a generic word than the leading space an empty symbol leaves.
    expect(alertSubjectLabel({ id: 1, is_watchlist_rule: true, watchlist_id: 4 })).toBe("Watchlist");
  });

  it("names a single-asset rule by its asset", () => {
    expect(alertSubjectLabel({ id: 1, asset_symbol: "SOL" })).toBe("SOL");
  });
});
