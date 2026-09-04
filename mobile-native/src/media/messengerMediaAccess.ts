import { useEffect, useState } from "react";

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

/** Recover the canonical attachment id from a legacy protected download URL. */
export function attachmentIdFromMediaUrl(url?: string | null): number {
  const match = PROTECTED_DOWNLOAD_RE.exec(String(url || ""));
  return match ? Number(match[1]) || 0 : 0;
}

/** Cleared on sign-out: a granted URL is scoped to the account that earned it. */
export function resetMessengerMediaAccess(): void {
  accessCache.clear();
  inflight.clear();
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

export type MessengerMediaAccessState = {
  url: string;
  loading: boolean;
  failed: boolean;
};

/**
 * Resolve `fallbackUrl` into something the renderer may safely load.
 *
 * A URL that is not a protected download path (an R2 signed URL, a static
 * asset, a local file) is returned untouched — it never needed a grant. Failure
 * is reported as failure, never as a session problem.
 */
export function useMessengerMediaAccessUrl(
  attachmentId: number | undefined,
  fallbackUrl: string
): MessengerMediaAccessState {
  const resolvedId = Number(attachmentId || 0) || attachmentIdFromMediaUrl(fallbackUrl);
  const needsGrant = resolvedId > 0 && isProtectedMessengerMediaUrl(fallbackUrl);
  const [state, setState] = useState<MessengerMediaAccessState>(() => ({
    url: needsGrant ? "" : fallbackUrl,
    loading: needsGrant,
    failed: false
  }));

  useEffect(() => {
    if (!needsGrant) {
      setState({ url: fallbackUrl, loading: false, failed: false });
      return;
    }
    let active = true;
    setState((previous) => ({ url: previous.url, loading: true, failed: false }));
    resolveMessengerMediaAccessUrl(resolvedId)
      .then((url) => {
        if (active) setState({ url, loading: false, failed: false });
      })
      .catch(() => {
        if (active) setState({ url: "", loading: false, failed: true });
      });
    return () => {
      active = false;
    };
  }, [needsGrant, resolvedId, fallbackUrl]);

  return state;
}
