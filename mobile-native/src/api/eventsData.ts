/**
 * Events data loader — the seam between the pure `eventsManager` derivation and
 * the live backend.
 *
 * SOURCE OF TRUTH, honestly bound:
 *   - Real upcoming events come from the LIVE scheduled-live surface
 *     (`events.ts` → `listScheduledLiveEvents`), the only hosted-event data the
 *     backend actually returns today. Those are all livestreams; each projects to
 *     a `HostedEvent` with lifecycle "published" (or "live" when the session is
 *     currently on air), carrying its real title, start, cover and — when live —
 *     viewer count.
 *   - There is no backend for drafts, in-person events, RSVPs, capacity, past
 *     results or promotion links (see EVENTS_MOCK_DATA_GAPS). So this loader adds
 *     NONE of those from real data. Deterministic samples that exercise those
 *     states exist ONLY when EXPO_PUBLIC_EVENTS_MOCK is on, and are clearly marked
 *     — never mixed into the real set in a shipping build.
 *
 * The screen consumes `EventsModel` and never fetches on its own, mirroring the
 * `ordersDashboard` → `OrdersManagerScreen` split.
 */

import {
  type HostedEvent,
  type LiveSessionStats,
  eventsMockEnabled
} from "./eventsManager";
import { listScheduledLiveEvents, loadCachedScheduledLiveEvents, type PulseScheduledEvent } from "./events";

export type EventsModel = {
  events: HostedEvent[];
  /** Real live-session stats keyed by event id (viewer count only today). */
  liveStats: Record<string, LiveSessionStats>;
  offline: boolean;
  error?: string;
  /** True when the set includes EXPO_PUBLIC_EVENTS_MOCK design samples. */
  usingMock: boolean;
};

const HOUR_MS = 60 * 60 * 1000;

/**
 * Load the hosted-events model. Projects the real scheduled-live surface, then —
 * only under the mock flag — appends deterministic samples so drafts, in-person,
 * live and past-results states are reviewable without a backend that lacks them.
 */
export async function loadEventsModel(now: number = Date.now()): Promise<EventsModel> {
  let offline = false;
  let error: string | undefined;
  let raw: PulseScheduledEvent[] = [];

  try {
    const state = await listScheduledLiveEvents({ limit: 24 });
    raw = state.items || [];
    if (state.ok === false) {
      offline = true;
      error = state.message;
    }
  } catch {
    offline = true;
    try {
      const cached = await loadCachedScheduledLiveEvents();
      raw = cached.items || [];
    } catch {
      raw = [];
    }
    error = "Couldn't reach the events service. Showing the last synced schedule.";
  }

  const events: HostedEvent[] = [];
  const liveStats: Record<string, LiveSessionStats> = {};

  for (const item of raw) {
    const projected = projectScheduledLive(item);
    if (!projected) continue;
    events.push(projected);
    if (projected.lifecycle === "live" && typeof item.viewer_count === "number") {
      // Only viewer_count is backed; orders-in-10-min has no channel (gap ledger).
      liveStats[projected.id] = { viewerCount: item.viewer_count };
    }
  }

  const usingMock = eventsMockEnabled();
  if (usingMock) {
    events.push(...mockEvents(now));
  }

  return { events, liveStats, offline, error, usingMock };
}

/**
 * Project one real scheduled-live item into a HostedEvent. Livestream type only —
 * that is all this surface carries. Returns null when it has no usable start time,
 * rather than inventing one.
 */
function projectScheduledLive(item: PulseScheduledEvent): HostedEvent | null {
  const id = Number(item.id || item.live_id || 0);
  if (!id) return null;

  const startsAt = item.scheduled_at || item.started_at;
  if (!startsAt) return null;

  const isLive = String(item.status || "").toLowerCase() === "live" || Boolean(item.started_at && !item.scheduled_at);

  return {
    id: `live-${id}`,
    type: "livestream",
    lifecycle: isLive ? "live" : "published",
    title: item.title || "Untitled livestream",
    startsAt,
    streamUrl: item.live_url || item.watch_url,
    liveId: isLive ? id : undefined,
    coverUrl: item.thumbnail_url || item.preview_url,
    keyDetail: item.category,
    interestedCount: typeof item.reaction_count === "number" ? item.reaction_count : undefined
  };
}

/* ------------------------------------------------------------------ *
 * Deterministic design samples — EXPO_PUBLIC_EVENTS_MOCK only
 * ------------------------------------------------------------------ *
 * These exercise the states the real surface can't yet produce: a next-event
 * hero with RSVPs + capacity, an in-person event, a currently-live banner, a
 * draft with a real blocker, and past results for both event types. They are
 * NEVER returned unless the flag is explicitly on.
 */

function iso(now: number, deltaMs: number): string {
  return new Date(now + deltaMs).toISOString();
}

function mockEvents(now: number): HostedEvent[] {
  return [
    {
      id: "mock-hero",
      type: "in_person",
      lifecycle: "published",
      title: "Spring Sample Sale — Pop-up",
      startsAt: iso(now, 3 * 24 * HOUR_MS + 4 * HOUR_MS),
      endsAt: iso(now, 3 * 24 * HOUR_MS + 7 * HOUR_MS),
      venue: "Warehouse 12, 480 Canal St",
      capacity: 60,
      goingCount: 55,
      interestedCount: 128,
      emoji: "🛍️",
      keyDetail: "24 products linked",
      attendees: [
        { id: "a1", name: "Maya" },
        { id: "a2", name: "Devon" },
        { id: "a3", name: "Priya" },
        { id: "a4", name: "Sam", visible: false }
      ]
    },
    {
      id: "mock-live",
      type: "livestream",
      lifecycle: "live",
      title: "Live drop: limited sneakers",
      startsAt: iso(now, -20 * 60 * 1000),
      liveId: 999001,
      streamUrl: "/pulse/live/999001",
      interestedCount: 340
    },
    {
      id: "mock-promoted",
      type: "livestream",
      lifecycle: "published",
      title: "Studio tour + Q&A",
      startsAt: iso(now, 6 * 24 * HOUR_MS),
      promotionCampaignId: "camp-1",
      emoji: "🎥"
    },
    {
      id: "mock-draft",
      type: "in_person",
      lifecycle: "draft",
      title: "Fall workshop (unpublished)",
      startsAt: iso(now, 20 * 24 * HOUR_MS),
      ticketed: true,
      hasTickets: false,
      emoji: "🍂"
    },
    {
      id: "mock-past-live",
      type: "livestream",
      lifecycle: "ended",
      title: "Holiday live sale",
      startsAt: iso(now, -9 * 24 * HOUR_MS),
      endsAt: iso(now, -9 * 24 * HOUR_MS + 2 * HOUR_MS),
      results: { reached: 4200, follows: 86 }
    },
    {
      id: "mock-past-inperson",
      type: "in_person",
      lifecycle: "ended",
      title: "Summer meetup",
      startsAt: iso(now, -30 * 24 * HOUR_MS),
      endsAt: iso(now, -30 * 24 * HOUR_MS + 3 * HOUR_MS),
      venue: "Rooftop, 88 Market St",
      results: { attended: 47, checkedIn: 41 }
    }
  ];
}
