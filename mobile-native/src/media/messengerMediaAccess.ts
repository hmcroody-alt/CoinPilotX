import { useCallback, useEffect, useRef, useState } from "react";

import { pulseApi } from "../api/pulseApi";
import { namespacedMediaId } from "./mediaCache";

/**
 * Messenger media access URLs.
 *
 * MEDIA IDENTITY is the attachment id. It is stable, it is what the message
 * carries, and it is what the on-disk cache keys on.
 *
 * MEDIA ACCESS URL is a short-lived, signed, single-attachment credential. It
 * rotates, it expires, and it is never stored as message content.
 *
 * Keeping these apart is what stops the native image loader from participating
 * in mobile session authentication. Handing `<Image>` a protected API path made
 * every thumbnail load run the persistent-cookie refresh path on the server,
 * which rotated the refresh token, tripped reuse/device-mismatch detection, and
 * signed the user out. The image loader cannot send an Authorization header, so
 * the credential has to travel in the URL — bounded to one attachment, one
 * viewer, and one short window.
 */

const PROTECTED_DOWNLOAD_RE = /\/api\/messages\/media\/(\d+)\/download(?:$|[?#])/;

/** Renew a little before real expiry so an in-flight load never races it. */
const RENEW_MARGIN_MS = 60_000;

/**
 * What the bubble needs to draw a card before any pixel of media arrives.
 *
 * The server has always sent this — `/access` embeds the whole attachment row —
 * and this module used to read two URLs off that response and drop the rest. So
 * the renderer knew a video existed but not its shape, its length, or whether
 * the poster was still being made, and the only card it could honestly draw was
 * a file card with the filename on it.
 *
 * `processingStatus` is the difference between "no poster yet" and "no poster
 * ever", which are the same empty string in `thumbnailUrl` and must not look the
 * same on screen.
 */
export type MessengerMediaMeta = {
  mediaType: string;
  durationMs: number;
  width: number;
  height: number;
  processingStatus: string;
  sizeBytes: number;
  filename: string;
};

export const EMPTY_MESSENGER_MEDIA_META: MessengerMediaMeta = {
  mediaType: "",
  durationMs: 0,
  width: 0,
  height: 0,
  processingStatus: "",
  sizeBytes: 0,
  filename: ""
};

/**
 * One grant, both URLs, and the row they describe.
 *
 * The preview and the original are two different objects behind one
 * authorization decision, so they are granted together and cached together.
 * Asking for them separately is what the previous renderer did, and because both
 * requests resolved the same attachment id they came back as the same
 * `/download` URL: the "thumbnail" was the full asset, and every bubble paid for
 * two grants to learn that.
 *
 * `thumbnailUrl` is empty when the pipeline has not produced a preview yet. That
 * is a real state, not a missing value — the caller shows a placeholder rather
 * than substituting the original, which for a 90-minute video would mean
 * downloading gigabytes to paint a card.
 */
type AccessEntry = { url: string; thumbnailUrl: string; meta: MessengerMediaMeta; expiresAt: number };

const accessCache = new Map<number, AccessEntry>();
const inflight = new Map<number, Promise<AccessEntry>>();

export function isProtectedMessengerMediaUrl(url?: string | null): boolean {
  return PROTECTED_DOWNLOAD_RE.test(String(url || ""));
}

/** Recover the canonical foundation media id from a protected download URL. */
export function attachmentIdFromMediaUrl(url?: string | null): number {
  const match = PROTECTED_DOWNLOAD_RE.exec(String(url || ""));
  return match ? Number(match[1]) || 0 : 0;
}

function positiveId(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.trunc(parsed) : 0;
}

/**
 * Everything a message knows about which media row it points at.
 *
 * These are NOT interchangeable integers. `/api/messages/media/<id>/access` is
 * keyed on the FOUNDATION `message_attachments` id and on nothing else:
 *
 *   - `mediaUploadId` is that foundation id, carried through Comm-v2 as
 *     `media_upload_id`.
 *   - `attachmentId` is a transport row id. For Comm-v2 it is the
 *     `comm_v2_message_attachments` row id, which is a DIFFERENT number in the
 *     same message. Historical production rows carried pairs like
 *     attachment_id=422 / media_upload_id=33; asking for 422 is a hard 404.
 */
export type MessengerMediaIdentity = {
  /** Foundation `message_attachments` id, when the payload carries it. */
  mediaUploadId?: number | null;
  /** Transport attachment row id. Not a foundation media id unless proven. */
  attachmentId?: number | null;
  /**
   * Set ONLY by a caller whose contract proves `attachmentId` is already a
   * foundation media id. Absent means "unknown", and unknown is not usable.
   */
  attachmentIdIsFoundationMedia?: boolean;
};

/**
 * The identity a messenger attachment is cached under.
 *
 * Same preference as `resolveCanonicalMessengerMediaId` — foundation id first,
 * transport id only as a fallback — but the answer is namespaced rather than a
 * bare integer, because these two ids come from different tables and their
 * sequences overlap. `media_upload_id = 7` and `attachment_id = 7` are two
 * different files; collapsed to `7` they become one cache entry, and one of the
 * two messages opens the other's attachment. See `namespacedMediaId`.
 *
 * This does NOT separate the two tables that both feed `media_upload_id` (a
 * Comm-v2 upload and a foundation `message_attachments` row). The payload
 * carries no discriminator for that, so it cannot be fixed on this side.
 */
export function messengerMediaCacheIdentity(identity?: MessengerMediaIdentity | null): string | null {
  return (
    namespacedMediaId("media_upload", identity?.mediaUploadId) ??
    namespacedMediaId("attachment", identity?.attachmentId)
  );
}

export type CanonicalMessengerMediaId = {
  id: number;
  source: "media_upload_id" | "media_url" | "attachment_id" | "unresolved";
  /**
   * Other ids that are ALSO proven foundation ids. Used for exactly one
   * bounded recovery attempt when the first choice turns out to be stale. An
   * unproven `attachmentId` never appears here — a retry must not become a
   * second way to request the wrong id.
   */
  alternates: number[];
};

/**
 * Choose the id to ask the access endpoint for, in strict priority order.
 *
 *   1. `media_upload_id`, when valid — the foundation id, stated outright.
 *   2. the id parsed out of a protected `/api/messages/media/<id>/download`
 *      URL. That path is minted by the server FROM the foundation id, so the
 *      URL is direct evidence rather than a sibling integer that happens to be
 *      truthy.
 *   3. `attachmentId`, only when its contract explicitly proves it is already a
 *      foundation media id.
 *   4. unresolved.
 *
 * The previous implementation ranked a bare transport id first and consulted
 * the URL only if that id was falsy, inverting 2 and 3: any truthy transport
 * id shadowed the canonical id sitting in the URL right next to it.
 */
export function resolveCanonicalMessengerMediaId(
  identity?: MessengerMediaIdentity | null,
  mediaUrl?: string | null
): CanonicalMessengerMediaId {
  const source = identity || {};
  const ranked: Array<[CanonicalMessengerMediaId["source"], number]> = [
    ["media_upload_id", positiveId(source.mediaUploadId)],
    ["media_url", attachmentIdFromMediaUrl(mediaUrl)],
    ["attachment_id", source.attachmentIdIsFoundationMedia ? positiveId(source.attachmentId) : 0]
  ];
  const usable = ranked.filter(([, id]) => id > 0);
  if (!usable.length) return { id: 0, source: "unresolved", alternates: [] };
  const [chosenSource, chosenId] = usable[0];
  const alternates: number[] = [];
  for (const [, id] of usable.slice(1)) {
    if (id !== chosenId && !alternates.includes(id)) alternates.push(id);
  }
  return { id: chosenId, source: chosenSource, alternates };
}

/** Cleared on sign-out: a granted URL is scoped to the account that earned it. */
export function resetMessengerMediaAccess(): void {
  accessCache.clear();
  inflight.clear();
}

/** Drop one cached grant. Media-layer only: touches no session state. */
export function invalidateMessengerMediaAccess(attachmentId: number): void {
  accessCache.delete(attachmentId);
}

function errorStatus(error: unknown): number {
  const status = Number((error as { status?: unknown } | null | undefined)?.status);
  return Number.isFinite(status) ? status : 0;
}

function errorCode(error: unknown): string {
  return String((error as { code?: unknown } | null | undefined)?.code || "");
}

/**
 * The id we asked for does not name media this viewer can have. Either the
 * identity was stale/wrong, or the media is genuinely gone. Retrying the SAME
 * id can never fix it.
 */
function isMissingMedia(error: unknown): boolean {
  return errorStatus(error) === 404 || errorCode(error) === "attachment_not_found";
}

/**
 * The grant itself lapsed. A routine, renewable media condition — deliberately
 * NOT an authentication event, and handled without any session call.
 */
function isExpiredGrant(error: unknown): boolean {
  const code = errorCode(error);
  return errorStatus(error) === 410 || code === "media_grant_expired" || code === "media_token_expired";
}

export type MessengerMediaGrant = { url: string; thumbnailUrl: string; meta: MessengerMediaMeta; attachmentId: number };

/**
 * Request a grant for `canonical`, with exactly ONE bounded recovery attempt.
 *
 *   - expired grant            -> drop it, mint one replacement, retry once
 *   - wrong/stale identity and a proven canonical alternate exists
 *                              -> correct the identity, request one new grant
 *   - canonical id truly 404s with no alternate
 *                              -> terminal; the media is unavailable
 *
 * There is no loop here and no recursion: the recovery path calls the plain
 * resolver, whose own failure propagates.
 */
export async function grantMessengerMediaAccess(
  canonical: Pick<CanonicalMessengerMediaId, "id" | "alternates">
): Promise<MessengerMediaGrant> {
  try {
    return { ...(await resolveMessengerMediaAccess(canonical.id)), attachmentId: canonical.id };
  } catch (error) {
    if (isExpiredGrant(error)) {
      invalidateMessengerMediaAccess(canonical.id);
      return { ...(await resolveMessengerMediaAccess(canonical.id)), attachmentId: canonical.id };
    }
    const alternate = isMissingMedia(error) ? canonical.alternates[0] || 0 : 0;
    if (!alternate) throw error;
    return { ...(await resolveMessengerMediaAccess(alternate)), attachmentId: alternate };
  }
}

function nonNegative(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}

function readMeta(attachment: Record<string, unknown> | undefined): MessengerMediaMeta {
  const row = attachment || {};
  return {
    mediaType: String(row.media_type || ""),
    durationMs: nonNegative(row.duration_ms),
    width: nonNegative(row.width),
    height: nonNegative(row.height),
    processingStatus: String(row.processing_status || ""),
    sizeBytes: nonNegative(row.size_bytes),
    filename: String(row.filename || "")
  };
}

async function requestAccessUrl(attachmentId: number): Promise<AccessEntry> {
  const response = await pulseApi<{
    ok?: boolean;
    access_url?: string;
    thumbnail_access_url?: string;
    expires_in?: number;
    attachment?: Record<string, unknown>;
  }>(`/api/messages/media/${attachmentId}/access`);
  const url = String(response.access_url || "");
  if (!url) throw new Error("messenger_media_access_url_missing");
  const ttlMs = Math.max(0, Number(response.expires_in || 0)) * 1000;
  const entry: AccessEntry = {
    url,
    thumbnailUrl: String(response.thumbnail_access_url || ""),
    meta: readMeta(response.attachment),
    expiresAt: Date.now() + ttlMs
  };
  accessCache.set(attachmentId, entry);
  return entry;
}

/**
 * Resolve a renderable URL for an attachment, reusing an unexpired grant.
 *
 * Concurrent callers for the same attachment share one request. That matters:
 * a conversation renders many thumbnails at once, and the whole point of this
 * change is that simultaneous media loads stop looking like suspicious
 * concurrent session activity.
 */
export async function resolveMessengerMediaAccess(
  attachmentId: number
): Promise<{ url: string; thumbnailUrl: string; meta: MessengerMediaMeta }> {
  if (!Number.isFinite(attachmentId) || attachmentId <= 0) throw new Error("messenger_media_attachment_required");
  const cached = accessCache.get(attachmentId);
  if (cached && cached.expiresAt - RENEW_MARGIN_MS > Date.now()) return cached;
  const pending = inflight.get(attachmentId);
  if (pending) return pending;
  const request = requestAccessUrl(attachmentId).finally(() => inflight.delete(attachmentId));
  inflight.set(attachmentId, request);
  return request;
}

/** The original asset's URL. Kept for callers that never show a preview. */
export async function resolveMessengerMediaAccessUrl(attachmentId: number): Promise<string> {
  return (await resolveMessengerMediaAccess(attachmentId)).url;
}

type AccessSnapshot = {
  url: string;
  /**
   * The preview's URL, or "" when the pipeline has not produced one. Never a
   * copy of `url`: a renderer that treats an empty preview as "use the original"
   * turns a thumbnail slot into a full-asset download.
   */
  thumbnailUrl: string;
  /**
   * The attachment row behind the grant. Empty until the grant resolves, so a
   * renderer must treat zeroes as "not known yet" rather than as measurements.
   */
  meta: MessengerMediaMeta;
  loading: boolean;
  failed: boolean;
  /** The canonical id returned a true 404. Retrying will not help. */
  unavailable: boolean;
};

export type MessengerMediaAccessState = AccessSnapshot & {
  /**
   * One-shot recovery for a renderer that watched the granted URL fail to
   * load (an expired grant is the usual cause). Bounded to a single use per
   * identity, and inert once the identity is known to be unavailable.
   */
  retry: () => void;
};

/**
 * Resolve `fallbackUrl` into something the renderer may safely load.
 *
 * A URL that is not a protected download path (an R2 signed URL, a static
 * asset, a local file) is returned untouched — it never needed a grant. Failure
 * is reported as failure, never as a session problem.
 */
export function useMessengerMediaAccessUrl(
  identity: MessengerMediaIdentity | undefined,
  fallbackUrl: string
): MessengerMediaAccessState {
  const canonical = resolveCanonicalMessengerMediaId(identity, fallbackUrl);
  const canonicalId = canonical.id;
  // Arrays are rebuilt every render, so the effect keys on a stable string.
  const alternateKey = canonical.alternates.join(",");
  const needsGrant = canonicalId > 0 && isProtectedMessengerMediaUrl(fallbackUrl);
  const identityKey = `${canonicalId}|${alternateKey}|${fallbackUrl}`;
  const [attempt, setAttempt] = useState(0);
  const retrySpentFor = useRef("");
  const unavailableFor = useRef("");
  const [state, setState] = useState<AccessSnapshot>(() => ({
    url: needsGrant ? "" : fallbackUrl,
    thumbnailUrl: "",
    meta: EMPTY_MESSENGER_MEDIA_META,
    loading: needsGrant,
    failed: false,
    unavailable: false
  }));

  useEffect(() => {
    if (!needsGrant) {
      setState({ url: fallbackUrl, thumbnailUrl: "", meta: EMPTY_MESSENGER_MEDIA_META, loading: false, failed: false, unavailable: false });
      return;
    }
    let active = true;
    setState((previous) => ({
      url: previous.url,
      thumbnailUrl: previous.thumbnailUrl,
      meta: previous.meta,
      loading: true,
      failed: false,
      unavailable: false
    }));
    const alternates = alternateKey ? alternateKey.split(",").map(Number).filter((id) => id > 0) : [];
    grantMessengerMediaAccess({ id: canonicalId, alternates })
      .then((granted) => {
        if (active) {
          setState({
            url: granted.url,
            thumbnailUrl: granted.thumbnailUrl,
            meta: granted.meta,
            loading: false,
            failed: false,
            unavailable: false
          });
        }
      })
      .catch((error) => {
        if (!active) return;
        const gone = isMissingMedia(error);
        if (gone) unavailableFor.current = identityKey;
        setState({ url: "", thumbnailUrl: "", meta: EMPTY_MESSENGER_MEDIA_META, loading: false, failed: true, unavailable: gone });
      });
    return () => {
      active = false;
    };
    // `attempt` is the retry trigger; it is intentionally a dependency.
  }, [needsGrant, canonicalId, alternateKey, fallbackUrl, identityKey, attempt]);

  const retry = useCallback(() => {
    if (!needsGrant) return;
    if (unavailableFor.current === identityKey) return;
    if (retrySpentFor.current === identityKey) return;
    retrySpentFor.current = identityKey;
    invalidateMessengerMediaAccess(canonicalId);
    setAttempt((count) => count + 1);
  }, [needsGrant, identityKey, canonicalId]);

  return { ...state, retry };
}
