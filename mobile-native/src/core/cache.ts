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
 * omission. The on-disk format is a bare JSON value with no stored timestamp,
 * so the age of a disk entry is genuinely unknown — it may predate the install
 * of this build. Pretending to enforce a TTL against it would be a lie that
 * financial screens might then trust. Disk behaviour is therefore unchanged
 * from before this tier existed, and callers that need freshness guarantees
 * must still revalidate against the server. See the performance constitution:
 * cached values may be *displayed*, but are never financial or security
 * authority.
 */

type MemoryEntry = { raw: string; storedAt: number };

/**
 * Bounded so a long session that visits many profiles/conversations cannot grow
 * the resident set without limit. Insertion order is the eviction order and a
 * read re-inserts, which makes this a plain LRU.
 */
const MEMORY_ENTRY_LIMIT = 64;

const memory = new Map<string, MemoryEntry>();

function remember(key: string, raw: string) {
  memory.delete(key);
  memory.set(key, { raw, storedAt: Date.now() });
  while (memory.size > MEMORY_ENTRY_LIMIT) {
    const oldest = memory.keys().next().value;
    if (oldest === undefined) break;
    memory.delete(oldest);
  }
}

function recall(key: string, maxAgeMs?: number): string | null {
  const entry = memory.get(key);
  if (!entry) return null;
  if (typeof maxAgeMs === "number" && Date.now() - entry.storedAt > maxAgeMs) {
    memory.delete(key);
    return null;
  }
  // Re-insert so the most recently used key is evicted last.
  memory.delete(key);
  memory.set(key, entry);
  return entry.raw;
}

export type ReadJsonCacheOptions = {
  /** Bounds the in-memory tier only — see the note above about disk age. */
  maxAgeMs?: number;
};

export async function readJsonCache<T>(
  key: string,
  normalize: (value: T) => T,
  options: ReadJsonCacheOptions = {}
): Promise<T | null> {
  const remembered = recall(key, options.maxAgeMs);
  if (remembered !== null) {
    try {
      return normalize(JSON.parse(remembered) as T);
    } catch {
      // A corrupt memory entry is not worth trusting; fall through to disk,
      // which will either succeed or clear itself below.
      memory.delete(key);
    }
  }
  try {
    const cached = await AsyncStorage.getItem(key);
    if (!cached) return null;
    const value = normalize(JSON.parse(cached) as T);
    remember(key, cached);
    return value;
  } catch {
    memory.delete(key);
    await AsyncStorage.removeItem(key).catch(() => undefined);
    return null;
  }
}

export async function writeJsonCache<T>(key: string, value: T) {
  const raw = JSON.stringify(value);
  remember(key, raw);
  await AsyncStorage.setItem(key, raw);
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
