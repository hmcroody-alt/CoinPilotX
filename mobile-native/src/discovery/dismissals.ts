/**
 * Dismissed discovery modules — §3 ("persist dismissals so a dismissed module
 * does not immediately return").
 *
 * ## Why dismissal expires
 *
 * A permanent dismissal and a session dismissal are both wrong. Permanent means
 * one annoyed swipe in month one removes a module forever, with no surface
 * anywhere in the app to bring it back — the user cannot undo a decision they
 * did not know they were making. Session-only means the module is back on the
 * next cold start, which reads as the dismiss button not working.
 *
 * So a dismissal lasts a fixed number of days from when it was made. Long
 * enough that "I don't want this" is respected well past the moment of
 * irritation, bounded so the feed can recover without a settings screen that
 * does not exist yet.
 *
 * ## Why the store is read once into memory
 *
 * Placement runs on every feed recomputation and must be synchronous — it is a
 * pure function and the tests depend on that. An async read inside it would
 * make the first render of every feed show a module the user dismissed, then
 * pop it out a frame later. Instead Home hydrates this once on mount and passes
 * the resulting set down; writes update memory first and persist in the
 * background, so a dismiss takes effect on the very next render.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import type { DiscoveryModuleKind } from "./discoveryRows";

const STORAGE_KEY = "pulse.discovery.dismissed.v1";

/** How long a dismissal is honoured. */
export const DISMISSAL_TTL_DAYS = 30;
const TTL_MS = DISMISSAL_TTL_DAYS * 24 * 60 * 60 * 1000;

type DismissalRecord = Partial<Record<DiscoveryModuleKind, number>>;

let memory: DismissalRecord = {};
let hydrated = false;

function livingKinds(record: DismissalRecord, now: number): Set<DiscoveryModuleKind> {
  const out = new Set<DiscoveryModuleKind>();
  for (const [kind, at] of Object.entries(record)) {
    if (typeof at !== "number" || !Number.isFinite(at)) continue;
    if (now - at >= TTL_MS) continue;
    out.add(kind as DiscoveryModuleKind);
  }
  return out;
}

/**
 * Read persisted dismissals. Safe to call repeatedly; a corrupt or absent value
 * is treated as "nothing dismissed" rather than throwing into Home's mount.
 */
export async function loadDismissedModules(now: number = Date.now()): Promise<Set<DiscoveryModuleKind>> {
  if (!hydrated) {
    try {
      const raw = await AsyncStorage.getItem(STORAGE_KEY);
      const parsed = raw ? (JSON.parse(raw) as unknown) : null;
      memory = parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as DismissalRecord) : {};
    } catch {
      memory = {};
    }
    hydrated = true;
  }
  return livingKinds(memory, now);
}

/** Dismiss a module. Returns the new set so the caller can re-render at once. */
export function dismissModule(
  kind: DiscoveryModuleKind,
  now: number = Date.now()
): Set<DiscoveryModuleKind> {
  memory = { ...memory, [kind]: now };
  hydrated = true;
  // Persisted in the background: a failed write costs the user one extra
  // dismissal after a restart, and is not worth blocking the tap on.
  AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(memory)).catch(() => undefined);
  return livingKinds(memory, now);
}

/** Test-only: forget everything, including the hydrated flag. */
export function __resetDismissals() {
  memory = {};
  hydrated = false;
}
