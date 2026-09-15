import { DEFAULT_STALE_AFTER_MS, describeAge, surfacePresentation } from "../freshness";

const base = {
  itemCount: 0,
  hasLoaded: true,
  isLoading: false,
  connectivity: "online" as const
};

describe("surfacePresentation", () => {
  describe("offline is not an error", () => {
    it("shows cached content offline instead of an error", () => {
      const result = surfacePresentation({
        ...base,
        itemCount: 20,
        ageMs: 12 * 60_000,
        connectivity: "offline",
        error: new Error("Network request failed")
      });
      expect(result.state).toBe("CONTENT");
      expect(result.notice).toBe("OFFLINE");
      expect(result.showSpinner).toBe(false);
    });

    it("reports the age of what it is showing so the notice can be specific", () => {
      const result = surfacePresentation({
        ...base,
        itemCount: 5,
        ageMs: 12 * 60_000,
        connectivity: "offline"
      });
      expect(result.ageMs).toBe(12 * 60_000);
      expect(result.fromCache).toBe(true);
    });

    it("errors only when offline with nothing cached", () => {
      const result = surfacePresentation({
        ...base,
        itemCount: 0,
        connectivity: "offline",
        error: new Error("Network request failed")
      });
      expect(result.state).toBe("ERROR");
      expect(result.notice).toBe("OFFLINE");
    });
  });

  describe("error and empty are mutually exclusive", () => {
    // The §91 mutation and the standing rule: a failed fetch is not an absence
    // of data. A surface that renders both leaves the reader unable to tell
    // whether to retry or to go post something.
    it("never returns EMPTY when there is an error", () => {
      const result = surfacePresentation({ ...base, itemCount: 0, error: new Error("boom") });
      expect(result.state).toBe("ERROR");
      expect(result.state).not.toBe("EMPTY");
    });

    it("only claims EMPTY after a completed load with no error", () => {
      expect(surfacePresentation({ ...base, itemCount: 0, hasLoaded: true }).state).toBe("EMPTY");
      expect(surfacePresentation({ ...base, itemCount: 0, hasLoaded: false }).state).toBe("LOADING");
      expect(surfacePresentation({ ...base, itemCount: 0, hasLoaded: true, isLoading: true }).state).toBe("LOADING");
    });

    it("is never in more than one state for any input combination", () => {
      for (const itemCount of [0, 3]) {
        for (const hasLoaded of [true, false]) {
          for (const isLoading of [true, false]) {
            for (const error of [undefined, new Error("x")]) {
              for (const connectivity of ["online", "degraded", "offline", "recovering"] as const) {
                const result = surfacePresentation({ itemCount, hasLoaded, isLoading, error, connectivity });
                expect(["LOADING", "CONTENT", "EMPTY", "ERROR"]).toContain(result.state);
                // A spinner may only ever replace content, never sit on top of it.
                if (result.showSpinner) expect(result.state).toBe("LOADING");
                if (result.state === "EMPTY") expect(error).toBeUndefined();
                if (result.state === "ERROR") expect(error).toBeDefined();
              }
            }
          }
        }
      }
    });
  });

  describe("a refresh never blanks the screen", () => {
    // §91: "reconnect clears the viewport". If a refresh in flight could return
    // LOADING while content is held, the list would unmount and the reader's
    // scroll position would go with it.
    it("keeps CONTENT while revalidating", () => {
      const result = surfacePresentation({ ...base, itemCount: 40, ageMs: 1_000, isLoading: true });
      expect(result.state).toBe("CONTENT");
      expect(result.notice).toBe("REFRESHING");
      expect(result.showSpinner).toBe(false);
    });

    it("keeps CONTENT when the refresh fails", () => {
      const result = surfacePresentation({ ...base, itemCount: 40, ageMs: 1_000, error: new Error("500") });
      expect(result.state).toBe("CONTENT");
    });
  });

  describe("staleness", () => {
    it("flags online content older than the threshold", () => {
      expect(surfacePresentation({ ...base, itemCount: 4, ageMs: DEFAULT_STALE_AFTER_MS + 1 }).notice).toBe("STALE");
      expect(surfacePresentation({ ...base, itemCount: 4, ageMs: DEFAULT_STALE_AFTER_MS - 1 }).notice).toBe("NONE");
    });

    it("honours a caller-supplied threshold", () => {
      expect(surfacePresentation({ ...base, itemCount: 4, ageMs: 5_000, staleAfterMs: 1_000 }).notice).toBe("STALE");
    });

    it("does not call unknown age stale", () => {
      // A legacy cache entry has no timestamp. Treating that as very old would
      // put a staleness banner over content written a second ago.
      const result = surfacePresentation({ ...base, itemCount: 4, ageMs: null });
      expect(result.notice).toBe("NONE");
      expect(result.ageMs).toBeNull();
      expect(result.fromCache).toBe(true);
    });

    it("treats live content as live", () => {
      const result = surfacePresentation({ ...base, itemCount: 4 });
      expect(result.fromCache).toBe(false);
      expect(result.notice).toBe("NONE");
    });

    it("prefers the offline notice over the stale one", () => {
      // Offline explains the staleness and tells the reader what to do about
      // it; "stale" alone reads like the app is choosing not to refresh.
      const result = surfacePresentation({
        ...base,
        itemCount: 4,
        ageMs: DEFAULT_STALE_AFTER_MS * 10,
        connectivity: "offline"
      });
      expect(result.notice).toBe("OFFLINE");
    });
  });

  it("treats degraded and recovering as usable, not as offline", () => {
    for (const connectivity of ["degraded", "recovering"] as const) {
      expect(surfacePresentation({ ...base, itemCount: 4, connectivity }).notice).toBe("NONE");
    }
  });
});

describe("describeAge", () => {
  it("returns parts rather than a sentence", () => {
    expect(describeAge(0)).toEqual({ unit: "now", value: 0 });
    expect(describeAge(12 * 60_000)).toEqual({ unit: "minutes", value: 12 });
    expect(describeAge(3 * 3_600_000)).toEqual({ unit: "hours", value: 3 });
    expect(describeAge(50 * 3_600_000)).toEqual({ unit: "days", value: 2 });
  });

  it("returns null for an unknown or nonsensical age", () => {
    expect(describeAge(null)).toBeNull();
    expect(describeAge(-1)).toBeNull();
    expect(describeAge(Number.NaN)).toBeNull();
  });

  it("does not format the string itself", () => {
    // Guards the i18n contract: if someone "simplifies" this back to returning
    // "12m ago", every non-English locale silently renders English.
    const source = require("fs").readFileSync(require.resolve("../freshness.ts"), "utf8");
    const body = source.slice(source.indexOf("export function describeAge"));
    expect(body).not.toContain("ago");
  });
});
