/**
 * The device translation cache (Stage 6).
 *
 * ## Two namespaces that never meet
 *
 * A `private` translation — a direct message, a support ticket — lives in
 * memory and is never written to disk, never keyed into the public store and
 * never leaves the process. A `public` translation may persist, because it is
 * text that was already published to an audience.
 *
 * The isolation is structural rather than conditional: the two namespaces are
 * two different maps, only one of them has a writer that can reach
 * `AsyncStorage`, and {@link translationCacheKey} stamps the namespace into the
 * key itself so a lookup in the wrong map cannot accidentally hit.
 *
 * ## Why private text is not persisted-and-encrypted
 *
 * Stage 6 asks for private translations to be device-only and encrypted at
 * rest. Not writing them at all is the stronger form of that: there is no rest.
 * The trade normally made for a cache — spend storage, save a network round
 * trip — does not apply here, because the provider that produced a private
 * translation is Apple's on-device engine, so a cache miss costs a local
 * inference and no money, no network and no latency worth a permanent plaintext
 * liability on the filesystem. Offline behaviour is unaffected for the same
 * reason: the model, not the cache, is what makes it work offline.
 *
 * ## Why the key carries a digest and not the text
 *
 * Stage 6 forbids raw private text in cache keys, logs and telemetry. The
 * digest below is an identity function, not a security primitive — it is
 * unsalted and small, so it must never be treated as a way to *hide* text. What
 * it buys is that a key which may be enumerated by a debugger, a crash report
 * or a future disk writer does not read as the message.
 */

import { invalidateJsonCache, readJsonCache, writeJsonCache } from "../../core/cache";
import type { TranslationPrivacy, TranslationProviderId } from "./types";

/** Bumped when the entry shape or the key grammar changes. */
const CACHE_VERSION = "v1";

/** Public entries kept on disk. Bounded so the single persisted blob stays small. */
const MAX_PERSISTED_ENTRIES = 200;

/** Entries kept in memory per namespace before the least-recently-used is dropped. */
const MAX_MEMORY_ENTRIES = 400;

/** How long a cached translation may be served. */
const ENTRY_TTL_MS = 7 * 24 * 60 * 60 * 1000;

const PERSIST_DEBOUNCE_MS = 1500;

const DISK_KEY_PREFIX = "translation:public:";

export type TranslationCacheEntry = {
  translatedText: string;
  sourceLanguage: string | null;
  targetLanguage: string;
  provider: Exclude<TranslationProviderId, "unavailable" | "cache">;
  storedAt: number;
};

export type TranslationCacheKeyParts = {
  privacy: TranslationPrivacy;
  /** The authenticated user this entry belongs to. `anon` when signed out. */
  userScope: string;
  contentId: string;
  contentVersion?: string | null;
  text: string;
  sourceLanguage: string | null;
  targetLanguage: string;
  /** Provider plus model identity, so an engine upgrade invalidates its output. */
  providerVersion: string;
};

/**
 * FNV-1a over UTF-16 code units, base-36. Deterministic across launches, which
 * is what a cache key needs and what `Hasher` on the Swift side deliberately is
 * not — the native digest is process-seeded because it only has to dedupe
 * in-flight work, whereas this one has to match an entry written yesterday.
 */
export function digestText(text: string) {
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return `${hash.toString(36)}${text.length.toString(36)}`;
}

/**
 * Normalised before digesting so that trailing whitespace or a re-wrapped
 * paragraph is a cache hit rather than a fresh inference.
 */
function normalizeForKey(text: string) {
  return text.replace(/\s+/g, " ").trim();
}

export function translationCacheKey(parts: TranslationCacheKeyParts) {
  return [
    CACHE_VERSION,
    parts.privacy,
    parts.userScope,
    parts.contentId,
    parts.contentVersion || "-",
    digestText(normalizeForKey(parts.text)),
    parts.sourceLanguage || "auto",
    parts.targetLanguage,
    parts.providerVersion
  ].join("|");
}

type Namespace = { entries: Map<string, TranslationCacheEntry>; persist: boolean };

const namespaces: Record<TranslationPrivacy, Namespace> = {
  private: { entries: new Map(), persist: false },
  public: { entries: new Map(), persist: true }
};

let userScope = "anon";
let hydratedScope: string | null = null;
let persistTimer: ReturnType<typeof setTimeout> | null = null;

export function currentCacheScope() {
  return userScope;
}

/**
 * Point the cache at a user, or at nobody.
 *
 * Both namespaces are dropped whenever the scope changes, including on logout
 * and on account switch, which is the Stage 6 requirement that one account can
 * never read another's translations. Dropping the *public* namespace too is
 * deliberate: its entries are keyed by user scope and would never be read
 * again, so keeping them would only leave which posts the previous account
 * translated sitting on disk.
 */
export function setTranslationCacheScope(nextScope: string | null | undefined) {
  const resolved = nextScope ? String(nextScope) : "anon";
  if (resolved === userScope) return;
  userScope = resolved;
  clearTranslationCache("scope_changed");
}

export function readTranslationCache(
  privacy: TranslationPrivacy,
  key: string
): TranslationCacheEntry | null {
  const namespace = namespaces[privacy];
  const entry = namespace.entries.get(key);
  if (!entry) return null;
  if (Date.now() - entry.storedAt > ENTRY_TTL_MS) {
    namespace.entries.delete(key);
    return null;
  }
  // Re-insert so iteration order is least-recently-used first.
  namespace.entries.delete(key);
  namespace.entries.set(key, entry);
  return entry;
}

export function writeTranslationCache(
  privacy: TranslationPrivacy,
  key: string,
  entry: TranslationCacheEntry
) {
  const namespace = namespaces[privacy];
  namespace.entries.delete(key);
  namespace.entries.set(key, entry);
  while (namespace.entries.size > MAX_MEMORY_ENTRIES) {
    const oldest = namespace.entries.keys().next();
    if (oldest.done) break;
    namespace.entries.delete(oldest.value);
  }
  if (namespace.persist) schedulePersist();
}

/**
 * Drop every translation of these content ids, in both namespaces.
 *
 * Called when content is edited or deleted. Matching is on the `contentId`
 * field of the key rather than on a separate index, so an entry cannot survive
 * because an index missed it.
 */
export function invalidateTranslationContent(contentIds: Iterable<string>) {
  const targets = new Set(Array.from(contentIds, id => String(id)));
  if (targets.size === 0) return;
  let removedPublic = false;
  for (const privacy of ["private", "public"] as const) {
    const namespace = namespaces[privacy];
    for (const key of Array.from(namespace.entries.keys())) {
      const contentId = key.split("|")[3];
      if (targets.has(contentId)) {
        namespace.entries.delete(key);
        if (namespace.persist) removedPublic = true;
      }
    }
  }
  if (removedPublic) schedulePersist();
}

/**
 * Empty both namespaces and the persisted blob.
 *
 * `reason` is recorded by the caller, not here — logout, account deletion and a
 * security reset all land on this function and the cache itself has no opinion
 * about which happened.
 */
export function clearTranslationCache(_reason: string) {
  namespaces.private.entries.clear();
  namespaces.public.entries.clear();
  if (persistTimer) {
    clearTimeout(persistTimer);
    persistTimer = null;
  }
  const scopeToClear = hydratedScope;
  hydratedScope = null;
  if (scopeToClear) {
    invalidateJsonCache(DISK_KEY_PREFIX + scopeToClear);
    void writeJsonCache(DISK_KEY_PREFIX + scopeToClear, []).catch(() => {
      /* best effort: a failed clear is retried on the next scope change */
    });
  }
}

/**
 * Load the persisted public entries for the current scope.
 *
 * Safe to call repeatedly; it only does work once per scope. Nothing awaits it
 * on a render path — a cold cache is a cache miss, never a delayed first paint
 * (Stage 7).
 */
export async function hydrateTranslationCache() {
  const scope = userScope;
  if (hydratedScope === scope) return;
  hydratedScope = scope;
  // `normalize` is identity here because every entry is validated field by
  // field below; a normalizer would be a second, weaker copy of that.
  const stored = await readJsonCache<Array<[string, TranslationCacheEntry]>>(
    DISK_KEY_PREFIX + scope,
    value => value
  );
  if (!Array.isArray(stored)) return;
  if (userScope !== scope) return;
  const now = Date.now();
  for (const pair of stored) {
    if (!Array.isArray(pair) || pair.length !== 2) continue;
    const [key, entry] = pair;
    if (typeof key !== "string" || !entry || typeof entry.translatedText !== "string") continue;
    // A persisted key that does not name this scope is not ours to serve.
    if (key.split("|")[2] !== scope) continue;
    if (key.split("|")[1] !== "public") continue;
    if (now - entry.storedAt > ENTRY_TTL_MS) continue;
    if (namespaces.public.entries.has(key)) continue;
    namespaces.public.entries.set(key, entry);
  }
}

function schedulePersist() {
  if (persistTimer) return;
  persistTimer = setTimeout(() => {
    persistTimer = null;
    void flushTranslationCache();
  }, PERSIST_DEBOUNCE_MS);
}

export async function flushTranslationCache() {
  const scope = userScope;
  const entries = Array.from(namespaces.public.entries.entries());
  const keep = entries.slice(Math.max(0, entries.length - MAX_PERSISTED_ENTRIES));
  try {
    await writeJsonCache(DISK_KEY_PREFIX + scope, keep);
  } catch {
    /* best effort: losing the persisted copy costs a re-translation, nothing else */
  }
}

export function translationCacheSizeForTests() {
  return { private: namespaces.private.entries.size, public: namespaces.public.entries.size };
}

export function resetTranslationCacheForTests() {
  namespaces.private.entries.clear();
  namespaces.public.entries.clear();
  if (persistTimer) {
    clearTimeout(persistTimer);
    persistTimer = null;
  }
  userScope = "anon";
  hydratedScope = null;
}
