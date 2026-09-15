/**
 * Bounded, account-scoped media cache — Stages 5, 30 and 35.
 *
 * ## What this replaces
 *
 * Nothing. Before this module the native app had no media cache at all: not one
 * call site of `downloadAsync`, not one byte written under `cacheDirectory` for
 * media. Every image and video was re-fetched from the CDN on every render, and
 * "save to Photos" did not exist because there was never a local file to save.
 * That is why this arrives as a foundation rather than a refactor.
 *
 * ## Why the cache is namespaced by account, and why that is a P0
 *
 * Stage 35 asks a security question: user A views private chat media, signs out,
 * user B signs in on the same handset — can B see A's thumbnails? With a flat
 * cache the answer is yes, because a cache key derived from a URL is identical
 * for both users and the file outlives the session. So the scope is part of the
 * *path*, not part of the key:
 *
 *     <cacheDirectory>/pulsesoc-media/u1234/<digest>
 *     <cacheDirectory>/pulsesoc-media/anon/<digest>
 *
 * Two consequences fall out of that choice and both matter. Signing out deletes
 * one directory rather than walking an index and hoping every entry was tagged,
 * so the purge cannot be partial. And a cache read while scoped to `u5678`
 * physically cannot resolve to a file written under `u1234`, so the isolation
 * holds even if the index is stale, corrupt, or restored from a backup.
 *
 * `anon` exists because media is legitimately cached before sign-in — public
 * feed previews on the launch screen. It is purged alongside the rest.
 *
 * ## Why the key comes from the shared identity authority
 *
 * PulseSoc serves private media through signed URLs whose signature and expiry
 * rotate on every issue. Keying on the full URL would therefore produce a fresh
 * miss every few minutes for a file that never changed — an unbounded download
 * loop that looks like a cache.
 *
 * Identity derivation is NOT done here. It belongs to `core/media/mediaIdentity`,
 * which the in-memory prefetch cache already uses, and having two modules derive
 * their own key for the same asset is how the memory tier and the disk tier come
 * to disagree about whether something is cached. It also cost us the Mux playback
 * id: that is the most stable identity a video has, the identity authority
 * prefers it, and this module could not see it at all — so the same video cached
 * under two different CDN hosts was two files.
 *
 * ## Why the rendition is part of the key
 *
 * A poster and the video it previews share a media id. Keying on the id alone
 * therefore made them the same cache entry, and whichever downloaded first
 * answered for both: fetch a reel's poster, and the cache would then report the
 * *video* as present and hand a decoder a JPEG. That is the specific reason a
 * rendition is required rather than optional — an entry that cannot say which
 * rendition it holds cannot answer "is this playable offline?" honestly.
 *
 * ## Eviction
 *
 * LRU by last access, with an age ceiling on top. Age matters independently of
 * size because a cache that never fills its quota still holds a year-old private
 * thumbnail forever, and "we were under quota" is not an answer to that.
 *
 * Integrity is verified on every read: the file must exist and its size must
 * match what was recorded at write. A truncated file — the app was killed
 * mid-write, the disk filled — reads back as a miss and is dropped, rather than
 * being handed to a decoder as a black rectangle. This is Stage 4's "never
 * substitute a black rectangle for unknown state" enforced at the storage layer,
 * where it is cheap, instead of in every screen.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  cacheDirectory,
  deleteAsync,
  getFreeDiskStorageAsync,
  getInfoAsync,
  makeDirectoryAsync,
  moveAsync
} from "expo-file-system/legacy";

import {
  mediaCacheKey as identityCacheKey,
  mediaIdentityOf,
  type MediaRendition
} from "../core/media/mediaIdentity";
import { trackMediaEvent } from "./mediaTelemetry";

export type MediaCacheEntry = {
  key: string;
  fileUri: string;
  bytes: number;
  mimeType?: string;
  createdAt: number;
  lastAccessAt: number;
};

export type MediaCacheStats = {
  scope: string;
  entries: number;
  bytes: number;
  maxBytes: number;
};

/**
 * Raised when the device cannot accept another file. Carries a `reason` the
 * telemetry layer understands, so callers surface an actionable message
 * (Stage 30) instead of a stack trace.
 */
export class MediaCacheFullError extends Error {
  readonly reason = "no_disk_space" as const;
  constructor(message = "Not enough storage to download this media.") {
    super(message);
    this.name = "MediaCacheFullError";
  }
}

const ROOT_DIRNAME = "pulsesoc-media";
const INDEX_PREFIX = "pulsesoc.native.mediacache.index.";
const ANON_SCOPE = "anon";

/**
 * Defaults, not constants — `configureMediaCache` moves them so a test can fill
 * the cache with three small files instead of 256MB of fixtures.
 */
let maxBytes = 256 * 1024 * 1024;
let maxAgeMs = 14 * 24 * 60 * 60 * 1000;
/**
 * Headroom left free on the device. Downloading until the disk is empty breaks
 * the OS, not just PulseSoc — iOS starts evicting other apps' caches and the
 * user sees the whole phone misbehave.
 */
let minFreeDiskBytes = 128 * 1024 * 1024;

let scope = ANON_SCOPE;
let indexCache: Record<string, MediaCacheEntry> | null = null;
let indexScope: string | null = null;

export function configureMediaCache(options: {
  maxBytes?: number;
  maxAgeMs?: number;
  minFreeDiskBytes?: number;
}) {
  if (typeof options.maxBytes === "number") maxBytes = options.maxBytes;
  if (typeof options.maxAgeMs === "number") maxAgeMs = options.maxAgeMs;
  if (typeof options.minFreeDiskBytes === "number") minFreeDiskBytes = options.minFreeDiskBytes;
}

/**
 * Point the cache at an account. Pass `null` for signed-out.
 *
 * Callers pass the numeric user id; it is normalized here so a caller cannot
 * accidentally produce a scope containing a path separator and escape the root.
 */
export function setMediaCacheScope(userId: number | string | null) {
  const next = userId ? `u${String(userId).replace(/[^A-Za-z0-9]/g, "")}` : ANON_SCOPE;
  if (next === scope) return;
  scope = next || ANON_SCOPE;
  indexCache = null;
  indexScope = null;
}

export function getMediaCacheScope(): string {
  return scope;
}

function scopeRoot(forScope: string = scope): string {
  return `${cacheDirectory}${ROOT_DIRNAME}/${forScope}/`;
}

function indexKey(forScope: string = scope): string {
  return `${INDEX_PREFIX}${forScope}`;
}

/**
 * Stable, non-cryptographic digest.
 *
 * FNV-1a rather than a crypto hash because this is a filename, not a security
 * boundary — the security boundary is the scope directory above it. A crypto
 * hash would mean pulling in a native module to make cache lookups slower.
 */
function digest(input: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return `${hash.toString(36)}${input.length.toString(36)}`;
}

/**
 * Tag a row id with the id space it came from.
 *
 * A row id is only unique within its own table, and the app routinely holds two
 * of them at once: a Comm-v2 message carries a `chat_media_uploads` id in
 * `media_upload_id` and a `comm_v2_attachments` id in `attachment_id`, drawn
 * from independent autoincrements, so the same number names two unrelated
 * files. Keyed as bare integers they collapse onto one entry and one file on
 * disk — whichever downloads first is what the other one opens.
 *
 * Nothing downstream can catch that. The entry is internally consistent and its
 * recorded size matches the file, so the integrity check on read passes; the
 * user simply gets the wrong document. Separating the id spaces at the point the
 * key is built is the only place it is cheap.
 *
 * Returns null when there is no usable id, which callers pass straight through
 * so the key falls back to the URL. That is honest; a fabricated id is not.
 */
export function namespacedMediaId(namespace: string, id: unknown): string | null {
  const safeNamespace = namespace.replace(/[^A-Za-z0-9_]/g, "").toLowerCase();
  const numeric = Number(id);
  if (!safeNamespace || !Number.isFinite(numeric) || numeric <= 0) return null;
  return `${safeNamespace}:${Math.trunc(numeric)}`;
}

const NAMESPACED_MEDIA_ID = /^[A-Za-z0-9_]+:[0-9]+$/;

/**
 * A bare number keys as itself, so every entry written before namespacing
 * existed still resolves. A namespaced identity is carried through whole and
 * must never meet `Number()`, which turns `attachment:7` into NaN and silently
 * drops the caller back to URL keying — the failure mode being fixed here,
 * wearing a different hat.
 */
function normalizeMediaIdentity(value: number | string | null | undefined): string {
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (NAMESPACED_MEDIA_ID.test(trimmed)) return trimmed.toLowerCase();
  }
  const numeric = Number(value || 0);
  return numeric > 0 ? String(numeric) : "";
}

/**
 * Normalize a media reference to a cache key.
 *
 * Identity comes from the shared authority so the disk tier and the memory tier
 * agree; the rendition is appended so two renditions of one asset are two
 * entries. Defaults to `full` because that is what every existing caller —
 * save-to-gallery, share, open-document — is actually asking for.
 *
 * Returns "" when there is nothing durable to key on. Callers must treat that as
 * "not cacheable" rather than inventing a key: a synthesised one would be unique
 * per call and would consume the budget while never being hit.
 */
export function mediaCacheKey(input: {
  mediaId?: number | string | null;
  url?: string | null;
  rendition?: MediaRendition;
}): string {
  // `0`, "" and "0" all mean "no id" here, but a bare 0 is a finite number and
  // the identity authority would accept it as row zero. Normalising first keeps
  // that judgement in the one place that knows this caller's conventions.
  //
  // The result stays a string all the way into the authority. A namespaced
  // identity must never meet `Number()` -- it yields NaN, which drops the caller
  // back to URL keying and reinstates the collision the namespace exists to
  // prevent. `mediaIdentityOf` takes a string row id, so `media_upload:7` and
  // `attachment:7` derive two identities, while a plain `7` derives the same
  // `media:7` it always did.
  const usableId = normalizeMediaIdentity(input.mediaId) || null;

  const identity = mediaIdentityOf({
    id: usableId,
    media_url: input.url ?? null
  });
  if (!identity) return "";
  return identityCacheKey(identity, input.rendition || "full");
}

async function readIndex(): Promise<Record<string, MediaCacheEntry>> {
  if (indexCache && indexScope === scope) return indexCache;
  const raw = await AsyncStorage.getItem(indexKey()).catch(() => null);
  let parsed: Record<string, MediaCacheEntry> = {};
  if (raw) {
    try {
      const value = JSON.parse(raw);
      if (value && typeof value === "object") parsed = value as Record<string, MediaCacheEntry>;
    } catch {
      // A corrupt index is a cold cache, not a crash. The files it described are
      // orphaned; `sweepOrphans` reclaims them on the next eviction pass.
      parsed = {};
    }
  }
  indexCache = parsed;
  indexScope = scope;
  return parsed;
}

async function writeIndex(next: Record<string, MediaCacheEntry>) {
  indexCache = next;
  indexScope = scope;
  await AsyncStorage.setItem(indexKey(), JSON.stringify(next)).catch(() => undefined);
}

async function ensureRootDirectory() {
  await makeDirectoryAsync(scopeRoot(), { intermediates: true }).catch(() => undefined);
}

/**
 * Where a downloader should write this key. Includes the extension so the OS,
 * the share sheet and Photos all infer the right type from the filename.
 */
export function cacheFileUriFor(key: string, extension = ""): string {
  const safeExtension = extension.replace(/[^A-Za-z0-9.]/g, "");
  const suffix = safeExtension ? (safeExtension.startsWith(".") ? safeExtension : `.${safeExtension}`) : "";
  return `${scopeRoot()}${digest(key)}${suffix}`;
}

/**
 * Look a key up, verifying the file is actually intact.
 *
 * Returns null on miss *and* on any integrity failure, dropping the bad entry on
 * the way out. Callers therefore only ever receive a file they can decode.
 */
export async function lookupCachedMedia(key: string): Promise<MediaCacheEntry | null> {
  if (!key) return null;
  const index = await readIndex();
  const entry = index[key];
  if (!entry) {
    trackMediaEvent({ name: "MEDIA_CACHE_MISS", key });
    return null;
  }

  const now = Date.now();
  if (maxAgeMs > 0 && now - entry.createdAt > maxAgeMs) {
    await dropEntries([entry], "age");
    trackMediaEvent({ name: "MEDIA_CACHE_MISS", key });
    return null;
  }

  const info = await getInfoAsync(entry.fileUri).catch(() => ({ exists: false }) as { exists: boolean });
  const size = Number((info as { size?: number }).size || 0);
  if (!info.exists || (entry.bytes > 0 && size !== entry.bytes)) {
    // Truncated or vanished. Never hand this to a decoder.
    await dropEntries([entry], "corrupt");
    trackMediaEvent({ name: "MEDIA_CACHE_MISS", key, reason: info.exists ? "corrupt" : undefined });
    return null;
  }

  const touched: MediaCacheEntry = { ...entry, lastAccessAt: now };
  await writeIndex({ ...index, [key]: touched });
  trackMediaEvent({ name: "MEDIA_CACHE_HIT", key, bytes: touched.bytes });
  return touched;
}

/**
 * Ask whether a key is present and intact, without counting as a use.
 *
 * `lookupCachedMedia` is the read path: it bumps the LRU and emits hit/miss
 * telemetry, both correct when something is about to consume the bytes. Asking
 * "what do I hold for this media?" is a different question — a playback-state
 * report probes several renditions per asset and would otherwise fabricate a
 * miss for every rendition that was never supposed to exist, and keep entries
 * alive purely by inspecting them.
 *
 * Integrity is still verified, and a bad entry is still dropped. A peek that
 * skipped verification would be the one thing worse than no peek: it would
 * report a vanished file as cached.
 */
export async function peekCachedMedia(key: string): Promise<MediaCacheEntry | null> {
  if (!key) return null;
  const index = await readIndex();
  const entry = index[key];
  if (!entry) return null;

  if (maxAgeMs > 0 && Date.now() - entry.createdAt > maxAgeMs) {
    await dropEntries([entry], "age");
    return null;
  }

  const info = await getInfoAsync(entry.fileUri).catch(() => ({ exists: false }) as { exists: boolean });
  const size = Number((info as { size?: number }).size || 0);
  if (!info.exists || (entry.bytes > 0 && size !== entry.bytes)) {
    await dropEntries([entry], "corrupt");
    return null;
  }
  return entry;
}

/**
 * Make room for `bytes`, or refuse.
 *
 * Called before a download starts rather than after it fails, so the user is
 * told "not enough storage" instead of watching a progress bar reach 90% and
 * die. Evicts first; only then reports the device genuinely full.
 */
export async function ensureRoomFor(bytes: number): Promise<void> {
  await ensureRootDirectory();
  const wanted = Math.max(0, Number(bytes) || 0);

  if (maxBytes > 0 && wanted > 0) {
    const index = await readIndex();
    const used = totalBytes(index);
    if (used + wanted > maxBytes) await evictMediaCache(used + wanted - maxBytes);
  }

  if (wanted > 0) {
    const free = await getFreeDiskStorageAsync().catch(() => Number.POSITIVE_INFINITY);
    if (Number.isFinite(free) && free - wanted < minFreeDiskBytes) {
      // One more eviction pass: our own cache is the storage we are allowed to
      // reclaim, and it may be the reason the disk is tight.
      await evictMediaCache(wanted);
      const freeAfter = await getFreeDiskStorageAsync().catch(() => Number.POSITIVE_INFINITY);
      if (Number.isFinite(freeAfter) && freeAfter - wanted < minFreeDiskBytes) {
        throw new MediaCacheFullError();
      }
    }
  }
}

/**
 * Adopt a finished download into the cache.
 *
 * Takes the file the downloader already wrote. If it landed somewhere else it is
 * moved rather than copied — a copy would briefly double the disk cost of the
 * largest file we handle, which is precisely when the disk is tightest.
 */
export async function commitCachedMedia(input: {
  key: string;
  fileUri: string;
  mimeType?: string;
  destinationUri?: string;
}): Promise<MediaCacheEntry | null> {
  if (!input.key || !input.fileUri) return null;
  await ensureRootDirectory();

  let fileUri = input.fileUri;
  if (input.destinationUri && input.destinationUri !== fileUri) {
    await deleteAsync(input.destinationUri, { idempotent: true }).catch(() => undefined);
    await moveAsync({ from: fileUri, to: input.destinationUri });
    fileUri = input.destinationUri;
  }

  const info = await getInfoAsync(fileUri).catch(() => ({ exists: false }) as { exists: boolean });
  if (!info.exists) return null;
  const bytes = Number((info as { size?: number }).size || 0);
  if (bytes <= 0) {
    // A zero-byte file is Stage 32's malformed-media case reaching storage.
    // Refuse it here so no surface ever renders it.
    await deleteAsync(fileUri, { idempotent: true }).catch(() => undefined);
    return null;
  }

  const now = Date.now();
  const entry: MediaCacheEntry = {
    key: input.key,
    fileUri,
    bytes,
    mimeType: input.mimeType,
    createdAt: now,
    lastAccessAt: now
  };

  const index = await readIndex();
  await writeIndex({ ...index, [input.key]: entry });
  await evictMediaCache(0);
  return entry;
}

function totalBytes(index: Record<string, MediaCacheEntry>): number {
  return Object.values(index).reduce((sum, entry) => sum + (Number(entry.bytes) || 0), 0);
}

/**
 * Evict aged-out entries, then LRU until the cache is under quota with at least
 * `headroom` bytes to spare.
 */
export async function evictMediaCache(headroom = 0): Promise<number> {
  const index = await readIndex();
  const entries = Object.values(index);
  if (!entries.length) return 0;

  const now = Date.now();
  const doomed: MediaCacheEntry[] = [];
  const survivors: MediaCacheEntry[] = [];
  for (const entry of entries) {
    if (maxAgeMs > 0 && now - entry.createdAt > maxAgeMs) doomed.push(entry);
    else survivors.push(entry);
  }

  const budget = Math.max(0, maxBytes - Math.max(0, headroom));
  survivors.sort((a, b) => a.lastAccessAt - b.lastAccessAt);
  let used = totalBytes(Object.fromEntries(survivors.map((entry) => [entry.key, entry])));
  while (maxBytes > 0 && used > budget && survivors.length) {
    const victim = survivors.shift() as MediaCacheEntry;
    doomed.push(victim);
    used -= Number(victim.bytes) || 0;
  }

  if (!doomed.length) return 0;
  await dropEntries(doomed, "quota");
  return doomed.length;
}

async function dropEntries(entries: MediaCacheEntry[], reason: "age" | "quota" | "corrupt") {
  if (!entries.length) return;
  const index = await readIndex();
  const next = { ...index };
  for (const entry of entries) {
    delete next[entry.key];
    await deleteAsync(entry.fileUri, { idempotent: true }).catch(() => undefined);
    trackMediaEvent({
      name: "MEDIA_CACHE_EVICTED",
      key: entry.key,
      bytes: entry.bytes,
      reason: reason === "corrupt" ? "corrupt" : undefined
    });
  }
  await writeIndex(next);
}

/**
 * Delete one entry that a consumer has proven is unusable.
 *
 * `lookupCachedMedia` already drops an entry whose file is missing or the wrong
 * size, but a file can pass both checks and still be unplayable — a download
 * that finished with the right byte count and the wrong bytes, or a container
 * truncated at a boundary the size check cannot see. Only the player that tried
 * to open it knows that, and without this it would have no way to say so: the
 * entry would be re-served on every attempt and the media would be permanently
 * broken for that account until an unrelated eviction happened to reach it.
 *
 * Reported as `corrupt` rather than `quota` so the eviction telemetry does not
 * read as budget pressure.
 */
export async function dropCachedMedia(key: string): Promise<boolean> {
  if (!key) return false;
  const index = await readIndex();
  const entry = index[key];
  if (!entry) return false;
  await dropEntries([entry], "corrupt");
  return true;
}

export async function mediaCacheStats(): Promise<MediaCacheStats> {
  const index = await readIndex();
  return { scope, entries: Object.keys(index).length, bytes: totalBytes(index), maxBytes };
}

/**
 * Delete one account's cache. Directory first, index second — in that order a
 * crash in between leaves a stale index pointing at nothing, which reads as a
 * cold cache. The reverse order would leave orphaned files with no index entry
 * to ever reclaim them.
 */
export async function clearMediaCache(forScope: string = scope): Promise<void> {
  await deleteAsync(scopeRoot(forScope), { idempotent: true }).catch(() => undefined);
  await AsyncStorage.removeItem(indexKey(forScope)).catch(() => undefined);
  if (forScope === scope) {
    indexCache = {};
    indexScope = scope;
  }
}

/**
 * Stage 35. Delete *every* account's cached media, not just the active scope.
 *
 * Signing out from scope `u1234` must not leave `u5678`'s files behind from an
 * earlier session on this handset — the next person to sign in as 5678 would
 * inherit them, and more to the point a forensic read of the device would find
 * private media belonging to someone who has already left. Removing the whole
 * root, then sweeping the index keys, covers both the scopes we know about and
 * any whose index was lost.
 */
export async function clearAllMediaCaches(): Promise<void> {
  await deleteAsync(`${cacheDirectory}${ROOT_DIRNAME}`, { idempotent: true }).catch(() => undefined);
  const keys = await AsyncStorage.getAllKeys().catch(() => [] as readonly string[]);
  const indexKeys = keys.filter((key) => key.startsWith(INDEX_PREFIX));
  if (indexKeys.length) await AsyncStorage.multiRemove([...indexKeys]).catch(() => undefined);
  indexCache = {};
  indexScope = scope;
}

/** Test-only: drop the in-memory index so the next read hits AsyncStorage. */
export function __resetMediaCacheMemory() {
  indexCache = null;
  indexScope = null;
  scope = ANON_SCOPE;
  maxBytes = 256 * 1024 * 1024;
  maxAgeMs = 14 * 24 * 60 * 60 * 1000;
  minFreeDiskBytes = 128 * 1024 * 1024;
}
