/**
 * Client-side call capabilities: server-decided flags and participant limits.
 *
 * The server is the single owner of "is multi-guest on" and "how many people
 * fit in a call". The client fetches this once per session, caches it, and
 * gates every multi-guest affordance (add-participant button, group start,
 * picker selection count) on the cached value. Defaults are conservative:
 * until the server says otherwise, group calling is OFF.
 *
 * NOT a protected realtime-audio path: no audio/engine work here.
 */

import { useSyncExternalStore } from "react";
import { readJsonCache, writeJsonCache } from "../core/cache";
import { getCallCapabilities, normalizeCapabilities, PulseCallCapabilities } from "../api/calls";

const CAPABILITIES_CACHE_KEY = "pulsesoc.native.calls.capabilities";
const REFRESH_INTERVAL_MS = 5 * 60 * 1000;

const DEFAULT_CAPABILITIES: PulseCallCapabilities = normalizeCapabilities({
  provider: "agora",
  group_calls_enabled: false
});

let capabilities: PulseCallCapabilities = DEFAULT_CAPABILITIES;
let lastFetchedAtMs = 0;
let fetchInFlight: Promise<PulseCallCapabilities> | null = null;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

export function getCachedCallCapabilities(): PulseCallCapabilities {
  return capabilities;
}

export function subscribeCallCapabilities(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Fetch fresh capabilities from the server (deduped; throttled unless forced).
 * Failures keep the last known value — never flip features off on a network
 * blip, and never flip them on without a server answer.
 */
export async function refreshCallCapabilities(force = false): Promise<PulseCallCapabilities> {
  if (fetchInFlight) return fetchInFlight;
  if (!force && lastFetchedAtMs && Date.now() - lastFetchedAtMs < REFRESH_INTERVAL_MS) {
    return capabilities;
  }
  fetchInFlight = (async () => {
    try {
      const fresh = await getCallCapabilities();
      capabilities = fresh;
      lastFetchedAtMs = Date.now();
      emit();
      await writeJsonCache(CAPABILITIES_CACHE_KEY, fresh).catch(() => undefined);
    } catch {
      /* keep last known value */
    } finally {
      fetchInFlight = null;
    }
    return capabilities;
  })();
  return fetchInFlight;
}

/** Hydrate from disk cache (fast path on cold start), then refresh from server. */
export async function loadCallCapabilities(): Promise<PulseCallCapabilities> {
  if (!lastFetchedAtMs) {
    const cached = await readJsonCache<PulseCallCapabilities>(CAPABILITIES_CACHE_KEY, normalizeCapabilities).catch(
      () => null
    );
    if (cached && !lastFetchedAtMs) {
      capabilities = normalizeCapabilities(cached);
      emit();
    }
  }
  return refreshCallCapabilities();
}

export function groupCallsEnabled(): boolean {
  return capabilities.group_calls_enabled === true;
}

export function maxParticipantsFor(callType: "audio" | "video"): number {
  const limit =
    callType === "video" ? capabilities.max_video_participants : capabilities.max_audio_participants;
  return Math.max(2, Number(limit || 0) || 2);
}

/** React binding for UI gating. */
export function useCallCapabilities(): PulseCallCapabilities {
  return useSyncExternalStore(subscribeCallCapabilities, getCachedCallCapabilities, getCachedCallCapabilities);
}

export function __resetCallCapabilitiesForTests() {
  capabilities = DEFAULT_CAPABILITIES;
  lastFetchedAtMs = 0;
  fetchInFlight = null;
}
