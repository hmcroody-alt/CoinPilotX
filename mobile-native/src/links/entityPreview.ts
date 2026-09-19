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
 * through to the post — and `getPublicProfile`, which is the same
 * `GET /api/pulse/profile/:key` `ProfileScreen` calls on mount. Identical
 * request, identical authorization, by construction rather than by review:
 * there is no second code path that could be made more permissive than the
 * first, because there is no second code path. A post the viewer may not open
 * returns the same 403 it would return on a tap, and the card says so.
 *
 * This cuts both ways, and that is the point. The profile route does not
 * currently refuse a viewer the target has blocked; neither, therefore, does
 * the card. The card is not *more* permissive than the screen, which is the
 * invariant worth having — and when that rule is tightened it is tightened in
 * one place and both inherit it. A card with its own authorization would have
 * had to be found and fixed separately, by someone who remembered it existed.
 *
 * The cost of this is that a preview is a real fetch. That is what the caching
 * below is for.
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
import { getPublicProfile, loadCachedProfile, PulseProfile } from "../api/profile";
import { resolveProfileTarget } from "../api/profileTarget";
import { getReelSharePreview, loadCachedReelSharePreview, PulseReelSharePreview } from "../api/reels";
import { PulseEntityRef } from "./pulseEntity";

/**
 * One shape for every kind, on purpose.
 *
 * A post and a profile are different objects but they are the *same card*: a
 * picture, somebody's name and handle, a line of their words, a way in. Giving
 * each kind its own preview type would have meant giving each kind its own
 * card component, and two cards drift — one of them gets the fix for the video
 * poster bug, or the unavailable state, or the a11y label, and the other does
 * not. So the fields are named for their role in the card rather than for
 * their origin in the payload: `caption` is a post's body and a profile's bio,
 * `thumbnailUrl` is a post's first still and a profile's cover.
 *
 * A reel joined without widening the type, which is the test of whether the
 * shape was right. Its poster is a `thumbnailUrl`, its creator is an author,
 * its caption is a caption, and `video` is `true` — the flag that already
 * existed to badge a post carrying video is the same flag that draws a reel's
 * play indicator, because it is the same question being asked.
 */
export type EntityPreview = {
  kind: "post" | "profile" | "reel";
  url: string;
  path: string;
  /** Display name, already trimmed. Empty when the server sent none. */
  authorName: string;
  /** Without the `@`; the card adds it. Empty when the server sent none. */
  authorHandle: string;
  authorAvatarUrl: string;
  thumbnailUrl: string;
  /** Short, flattened. Empty for a post with no caption or a bio-less profile. */
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
 * A profile, in the same five fields a post uses.
 *
 * The mapping is deliberate rather than incidental: a profile's cover is the
 * card's picture, its bio is the card's caption, and its owner is the card's
 * author — a profile is the one entity whose author is itself. `video` is
 * always false, which is not a stub: a profile has no video to badge, and the
 * badge only ever renders over a thumbnail the card actually has.
 */
export function previewFromProfile(profile: PulseProfile, ref: PulseEntityRef): EntityPreview {
  return {
    kind: "profile",
    url: ref.url,
    path: ref.path,
    authorName: flatten(profile.display_name || profile.full_name || ""),
    // `public_player_id` is the fallback rather than the primary because it is
    // the machine-facing handle; a person who has chosen a username should see
    // the one they chose.
    authorHandle: flatten(profile.username || profile.public_player_id || "").replace(/^@/, ""),
    // The small avatar first: the card draws it at 26pt, so the full-size asset
    // would be a larger download for an identical number of pixels.
    authorAvatarUrl: flatten(profile.avatar_thumbnail_url || profile.avatar_url || ""),
    thumbnailUrl: flatten(profile.cover_url || profile.banner_url || ""),
    caption: shortCaption(profile.bio || ""),
    video: false
  };
}

/**
 * A reel, in the same fields a post uses.
 *
 * There is no `previewImage` call here and no `feedRenderableMedia` walk,
 * because there is no media list to walk: the server sends one already-chosen
 * still and nothing else. That asymmetry with `previewFromPost` is deliberate
 * rather than incomplete — a post preview is a projection this client performs
 * over a payload built for a player, while a reel preview is a projection the
 * *server* performs, precisely so the playback urls never cross the wire into
 * a chat bubble that can be forwarded onward.
 *
 * `video` is unconditionally true. A reel is a video; a record that arrived
 * with a missing or wrong media type must not make the card stop drawing its
 * play indicator over a clip that is really there. The one thing that can
 * suppress the indicator is the absence of a poster, and that is enforced in
 * the card rather than here: the badge is only ever drawn over a picture that
 * exists.
 */
export function previewFromReel(reel: PulseReelSharePreview, ref: PulseEntityRef): EntityPreview {
  return {
    kind: "reel",
    // The *sender's* url, not the server's canonical one. Someone who pasted a
    // link with a `?pulse_src=share` on it sent that link, and a tap should
    // open what they sent rather than a tidier rewrite of it.
    url: ref.url,
    path: ref.path,
    authorName: flatten(reel.author.display_name),
    authorHandle: flatten(reel.author.username).replace(/^@/, ""),
    authorAvatarUrl: flatten(reel.author.avatar_url),
    thumbnailUrl: flatten(reel.poster_url),
    caption: shortCaption(reel.caption),
    video: true
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

/**
 * The live read, per kind. Each arm calls the destination screen's own loader.
 *
 * `null` means "the server answered, and the answer was nothing" — a 200 with
 * no object — which is `missing` rather than an error. Throwing is left to the
 * API layer so the single `catch` below classifies every kind identically.
 */
async function fetchEntity(ref: PulseEntityRef): Promise<EntityPreview | null> {
  if (ref.kind === "profile") {
    const target = resolveProfileTarget(ref.id);
    if (!target) return null;
    const profile = await getPublicProfile(target);
    // A payload with no `user_id` is not a person; normalizeProfile will still
    // hand back an object, so the emptiness has to be checked rather than
    // assumed away by the type.
    return profile?.user_id ? previewFromProfile(profile, ref) : null;
  }
  if (ref.kind === "reel") {
    // `GET /api/pulse/reels/:id` — the same route, the same viewer scoping and
    // the same 404 a tap would meet. It answers a projection rather than the
    // player payload, so the card cannot receive a playback url even by
    // accident; see `pulse_reel_share_preview` in `bot.py`.
    const reel = await getReelSharePreview(ref.id);
    return reel ? previewFromReel(reel, ref) : null;
  }
  const detail = await getPostDetail(ref.id);
  return detail.post ? previewFromPost(detail.post, ref) : null;
}

/**
 * Whatever this device already holds for the entity. Only consulted offline.
 *
 * Both loaders write their own cache and both destination screens read it for
 * exactly this reason. A card that has the object sitting in storage and still
 * draws "unavailable" because the network is down is choosing the worse of two
 * available answers.
 */
async function cachedEntity(ref: PulseEntityRef): Promise<EntityPreview | null> {
  if (ref.kind === "profile") {
    const target = resolveProfileTarget(ref.id);
    if (!target) return null;
    const profile = await loadCachedProfile(target).catch(() => null);
    return profile?.user_id ? previewFromProfile(profile, ref) : null;
  }
  if (ref.kind === "reel") {
    // Written by `getReelSharePreview` on the way past, so a reel whose card
    // has been drawn once this install survives the network going away. It is
    // the preview that is cached, never the reel -- there is nothing playable
    // in it to go stale.
    const reel = await loadCachedReelSharePreview(ref.id).catch(() => null);
    return reel ? previewFromReel(reel, ref) : null;
  }
  const cached = await loadCachedPostDetail(ref.id).catch(() => null);
  return cached?.post ? previewFromPost(cached.post, ref) : null;
}

async function fetchPreview(ref: PulseEntityRef): Promise<EntityPreviewState> {
  try {
    const preview = await fetchEntity(ref);
    if (!preview) return { status: "unavailable", reason: "missing" };
    return { status: "ready", preview };
  } catch (error) {
    const { state, cacheable } = stateForError(error);
    if (!cacheable) {
      const preview = await cachedEntity(ref).catch(() => null);
      if (preview) return { status: "ready", preview };
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
