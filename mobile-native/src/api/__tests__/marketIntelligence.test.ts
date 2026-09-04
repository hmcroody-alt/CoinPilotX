/**
 * The client is not an authority on what the analysis found.
 *
 * Every number in this layer is a claim the server made, and the one thing the
 * client can do wrong is turn a claim it did not receive into one it renders.
 * There are three shapes of that mistake and each gets a test here.
 *
 * The first is substituting zero for absence. A missing opportunity score and a
 * score of zero are the same pixel once they are both a number, and only one of
 * them is true — so `num()` must return null and never 0.
 *
 * The second is defaulting an unknown field to confidence KNOWN. This whole
 * feature exists to separate what was measured from what was inferred from what
 * could not be seen, and a version boundary where the server grows a value this
 * client does not recognise must degrade toward UNAVAILABLE, not toward fact.
 *
 * The third is precision. A support level of 0.00004182 rendered to two decimals
 * is 0.00, which is not a rounded number — it is a different claim.
 */

const mockPulseApi = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

import {
  UNKNOWN_READING,
  formatLevel,
  formatPct,
  formatRatio,
  formatScore,
  getAssetIntelligence,
  humanizeState,
  normalizeIntelligence,
  normalizeIntelligenceDetail,
  normalizeRegime,
  normalizeRotation
} from "../marketIntelligence";

beforeEach(() => {
  mockPulseApi.mockReset();
});

// ---------------------------------------------------------------------------
// Absence
// ---------------------------------------------------------------------------

describe("a row with no analysis", () => {
  it("is null rather than a cautious verdict", () => {
    expect(normalizeIntelligence(null)).toBeNull();
    expect(normalizeIntelligence(undefined)).toBeNull();
    expect(normalizeIntelligence("nope")).toBeNull();
  });

  it("is distinguishable from a row whose scores could not be computed", () => {
    const verdict = normalizeIntelligence({
      symbol: "TEST",
      action: { state: "DATA_UNAVAILABLE", label: "Data unavailable", tone: "muted" },
      opportunity: { score: null },
      entry: { score: null },
      risk: { level: null }
    });
    // Not null — the server did answer. It answered that it could not tell.
    expect(verdict).not.toBeNull();
    expect(verdict?.action.state).toBe("DATA_UNAVAILABLE");
    expect(verdict?.opportunity.score).toBeNull();
  });
});

describe("missing numbers", () => {
  it("stay missing rather than becoming zero", () => {
    const verdict = normalizeIntelligence({
      symbol: "TEST",
      action: { state: "WAIT", label: "Wait", tone: "caution" },
      opportunity: {},
      entry: { score: null },
      risk: {}
    });
    expect(verdict?.opportunity.score).toBeNull();
    expect(verdict?.entry.score).toBeNull();
    expect(verdict?.risk.level).toBeNull();
  });

  it("survive a value that is not a number at all", () => {
    const verdict = normalizeIntelligence({
      symbol: "TEST",
      action: { state: "WAIT", label: "Wait", tone: "caution" },
      opportunity: { score: "high" },
      entry: { score: NaN },
      risk: { level: "SEVERE" }        // not one of the four declared levels
    });
    expect(verdict?.opportunity.score).toBeNull();
    expect(verdict?.entry.score).toBeNull();
    expect(verdict?.risk.level).toBeNull();
  });

  it("read as -- on screen and never as a figure", () => {
    expect(formatScore(null)).toBe(UNKNOWN_READING);
    expect(formatPct(null)).toBe(UNKNOWN_READING);
    expect(formatRatio(null)).toBe(UNKNOWN_READING);
    expect(formatLevel(null)).toBe(UNKNOWN_READING);
  });

  it("does not confuse a real zero with a missing one", () => {
    expect(formatScore(0)).not.toBe(UNKNOWN_READING);
    expect(formatPct(0)).not.toBe(UNKNOWN_READING);
  });
});

// ---------------------------------------------------------------------------
// Confidence
// ---------------------------------------------------------------------------

describe("confidence at a version boundary", () => {
  it("degrades an unrecognised value to UNAVAILABLE, never to KNOWN", () => {
    const verdict = normalizeIntelligence({
      symbol: "TEST",
      action: { state: "WAIT", label: "Wait", tone: "caution" },
      opportunity: { score: 61, confidence: "PRETTY_SURE" },
      entry: { score: 40, confidence: null },
      risk: { level: "MODERATE", confidence: 7 }
    });
    expect(verdict?.opportunity.confidence).toBe("UNAVAILABLE");
    expect(verdict?.entry.confidence).toBe("UNAVAILABLE");
    expect(verdict?.risk.confidence).toBe("UNAVAILABLE");
  });

  it("passes through the three it does recognise", () => {
    const verdict = normalizeIntelligence({
      symbol: "TEST",
      action: { state: "HOLD", label: "Hold", tone: "neutral" },
      opportunity: { score: 61, confidence: "KNOWN" },
      entry: { score: 40, confidence: "INFERRED" },
      risk: { level: "LOW", confidence: "UNAVAILABLE" }
    });
    expect(verdict?.opportunity.confidence).toBe("KNOWN");
    expect(verdict?.entry.confidence).toBe("INFERRED");
    expect(verdict?.risk.confidence).toBe("UNAVAILABLE");
  });
});

// ---------------------------------------------------------------------------
// The two depths
// ---------------------------------------------------------------------------

describe("the drill-down payload", () => {
  const detail = {
    symbol: "TEST",
    ok: true,
    personalized: true,
    action: { state: "WAIT_FOR_PULLBACK", label: "Wait for pullback", tone: "caution" },
    opportunity: { score: 93, band: "HIGH", confidence: "KNOWN" },
    entry: { score: 39, band: "LOW", confidence: "KNOWN" },
    risk: { level: "HIGH", confidence: "KNOWN" },
    why: {
      action: [{ code: "extended", text: "Price is 2.9 sd above its 3-day mean.", confidence: "KNOWN" }],
      opportunity: [{ code: "aligned", text: "All four timeframes point up.", confidence: "KNOWN" }],
      entry: [{ code: "chase", text: "Buying here is buying the extension.", confidence: "INFERRED" }]
    }
  };

  /** Narrowed once, because a null here would be its own failure. */
  function parse(value: unknown) {
    const parsed = normalizeIntelligenceDetail(value);
    expect(parsed).not.toBeNull();
    return parsed!;
  }

  it("keeps the three reason lists apart under whyDetail", () => {
    const parsed = parse(detail);
    expect(parsed.whyDetail.action).toHaveLength(1);
    expect(parsed.whyDetail.opportunity).toHaveLength(1);
    expect(parsed.whyDetail.entry).toHaveLength(1);
  });

  it("rebuilds the flat row-shaped why from the action reasons", () => {
    // The two depths reuse one key for two shapes. A component that had to
    // type-test at render time would eventually ship an empty section, so the
    // normalizer resolves it once, here.
    const parsed = parse(detail);
    expect(Array.isArray(parsed.why)).toBe(true);
    expect(parsed.why[0].text).toContain("3-day mean");
  });

  it("does not claim personalization the server did not grant", () => {
    const parsed = parse({ ...detail, personalized: false, ok: false });
    expect(parsed.personalized).toBe(false);
    expect(parsed.ok).toBe(false);
  });

  it("survives a completely empty response without throwing", () => {
    const parsed = parse({});
    expect(parsed.ok).toBe(false);
    expect(parsed.whyDetail.action).toEqual([]);
    expect(parsed.opportunity.score).toBeNull();
  });

  it("returns null when the response was not an object at all", () => {
    expect(normalizeIntelligenceDetail(null)).toBeNull();
  });
});

describe("the drill-down request", () => {
  it("asks for the symbol in upper case and carries no risk budget by default", async () => {
    mockPulseApi.mockResolvedValue({ symbol: "BTC", ok: true });
    await getAssetIntelligence("btc");
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/market/assets/BTC/intelligence");
  });

  it("passes a risk budget through when one was chosen", async () => {
    mockPulseApi.mockResolvedValue({ symbol: "BTC", ok: true });
    await getAssetIntelligence("BTC", 2.5);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/market/assets/BTC/intelligence?riskBudgetPct=2.5");
  });
});

// ---------------------------------------------------------------------------
// Board-level readings
// ---------------------------------------------------------------------------

describe("market regime", () => {
  it("is null when the board was too small to measure it", () => {
    expect(normalizeRegime(null)).toBeNull();
    expect(normalizeRegime(undefined)).toBeNull();
  });

  it("keeps breadth null rather than reporting a zero-percent market", () => {
    const regime = normalizeRegime({ state: "NEUTRAL", label: "Mixed", detail: "", basis: "" });
    expect(regime?.breadthPct).toBeNull();
    expect(regime?.advancers).toBeNull();
  });
});

describe("rotation", () => {
  it("is null when absent and empty-grouped when present but unresolved", () => {
    expect(normalizeRotation(null)).toBeNull();
    const rotation = normalizeRotation({ groups: [], leader: null, basis: "board", confidence: "INFERRED" });
    expect(rotation?.groups).toEqual([]);
    expect(rotation?.leader).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Rendering claims
// ---------------------------------------------------------------------------

describe("formatLevel", () => {
  it("keeps enough precision for a sub-cent asset to still be a price", () => {
    // 0.00 is not a rounded 0.00004182; it is a different claim entirely.
    expect(formatLevel(0.00004182)).not.toBe("0.00");
    expect(formatLevel(0.00004182)).toContain("0.0000");
  });

  it("does not pad a large level with meaningless decimals", () => {
    expect(formatLevel(64230)).not.toContain(".");
  });
});

describe("formatPct", () => {
  it("signs a gain so the direction survives being read alone", () => {
    expect(formatPct(3.2).startsWith("+")).toBe(true);
    expect(formatPct(-3.2).startsWith("-")).toBe(true);
  });
});

describe("humanizeState", () => {
  it("turns a wire constant into a sentence fragment a person reads", () => {
    expect(humanizeState("WAIT_FOR_PULLBACK")).toBe("Wait for pullback");
    expect(humanizeState("HOLD")).toBe("Hold");
  });
});
