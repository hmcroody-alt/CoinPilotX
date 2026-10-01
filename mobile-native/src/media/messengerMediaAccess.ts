import { useCallback, useEffect, useRef, useState } from "react";

import { pulseApi } from "../api/pulseApi";

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

type AccessEntry = { url: string; expiresAt: number };

const accessCache = new Map<number, AccessEntry>();
const inflight = new Map<number, Promise<string>>();

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

export type MessengerMediaGrant = { url: string; attachmentId: number };

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
    return { url: await resolveMessengerMediaAccessUrl(canonical.id), attachmentId: canonical.id };
  } catch (error) {
    if (isExpiredGrant(error)) {
      invalidateMessengerMediaAccess(canonical.id);
      return { url: await resolveMessengerMediaAccessUrl(canonical.id), attachmentId: canonical.id };
    }
    const alternate = isMissingMedia(error) ? canonical.alternates[0] || 0 : 0;
    if (!alternate) throw error;
    return { url: await resolveMessengerMediaAccessUrl(alternate), attachmentId: alternate };
  }
}

async function requestAccessUrl(attachmentId: number): Promise<string> {
  const response = await pulseApi<{ ok?: boolean; access_url?: string; expires_in?: number }>(
    `/api/messages/media/${attachmentId}/access`
  );
  const url = String(response.access_url || "");
  if (!url) throw new Error("messenger_media_access_url_missing");
  const ttlMs = Math.max(0, Number(response.expires_in || 0)) * 1000;
  accessCache.set(attachmentId, { url, expiresAt: Date.now() + ttlMs });
  return url;
}

/**
 * Resolve a renderable URL for an attachment, reusing an unexpired grant.
 *
 * Concurrent callers for the same attachment share one request. That matters:
 * a conversation renders many thumbnails at once, and the whole point of this
 * change is that simultaneous media loads stop looking like suspicious
 * concurrent session activity.
 */
export async function resolveMessengerMediaAccessUrl(attachmentId: number): Promise<string> {
  if (!Number.isFinite(attachmentId) || attachmentId <= 0) throw new Error("messenger_media_attachment_required");
  const cached = accessCache.get(attachmentId);
  if (cached && cached.expiresAt - RENEW_MARGIN_MS > Date.now()) return cached.url;
  const pending = inflight.get(attachmentId);
  if (pending) return pending;
  const request = requestAccessUrl(attachmentId).finally(() => inflight.delete(attachmentId));
  inflight.set(attachmentId, request);
  return request;
}

type AccessSnapshot = {
  url: string;
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
    loading: needsGrant,
    failed: false,
    unavailable: false
  }));

  useEffect(() => {
    if (!needsGrant) {
      setState({ url: fallbackUrl, loading: false, failed: false, unavailable: false });
      return;
    }
    let active = true;
    setState((previous) => ({ url: previous.url, loading: true, failed: false, unavailable: false }));
    const alternates = alternateKey ? alternateKey.split(",").map(Number).filter((id) => id > 0) : [];
    grantMessengerMediaAccess({ id: canonicalId, alternates })
      .then((granted) => {
        if (active) setState({ url: granted.url, loading: false, failed: false, unavailable: false });
      })
      .catch((error) => {
        if (!active) return;
        const gone = isMissingMedia(error);
        if (gone) unavailableFor.current = identityKey;
        setState({ url: "", loading: false, failed: true, unavailable: gone });
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
