/**
 * Events manager — the derivation layer the seller Events screen renders.
 *
 * This is the hosted-events domain (workshops, live sales, pop-ups): an event
 * has a lifecycle, a start/end, a venue or stream link, capacity + RSVPs, an
 * optional promotion linked to an Advertising campaign, and — once it is over —
 * results. The existing `src/api/events.ts` is a thin projection of the live
 * discovery feed (scheduled-live items); it is NOT this model, so everything a
 * hosted-events manager needs is defined here.
 *
 * SINGLE SOURCES OF TRUTH — this module never invents the numbers it shows:
 *   - promoted-event reach/follows come from the linked Advertising campaign
 *     object (the Advertising mission owns those figures). No campaign → no
 *     promoted metric shown.
 *   - live viewer/order stats come from the live-session service. Only
 *     `viewer_count` is backed today; orders-in-last-10-min has no channel, so
 *     it is absent (never faked) — the banner renders without it.
 *   - attributed sales follow the platform attribution model. No model exists
 *     today, so attributed-sales is null and flagged, exactly like the Insights
 *     and Payments missions do it. Money is display-only formatting of real
 *     minor units; nothing is fabricated.
 *
 * Everything here is pure and framework-free so it can be unit-tested and reused
 * by an event-detail / results view later without dragging navigation in.
 */

import { formatMinor } from "./commerceInbox";
import type { AdAnalyticsRow } from "./businessOs";
import { envFlagOn } from "../core/envFlag";

const HOUR_MS = 60 * 60 * 1000;
const MINUTE_MS = 60 * 1000;
const DAY_MS = 24 * HOUR_MS;

/* ------------------------------------------------------------------ *
 * Feature flags (all OFF by default; read at call time)
 *
 * `envFlagOn` is the shared reader in `core/envFlag.ts`. The local parser it
 * replaced accepted the same four values, so nothing here changed except that
 * the rule is no longer this file's to decide.
 * ------------------------------------------------------------------ */

/** Live commerce stats (watching / orders-in-10-min) on the live banner. */
export const eventsLiveStatsEnabled = () => envFlagOn("EXPO_PUBLIC_EVENTS_LIVE_STATS");
/** Attributed-sales figures on past-event results. Off — no attribution model. */
export const eventAttributionEnabled = () => envFlagOn("EXPO_PUBLIC_EVENTS_ATTRIBUTION");
/** Deterministic mock events for design review. Off — real events only. */
export const eventsMockEnabled = () => envFlagOn("EXPO_PUBLIC_EVENTS_MOCK");

/* ------------------------------------------------------------------ *
 * Model
 * ------------------------------------------------------------------ */

export type EventType = "in_person" | "livestream";
export type EventLifecycle = "draft" | "published" | "live" | "ended" | "cancelled";

export type EventAttendee = {
  id: string;
  name: string;
  avatarUrl?: string;
  /**
   * Attendee visibility per the platform's privacy rules. `false` = this person
   * opted out of appearing in public attendee lists; the row still counts them
   * but never renders their identity.
   */
  visible?: boolean;
};

export type HostedEvent = {
  id: string;
  type: EventType;
  lifecycle: EventLifecycle;
  title: string;
  /** ISO 8601 with offset — the event's real start in its own timezone. */
  startsAt: string;
  endsAt?: string;
  /** In-person venue address, when type === "in_person". */
  venue?: string;
  /** Stream/watch URL, when type === "livestream". */
  streamUrl?: string;
  /** The live-session id, present once a livestream event goes live. */
  liveId?: number;
  capacity?: number;
  goingCount?: number;
  interestedCount?: number;
  attendees?: EventAttendee[];
  coverUrl?: string;
  emoji?: string;
  /** One key meta detail shown on the row ("products linked", "booth #12"). */
  keyDetail?: string;
  /** Tickets configured? Publishing is gated on this for ticketed events. */
  hasTickets?: boolean;
  ticketed?: boolean;
  /** Linked Advertising campaign id, when the event has been promoted. */
  promotionCampaignId?: string;
  /** Backend-supplied publish blockers, if the server already computed them. */
  publishBlockers?: string[];
  /** Past-event raw result counters (real records or absent — never invented). */
  results?: EventResultCounters;
  timezone?: string;
};

/** Raw result counters as the backend returns them (no derived money here). */
export type EventResultCounters = {
  reached?: number;
  follows?: number;
  attended?: number;
  checkedIn?: number;
  /** Real attributed-sales minor units, or omitted when no attribution model. */
  attributedSalesMinor?: number;
  currency?: string;
};

/* ------------------------------------------------------------------ *
 * Lifecycle helpers
 * ------------------------------------------------------------------ */

const UPCOMING: ReadonlySet<EventLifecycle> = new Set<EventLifecycle>(["published", "live"]);

/** An event belongs in the Upcoming tab when published or currently live. */
export function isUpcoming(event: HostedEvent): boolean {
  return UPCOMING.has(event.lifecycle);
}

/** Past tab: ended or cancelled events. */
export function isPast(event: HostedEvent): boolean {
  return event.lifecycle === "ended" || event.lifecycle === "cancelled";
}

/** Drafts tab. */
export function isDraft(event: HostedEvent): boolean {
  return event.lifecycle === "draft";
}

export type EventTab = "upcoming" | "past" | "drafts";

export function eventMatchesTab(event: HostedEvent, tab: EventTab): boolean {
  if (tab === "upcoming") return isUpcoming(event);
  if (tab === "past") return isPast(event);
  return isDraft(event);
}

/**
 * Publish validation. The screen shows the *real* blocker on a draft's LED. If
 * the backend already computed blockers we trust them; otherwise we derive the
 * obvious ones from missing fields. Returns [] when the draft is publishable.
 */
export function publishBlockers(event: HostedEvent): string[] {
  if (event.publishBlockers && event.publishBlockers.length) return event.publishBlockers;
  const blockers: string[] = [];
  if (!event.startsAt) blockers.push("Set a start time to publish");
  if (event.ticketed && !event.hasTickets) blockers.push("Add tickets to publish");
  if (event.type === "in_person" && !event.venue) blockers.push("Add a venue to publish");
  if (event.type === "livestream" && !event.streamUrl && !event.liveId) {
    blockers.push("Set up the stream to publish");
  }
  if (typeof event.capacity === "number" && event.capacity <= 0) {
    blockers.push("Set capacity to publish");
  }
  return blockers;
}

/* ------------------------------------------------------------------ *
 * Countdown — computed from the event's real start, minute-level
 * ------------------------------------------------------------------ */

export type CountdownPhase = "days" | "soon" | "live" | "ended";

export type Countdown = {
  phase: CountdownPhase;
  days: number;
  hours: number;
  minutes: number;
  /** Screen-ready label: "6d", "Starting soon", "Live now", "Ended". */
  short: string;
  /** Accessible full sentence: "Starts in 6 days, 4 hours". */
  sentence: string;
};

/**
 * Derive the countdown to an event's start. Pure ms math off the real start (and
 * end, if present) so it is timezone-agnostic in magnitude; the *display* of the
 * date itself is formatted elsewhere from the ISO's own offset. Switches to
 * "Starting soon" inside the final hour and to "Live"/"Ended" once the window
 * opens/closes so the screen can hand off to the live banner at start.
 */
export function deriveCountdown(event: HostedEvent, now: number): Countdown {
  const startMs = Date.parse(event.startsAt);
  const endMs = event.endsAt ? Date.parse(event.endsAt) : startMs + 2 * HOUR_MS;

  if (Number.isNaN(startMs)) {
    return { phase: "days", days: 0, hours: 0, minutes: 0, short: "", sentence: "Start time not set" };
  }
  if (now >= endMs) {
    return { phase: "ended", days: 0, hours: 0, minutes: 0, short: "Ended", sentence: "This event has ended" };
  }
  if (now >= startMs) {
    return { phase: "live", days: 0, hours: 0, minutes: 0, short: "Live now", sentence: "Live now" };
  }

  const remaining = startMs - now;
  const days = Math.floor(remaining / DAY_MS);
  const hours = Math.floor((remaining % DAY_MS) / HOUR_MS);
  const minutes = Math.floor((remaining % HOUR_MS) / MINUTE_MS);

  if (remaining < HOUR_MS) {
    return {
      phase: "soon",
      days: 0,
      hours: 0,
      minutes,
      short: "Starting soon",
      sentence: minutes > 0 ? `Starting soon, in ${minutes} minute${minutes === 1 ? "" : "s"}` : "Starting soon"
    };
  }

  const parts: string[] = [];
  if (days > 0) parts.push(`${days} day${days === 1 ? "" : "s"}`);
  if (hours > 0) parts.push(`${hours} hour${hours === 1 ? "" : "s"}`);
  if (days === 0 && minutes > 0) parts.push(`${minutes} minute${minutes === 1 ? "" : "s"}`);

  const short = days > 0 ? `${days}d ${hours}h` : `${hours}h ${minutes}m`;
  return {
    phase: "days",
    days,
    hours,
    minutes,
    short,
    sentence: `Starts in ${parts.join(", ") || "under a minute"}`
  };
}

/* ------------------------------------------------------------------ *
 * Capacity
 * ------------------------------------------------------------------ */

export type Capacity = {
  going: number;
  capacity?: number;
  /** 0..1 fill. Undefined capacity → 0 (bar hidden by the component). */
  fill: number;
  nearlyFull: boolean;
  full: boolean;
  /** "18 going · 6 spots" / "Sold out" / "24 going". */
  spotsLabel: string;
  /** Accessible: "18 of 24 spots taken, nearly full". */
  a11yLabel: string;
};

const NEARLY_FULL = 0.9;

export function deriveCapacity(goingCount: number | undefined, capacity: number | undefined): Capacity {
  const going = Math.max(0, goingCount ?? 0);
  if (typeof capacity !== "number" || capacity <= 0) {
    return {
      going,
      capacity: undefined,
      fill: 0,
      nearlyFull: false,
      full: false,
      spotsLabel: `${going} going`,
      a11yLabel: `${going} going`
    };
  }
  const fill = Math.min(1, going / capacity);
  const full = going >= capacity;
  const nearlyFull = !full && fill >= NEARLY_FULL;
  const spotsLeft = Math.max(0, capacity - going);
  const spotsLabel = full ? "Sold out" : `${going} going · ${spotsLeft} spot${spotsLeft === 1 ? "" : "s"}`;
  const a11yLabel = full
    ? `${going} of ${capacity} spots taken, sold out`
    : `${going} of ${capacity} spots taken${nearlyFull ? ", nearly full" : ""}`;
  return { going, capacity, fill, nearlyFull, full, spotsLabel, a11yLabel };
}

/* ------------------------------------------------------------------ *
 * Attendee (avatar-stack) summary — respects privacy visibility
 * ------------------------------------------------------------------ */

export type AttendeeSummary = {
  /** Up to `max` visible attendees to render as avatars. */
  shown: EventAttendee[];
  /** Everyone beyond the shown avatars, for the "+N" chip. */
  overflow: number;
  a11yLabel: string;
};

export function summarizeAttendees(
  attendees: EventAttendee[] | undefined,
  goingCount: number | undefined,
  max = 3
): AttendeeSummary {
  const visible = (attendees || []).filter((a) => a.visible !== false);
  const shown = visible.slice(0, max);
  const total = Math.max(goingCount ?? visible.length, visible.length);
  const overflow = Math.max(0, total - shown.length);
  const names = shown.map((a) => a.name);
  let a11yLabel: string;
  if (shown.length === 0) {
    a11yLabel = total > 0 ? `${total} attending` : "No attendees yet";
  } else if (overflow > 0) {
    a11yLabel = `Attendees include ${names.join(", ")} and ${overflow} others`;
  } else {
    a11yLabel = `Attendees: ${names.join(", ")}`;
  }
  return { shown, overflow, a11yLabel };
}

/* ------------------------------------------------------------------ *
 * Live-now banner — real viewers, honest about missing order stats
 * ------------------------------------------------------------------ */

export type LiveSessionStats = {
  /** Real viewer count from the live-session service. */
  viewerCount?: number;
  /** Orders in the last 10 min — no backend channel today; usually absent. */
  ordersLast10Min?: number;
};

export type LiveBanner = {
  eventId: string;
  title: string;
  liveId?: number;
  /** Present only when a real number exists; the banner shows no fake stats. */
  watching?: number;
  orders?: number;
  /** Assertive a11y announcement on appearance. */
  a11yAnnouncement: string;
  statsLine?: string;
};

/**
 * Build the live-now banner for a currently-live event, or null when nothing is
 * live. Stats appear only when the live-stats flag is on AND a real number is
 * present; otherwise the banner renders with just the name (never fake numbers).
 */
export function deriveLiveBanner(
  liveEvent: HostedEvent | undefined,
  stats: LiveSessionStats | undefined
): LiveBanner | null {
  if (!liveEvent || liveEvent.lifecycle !== "live") return null;

  const statsOn = eventsLiveStatsEnabled();
  const watching = statsOn && typeof stats?.viewerCount === "number" ? stats.viewerCount : undefined;
  const orders = statsOn && typeof stats?.ordersLast10Min === "number" ? stats.ordersLast10Min : undefined;

  const segments: string[] = [];
  if (typeof watching === "number") segments.push(`${watching} watching`);
  if (typeof orders === "number") segments.push(`${orders} orders in the last 10 min`);
  const statsLine = segments.length ? segments.join(" · ") : undefined;

  const a11yAnnouncement =
    typeof watching === "number"
      ? `Live now: ${liveEvent.title}, ${watching} watching`
      : `Live now: ${liveEvent.title}`;

  return {
    eventId: liveEvent.id,
    title: liveEvent.title,
    liveId: liveEvent.liveId,
    watching,
    orders,
    statsLine,
    a11yAnnouncement
  };
}

/* ------------------------------------------------------------------ *
 * Upcoming-row status LED — published / promoted / draft
 * ------------------------------------------------------------------ */

export type EventStatusKind = "published" | "promoted" | "draft";

export type EventStatus = {
  kind: EventStatusKind;
  /** "Published · 42 interested" / "Promoted · 3.2k reach" / draft blocker. */
  line: string;
  /** True only for the published-LED ping animation. */
  ping: boolean;
};

/**
 * Derive the status-LED line for an upcoming/draft row. A promoted event reads
 * its reach from the linked campaign row — the SAME figure Advertising shows,
 * never a duplicate metric. Passing no campaign for a promoted event yields the
 * promoted label without a reach number (honest) rather than a fabricated one.
 */
export function deriveEventStatus(event: HostedEvent, campaign?: AdAnalyticsRow): EventStatus {
  if (event.lifecycle === "draft") {
    const blockers = publishBlockers(event);
    return {
      kind: "draft",
      line: blockers.length ? blockers[0] : "Draft",
      ping: false
    };
  }
  if (event.promotionCampaignId) {
    const reach = campaignReach(campaign);
    const reachLabel = typeof reach === "number" ? ` · ${compactCount(reach)} reach` : "";
    return { kind: "promoted", line: `Promoted${reachLabel}`, ping: false };
  }
  const interested = event.interestedCount;
  const interestLabel = typeof interested === "number" ? ` · ${compactCount(interested)} interested` : "";
  return { kind: "published", line: `Published${interestLabel}`, ping: true };
}

/**
 * Reach from an Advertising campaign row. Advertising's analytics rows expose
 * impressions today (reach/follows are a documented gap in that mission), so we
 * read impressions as the delivered-reach proxy and return undefined when even
 * that is absent — no invented figure.
 */
export function campaignReach(campaign?: AdAnalyticsRow): number | undefined {
  if (!campaign) return undefined;
  return typeof campaign.impressions === "number" ? campaign.impressions : undefined;
}

/* ------------------------------------------------------------------ *
 * Past-event results
 * ------------------------------------------------------------------ */

export type EventResults = {
  type: EventType;
  /** Ordered display metrics: [label, value]. */
  metrics: Array<{ label: string; value: string }>;
  /**
   * True when attributed sales were withheld because no attribution model
   * exists — the screen shows a flagged "—" rather than a number.
   */
  salesWithheld: boolean;
  a11yLabel: string;
};

/**
 * Build the results line for a past event. Livestreams show reached · follows ·
 * attributed sales; in-person shows attended · checked in · attributed sales.
 * Reach/follows for a promoted livestream come from the SAME campaign source
 * Advertising uses (reconciliation requirement). Attributed sales render only
 * when the attribution flag is on AND a real figure is present; otherwise the
 * metric is withheld and flagged — never invented.
 */
export function deriveEventResults(event: HostedEvent, campaign?: AdAnalyticsRow): EventResults {
  const r = event.results || {};
  const metrics: Array<{ label: string; value: string }> = [];

  // Prefer the campaign figure when the event was promoted, so a livestream that
  // also ran as a Post ad shows Advertising's number, not a second one.
  const reach = event.promotionCampaignId ? campaignReach(campaign) ?? r.reached : r.reached;

  if (event.type === "livestream") {
    metrics.push({ label: "Reached", value: countOrDash(reach) });
    metrics.push({ label: "Follows", value: countOrDash(r.follows) });
  } else {
    metrics.push({ label: "Attended", value: countOrDash(r.attended) });
    metrics.push({ label: "Checked in", value: countOrDash(r.checkedIn) });
  }

  const salesShown = eventAttributionEnabled() && typeof r.attributedSalesMinor === "number";
  metrics.push({
    label: "Attributed sales",
    value: salesShown ? formatMinor(r.attributedSalesMinor as number, r.currency) : "—"
  });

  const a11yLabel = metrics.map((m) => `${m.label} ${m.value}`).join(", ");
  return { type: event.type, metrics, salesWithheld: !salesShown, a11yLabel };
}

/* ------------------------------------------------------------------ *
 * Next-event hero selection
 * ------------------------------------------------------------------ */

/** The soonest upcoming *published* event, for the hero. Null when none. */
export function nextEventHero(events: HostedEvent[], now: number): HostedEvent | null {
  const upcoming = events
    .filter((e) => e.lifecycle === "published" && Date.parse(e.startsAt) >= now)
    .sort((a, b) => Date.parse(a.startsAt) - Date.parse(b.startsAt));
  return upcoming[0] ?? null;
}

/** The single currently-live event, for the banner. Null when none. */
export function liveEvent(events: HostedEvent[]): HostedEvent | null {
  return events.find((e) => e.lifecycle === "live") ?? null;
}

/* ------------------------------------------------------------------ *
 * Formatting helpers (display-only)
 * ------------------------------------------------------------------ */

function countOrDash(n: number | undefined): string {
  return typeof n === "number" ? compactCount(n) : "—";
}

/** Compact large counts: 1200 → "1.2k". Display only. */
export function compactCount(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) {
    const k = n / 1000;
    return `${k >= 10 ? Math.round(k) : Math.round(k * 10) / 10}k`;
  }
  const m = n / 1_000_000;
  return `${m >= 10 ? Math.round(m) : Math.round(m * 10) / 10}m`;
}

/** The type tag shown on the hero cover: "LIVESTREAM" / "IN-PERSON". */
export function eventTypeTag(event: HostedEvent): string {
  return event.type === "livestream" ? "LIVESTREAM" : "IN-PERSON";
}

/* ------------------------------------------------------------------ *
 * MOCK-DATA gap ledger — every field with no live backend source
 * ------------------------------------------------------------------ */

export type MockDataGap = {
  field: string;
  backendWork: string;
  gatedBy: string;
};

export const EVENTS_MOCK_DATA_GAPS: MockDataGap[] = [
  {
    field: "hosted-event model (lifecycle, capacity, tickets, venue/stream)",
    backendWork: "A hosted-events service — events.ts today is only a scheduled-live projection",
    gatedBy: "real events render; deterministic samples behind EXPO_PUBLIC_EVENTS_MOCK"
  },
  {
    field: "RSVP / attendee identities + check-in records",
    backendWork: "Persist RSVPs and check-ins per event; return attendees with privacy visibility",
    gatedBy: "shown only when the event carries real attendees"
  },
  {
    field: "live orders-in-last-10-min stat",
    backendWork: "A live-commerce orders channel — live service exposes viewer_count only",
    gatedBy: "EXPO_PUBLIC_EVENTS_LIVE_STATS (banner shows no order number until backed)"
  },
  {
    field: "promoted-event reach / follows",
    backendWork: "Post-ad KPIs (reach, new followers) — a documented gap in the Advertising mission",
    gatedBy: "read from the linked campaign row's impressions; omitted when absent"
  },
  {
    field: "attributed sales on past-event results",
    backendWork: "An attribution model — none exists; Insights/Payments rule applies",
    gatedBy: "EXPO_PUBLIC_EVENTS_ATTRIBUTION (metric withheld as — until backed)"
  },
  {
    field: "offer/ticket TTL + waitlist",
    backendWork: "Confirm capacity/waitlist semantics once the events backend exists",
    gatedBy: "waitlist only shown if the platform exposes one (it does not yet)"
  }
];

/**
 * The row count, written out — a literal, not `EVENTS_MOCK_DATA_GAPS.length`.
 *
 * Derived from the array, it made the accompanying test compare the length to
 * itself, which passes for every value and locks nothing. `INBOX_MOCK_DATA_GAP_COUNT`
 * is what the convention was meant to look like: Tier 0.4 raised that table and
 * had to edit its literal to do it, which is the deliberate, visible change the
 * ledger exists to force.
 */
export const EVENTS_MOCK_DATA_GAP_COUNT = 6;
