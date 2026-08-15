/**
 * §14 — discovery analytics.
 *
 * The requirement with teeth here is "no double-counting". An impression fires
 * from a viewability callback, and viewability callbacks fire again on every
 * scroll reversal, orientation change and re-render. Without de-duplication a
 * user who scrolls a carousel back and forth reports ten impressions of one
 * card, which does not make the metric noisy so much as it makes it wrong in a
 * direction that flatters the feature.
 *
 * The second requirement is that analytics can never break a render. A sink is
 * arbitrary third-party code called from inside a view callback; if it throws,
 * the throw must stop there.
 */
import {
  __countedDiscoveryImpressions,
  resetDiscoveryImpressions,
  setDiscoveryAnalyticsSink,
  trackDiscoveryEvent
} from "../analytics";

let events: Parameters<typeof trackDiscoveryEvent>[0][] = [];

beforeEach(() => {
  events = [];
  resetDiscoveryImpressions();
  setDiscoveryAnalyticsSink((event) => {
    events.push(event);
  });
});

afterEach(() => {
  setDiscoveryAnalyticsSink(null);
  resetDiscoveryImpressions();
});

describe("impression de-duplication", () => {
  it("counts a card impression once no matter how often viewability re-fires", () => {
    for (let i = 0; i < 5; i += 1) {
      trackDiscoveryEvent({ name: "card_impression", kind: "reels", slot: 0, target: "42", position: 1 });
    }

    expect(events).toHaveLength(1);
  });

  it("counts each card in a carousel separately", () => {
    trackDiscoveryEvent({ name: "card_impression", kind: "reels", slot: 0, target: "1" });
    trackDiscoveryEvent({ name: "card_impression", kind: "reels", slot: 0, target: "2" });

    expect(events).toHaveLength(2);
  });

  it("counts the same card in two different rows separately", () => {
    // Same reel, different feed position: two genuine impressions, and §14 asks
    // for feed position precisely so they can be told apart.
    trackDiscoveryEvent({ name: "card_impression", kind: "reels", slot: 0, target: "1" });
    trackDiscoveryEvent({ name: "card_impression", kind: "reels", slot: 2, target: "1" });

    expect(events).toHaveLength(2);
  });

  it("de-duplicates module impressions too", () => {
    trackDiscoveryEvent({ name: "module_impression", kind: "groups", slot: 1 });
    trackDiscoveryEvent({ name: "module_impression", kind: "groups", slot: 1 });

    expect(events).toHaveLength(1);
  });

  it("does not let a module impression suppress a card impression", () => {
    trackDiscoveryEvent({ name: "module_impression", kind: "groups", slot: 1 });
    trackDiscoveryEvent({ name: "card_impression", kind: "groups", slot: 1 });

    expect(events).toHaveLength(2);
  });
});

describe("interaction events", () => {
  it("never de-duplicates taps — two taps are two taps", () => {
    trackDiscoveryEvent({ name: "card_tap", kind: "reels", slot: 0, target: "42" });
    trackDiscoveryEvent({ name: "card_tap", kind: "reels", slot: 0, target: "42" });

    expect(events).toHaveLength(2);
  });

  it("carries the exact destination id, which is what §14 is for", () => {
    trackDiscoveryEvent({ name: "reel_opened", kind: "reels", slot: 3, target: "907", position: 2 });

    expect(events[0]).toMatchObject({ name: "reel_opened", target: "907", slot: 3, position: 2 });
  });

  it("records a recommendation-failure reason", () => {
    trackDiscoveryEvent({ name: "module_impression", kind: "groups", slot: -1, reason: "groups_unavailable" });

    expect(events[0]).toMatchObject({ reason: "groups_unavailable" });
  });
});

describe("resilience", () => {
  it("swallows a throwing sink so analytics cannot break a render", () => {
    setDiscoveryAnalyticsSink(() => {
      throw new Error("ingest exploded");
    });

    expect(() => trackDiscoveryEvent({ name: "card_tap", kind: "reels", slot: 0 })).not.toThrow();
  });

  it("falls back to the dev sink when the installed one is removed", () => {
    // `null` means "back to the default", not "drop events on the floor" — an
    // event with nowhere to go should still be visible in a dev console.
    const log = jest.spyOn(console, "log").mockImplementation(() => undefined);
    setDiscoveryAnalyticsSink(null);

    expect(() => trackDiscoveryEvent({ name: "card_tap", kind: "people", slot: 0 })).not.toThrow();
    expect(log).toHaveBeenCalled();

    log.mockRestore();
  });
});

describe("the reset boundary", () => {
  it("lets a card be counted again after a refresh", () => {
    // A pull-to-refresh rebuilds the modules, so the next impression of the same
    // card is a new one. `useHomeDiscovery` calls reset on every load for this.
    trackDiscoveryEvent({ name: "card_impression", kind: "reels", slot: 0, target: "1" });
    resetDiscoveryImpressions();
    trackDiscoveryEvent({ name: "card_impression", kind: "reels", slot: 0, target: "1" });

    expect(events).toHaveLength(2);
  });

  it("clears the counted set", () => {
    trackDiscoveryEvent({ name: "card_impression", kind: "reels", slot: 0, target: "1" });
    expect(__countedDiscoveryImpressions()).toHaveLength(1);

    resetDiscoveryImpressions();

    expect(__countedDiscoveryImpressions()).toEqual([]);
  });
});
