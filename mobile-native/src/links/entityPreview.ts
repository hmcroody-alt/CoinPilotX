/**
 * Turning a recognised PulseSoc link into something a card can draw.
 *
 * ## Authorization is not re-implemented here, and that is the whole design
 *
 * A preview of a post is a partial disclosure of that post. Anything that shows
 * an author, a caption and a thumbnail to someone who cannot open the post has
 * published exactly what the post's visibility existed to prevent — and it has
 * done it in a *conversation*, where the person did not even ask to see it.
 *
 * So the preview does not have a preview endpoint. It calls `getPostDetail`,
 * which is the same `GET /api/pulse/posts/:id` the app calls when someone taps
 * through to the post. Identical request, identical authorization, by
 * construction rather than by review: there is no second code path that could
 * be made more permissive than the first, because there is no second code path.
 * A post the viewer may not open returns the same 403 it would return on a tap,
 * and the card says so.
 *
 * The cost of this is that a preview is a real post fetch. That is what the
 * caching below is for.
 *
 * ## The cache has two shapes because failures do
 *
 * A 403 or a 404 is an *answer*. The post is not available to this viewer and
 * will not become available because the chat scrolled; re-asking on every
 * re-render would be a request storm that learns nothing. Those are cached.
 *
 * A dropped connection is not an answer. Caching it would leave a card reading
 * "unavailable" for the rest of the session on a post that is perfectly fine,
 * and the user's only recovery would be to restart the app. Those are not
 * cached, so the next render of that card tries again.
 *
 * ## Two layers of dedupe, deliberately
 *
 * `resolved` stops the second *render* from refetching. `inFlight` stops the
 * second *concurrent* render from starting a parallel fetch before the first
 * has anything to cache — which is the common case, because a conversation page
 * mounts twenty bubbles at once and several may quote the same post. A cache
 * without an in-flight table dedupes everything except the burst that actually
 * needed it.
 */

import { useEffect, useState } from "react";
import { getPostDetail, loadCachedPostDetail, PulsePost } from "../api/feed";
import { PulseApiError } from "../api/pulseApi";
import { mediaPosterUrl, feedRenderableMedia } from "../api/feed";
import { PulseEntityRef } from "./pulseEntity";

export type EntityPreview = {
  kind: "post";
  url: string;
  path: string;
  /** Display name, already trimmed. Empty when the server sent none. */
  authorName: string;
  /** Without the `@`; the card adds it. Empty when the server sent none. */
  authorHandle: string;
  authorAvatarUrl: string;
  thumbnailUrl: string;
  /** Short, flattened. Empty for a post with no caption. */
  caption: string;
  /** Whether the post carries video, so the card can mark the thumbnail. */
  video: boolean;
};

export type EntityPreviewState =
  /** No answer yet. The card draws its shell at full size so nothing reflows. */
  | { status: "loading" }
  | { status: "ready"; preview: EntityPreview }
  /** A definite no: gone, or not for this viewer. Carries the line to show. */
  | { status: "unavailable"; reason: "forbidden" | "missing" | "error" };

const CAPTION_LIMIT = 140;

const resolved = new Map<string, EntityPreviewState>();
const inFlight = new Map<string, Promise<EntityPreviewState>>();

function entityKey(ref: PulseEntityRef) {
  return `${ref.kind}:${ref.id}`;
}

function flatten(value: unknown) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function shortCaption(value: string) {
  const flat = flatten(value);
  if (flat.length <= CAPTION_LIMIT) return flat;
  const clipped = flat.slice(0, CAPTION_LIMIT);
  const lastSpace = clipped.lastIndexOf(" ");
  const base = lastSpace > CAPTION_LIMIT * 0.6 ? clipped.slice(0, lastSpace) : clipped;
  return `${base.replace(/[\s.,;:!?-]+$/, "")}…`;
}

/**
 * The first frame worth showing, if there is one.
 *
 * Only media the feed itself considers renderable is eligible, so the card and
 * the post agree about what the post looks like. A record the feed would skip
 * is skipped here too rather than producing a card with a broken image in it.
 *
 * It asks for the *poster*, not the display URL. Asking for the display URL is
 * what made every shared video post draw a black rectangle with a "Video" badge
 * over it: `mediaDisplayUrl` prefers `playback_url`, the card handed an `.m3u8`
 * to an `<Image>`, and the image reported no error while drawing nothing. The
 * badge was the giveaway -- it only renders inside the branch that has a
 * thumbnail, so the card believed it had a picture the whole time.
 *
 * A video record with no still yields `""` and the loop moves on, which is why
 * the post-level fallback below still matters: it is the last chance to find a
 * picture before the card renders with none.
 */
function previewImage(post: PulsePost) {
  const media = feedRenderableMedia(post.media || post.media_assets || post.attachments);
  for (const record of media) {
    const url = flatten(mediaPosterUrl(record));
    if (url) return url;
  }
  return flatten(post.thumbnail_url || post.image_url || "");
}

function hasVideo(post: PulsePost) {
  if (post.video_url) return true;
  return (post.media || post.media_assets || []).some((record) =>
    String(record?.media_type || record?.type || "").toLowerCase().includes("video")
  );
}

export function previewFromPost(post: PulsePost, ref: PulseEntityRef): EntityPreview {
  const author = post.author || post.user || {};
  return {
    kind: "post",
    url: ref.url,
    path: ref.path,
    authorName: flatten(author.display_name || author.name || post.author_name || ""),
    authorHandle: flatten(author.username || author.handle || post.author_username || "").replace(/^@/, ""),
    authorAvatarUrl: flatten(author.avatar_url || post.author_avatar_url || ""),
    thumbnailUrl: previewImage(post),
    caption: shortCaption(post.body || post.text || post.content || ""),
    video: hasVideo(post)
  };
}

/**
 * A terminal answer is cached; a transport failure is not.
 *
 * 401 is grouped with 403: from the card's point of view "you are not signed in
 * for this" and "you are not allowed this" produce the same line, and telling
 * the two apart in a chat bubble would be a disclosure rather than a kindness.
 */
function stateForError(error: unknown): { state: EntityPreviewState; cacheable: boolean } {
  const status = error instanceof PulseApiError ? error.status : 0;
  if (status === 403 || status === 401) return { state: { status: "unavailable", reason: "forbidden" }, cacheable: true };
  if (status === 404 || status === 410) return { state: { status: "unavailable", reason: "missing" }, cacheable: true };
  return { state: { status: "unavailable", reason: "error" }, cacheable: false };
}

async function fetchPreview(ref: PulseEntityRef): Promise<EntityPreviewState> {
  try {
    const detail = await getPostDetail(ref.id);
    if (!detail.post) return { status: "unavailable", reason: "missing" };
    return { status: "ready", preview: previewFromPost(detail.post, ref) };
  } catch (error) {
    const { state, cacheable } = stateForError(error);
    if (!cacheable) {
      /**
       * Offline, but this post may already be on the device.
       *
       * `getPostDetail` writes its own cache, and the post detail screen reads
       * it for exactly this reason. A card that has the post sitting in storage
       * and still draws "unavailable" because the network is down is choosing
       * the worse of two available answers.
       */
      const cached = await loadCachedPostDetail(ref.id).catch(() => null);
      if (cached?.post) return { status: "ready", preview: previewFromPost(cached.post, ref) };
    }
    if (cacheable) resolved.set(entityKey(ref), state);
    return state;
  }
}

/** Resolve once per entity, joining any resolution already running. */
export function resolveEntityPreview(ref: PulseEntityRef): Promise<EntityPreviewState> {
  const key = entityKey(ref);
  const cached = resolved.get(key);
  if (cached) return Promise.resolve(cached);
  const running = inFlight.get(key);
  if (running) return running;
  const promise = fetchPreview(ref)
    .then((state) => {
      if (state.status === "ready") resolved.set(key, state);
      return state;
    })
    .finally(() => {
      inFlight.delete(key);
    });
  inFlight.set(key, promise);
  return promise;
}

/** Test seam. Nothing in the app calls this; a stale process is the point. */
export function clearEntityPreviewCache() {
  resolved.clear();
  inFlight.clear();
}

/**
 * The card's view of an entity.
 *
 * Returns a cached answer on the *first* render rather than flashing a shell
 * and settling: a conversation that scrolls back to a post it has already shown
 * should not blink. The effect only runs for entities with no answer yet.
 */
export function useEntityPreview(ref: PulseEntityRef | null): EntityPreviewState {
  const key = ref ? entityKey(ref) : "";
  const [state, setState] = useState<EntityPreviewState>(() => (key && resolved.get(key)) || { status: "loading" });

  useEffect(() => {
    if (!ref) return undefined;
    const cached = resolved.get(key);
    if (cached) {
      setState(cached);
      return undefined;
    }
    let active = true;
    setState({ status: "loading" });
    resolveEntityPreview(ref)
      .then((next) => {
        if (active) setState(next);
      })
      .catch(() => {
        // `resolveEntityPreview` resolves rather than rejects, but a card must
        // not be the thing that takes the conversation down if that ever stops
        // being true.
        if (active) setState({ status: "unavailable", reason: "error" });
      });
    return () => {
      active = false;
    };
    // `ref` is rebuilt every render by the body parser; `key` is its identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return state;
}
