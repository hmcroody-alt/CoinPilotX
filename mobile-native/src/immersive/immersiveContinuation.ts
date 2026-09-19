/**
 * Where the next page of immersive media comes from.
 *
 * `immersiveSession.ts` decides *when* a continuation is warranted and what to
 * do with the result; this module is the only part of the engine that talks to
 * the network. The split is deliberate — the session's rules (dedupe, cursor
 * movement, origin return) are the ones that fail silently, so they are tested
 * without a fetch anywhere near them, and this module is kept thin enough that
 * its own tests are about *mapping*, not about behaviour.
 *
 * ## The parity rule: the same query the origin would have made
 *
 * Continuation calls the endpoint the origin surface already uses, with the
 * origin's own parameters. Not a new "immersive feed" endpoint, and not a
 * generic one. The reason is that ranking is a property of the query: a user who
 * taps a post in Following and swipes onward is still in Following, and serving
 * them `for_you` from item two onward would be a silent re-ranking they never
 * asked for. It would also be the second differently-ranked query this engine
 * exists to avoid.
 *
 * A practical consequence worth naming: because the engine issues the *same*
 * request with the *same* feed key, the cache write that `listFeed` and
 * `listReels` perform at offset 0 writes exactly what the origin screen would
 * have written itself. The clobber is benign by construction rather than by
 * luck — and offset 0 is refused anyway, for the reason below.
 *
 * ## Kind is decided by the endpoint, never by the payload (§38)
 *
 * `listFeed` returns posts and `listReels` returns reels. So an entry's `kind`
 * is a fact about which door the page came through, and is stamped here rather
 * than read out of a field. A payload that mislabels itself — a reel row that
 * arrives through the feed with `content_type: "video"` — cannot therefore
 * collide with the reel of the same number in the session's seen-set, which is
 * the whole failure this engine's id namespacing exists to prevent.
 */
import { PulsePost, listFeed, feedRenderableMedia } from "../api/feed";
import { PulseReel, listReels } from "../api/reels";
import { listPublicProfilePosts } from "../api/profile";
import type { ProfileTargetInput, NativeProfileTarget } from "../api/profileTarget";
import type { ImmersiveEntry, ImmersiveSource } from "./immersiveSession";

/**
 * Everything needed to ask for more, beyond what the session already holds.
 *
 * Kept separate from `ImmersiveOrigin` rather than folded into it, because the
 * two describe different facts: the origin names *which row on which surface*,
 * and this names *which query*. `source: "PROFILE"` plus the tapped post does
 * not say whose profile. Folding it in would also make the session module — the
 * one that is deliberately pure — import profile and reel API types.
 */
export type ImmersiveContinuationRequest = {
  source: ImmersiveSource;
  /** The offset to fetch from. This is the session's `nextCursor`. */
  cursor: number;
  /** Reels lane, when the origin was Reels. Defaults to the lane Reels opens on. */
  lane?: string;
  /** Whose profile, when the origin was a profile. */
  profile?: ProfileTargetInput | NativeProfileTarget;
  limit?: number;
};

/**
 * A page, in the shape `appendImmersivePage` consumes.
 *
 * `exhausted` is carried through from the server's `has_more` rather than
 * inferred from an empty `entries`, because `entries` can be empty after
 * filtering — a page of text posts, a page of livestreams — while more media
 * genuinely remains behind it. The session makes the same distinction for the
 * same reason; this is where the server's word enters.
 */
export type ImmersivePage = {
  entries: ImmersiveEntry[];
  nextCursor: number;
  exhausted: boolean;
  /** Set when the fetch failed. The session is not advanced or ended on a failure. */
  error?: unknown;
};

const DEFAULT_LIMIT = 12;

/** The feed key each home tab is multiplexed on, matching `HomeScreen`'s `FEED_TABS`. */
const HOME_FEED_KEYS: Partial<Record<ImmersiveSource, string>> = {
  HOME_FOR_YOU: "for_you",
  HOME_FOLLOWING: "following",
  HOME_FRIENDS: "friends"
};

/**
 * A post becomes an entry only if there is something to look at.
 *
 * Text posts are real posts and belong in the feed; they do not belong in a
 * full-screen media queue, where they would be a frame the user swipes into and
 * finds blank. `feedRenderableMedia` is reused rather than reimplemented so that
 * "has media" means the same thing here as it does in the feed row the user
 * tapped — a post that showed a picture in the feed and nothing in the immersive
 * view would be the same bug in a new place.
 */
function postToEntry(post: PulsePost): ImmersiveEntry | null {
  const id = Number(post?.post_id || post?.id || 0);
  if (!Number.isFinite(id) || id <= 0) return null;
  if (!feedRenderableMedia(post?.media).length) return null;
  const authorId = Number(post?.author?.user_id || post?.author?.id || post?.user_id || 0);
  return { kind: "post", id, ...(authorId > 0 ? { authorId } : {}) };
}

/**
 * A reel becomes an entry unless it is a livestream.
 *
 * Live is checked explicitly rather than left to `normalizeReel`'s negative id.
 * That negative id *would* be rejected downstream by `entryKey`, but relying on
 * the sign of a number another module chose is a coupling that breaks the day
 * that module stops choosing it, and it would break by admitting a live session
 * into the immersive queue — which is squarely inside the livestream hard lock.
 * Two independent nets, and this is the one that says why.
 */
function reelToEntry(reel: PulseReel): ImmersiveEntry | null {
  const type = String(reel?.content_type || reel?.post_type || "").toLowerCase();
  if (type === "live" || Number(reel?.live_session_id || reel?.live?.live_session_id || 0) > 0) return null;
  const id = Number(reel?.reel_id || reel?.id || 0);
  if (!Number.isFinite(id) || id <= 0) return null;
  const authorId = Number(reel?.author?.user_id || reel?.author?.id || 0);
  return { kind: "reel", id, ...(authorId > 0 ? { authorId } : {}) };
}

function mapEntries<T>(items: readonly T[] | null | undefined, to: (item: T) => ImmersiveEntry | null) {
  const entries: ImmersiveEntry[] = [];
  for (const item of items || []) {
    const entry = to(item);
    if (entry) entries.push(entry);
  }
  return entries;
}

/**
 * Fetch the next page for a session.
 *
 * ## Why offset 0 is refused
 *
 * The only thing offset 0 can return is the page the session was already seeded
 * from, so the round trip buys nothing: every item dedupes away against `seen`.
 * Worse, it is reachable in a loop — a server that answers with `next_offset: 0`
 * would leave the session permanently one swipe from the end and permanently
 * requesting the same page, which is an unbounded request loop against
 * production wearing the costume of a working feed. Reporting it as exhausted
 * ends the session honestly instead.
 *
 * ## Why a failure is not an ending
 *
 * A dropped connection means "not right now", not "there is no more media".
 * Returning `exhausted: false` with the cursor unmoved leaves the session in
 * exactly the state it was in, so a later swipe can try again. The alternative —
 * treating a timeout as the end of the feed — permanently truncates a session
 * because of one bad moment on a train.
 */
export async function fetchImmersivePage(request: ImmersiveContinuationRequest): Promise<ImmersivePage> {
  const cursor = Math.trunc(Number(request?.cursor));
  if (!Number.isFinite(cursor) || cursor <= 0) {
    return { entries: [], nextCursor: 0, exhausted: true };
  }
  const limit = Math.max(1, Math.trunc(Number(request?.limit) || DEFAULT_LIMIT));

  try {
    const homeFeed = HOME_FEED_KEYS[request.source];
    if (homeFeed) {
      const data = await listFeed({ feed: homeFeed, tab: homeFeed, limit, offset: cursor });
      return {
        entries: mapEntries(data.posts, postToEntry),
        nextCursor: Number(data.next_offset ?? cursor + (data.posts?.length || 0)),
        exhausted: !data.has_more
      };
    }

    if (request.source === "PROFILE") {
      if (!request.profile) return { entries: [], nextCursor: cursor, exhausted: true };
      const data = await listPublicProfilePosts(request.profile, { limit, offset: cursor, mediaOnly: true });
      return {
        entries: mapEntries(data.posts, postToEntry),
        nextCursor: Number(data.next_offset ?? cursor + (data.posts?.length || 0)),
        exhausted: !data.has_more
      };
    }

    if (request.source === "REELS") {
      const data = await listReels({ lane: request.lane || "for_you", limit, offset: cursor });
      return {
        entries: mapEntries(data.reels, reelToEntry),
        nextCursor: Number(data.next_offset ?? cursor + (data.reels?.length || 0)),
        exhausted: !data.has_more
      };
    }

    // SEARCH, GROUP and SHARED_POST. `sourceCanContinue` already refuses these,
    // so reaching here means a caller asked anyway -- answer with the truth
    // rather than inventing a query for a surface that has no next page.
    return { entries: [], nextCursor: cursor, exhausted: true };
  } catch (error) {
    return { entries: [], nextCursor: cursor, exhausted: false, error };
  }
}
