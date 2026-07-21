import {
  clearPerfSamples,
  configurePerfTracing,
  getPerfSamples,
  isPerfTracingEnabled,
  recordDuration,
  setPerfContext,
  startSpan
} from "../perfTrace";

describe("perfTrace", () => {
  beforeEach(() => {
    // Start each test from a known state: disabled, no sink, empty buffer.
    configurePerfTracing({ enabled: false, sink: null });
    clearPerfSamples();
  });

  it("is a no-op when disabled with no sink", () => {
    expect(isPerfTracingEnabled()).toBe(false);
    const span = startSpan("screen.firstRender", { route: "Test" });
    span.end();
    recordDuration("api.request", 123);
    expect(getPerfSamples()).toHaveLength(0);
  });

  it("records durations and merges context when enabled", () => {
    setPerfContext({ appVersion: "0.1.0" });
    configurePerfTracing({ enabled: true });
    recordDuration("api.request", 42, { route: "/api/orders", method: "GET" });
    const [sample] = getPerfSamples();
    expect(sample.name).toBe("api.request");
    expect(sample.durationMs).toBe(42);
    expect(sample.attributes.route).toBe("/api/orders");
    expect(sample.attributes.appVersion).toBe("0.1.0");
  });

  it("routes samples to a registered sink even when the enabled flag is false", () => {
    const received: string[] = [];
    configurePerfTracing({ enabled: false, sink: (s) => received.push(s.name) });
    expect(isPerfTracingEnabled()).toBe(true);
    recordDuration("screen.interactive", 10);
    expect(received).toEqual(["screen.interactive"]);
  });

  it("drops attribute keys that could carry private content", () => {
    configurePerfTracing({ enabled: true });
    recordDuration("api.request", 1, { route: "/x", email: "a@b.com", authToken: "secret", queryText: "hi" });
    const [sample] = getPerfSamples();
    expect(sample.attributes.route).toBe("/x");
    expect(sample.attributes.email).toBeUndefined();
    expect(sample.attributes.authToken).toBeUndefined();
    expect(sample.attributes.queryText).toBeUndefined();
  });

  it("truncates long string attribute values", () => {
    configurePerfTracing({ enabled: true });
    recordDuration("api.request", 1, { route: "r".repeat(400) });
    const [sample] = getPerfSamples();
    expect(String(sample.attributes.route).length).toBe(120);
  });

  it("ignores a second end() call on the same span", () => {
    configurePerfTracing({ enabled: true });
    const span = startSpan("screen.firstRender");
    span.end();
    span.end();
    expect(getPerfSamples()).toHaveLength(1);
  });

  it("never throws when the sink throws", () => {
    configurePerfTracing({
      enabled: true,
      sink: () => {
        throw new Error("sink boom");
      }
    });
    expect(() => recordDuration("api.request", 5)).not.toThrow();
  });
});
