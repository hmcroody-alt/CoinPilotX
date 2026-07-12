import AsyncStorage from "@react-native-async-storage/async-storage";
import { AppState } from "react-native";
import { PulseApiError, pulseApi } from "../api/pulseApi";

export type NativeSyncSubsystem =
  | "activity"
  | "notifications"
  | "orders"
  | "marketplace"
  | "seller_inventory"
  | "messenger"
  | "calls"
  | "safety"
  | "verification"
  | "premium"
  | "intelligence"
  | "status";

export type NativeSyncEvent = {
  event_id?: string | number;
  id?: string | number;
  event_type?: string;
  type?: string;
  domain?: string;
  category?: string;
  entity_type?: string;
  entity_id?: string | number;
  target_url?: string;
  deep_link?: string;
  created_at?: string;
  updated_at?: string;
  invalidates?: NativeSyncSubsystem[];
  invalidate?: NativeSyncSubsystem[];
  metadata?: Record<string, unknown>;
};

export type NativeSyncCursor = {
  latestEventId?: string;
  lastEventAt?: string;
  lastSyncedAt?: string;
  lastFullResyncAt?: string;
};

export type NativeSyncPollResult = {
  cursor: NativeSyncCursor;
  events: NativeSyncEvent[];
  invalidated: NativeSyncSubsystem[];
  fullResync: boolean;
  source: "delta" | "fallback" | "idle";
};

type NativeSyncResponse = {
  ok?: boolean;
  events?: NativeSyncEvent[];
  cursor?: NativeSyncCursor | string;
  latest_event_id?: string | number;
  latestEventId?: string | number;
  last_event_at?: string;
  lastEventAt?: string;
  message?: string;
};

type NativeSyncHandler = (context: { reason: string; subsystems: NativeSyncSubsystem[]; events: NativeSyncEvent[] }) => void | Promise<void>;

type StartNativeSyncOptions = {
  subsystems?: NativeSyncSubsystem[];
  pollIntervalMs?: number;
  endpoint?: string;
  fullResyncOnStart?: boolean;
};

type PollNativeSyncOptions = StartNativeSyncOptions & {
  reason?: string;
  events?: NativeSyncEvent[];
};

const SYNC_CURSOR_KEY = "pulsesoc.native.event_sync.cursor";
const DEFAULT_SYNC_ENDPOINT = "/api/pulse/sync/events";
const DEFAULT_SUBSYSTEMS: NativeSyncSubsystem[] = ["activity", "notifications", "orders", "marketplace", "seller_inventory"];
const DEFAULT_POLL_INTERVAL_MS = 45_000;

const handlers = new Map<NativeSyncSubsystem, Set<NativeSyncHandler>>();
let activeStop: (() => void) | null = null;
let activePoll: Promise<NativeSyncPollResult> | null = null;
let invalidating = false;

export function registerSyncInvalidation(subsystem: NativeSyncSubsystem, handler: NativeSyncHandler) {
  const nextHandlers = handlers.get(subsystem) || new Set<NativeSyncHandler>();
  nextHandlers.add(handler);
  handlers.set(subsystem, nextHandlers);
  return () => {
    nextHandlers.delete(handler);
    if (!nextHandlers.size) handlers.delete(subsystem);
  };
}

export async function invalidateNativeSync(subsystems: NativeSyncSubsystem[], reason = "manual", events: NativeSyncEvent[] = []) {
  const uniqueSubsystems = dedupeSubsystems(subsystems);
  if (!uniqueSubsystems.length || invalidating) return;
  invalidating = true;
  try {
    const uniqueHandlers = new Set<NativeSyncHandler>();
    uniqueSubsystems.forEach((subsystem) => {
      (handlers.get(subsystem) || []).forEach((handler) => uniqueHandlers.add(handler));
    });
    await Promise.all(
      Array.from(uniqueHandlers).map((handler) =>
        Promise.resolve(handler({ reason, subsystems: uniqueSubsystems, events })).catch(() => undefined)
      )
    );
  } finally {
    invalidating = false;
  }
}

export async function loadNativeSyncCursor(): Promise<NativeSyncCursor | null> {
  try {
    const raw = await AsyncStorage.getItem(SYNC_CURSOR_KEY);
    if (!raw) return null;
    return normalizeCursor(JSON.parse(raw) as NativeSyncCursor);
  } catch {
    await AsyncStorage.removeItem(SYNC_CURSOR_KEY).catch(() => undefined);
    return null;
  }
}

export async function saveNativeSyncCursor(cursor: NativeSyncCursor) {
  await AsyncStorage.setItem(SYNC_CURSOR_KEY, JSON.stringify(normalizeCursor(cursor)));
}

export async function resetNativeSyncCursor() {
  await AsyncStorage.removeItem(SYNC_CURSOR_KEY);
}

export function startNativeEventSync(options: StartNativeSyncOptions = {}) {
  if (activeStop) activeStop();

  const pollIntervalMs = Math.max(15_000, options.pollIntervalMs || DEFAULT_POLL_INTERVAL_MS);
  const poll = (reason: string) =>
    pollNativeSync({
      ...options,
      reason
    }).catch(() => undefined);

  if (options.fullResyncOnStart) poll("startup").catch(() => undefined);
  const interval = setInterval(() => poll("interval"), pollIntervalMs);
  const appState = AppState.addEventListener("change", (state) => {
    if (state === "active") poll("app_active").catch(() => undefined);
  });

  activeStop = () => {
    clearInterval(interval);
    appState.remove();
    if (activeStop === stop) activeStop = null;
  };
  const stop = activeStop;
  return stop;
}

export async function pollNativeSync(options: PollNativeSyncOptions = {}): Promise<NativeSyncPollResult> {
  if (activePoll) return activePoll;
  activePoll = doPollNativeSync(options).finally(() => {
    activePoll = null;
  });
  return activePoll;
}

async function doPollNativeSync(options: PollNativeSyncOptions): Promise<NativeSyncPollResult> {
  const existingCursor = await loadNativeSyncCursor();
  const subsystems = dedupeSubsystems(options.subsystems || DEFAULT_SUBSYSTEMS);
  const reason = options.reason || "poll";

  try {
    const response = await fetchDeltaEvents(options.endpoint || DEFAULT_SYNC_ENDPOINT, existingCursor);
    const events = normalizeEvents([...(options.events || []), ...(response.events || [])]);
    const invalidated = events.length ? dedupeSubsystems(events.flatMap(subsystemsForSyncEvent)) : [];
    const nextCursor = nextCursorFromResponse(existingCursor, response, events);
    await saveNativeSyncCursor(nextCursor);
    if (invalidated.length) await invalidateNativeSync(invalidated, reason, events);
    return { cursor: nextCursor, events, invalidated, fullResync: false, source: events.length ? "delta" : "idle" };
  } catch (error) {
    const shouldFullResync = shouldFallbackToFullRefresh(error) || !existingCursor;
    const fallbackCursor = normalizeCursor({
      ...existingCursor,
      lastSyncedAt: new Date().toISOString(),
      lastFullResyncAt: shouldFullResync ? new Date().toISOString() : existingCursor?.lastFullResyncAt
    });
    await saveNativeSyncCursor(fallbackCursor).catch(() => undefined);
    if (shouldFullResync) await invalidateNativeSync(subsystems, `${reason}:full_resync_fallback`, options.events || []);
    return { cursor: fallbackCursor, events: options.events || [], invalidated: shouldFullResync ? subsystems : [], fullResync: shouldFullResync, source: "fallback" };
  }
}

async function fetchDeltaEvents(endpoint: string, cursor: NativeSyncCursor | null) {
  const query = new URLSearchParams({ limit: "100" });
  if (cursor?.latestEventId) query.set("after_id", cursor.latestEventId);
  if (cursor?.lastEventAt) query.set("after", cursor.lastEventAt);
  return pulseApi<NativeSyncResponse>(`${endpoint}?${query.toString()}`);
}

export function subsystemsForSyncEvent(event: NativeSyncEvent): NativeSyncSubsystem[] {
  const explicit = [...(event.invalidates || []), ...(event.invalidate || [])].filter(isNativeSyncSubsystem);
  if (explicit.length) return dedupeSubsystems(explicit);

  const haystack = [
    event.event_type,
    event.type,
    event.domain,
    event.category,
    event.entity_type,
    event.target_url,
    event.deep_link
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  const result: NativeSyncSubsystem[] = [];
  if (/(order|purchase|payment|checkout|refund|dispute|receipt|shipping)/.test(haystack)) {
    result.push("orders", "activity", "notifications");
  }
  if (/(listing|marketplace|seller|merchant|inventory|product|storefront)/.test(haystack)) {
    result.push("marketplace", "seller_inventory", "activity");
  }
  if (/(message|conversation|chat|thread)/.test(haystack)) {
    result.push("messenger", "activity");
  }
  if (/(call|ring|missed|decline|answer)/.test(haystack)) {
    result.push("calls", "activity", "notifications");
  }
  if (/(notification|badge|inbox|activity)/.test(haystack)) {
    result.push("activity", "notifications");
  }
  if (/(safety|report|block|mute|appeal|enforcement|strike)/.test(haystack)) {
    result.push("safety", "activity", "notifications");
  }
  if (/(verification|badge|identity|kyc)/.test(haystack)) {
    result.push("verification", "activity", "notifications");
  }
  if (/(premium|subscription|entitlement|founder)/.test(haystack)) {
    result.push("premium", "activity", "notifications");
  }
  if (/(alert|intelligence|crypto|market)/.test(haystack)) {
    result.push("intelligence", "activity", "notifications");
  }
  if (/(pulse_status|status_created|status_updated|status_deleted|status_viewed|status_reaction|status_reply|status_shared)/.test(haystack)) {
    result.push("status", "activity");
  }
  return dedupeSubsystems(result.length ? result : ["activity", "notifications"]);
}

function nextCursorFromResponse(existing: NativeSyncCursor | null, response: NativeSyncResponse, events: NativeSyncEvent[]) {
  const latestEvent = events[events.length - 1];
  const responseCursor = typeof response.cursor === "object" ? response.cursor : {};
  return normalizeCursor({
    ...existing,
    ...responseCursor,
    latestEventId:
      stringValue(response.latest_event_id) ||
      stringValue(response.latestEventId) ||
      stringValue(latestEvent?.event_id) ||
      stringValue(latestEvent?.id) ||
      existing?.latestEventId,
    lastEventAt:
      response.last_event_at ||
      response.lastEventAt ||
      latestEvent?.created_at ||
      latestEvent?.updated_at ||
      existing?.lastEventAt,
    lastSyncedAt: new Date().toISOString()
  });
}

function normalizeEvents(events: NativeSyncEvent[]) {
  const seen = new Set<string>();
  return events.filter((event, index) => {
    const id = stringValue(event.event_id) || stringValue(event.id) || `${event.event_type || event.type || "event"}:${event.created_at || event.updated_at || index}`;
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

function normalizeCursor(cursor: NativeSyncCursor | null | undefined): NativeSyncCursor {
  return {
    latestEventId: cursor?.latestEventId ? String(cursor.latestEventId) : undefined,
    lastEventAt: cursor?.lastEventAt,
    lastSyncedAt: cursor?.lastSyncedAt || new Date().toISOString(),
    lastFullResyncAt: cursor?.lastFullResyncAt
  };
}

function shouldFallbackToFullRefresh(error: unknown) {
  if (error instanceof PulseApiError) return [404, 405, 501, 503].includes(error.status);
  return true;
}

function stringValue(value: unknown) {
  return value === undefined || value === null || value === "" ? undefined : String(value);
}

function dedupeSubsystems(subsystems: NativeSyncSubsystem[]) {
  return Array.from(new Set(subsystems.filter(isNativeSyncSubsystem)));
}

function isNativeSyncSubsystem(value: unknown): value is NativeSyncSubsystem {
  return [
    "activity",
    "notifications",
    "orders",
    "marketplace",
    "seller_inventory",
    "messenger",
    "calls",
    "safety",
    "verification",
    "premium",
    "intelligence"
  ].includes(String(value));
}
