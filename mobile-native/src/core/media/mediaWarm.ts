/**
 * The primitives that actually move bytes.
 *
 * Everything above this file decides *what* to warm and *in what order*. This
 * file is the only place that knows *how*, and it is deliberately the smallest
 * surface in the foundation: two operations, both bounded, both cancellable in
 * the sense the platform allows.
 *
 * WHY VIDEO WARMING IS NOT "FETCH THE VIDEO"
 *
 * A 90-minute upload is on the order of a gigabyte. Prefetching it to make the
 * first frame appear sooner would spend the user's data plan and the device's
 * disk on content they may watch four seconds of. What actually costs time
 * before the first frame is not the bytes -- it is DNS, TLS, the CDN's cold
 * edge, and for HLS the round trip that fetches the manifest before any segment
 * can even be named. Those are cheap to pay early.
 *
 * So a video warm fetches the manifest (kilobytes), or for a progressive file a
 * capped range from the front (the header and enough to start). Never the body.
 * VIDEO_PREFETCH_BYTE_CAP is the ceiling and videoWarmPlan is pure so that the
 * bound is a thing a test can assert rather than a promise in a comment.
 */

import { Image } from "react-native";

import { absoluteApiUrl } from "../../api/config";
import { isVideoMedia, renditionUrl } from "./mediaIdentity";
import type { MediaDescriptor, MediaRendition } from "./mediaIdentity";
import type { PrefetchSignal } from "./mediaPrefetchQueue";
import { PrefetchCancelledError } from "./mediaPrefetchQueue";

/**
 * 512KB. Large enough to carry an MP4 moov atom and the first seconds of most
 * encodes, small enough that warming four videos costs less than one feed
 * photo at full resolution.
 */
export const VIDEO_PREFETCH_BYTE_CAP = 512 * 1024;

/** Manifests are text. Anything past this is a malformed or hostile response. */
export const MANIFEST_BYTE_CAP = 64 * 1024;

export type VideoWarmPlan =
  | { kind: "hls"; url: string; maxBytes: number }
  | { kind: "progressive"; url: string; maxBytes: number }
  | { kind: "none"; reason: "no_url" | "not_video" };

/**
 * Decide what a video warm is allowed to fetch.
 *
 * Pure, and the only producer of a video warm request. The `maxBytes` field is
 * never absent and never Infinity: a plan that could not name its own ceiling
 * would be indistinguishable from downloading the whole asset.
 */
export function videoWarmPlan(media: MediaDescriptor): VideoWarmPlan {
  if (!isVideoMedia(media)) return { kind: "none", reason: "not_video" };
  const raw = renditionUrl(media, "manifest") ?? renditionUrl(media, "full");
  if (!raw) return { kind: "none", reason: "no_url" };
  const url = absoluteApiUrl(raw);
  if (!url) return { kind: "none", reason: "no_url" };

  // Query strings carry signatures, so the extension has to be read from the
  // path alone -- `.m3u8?token=...` is still HLS.
  const path = url.split("#")[0]?.split("?")[0] ?? url;
  if (/\.m3u8$/i.test(path)) return { kind: "hls", url, maxBytes: MANIFEST_BYTE_CAP };
  return { kind: "progressive", url, maxBytes: VIDEO_PREFETCH_BYTE_CAP };
}

export type WarmResult = { ok: boolean; bytes: number; url: string };

/**
 * Warm an image into the platform image cache.
 *
 * Image.prefetch has no cancellation, so cancelling mid-flight cannot stop the
 * download -- it stops us *recording* the result, which keeps a cancelled warm
 * from occupying a cache entry that a live surface needs. Pretending otherwise
 * would be the more dangerous lie.
 */
export async function warmImageUrl(url: string, signal?: PrefetchSignal): Promise<WarmResult> {
  const resolved = absoluteApiUrl(url);
  if (!resolved) return { ok: false, bytes: 0, url: "" };
  signal?.throwIfCancelled();
  const ok = await Image.prefetch(resolved).catch(() => false);
  signal?.throwIfCancelled();
  return { ok: Boolean(ok), bytes: 0, url: resolved };
}

/**
 * Warm a video's front matter.
 *
 * The body is read and discarded. That looks wasteful and is the point: reading
 * it is what completes the response so the URL lands in the platform HTTP cache
 * and the connection to the CDN stays warm for the player that follows. What is
 * *not* done is reading more than the plan allows.
 */
export async function warmVideoPlan(
  plan: VideoWarmPlan,
  signal?: PrefetchSignal,
  fetchImpl: typeof fetch = fetch
): Promise<WarmResult> {
  if (plan.kind === "none") return { ok: false, bytes: 0, url: "" };
  signal?.throwIfCancelled();

  const headers: Record<string, string> =
    plan.kind === "progressive" ? { Range: `bytes=0-${plan.maxBytes - 1}` } : {};

  try {
    const response = await fetchImpl(plan.url, { method: "GET", headers });
    signal?.throwIfCancelled();
    if (!response.ok && response.status !== 206) return { ok: false, bytes: 0, url: plan.url };

    // A server that ignores Range answers 200 with the entire file. Reading it
    // would be the exact unbounded download this module exists to prevent, so
    // the response is dropped on the floor: the connection and the DNS entry
    // are still warm, which was most of the value.
    if (plan.kind === "progressive" && response.status === 200) {
      const declared = Number(response.headers?.get?.("content-length") ?? 0);
      if (!Number.isFinite(declared) || declared > plan.maxBytes) {
        return { ok: true, bytes: 0, url: plan.url };
      }
    }

    const body = await response.text();
    signal?.throwIfCancelled();
    return { ok: true, bytes: Math.min(body.length, plan.maxBytes), url: plan.url };
  } catch (error) {
    if (error instanceof PrefetchCancelledError) throw error;
    return { ok: false, bytes: 0, url: plan.url };
  }
}

/**
 * The image renditions worth warming for a descriptor, in the order they pay
 * off: whatever will be painted first, then nothing else.
 *
 * A video contributes only its poster here. Warming a video's `full` rendition
 * as an image would download the video through the image cache, which is both
 * the §16 failure and an unbounded one.
 */
export function imageWarmTargets(media: MediaDescriptor, rendition: MediaRendition): string | null {
  if (rendition === "manifest") return null;
  if (isVideoMedia(media) && rendition !== "poster" && rendition !== "thumb") return null;
  const raw = renditionUrl(media, rendition);
  if (!raw) return null;
  const resolved = absoluteApiUrl(raw);
  return resolved || null;
}
