import AsyncStorage from "@react-native-async-storage/async-storage";

/**
 * The canonical client cache. Every `loadCached*` / `cache*` helper in `src/api`
 * funnels through here, so this file is the one place a latency improvement
 * reaches all of them at once.
 *
 * WHY A MEMORY TIER
 *
 * `AsyncStorage.getItem` is a round trip across the native bridge. That cost is
 * paid per read, and the hot screens read the same key repeatedly — a mount, a
 * focus, a parent and a child header all asking for the same profile inside one
 * interaction. Serving those from memory turns a bridge hop into a resolved
 * microtask.
 *
 * WHAT IS STORED IN MEMORY
 *
 * The serialized string, not the parsed object. Returning a shared parsed
 * reference would hand two screens the same mutable object, and a caller that
 * spread-merged into it would corrupt the other's state. Re-parsing per read
 * keeps every caller's object private. The parse is not the expensive part; the
 * bridge is.
 *
 * ON STALENESS
 *
 * `maxAgeMs` bounds the MEMORY tier only, and that is deliberate rather than an
 * omission. Pretending to enforce a TTL against an entry whose age is unknown
 * would be a lie that financial screens might then trust. See the performance
 * constitution: cached values may be *displayed*, but are never financial or
 * security authority.
 *
 * That reasoning is unchanged. What changed is that disk age is no longer
 * unknown: entries are now written inside a small envelope carrying the time
 * they were stored, so a screen can say "last updated 12 minutes ago" as a
 * fact rather than as a guess. The fix for an unknowable age is to record it,
 * not to invent a TTL on top of it.
 *
 * READING BOTH FORMATS
 *
 * Entries written by earlier builds are bare JSON values with no timestamp, and
 * there are ~156 call sites, so a flag-day migration would mean either a very
 * large single change or a build in which every cache read misses at once —
 * exactly the cold-start blanking this cache exists to avoid. The reader
 * therefore accepts both shapes and callers migrate as they are rewritten.
 *
 * A legacy entry reports `storedAt: null`, which means UNKNOWN and must stay
 * unknown. Defaulting it to "now" would be the same lie in a new place: a value
 * cached six months ago would present itself as fresh on first read. Anything
 * rendering freshness has to handle the null, and that is the point of it.
 */

/**
 * The on-disk envelope.
 *
 * `v` is both a version and the discriminator. A bare cached value could in
 * principle be an object, so the check below requires the exact version number
 * *and* the presence of `value` before treating something as an envelope —
 * a looser check would misread a cached record that happened to have a `v`
 * field as an envelope and hand callers `undefined`.
 */
type CacheEnvelope<T> = { v: 1; storedAt: number; value: T };

const ENVELOPE_VERSION = 1 as const;

function isEnvelope<T>(parsed: unknown): parsed is CacheEnvelope<T> {
  return (
    typeof parsed === "object" &&
    parsed !== null &&
    (parsed as { v?: unknown }).v === ENVELOPE_VERSION &&
    "value" in (parsed as Record<string, unknown>) &&
    typeof (parsed as { storedAt?: unknown }).storedAt === "number"
  );
}

/**
 * `cachedAt` is when this entered the memory tier; `storedAt` is when the value
 * was actually written, which may be days earlier and may be unknown.
 *
 * They are kept apart on purpose. `maxAgeMs` continues to mean exactly what it
 * meant before — how long since this became resident — so no existing caller's
 * behaviour moves. Freshness reporting reads `storedAt`, which is the question
 * callers were really asking but could not previously get an answer to.
 */
type MemoryEntry = { raw: string; cachedAt: number; storedAt: number | null };

/**
 * Bounded so a long session that visits many profiles/conversations cannot grow
 * the resident set without limit. Insertion order is the eviction order and a
 * read re-inserts, which makes this a plain LRU.
 */
const MEMORY_ENTRY_LIMIT = 64;

const memory = new Map<string, MemoryEntry>();

function remember(key: string, raw: string, storedAt: number | null) {
  memory.delete(key);
  memory.set(key, { raw, cachedAt: Date.now(), storedAt });
  while (memory.size > MEMORY_ENTRY_LIMIT) {
    const oldest = memory.keys().next().value;
    if (oldest === undefined) break;
    memory.delete(oldest);
  }
}

function recall(key: string, maxAgeMs?: number): MemoryEntry | null {
  const entry = memory.get(key);
  if (!entry) return null;
  if (typeof maxAgeMs === "number" && Date.now() - entry.cachedAt > maxAgeMs) {
    memory.delete(key);
    return null;
  }
  // Re-insert so the most recently used key is evicted last.
  memory.delete(key);
  memory.set(key, entry);
  return entry;
}

export type ReadJsonCacheOptions = {
  /**
   * Bounds the in-memory tier only, by residency age. Unchanged in meaning from
   * before the envelope existed, deliberately: ~156 call sites pass this and
   * none of them should shift behaviour because the storage format grew a
   * timestamp.
   */
  maxAgeMs?: number;
  /**
   * Refuse a disk entry written longer ago than this. Opt-in, and off by
   * default.
   *
   * Off by default because the usual correct response to a stale cache is to
   * *show it with its age* and revalidate behind it, not to withhold it — an
   * expiry here turns a slightly-out-of-date screen into an empty one, which is
   * worse for the reader and is the failure this cache exists to prevent. It is
   * offered for the minority of callers where showing something old is worse
   * than showing nothing.
   *
   * Only ever applied when the age is actually known. A legacy entry is not
   * assumed to be expired, because it is not assumed to be anything.
   */
  maxDiskAgeMs?: number;
};

/** A cached value together with what is known about its age. */
export type JsonCacheEntry<T> = {
  value: T;
  /** When the value was written, or null when it predates the envelope. */
  storedAt: number | null;
  /** Convenience: age in ms, or null when `storedAt` is unknown. */
  ageMs: number | null;
};

/**
 * Read a cached value along with its age.
 *
 * Prefer this over {@link readJsonCache} anywhere the screen reports freshness
 * to the reader — the whole reason the envelope exists is so that "Last updated
 * 12 min ago" is a measurement.
 */
export async function readJsonCacheEntry<T>(
  key: string,
  normalize: (value: T) => T,
  options: ReadJsonCacheOptions = {}
): Promise<JsonCacheEntry<T> | null> {
  const remembered = recall(key, options.maxAgeMs);
  if (remembered) {
    try {
      return entryOf(normalize(JSON.parse(remembered.raw) as T), remembered.storedAt);
    } catch {
      // A corrupt memory entry is not worth trusting; fall through to disk,
      // which will either succeed or clear itself below.
      memory.delete(key);
    }
  }
  try {
    const cached = await AsyncStorage.getItem(key);
    if (!cached) return null;
    const parsed = JSON.parse(cached) as unknown;

    // Both shapes land here. An envelope yields a real timestamp; anything else
    // is a value written by an earlier build and its age is unknown.
    const isWrapped = isEnvelope<T>(parsed);
    const rawValue = isWrapped ? (parsed as CacheEnvelope<T>).value : (parsed as T);
    const storedAt = isWrapped ? (parsed as CacheEnvelope<T>).storedAt : null;

    if (
      typeof options.maxDiskAgeMs === "number" &&
      storedAt !== null &&
      Date.now() - storedAt > options.maxDiskAgeMs
    ) {
      memory.delete(key);
      await AsyncStorage.removeItem(key).catch(() => undefined);
      return null;
    }

    // Remember the unwrapped, un-normalized value: `normalize` is contracted to
    // run on every read, including memory hits.
    remember(key, JSON.stringify(rawValue), storedAt);
    return entryOf(normalize(rawValue), storedAt);
  } catch {
    memory.delete(key);
    await AsyncStorage.removeItem(key).catch(() => undefined);
    return null;
  }
}

function entryOf<T>(value: T, storedAt: number | null): JsonCacheEntry<T> {
  return { value, storedAt, ageMs: storedAt === null ? null : Math.max(0, Date.now() - storedAt) };
}

/**
 * Read a cached value.
 *
 * Kept exactly as it was so that every existing caller is unaffected by the
 * storage format change. Callers that need the age use
 * {@link readJsonCacheEntry}.
 */
export async function readJsonCache<T>(
  key: string,
  normalize: (value: T) => T,
  options: ReadJsonCacheOptions = {}
): Promise<T | null> {
  const entry = await readJsonCacheEntry<T>(key, normalize, options);
  return entry ? entry.value : null;
}

export async function writeJsonCache<T>(key: string, value: T) {
  const storedAt = Date.now();
  const raw = JSON.stringify(value);
  remember(key, raw, storedAt);
  await AsyncStorage.setItem(key, JSON.stringify({ v: ENVELOPE_VERSION, storedAt, value } satisfies CacheEnvelope<T>));
}

/**
 * Drop a key from the memory tier.
 *
 * Required by any code path that removes or rewrites a cache key through
 * `AsyncStorage` directly instead of through `writeJsonCache`. Without this the
 * memory tier would keep serving a value the caller believes it deleted.
 */
export function invalidateJsonCache(key: string) {
  memory.delete(key);
}

/** Clears the whole memory tier. For sign-out and for test isolation. */
export function resetJsonCacheMemory() {
  memory.clear();
}
