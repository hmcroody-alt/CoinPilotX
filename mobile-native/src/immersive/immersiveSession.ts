/**
 * The session model for the immersive media engine.
 *
 * This is the "what am I looking at, where did it come from, and what comes
 * next" layer. It is deliberately a *pure* module with no React and no network:
 * everything here is a value transformation, so the rules that matter (dedupe,
 * cursor movement, origin return) can be tested without mounting a player or
 * mocking a fetch.
 *
 * ## Why a session and not just an array
 *
 * The viewer already takes an `items` array, and for a marketplace listing or a
 * chat gallery that is the right shape — the collection is finite and known.
 * Immersive media is neither. It starts from one tapped item, continues into
 * other people's media indefinitely, and must eventually come back to the exact
 * row it launched from. None of those three are properties of an array; they are
 * properties of a *traversal*. So the session owns the array rather than being
 * one.
 *
 * ## Identity: the id-collision trap, for the third time
 *
 * A post id and a reel id are both small integers from different tables, so post
 * 38 and reel 38 are unrelated objects wearing the same number. This codebase
 * has now been bitten by that shape twice in other id spaces — `namespacedMediaId`
 * exists because a `chat_media_uploads` id and a `comm_v2_attachments` id
 * collapsed onto one cache entry and served one file for the other, and
 * `pulseEntity` carries `kind` alongside `id` for the same reason.
 *
 * Here the consequence would be the dedupe set silently swallowing real media:
 * you watch post 38, the seen-set records `38`, and reel 38 is then never shown
 * to you again. That is invisible — nothing errors, the feed simply has a hole
 * in it — which is exactly the failure profile that survives review. So every
 * key in this module is `kind:id`, never a bare number, and there is no function
 * here that accepts an id without its kind.
 *
 * This is also §38 of the brief ("a Post remains a Post and a Reel remains a
 * Reel") expressed as a data structure rather than as a convention. The engine
 * unifies *presentation*; it must not unify identity.
 */

/**
 * Where the session was launched from.
 *
 * Named sources rather than a free string because §25 requires returning to the
 * exact origin, and "return to wherever this came from" is only implementable if
 * the set of wheres is closed. A new surface adding itself here is a deliberate
 * act that shows up in review; a new surface passing `"profile-v2"` as a string
 * is a silent no-op at return time.
 */
export type ImmersiveSource =
  | "HOME_FOR_YOU"
  | "HOME_FOLLOWING"
  | "HOME_FRIENDS"
  | "PROFILE"
  | "GROUP"
  | "SEARCH"
  | "REELS"
  | "SHARED_POST";

/**
 * Sources that can serve more media after the seeded items run out.
 *
 * `SHARED_POST` cannot: it is one object someone sent in a message, and
 * continuing from it into a ranked feed would take a person who tapped a
 * specific shared thing and quietly drop them into general browsing. `SEARCH`
 * and `GROUP` cannot either, for a narrower reason — their APIs return a flat
 * array with no cursor at all (verified in the Stage 0 audit), so there is no
 * "next page" to ask for. Continuing them would mean inventing a second,
 * differently-ranked query, which is the duplication this engine exists to
 * avoid.
 *
 * A non-continuable session is not degraded. It is a finite gallery that ends,
 * which is the honest behaviour for a finite collection.
 */
const CONTINUABLE: ReadonlySet<ImmersiveSource> = new Set<ImmersiveSource>([
  "HOME_FOR_YOU",
  "HOME_FOLLOWING",
  "HOME_FRIENDS",
  "PROFILE",
  "REELS"
]);

export function sourceCanContinue(source: ImmersiveSource) {
  return CONTINUABLE.has(source);
}

/** A Post is a Post and a Reel is a Reel. There is no third thing. */
export type ImmersiveKind = "post" | "reel";

/**
 * One item in the traversal.
 *
 * `mediaIndex` is here because a post is not always one piece of media: a
 * carousel post is several, and §6 requires the carousel stay contained — you
 * swipe *within* a post horizontally, and vertically you leave it. So the
 * vertical queue holds one entry per *post*, and the horizontal axis is the
 * entry's own media list. An entry therefore names the object, not the frame.
 */
export type ImmersiveEntry = {
  kind: ImmersiveKind;
  id: number;
  /** Who posted it. Used for the overlay and for nothing else. */
  authorId?: number;
};

/**
 * The stable key for an entry, namespaced by kind.
 *
 * Returns null for an entry with no usable id rather than fabricating one. A
 * fabricated key would make two unidentifiable items dedupe against each other,
 * which is worse than showing both: the caller can drop a null-keyed entry, but
 * it cannot recover one that was silently merged.
 */
export function entryKey(entry: ImmersiveEntry | null | undefined): string | null {
  const id = Number(entry?.id);
  if (!entry?.kind || !Number.isFinite(id) || id <= 0) return null;
  return `${entry.kind}:${Math.trunc(id)}`;
}

/**
 * Enough to put the user back exactly where they were (§25).
 *
 * The audit found that each screen holds its own private `posts` array, so the
 * engine cannot reach into the origin's list to restore it. What it *can* do is
 * hand the origin back the identity of the row to scroll to and let the origin —
 * which still has its list, unchanged, because the engine opened over it rather
 * than replacing it — do the restoring. Hence `entry` rather than an offset: an
 * index into a list that may have prepended new items since is not a position,
 * it is a guess.
 */
export type ImmersiveOrigin = {
  source: ImmersiveSource;
  /** The exact row the user tapped, to scroll back to on close. */
  entry: ImmersiveEntry;
  /**
   * The origin's own pagination cursor at launch, when it had one.
   *
   * Continuation resumes from here rather than from zero, so the first
   * continuation page is the one the origin *would* have loaded next. Without
   * it the engine would re-serve the whole first page, every item of which the
   * user has already scrolled past to reach the thing they tapped.
   */
  cursor?: number;
};

export type ImmersiveSession = {
  origin: ImmersiveOrigin;
  /** The vertical queue, in order. Never contains duplicates. */
  queue: readonly ImmersiveEntry[];
  /** Index into `queue`. */
  cursor: number;
  /**
   * Every key ever admitted to this session, including ones since recycled out
   * of `queue` (§35). Distinct from the queue precisely so that a bounded window
   * does not reopen the door to items the user already watched.
   */
  seen: ReadonlySet<string>;
  /** Where the next continuation fetch starts. */
  nextCursor: number;
  /** False once the server says there is no more, or the source cannot continue. */
  canContinue: boolean;
};

/**
 * Start a session at the tapped item.
 *
 * `seed` is the media the origin already holds — normally the origin's loaded
 * page — so the first vertical swipes are instant and need no network. The
 * tapped entry is hoisted to the front rather than merely included, because
 * "tap this, get this" is the one guarantee the whole engine rests on, and
 * opening on index N of a list that happens to contain the item is how that
 * guarantee gets lost the first time ranking shifts.
 *
 * Entries without a usable key are dropped. An unopenable item in the queue is a
 * dead frame the user swipes into and cannot act on.
 */
export function beginImmersiveSession(
  origin: ImmersiveOrigin,
  seed: readonly ImmersiveEntry[] = []
): ImmersiveSession {
  const tappedKey = entryKey(origin.entry);
  const queue: ImmersiveEntry[] = [];
  const seen = new Set<string>();
  if (tappedKey) {
    queue.push(origin.entry);
    seen.add(tappedKey);
  }
  for (const entry of seed) {
    const key = entryKey(entry);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    queue.push(entry);
  }
  return {
    origin,
    queue,
    cursor: 0,
    seen,
    nextCursor: Number(origin.cursor) > 0 ? Math.trunc(Number(origin.cursor)) : queue.length,
    canContinue: sourceCanContinue(origin.source)
  };
}

/**
 * Append a continuation page, dropping anything already seen (§28).
 *
 * The dedupe is against `seen`, not against `queue`, and the difference is the
 * whole point: offset pagination over a *ranked* feed re-serves items whenever
 * ranking shifts between pages, and a window-bounded queue has already forgotten
 * the early ones. Deduping against the queue would therefore let page 3 re-show
 * what page 1 showed, which reads as the feed looping.
 *
 * `exhausted` is the server's word, passed through rather than inferred from an
 * empty page: a page can come back empty because everything in it was a
 * duplicate while more genuinely remains behind it, and treating that as the end
 * would truncate the session early.
 *
 * ## A page that changes nothing returns the same session
 *
 * Identity is preserved when a page adds no entries *and* moves neither the
 * cursor nor the continuation flag — the same contract `moveImmersiveCursor`
 * already holds for a move that does not move.
 *
 * This is not a micro-optimisation; it is a termination condition. A React
 * caller re-runs its "should I fetch more?" effect whenever the session's
 * identity changes, so a session that is a new object after every all-duplicate
 * page asks for another one immediately, forever. That is a tight request loop
 * against production, and it presents as a feed that is permanently loading
 * rather than as an error. A server stuck re-serving the same page with the same
 * cursor is exactly the condition that produces it.
 *
 * A page that adds nothing but *does* advance the cursor is a different thing
 * and does return a new session: walking the offset forward through a stale
 * region is how a ranked feed gets past it.
 */
export function appendImmersivePage(
  session: ImmersiveSession,
  page: readonly ImmersiveEntry[],
  options: { nextCursor?: number; exhausted?: boolean } = {}
): ImmersiveSession {
  const seen = new Set(session.seen);
  const added: ImmersiveEntry[] = [];
  for (const entry of page) {
    const key = entryKey(entry);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    added.push(entry);
  }
  const nextCursor = Number.isFinite(Number(options.nextCursor))
    ? Math.trunc(Number(options.nextCursor))
    : session.nextCursor + page.length;
  const canContinue = options.exhausted ? false : session.canContinue;
  if (!added.length && nextCursor === session.nextCursor && canContinue === session.canContinue) {
    return session;
  }
  return {
    ...session,
    queue: added.length ? [...session.queue, ...added] : session.queue,
    seen,
    nextCursor,
    canContinue
  };
}

/** Clamped, because a cursor past the end is a blank frame, not an end-of-list. */
export function moveImmersiveCursor(session: ImmersiveSession, to: number): ImmersiveSession {
  if (!session.queue.length) return session;
  const clamped = Math.max(0, Math.min(Math.trunc(to), session.queue.length - 1));
  return clamped === session.cursor ? session : { ...session, cursor: clamped };
}

export function currentImmersiveEntry(session: ImmersiveSession): ImmersiveEntry | null {
  return session.queue[session.cursor] || null;
}

/**
 * Whether to go and get more, given how close the cursor is to the end.
 *
 * Asked as a question about the *session* rather than performed as a fetch,
 * because the session module does no I/O. The caller owns the request; this owns
 * when one is warranted.
 *
 * The threshold is in items-remaining rather than a scroll percentage: a
 * percentage of a 4-item queue and a percentage of a 400-item queue describe
 * completely different amounts of warning.
 */
export function shouldContinueImmersive(session: ImmersiveSession, lookahead = 3) {
  if (!session.canContinue) return false;
  return session.queue.length - session.cursor - 1 <= Math.max(0, lookahead);
}

/**
 * What the origin should scroll back to on close (§25).
 *
 * Returns the *tapped* entry, not the entry the cursor ended on. Those differ
 * once the user has swiped onward, and returning them to item 40 of somebody
 * else's media — which their origin list does not contain and cannot scroll to —
 * would land them at the top of a feed they were halfway down. The thing they
 * tapped is the thing they will be looking for when they come back.
 */
export function immersiveReturnTarget(session: ImmersiveSession): ImmersiveEntry {
  return session.origin.entry;
}
