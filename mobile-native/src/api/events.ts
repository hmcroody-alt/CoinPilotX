import { readJsonCache, writeJsonCache } from "../core/cache";
import { listLiveNow, loadCachedLiveDiscovery, normalizeLiveItems, PulseLiveItem } from "./live";

const EVENTS_CACHE_KEY = "pulsesoc.native.events.scheduled_live";

export type PulseScheduledEvent = PulseLiveItem & {
  event_kind: "scheduled_live";
  event_url: string;
  watch_url: string;
  schedule_url: string;
  create_url: string;
};

export type EventsGatewayState = {
  ok?: boolean;
  items: PulseScheduledEvent[];
  trace_id?: string;
  ranking?: string;
  message?: string;
};

export async function listScheduledLiveEvents(params: { limit?: number } = {}) {
  const live = await listLiveNow({ limit: params.limit || 24 });
  const items = eventItemsFromLive(live.scheduled || []);
  const state: EventsGatewayState = {
    ok: live.ok,
    items,
    trace_id: live.trace_id,
    ranking: live.ranking,
    message: live.message
  };
  await cacheScheduledLiveEvents(state).catch(() => undefined);
  return state;
}

export async function loadCachedScheduledLiveEvents() {
  const cachedEvents = await readJsonCache<EventsGatewayState>(EVENTS_CACHE_KEY, (data) => ({
    ...data,
    items: normalizeEventItems(data.items || [])
  }));
  if (cachedEvents?.items?.length) return cachedEvents;

  const cachedLive = await loadCachedLiveDiscovery();
  return {
    ok: cachedLive.ok,
    items: eventItemsFromLive(cachedLive.scheduled || cachedLive.events || []),
    trace_id: cachedLive.trace_id,
    ranking: cachedLive.ranking,
    message: cachedLive.message
  };
}

export async function cacheScheduledLiveEvents(state: EventsGatewayState) {
  await writeJsonCache(EVENTS_CACHE_KEY, {
    ...state,
    items: normalizeEventItems(state.items || []).slice(0, 60)
  });
}

export function normalizeEventItems(items: PulseLiveItem[]) {
  return eventItemsFromLive(items);
}

export function eventItemsFromLive(items: PulseLiveItem[]): PulseScheduledEvent[] {
  return normalizeLiveItems(items || [])
    .filter((item) => Number(item.id || item.live_id || 0) > 0)
    .map((item) => {
      const liveId = Number(item.id || item.live_id);
      return {
        ...item,
        id: liveId,
        live_id: liveId,
        event_kind: "scheduled_live",
        event_url: `/pulse/events/${liveId}`,
        watch_url: `/pulse/live/${liveId}`,
        schedule_url: "/pulse/live/schedule",
        create_url: "/pulse/live/events/create"
      };
    });
}

export async function openEventsWebFallback(mode: "events" | "schedule" | "create" = "events") {
  const path = mode === "schedule"
    ? "/pulse/live/schedule"
    : mode === "create"
      ? "/pulse/live/events/create"
      : "/pulse/events";
  return {
    ok: false,
    path,
    status: "native_provider_boundary",
    message: "This part of Events is not available in the app yet. Continue on the PulseSoc website."
  };
}
