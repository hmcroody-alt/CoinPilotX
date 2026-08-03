/**
 * Tests for the hosted-events derivation layer — the model the Events screen
 * renders. Pinned outright:
 *
 * 1. COUNTDOWN reads the event's real start, ticks days/hours/minutes, switches
 *    to "Starting soon" inside the final hour, and hands off to live/ended once
 *    the window opens/closes.
 * 2. CAPACITY derives fill, the >90% "nearly full" amber threshold, and sold-out.
 * 3. SINGLE SOURCES OF TRUTH: promoted reach reads the linked Advertising
 *    campaign row (not a second metric); attributed sales are WITHHELD (—) when
 *    the attribution flag is off — never invented; live order stats are absent
 *    until the live-stats flag is on.
 * 4. PRIVACY: attendee summaries never surface an attendee whose visibility is
 *    false, but still count them.
 * 5. PUBLISH validation surfaces the real blocker on a draft.
 * 6. MOCK-DATA gap ledger length is asserted, so changing it is reviewed.
 */

import {
  AttendeeSummary,
  EVENTS_MOCK_DATA_GAPS,
  EVENTS_MOCK_DATA_GAP_COUNT,
  HostedEvent,
  compactCount,
  deriveCapacity,
  deriveCountdown,
  deriveEventResults,
  deriveEventStatus,
  deriveLiveBanner,
  eventMatchesTab,
  liveEvent,
  nextEventHero,
  publishBlockers,
  summarizeAttendees
} from "../eventsManager";
import type { AdAnalyticsRow } from "../businessOs";

const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;
const NOW = 1_700_000_000_000;

function event(overrides: Partial<HostedEvent> = {}): HostedEvent {
  return {
    id: "evt-1",
    type: "livestream",
    lifecycle: "published",
    title: "Friday plant drop",
    startsAt: new Date(NOW + 6 * DAY + 4 * HOUR).toISOString(),
    ...overrides
  };
}

beforeEach(() => {
  delete process.env.EXPO_PUBLIC_EVENTS_LIVE_STATS;
  delete process.env.EXPO_PUBLIC_EVENTS_ATTRIBUTION;
  delete process.env.EXPO_PUBLIC_EVENTS_MOCK;
});

describe("countdown", () => {
  it("computes days/hours for a multi-day-away event and a full sentence", () => {
    const c = deriveCountdown(event({ startsAt: new Date(NOW + 6 * DAY + 4 * HOUR).toISOString() }), NOW);
    expect(c.phase).toBe("days");
    expect(c.days).toBe(6);
    expect(c.hours).toBe(4);
    expect(c.short).toBe("6d 4h");
    expect(c.sentence).toBe("Starts in 6 days, 4 hours");
  });

  it("switches to Starting soon inside the final hour", () => {
    const c = deriveCountdown(event({ startsAt: new Date(NOW + 25 * 60 * 1000).toISOString() }), NOW);
    expect(c.phase).toBe("soon");
    expect(c.short).toBe("Starting soon");
    expect(c.sentence).toContain("Starting soon");
  });

  it("reports live once the window has opened and ended once it closes", () => {
    const started = event({
      startsAt: new Date(NOW - 10 * 60 * 1000).toISOString(),
      endsAt: new Date(NOW + 50 * 60 * 1000).toISOString()
    });
    expect(deriveCountdown(started, NOW).phase).toBe("live");
    const done = event({
      startsAt: new Date(NOW - 3 * HOUR).toISOString(),
      endsAt: new Date(NOW - 1 * HOUR).toISOString()
    });
    expect(deriveCountdown(done, NOW).phase).toBe("ended");
  });
});

describe("capacity", () => {
  it("derives fill, nearly-full amber threshold, and an accessible label", () => {
    const c = deriveCapacity(22, 24);
    expect(c.fill).toBeCloseTo(22 / 24);
    expect(c.nearlyFull).toBe(true);
    expect(c.full).toBe(false);
    expect(c.spotsLabel).toBe("22 going · 2 spots");
    expect(c.a11yLabel).toBe("22 of 24 spots taken, nearly full");
  });

  it("reports sold out at capacity", () => {
    const c = deriveCapacity(24, 24);
    expect(c.full).toBe(true);
    expect(c.spotsLabel).toBe("Sold out");
    expect(c.a11yLabel).toContain("sold out");
  });

  it("hides the bar (fill 0) when capacity is unknown", () => {
    const c = deriveCapacity(9, undefined);
    expect(c.fill).toBe(0);
    expect(c.spotsLabel).toBe("9 going");
  });
});

describe("single sources of truth", () => {
  it("reads promoted reach from the linked campaign row, not a second metric", () => {
    const campaign = { campaign_id: 7, impressions: 3200 } as unknown as AdAnalyticsRow;
    const status = deriveEventStatus(event({ promotionCampaignId: "camp-7" }), campaign);
    expect(status.kind).toBe("promoted");
    expect(status.line).toBe("Promoted · 3.2k reach");
  });

  it("shows Promoted with no reach number when the campaign figure is absent", () => {
    const status = deriveEventStatus(event({ promotionCampaignId: "camp-7" }), undefined);
    expect(status.line).toBe("Promoted");
  });

  it("withholds attributed sales (—) when the attribution flag is off, even with a real figure", () => {
    const past = event({
      lifecycle: "ended",
      type: "livestream",
      results: { reached: 1500, follows: 40, attributedSalesMinor: 24000, currency: "USD" }
    });
    const r = deriveEventResults(past);
    expect(r.salesWithheld).toBe(true);
    expect(r.metrics.find((m) => m.label === "Attributed sales")?.value).toBe("—");
  });

  it("shows attributed sales only when the flag is on and a real figure exists", () => {
    process.env.EXPO_PUBLIC_EVENTS_ATTRIBUTION = "1";
    const past = event({
      lifecycle: "ended",
      type: "livestream",
      results: { reached: 1500, follows: 40, attributedSalesMinor: 24000, currency: "USD" }
    });
    const r = deriveEventResults(past);
    expect(r.salesWithheld).toBe(false);
    expect(r.metrics.find((m) => m.label === "Attributed sales")?.value).toBe("$240");
  });

  it("reconciles a promoted livestream's reach to the campaign figure in results", () => {
    process.env.EXPO_PUBLIC_EVENTS_ATTRIBUTION = "1";
    const campaign = { campaign_id: 9, impressions: 5000 } as unknown as AdAnalyticsRow;
    const past = event({
      lifecycle: "ended",
      type: "livestream",
      promotionCampaignId: "camp-9",
      results: { reached: 111, follows: 40 } // stale local reach ignored in favour of campaign
    });
    const r = deriveEventResults(past, campaign);
    expect(r.metrics.find((m) => m.label === "Reached")?.value).toBe("5k");
  });
});

describe("live banner honesty", () => {
  it("renders no banner when nothing is live", () => {
    expect(deriveLiveBanner(event({ lifecycle: "published" }), { viewerCount: 200 })).toBeNull();
  });

  it("renders the banner without numbers when live-stats flag is off", () => {
    const banner = deriveLiveBanner(event({ lifecycle: "live" }), { viewerCount: 212, ordersLast10Min: 3 });
    expect(banner).not.toBeNull();
    expect(banner?.watching).toBeUndefined();
    expect(banner?.statsLine).toBeUndefined();
    expect(banner?.a11yAnnouncement).toBe("Live now: Friday plant drop");
  });

  it("renders real viewers + orders when the flag is on", () => {
    process.env.EXPO_PUBLIC_EVENTS_LIVE_STATS = "1";
    const banner = deriveLiveBanner(event({ lifecycle: "live", liveId: 88 }), { viewerCount: 212, ordersLast10Min: 3 });
    expect(banner?.watching).toBe(212);
    expect(banner?.orders).toBe(3);
    expect(banner?.statsLine).toBe("212 watching · 3 orders in the last 10 min");
    expect(banner?.a11yAnnouncement).toBe("Live now: Friday plant drop, 212 watching");
  });
});

describe("attendee privacy", () => {
  it("never surfaces a non-visible attendee but still counts them", () => {
    const summary: AttendeeSummary = summarizeAttendees(
      [
        { id: "a", name: "Maya" },
        { id: "b", name: "Jordan", visible: false },
        { id: "c", name: "Alex" }
      ],
      18,
      3
    );
    expect(summary.shown.map((a) => a.name)).toEqual(["Maya", "Alex"]);
    expect(summary.overflow).toBe(16);
    expect(summary.a11yLabel).toBe("Attendees include Maya, Alex and 16 others");
  });
});

describe("publish validation + tabs + selection", () => {
  it("surfaces the real blocker on a ticketed draft with no tickets", () => {
    const blockers = publishBlockers(event({ lifecycle: "draft", ticketed: true, hasTickets: false }));
    expect(blockers).toContain("Add tickets to publish");
    const status = deriveEventStatus(event({ lifecycle: "draft", ticketed: true, hasTickets: false }));
    expect(status.kind).toBe("draft");
    expect(status.line).toBe("Add tickets to publish");
  });

  it("routes events to the correct tab and picks the soonest published hero", () => {
    const a = event({ id: "a", lifecycle: "published", startsAt: new Date(NOW + 2 * DAY).toISOString() });
    const b = event({ id: "b", lifecycle: "published", startsAt: new Date(NOW + 1 * DAY).toISOString() });
    const d = event({ id: "d", lifecycle: "draft" });
    const l = event({ id: "l", lifecycle: "live" });
    expect(eventMatchesTab(d, "drafts")).toBe(true);
    expect(eventMatchesTab(l, "upcoming")).toBe(true);
    expect(nextEventHero([a, b, d], NOW)?.id).toBe("b");
    expect(liveEvent([a, l])?.id).toBe("l");
  });

  it("compacts large counts for display", () => {
    expect(compactCount(950)).toBe("950");
    expect(compactCount(1200)).toBe("1.2k");
    expect(compactCount(15000)).toBe("15k");
  });

  it("locks the MOCK-DATA gap ledger length", () => {
    expect(EVENTS_MOCK_DATA_GAPS.length).toBe(EVENTS_MOCK_DATA_GAP_COUNT);
  });
});
