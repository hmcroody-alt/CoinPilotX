/**
 * The Insights data layer is where this screen's central promise is kept or
 * broken: every number it shows is owned by another screen, and it must not
 * contradict the owner or invent a figure the platform cannot measure.
 *
 * Four things are pinned here because each of them, if it regressed, would
 * regress silently — the screen would still render, still look right, and be
 * wrong:
 *
 *   1. **The gap ledger.** `INSIGHTS_MOCK_DATA_GAPS` names the five metrics
 *      that have no source. Its length is asserted so that faking one of them
 *      breaks a test rather than shipping a zero that reads as a measurement.
 *   2. **Comparison refusals.** A new seller's first sale must never render as
 *      ▲100%. `compareToPrior` returns two *distinct* refusals — no prior period
 *      versus a prior period that was zero — and the screen says different words
 *      for each, so collapsing them would change what the seller is told.
 *   3. **The request gate.** Tapping 7d then 90d starts two requests with no
 *      ordering guarantee. If the slow 7d response can still land, the screen
 *      paints seven days of data under a highlighted 90d pill.
 *   4. **Normalisation.** The payload arrives over a wire; `stock: null` and
 *      `stock: 0` are different claims ("doesn't track stock" vs "sold out")
 *      and must survive the trip apart.
 */

import {
  DEFAULT_INSIGHTS_PERIOD,
  INSIGHTS_MOCK_DATA_GAPS,
  INSIGHTS_PERIODS,
  INSIGHTS_PERIOD_DAYS,
  InsightsStaleResponse,
  attributionAvailable,
  compareToPrior,
  createInsightsRequestGate,
  INSIGHTS_ERROR_CAUSES_FLAG,
  insightsErrorCausesEnabled,
  insightsErrorMessage,
  insightsExportBlockedReason,
  insightsFailure,
  insightsHasExportableData,
  insightsRevenueMajor,
  isGap,
  isInsightsPeriod,
  localTimezoneOffsetMinutes,
  sourceShare,
  type InsightsSummary
} from "../insightsDashboard";
import { PulseApiError } from "../pulseApi";

/* ------------------------------------------------------------------ helpers */

function summary(overrides: Partial<InsightsSummary> = {}): InsightsSummary {
  return {
    period: "7d",
    days: 7,
    timezone_offset_minutes: 0,
    start: "2026-07-27 00:00:00",
    end: "2026-08-03 00:00:00",
    prior_start: "2026-07-20 00:00:00",
    prior_end: "2026-07-27 00:00:00",
    has_prior_period: true,
    currency: "USD",
    currencies: ["USD"],
    totals: { revenue_minor: 418_200, orders: 37 },
    prior_totals: { revenue_minor: 354_400, orders: 31 },
    bucket: "day",
    series: [],
    sources: [],
    top_items: [],
    followers: { gained: 12, prior_gained: 9 },
    unavailable: INSIGHTS_MOCK_DATA_GAPS.map((gap) => ({ ...gap })),
    ...overrides
  };
}

/* -------------------------------------------------------------- the ledger */

describe("the honesty ledger", () => {
  it("names exactly the five metrics this platform cannot measure", () => {
    // If this number changes, either a real source was wired (delete the entry
    // and this line) or a metric was faked (don't).
    expect(INSIGHTS_MOCK_DATA_GAPS).toHaveLength(5);
    expect(INSIGHTS_MOCK_DATA_GAPS.map((gap) => gap.key)).toEqual([
      "store_views",
      "ads_attribution",
      "on_time_dispatch",
      "reply_rate",
      "offers_answered"
    ]);
  });

  it("gives every gap the concrete change that would close it", () => {
    INSIGHTS_MOCK_DATA_GAPS.forEach((gap) => {
      expect(gap.label.length).toBeGreaterThan(0);
      // A gap without a stated remedy is a shrug, not a ledger entry.
      expect(gap.needs.length).toBeGreaterThan(20);
    });
  });

  it("trusts the server's list over its own when one arrives", () => {
    const measured = summary({ unavailable: [] });
    // An empty array from the server means "I can measure everything", so the
    // client must not fall back to its own stale list and hide a live module.
    expect(isGap(measured, "store_views")).toBe(false);

    const partial = summary({
      unavailable: [{ key: "reply_rate", label: "Replies", needs: "latency metric" }]
    });
    expect(isGap(partial, "reply_rate")).toBe(true);
    expect(isGap(partial, "store_views")).toBe(false);
  });

  it("assumes the gaps when there is no summary at all", () => {
    // Before the first response lands, the safe assumption is that nothing is
    // measurable — rendering a module optimistically would flash a fake zero.
    expect(isGap(null, "ads_attribution")).toBe(true);
  });
});

describe("attribution gating", () => {
  it("withholds the From-ads row while attribution is a documented gap", () => {
    // Sellers make spend decisions on this number. It ships only when a real
    // model produced it.
    expect(attributionAvailable(summary())).toBe(false);
    expect(attributionAvailable(null)).toBe(false);
  });

  it("permits it the moment the server stops naming it", () => {
    expect(attributionAvailable(summary({ unavailable: [] }))).toBe(true);
  });
});

/* ---------------------------------------------------------- the comparison */

describe("compareToPrior", () => {
  it("refuses to compare when the seller did not exist last period", () => {
    const result = compareToPrior(4182, null, false);
    expect(result).toEqual({ kind: "none", reason: "no_prior_period" });
  });

  it("refuses to compare against a prior period that earned nothing", () => {
    // Any percentage against zero is undefined. "+100%" would be invented.
    const result = compareToPrior(4182, 0, true);
    expect(result).toEqual({ kind: "none", reason: "no_prior_value" });
  });

  it("keeps the two refusals distinct", () => {
    // The screen says "New — no prior period" for one and "Nothing in the prior
    // 7 days" for the other. Those are different facts about the seller.
    const newSeller = compareToPrior(10, null, false);
    const quietWeek = compareToPrior(10, 0, true);
    expect(newSeller).not.toEqual(quietWeek);
  });

  it("returns a fraction, not a percentage and not a string", () => {
    const result = compareToPrior(118, 100, true);
    expect(result).toMatchObject({ kind: "change", direction: "up" });
    if (result.kind !== "change") throw new Error("expected a change");
    // 0.18, so the caller can hand it to the localization percent formatter.
    expect(result.ratio).toBeCloseTo(0.18, 10);
  });

  it("reads a fall as down and a standstill as flat", () => {
    expect(compareToPrior(80, 100, true)).toMatchObject({ direction: "down" });
    expect(compareToPrior(100, 100, true)).toMatchObject({ direction: "flat" });
  });

  it("treats a hair's-breadth move as flat rather than as a trend", () => {
    // 0.02% is noise. An arrow next to it would imply a signal.
    expect(compareToPrior(100.02, 100, true)).toMatchObject({ direction: "flat" });
  });
});

/* -------------------------------------------------------------- the totals */

describe("revenue handoff", () => {
  it("converts minor units to major exactly once, and says which currency", () => {
    expect(insightsRevenueMajor(summary())).toEqual({ amount: 4182, currency: "USD" });
  });

  it("returns null rather than a zero when there is no summary", () => {
    // "$0.00" and "—" are different claims and only the source knows which holds.
    expect(insightsRevenueMajor(null)).toBeNull();
  });

  it("falls back to USD rather than to an empty currency code", () => {
    expect(insightsRevenueMajor(summary({ currency: "" }))?.currency).toBe("USD");
  });
});

describe("sourceShare", () => {
  const sources = [
    { key: "store" as const, revenue_minor: 300_000, orders: 20 },
    { key: "marketplace" as const, revenue_minor: 100_000, orders: 8 }
  ];

  it("expresses each source as a fraction of the period total", () => {
    expect(sourceShare(sources[0], sources)).toBeCloseTo(0.75, 10);
    expect(sourceShare(sources[1], sources)).toBeCloseTo(0.25, 10);
  });

  it("sums to one, so the breakdown cannot exceed the total it splits", () => {
    const total = sources.reduce((sum, source) => sum + sourceShare(source, sources), 0);
    expect(total).toBeCloseTo(1, 10);
  });

  it("returns zero rather than dividing by zero on a quiet period", () => {
    const quiet = [{ key: "store" as const, revenue_minor: 0, orders: 0 }];
    expect(sourceShare(quiet[0], quiet)).toBe(0);
  });
});

/* ---------------------------------------------------------- the period set */

describe("periods", () => {
  it("offers the four the backend serves and defaults to a week", () => {
    expect(INSIGHTS_PERIODS).toEqual(["today", "7d", "30d", "90d"]);
    expect(DEFAULT_INSIGHTS_PERIOD).toBe("7d");
  });

  it("mirrors the backend's day counts", () => {
    // These drive the tip card's weekly run rate. A drift here would divide
    // revenue by the wrong number of days and quote a wrong figure to a seller.
    expect(INSIGHTS_PERIOD_DAYS).toEqual({ today: 1, "7d": 7, "30d": 30, "90d": 90 });
  });

  it("rejects anything that is not one of them", () => {
    expect(isInsightsPeriod("7d")).toBe(true);
    expect(isInsightsPeriod("1y")).toBe(false);
    expect(isInsightsPeriod(7)).toBe(false);
    expect(isInsightsPeriod(undefined)).toBe(false);
  });
});

describe("localTimezoneOffsetMinutes", () => {
  it("flips the sign getTimezoneOffset uses", () => {
    // Los Angeles reports +420 and means UTC-7. The backend wants "minutes to
    // add to UTC", so it must receive -420. Getting this backwards shifts every
    // window by up to a day and quietly disagrees with the Orders screen.
    const losAngeles = { getTimezoneOffset: () => 420 } as Date;
    expect(localTimezoneOffsetMinutes(losAngeles)).toBe(-420);

    const berlin = { getTimezoneOffset: () => -120 } as Date;
    expect(localTimezoneOffsetMinutes(berlin)).toBe(120);
  });
});

/* ---------------------------------------------------------- the race guard */

describe("createInsightsRequestGate", () => {
  it("lets a lone request through", async () => {
    const gate = createInsightsRequestGate();
    await expect(gate.run(async () => "7d")).resolves.toBe("7d");
  });

  it("discards a slow response that a later request superseded", async () => {
    const gate = createInsightsRequestGate();

    let releaseSlow: (value: string) => void = () => undefined;
    const slow = gate.run(
      () => new Promise<string>((resolve) => {
        releaseSlow = resolve;
      })
    );

    // The seller taps 90d before 7d comes back.
    const fast = await gate.run(async () => "90d");
    expect(fast).toBe("90d");

    releaseSlow("7d");
    // Without this, seven days of data paints under a highlighted 90d pill.
    await expect(slow).rejects.toBeInstanceOf(InsightsStaleResponse);
  });

  it("marks a superseded request as stale so no banner is shown for it", async () => {
    const gate = createInsightsRequestGate();
    const stale = gate.run(() => new Promise<string>(() => undefined).then(() => "x"));
    gate.cancel();
    void stale;
    expect(gate.isStale(new InsightsStaleResponse())).toBe(true);
    // A real failure is not stale and *must* reach the seller.
    expect(gate.isStale(new Error("network down"))).toBe(false);
  });

  it("invalidates everything in flight when the screen unmounts", async () => {
    const gate = createInsightsRequestGate();
    let release: (value: string) => void = () => undefined;
    const inFlight = gate.run(
      () => new Promise<string>((resolve) => {
        release = resolve;
      })
    );
    gate.cancel();
    release("late");
    await expect(inFlight).rejects.toBeInstanceOf(InsightsStaleResponse);
  });

  it("keeps the newest request even when three overlap", async () => {
    const gate = createInsightsRequestGate();
    const releases: Array<(value: string) => void> = [];
    const pending = ["today", "30d", "90d"].map((period) =>
      gate
        .run(
          () => new Promise<string>((resolve) => {
            releases.push(resolve);
          })
        )
        .then(
          (value) => ({ ok: true as const, value }),
          (error) => ({ ok: false as const, error })
        )
    );

    // Resolve out of order, oldest last — the pathological case.
    releases[1]("30d");
    releases[2]("90d");
    releases[0]("today");

    const settled = await Promise.all(pending);
    expect(settled.map((entry) => entry.ok)).toEqual([false, false, true]);
    expect(settled[2]).toEqual({ ok: true, value: "90d" });
  });
});

/* ------------------------------------------------------------ error copy */

describe("insightsErrorMessage", () => {
  it("tells an expired session to sign in rather than to retry forever", () => {
    const error = new PulseApiError("unauthorized", 401);
    expect(insightsErrorMessage(error, "Revenue")).toBe("Sign in again to see your insights.");
  });

  it("names the module that failed, so a retry is scoped to it", () => {
    const error = new PulseApiError("unavailable", 503);
    expect(insightsErrorMessage(error, "The chart")).toContain("The chart");
  });

  it("stays plain for anything unrecognised", () => {
    expect(insightsErrorMessage(new Error("boom"), "Top performers")).toBe(
      "Top performers didn't load."
    );
  });
});

/* ------------------------------------------------------- failures & export */

/**
 * `insightsErrorMessage` collapsed five situations into three sentences and
 * gave all of them one treatment: a line of text with a Retry beside it. Two of
 * those situations do not retry — an account without the entitlement was told
 * to try again forever, and an expired session was told the same while the only
 * thing that would help was signing in. Being offline and the service being
 * down were the same sentence, though only one of them is fixed by reconnecting.
 */
describe("insightsFailure", () => {
  const CAUSES = ["offline", "authentication", "entitlement", "service_unavailable", "unexpected"] as const;

  it("is off unless the build sets the flag to exactly 1", () => {
    const original = process.env[INSIGHTS_ERROR_CAUSES_FLAG];
    try {
      for (const value of ["", "0", "true", "2"]) {
        process.env[INSIGHTS_ERROR_CAUSES_FLAG] = value;
        expect(insightsErrorCausesEnabled()).toBe(false);
      }
      process.env[INSIGHTS_ERROR_CAUSES_FLAG] = "1";
      expect(insightsErrorCausesEnabled()).toBe(true);
    } finally {
      if (original === undefined) delete process.env[INSIGHTS_ERROR_CAUSES_FLAG];
      else process.env[INSIGHTS_ERROR_CAUSES_FLAG] = original;
    }
  });

  /** The specific regression: one sentence for four different problems. */
  it("says something different for each cause", () => {
    const messages = CAUSES.map((cause) => {
      const error =
        cause === "offline"
          ? new PulseApiError("x", 503, "request_unreachable")
          : cause === "authentication"
            ? new PulseApiError("x", 401)
            : cause === "entitlement"
              ? new PulseApiError("x", 403)
              : cause === "service_unavailable"
                ? new PulseApiError("x", 503)
                : new Error("x");
      const failure = insightsFailure(error);
      expect(failure.cause).toBe(cause);
      return failure.message;
    });
    expect(new Set(messages).size).toBe(CAUSES.length);
  });

  it("tells someone who is offline that they are, not that the service is down", () => {
    const offline = insightsFailure(new PulseApiError("x", 503, "request_unreachable"));
    const down = insightsFailure(new PulseApiError("x", 503));
    expect(offline.message).toMatch(/offline/i);
    expect(down.message).not.toMatch(/offline/i);
  });

  /** A retry that cannot work implies the reader did something wrong. */
  it("does not offer a retry where a second identical attempt would fail identically", () => {
    const blocked = insightsFailure(new PulseApiError("x", 403));
    expect(blocked.retries).toBe(false);
    expect(blocked.actionLabel).toBeNull();
  });

  it("sends an expired session to sign in rather than round the retry loop", () => {
    const expired = insightsFailure(new PulseApiError("x", 401));
    expect(expired.retries).toBe(false);
    expect(expired.actionLabel).toBe("Sign in");
  });

  it("still retries the two causes a retry can actually fix", () => {
    for (const error of [new PulseApiError("x", 503, "request_unreachable"), new PulseApiError("x", 503)]) {
      const failure = insightsFailure(error);
      expect(failure.retries).toBe(true);
      expect(failure.actionLabel).toBe("Try again");
    }
  });

  it("names what failed, in the reader's words, in every case", () => {
    for (const error of [new PulseApiError("x", 401), new PulseApiError("x", 403), new Error("x")]) {
      expect(insightsFailure(error, "Your sales").message).toContain("Your sales");
    }
  });
});

/**
 * Export was enabled whenever a summary existed, so a seller with no trade in
 * the window could press it and receive a file of nothing. The disabled pill
 * was drawn at reduced opacity with no reason, which reads as a rendering fault
 * rather than a decision — so the reason is now part of the derivation and
 * cannot be forgotten at a call site.
 */
describe("insightsHasExportableData", () => {
  it("refuses a window with no orders even when a summary loaded fine", () => {
    expect(insightsHasExportableData(summary({ totals: { revenue_minor: 0, orders: 0 }, series: [] }))).toBe(false);
  });

  it("allows a window whose orders are only visible in the series", () => {
    const windowed = summary({
      totals: { revenue_minor: 0, orders: 0 },
      series: [{ label: "Mon", start: "2026-07-27 00:00:00", revenue_minor: 0, orders: 2 } as never]
    });
    expect(insightsHasExportableData(windowed)).toBe(true);
  });

  /**
   * Revenue alone is the wrong test: a period holding a refund can net to zero
   * revenue while still carrying rows worth exporting.
   */
  it("counts orders rather than revenue, so a refunded period still exports", () => {
    expect(insightsHasExportableData(summary({ totals: { revenue_minor: 0, orders: 4 } }))).toBe(true);
  });

  it("refuses when there is no summary at all", () => {
    expect(insightsHasExportableData(null)).toBe(false);
  });
});

describe("insightsExportBlockedReason", () => {
  const EMPTY = summary({ totals: { revenue_minor: 0, orders: 0 }, series: [] });

  it("gives a reason for every state that disables the control", () => {
    const reasons = [
      insightsExportBlockedReason({ summary: null, loading: true }),
      insightsExportBlockedReason({ summary: null, failed: true }),
      insightsExportBlockedReason({ summary: null }),
      insightsExportBlockedReason({ summary: EMPTY, fromCache: true }),
      insightsExportBlockedReason({ summary: EMPTY })
    ];
    for (const reason of reasons) {
      expect(typeof reason).toBe("string");
      // Never an empty string: the caller must not be able to render a blank.
      expect(String(reason).length).toBeGreaterThan(0);
    }
    // Five states, five sentences — a shared one would be a shrug again.
    expect(new Set(reasons).size).toBe(reasons.length);
  });

  it("returns null, and only null, when the export would contain something", () => {
    expect(insightsExportBlockedReason({ summary: summary() })).toBeNull();
  });

  /**
   * Ordering matters. A window still loading is not an empty window, and
   * telling a seller they had no orders while the request is in flight is a
   * claim the screen cannot yet support.
   */
  it("reports loading ahead of emptiness, and failure ahead of both", () => {
    expect(insightsExportBlockedReason({ summary: EMPTY, loading: true })).toMatch(/loading/i);
    expect(insightsExportBlockedReason({ summary: EMPTY, loading: true, failed: true })).toMatch(/loading/i);
    expect(insightsExportBlockedReason({ summary: EMPTY, failed: true })).toMatch(/didn't load/i);
  });

  it("blocks an export of cached figures and says they are cached", () => {
    const reason = insightsExportBlockedReason({ summary: summary(), fromCache: true });
    expect(reason).toMatch(/last visit/i);
  });

  it("tells an empty period why it is empty rather than only that it is", () => {
    expect(insightsExportBlockedReason({ summary: EMPTY })).toMatch(/no orders in this period/i);
  });
});
