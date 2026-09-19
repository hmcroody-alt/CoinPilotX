/**
 * Which items are mounted, which are warming, and which one is playing.
 *
 * §13 asks for the current item plus the next one or two to be preloaded. §35
 * asks for a bounded window so an endless session does not accumulate an endless
 * number of decoders. Those are the same question asked from two sides — how far
 * ahead to warm, and how far back to keep — so they are answered in one place.
 *
 * ## This is not a second playback arbiter
 *
 * `mediaPlaybackCoordinator` already owns "one active media at a time" across
 * the whole app, including calls and livestreams, and the audit was explicit
 * that a second arbiter would silently outrank the call priority — a realtime
 * audio violation wearing a media-engine costume. This module never pauses
 * anything and never claims anything. It answers a narrower question: of the
 * items in *this session*, which single one is the engine's candidate to play.
 * The caller takes that candidate to the coordinator, which may still refuse it
 * because a call is up. Candidate and permission are different things and stay
 * in different modules.
 *
 * ## Why the bound refuses to be widened
 *
 * `ahead` and `behind` are clamped to a hard ceiling rather than merely
 * defaulted. A bounded window whose bound is a parameter is not a bound — the
 * first screen that wants smoother scrolling passes `ahead: 8`, nothing visibly
 * breaks on a desk-charged iPhone 16 Pro, and the regression lands on older
 * hardware as a memory-pressure kill that no stack trace attributes to a
 * preload setting. The ceiling is the feature; the parameter is only allowed to
 * ask for less.
 */
import { ImmersiveEntry, ImmersiveSession, entryKey } from "./immersiveSession";

/**
 * What a mounted item is doing.
 *
 * - `active`   — the one item the engine wants playing. Never more than one.
 * - `preload`  — mounted and warming its first frame, not playing, muted.
 * - `retained` — mounted and idle, kept only so a backward swipe lands on a
 *                real frame instead of the black card §14 exists to prevent.
 *
 * `retained` is deliberately not the same as `preload`. Both are mounted, but a
 * retained item has already been watched and must not re-buffer: swiping back
 * and forth across one boundary would otherwise re-download the same video
 * repeatedly, which is invisible on wifi and expensive on a phone plan.
 */
export type ImmersiveSlotRole = "active" | "preload" | "retained";

export type ImmersiveSlot = {
  entry: ImmersiveEntry;
  /** `kind:id`, the same namespaced key the session dedupes on. Mount on this. */
  key: string;
  /** Index into the session's queue, so the caller can move the cursor to it. */
  index: number;
  role: ImmersiveSlotRole;
};

export type ImmersiveWindowOptions = {
  /** How many items ahead of the cursor to warm. Clamped to `MAX_IMMERSIVE_AHEAD`. */
  ahead?: number;
  /** How many watched items to keep mounted behind. Clamped to `MAX_IMMERSIVE_BEHIND`. */
  behind?: number;
};

/** §13's "next 1–2", taken at the top of its range. */
export const DEFAULT_IMMERSIVE_AHEAD = 2;
/** One is enough: it covers the swipe a user actually reverses, which is the last one. */
export const DEFAULT_IMMERSIVE_BEHIND = 1;

export const MAX_IMMERSIVE_AHEAD = 3;
export const MAX_IMMERSIVE_BEHIND = 2;

/** The most items that can ever be mounted at once: the ceiling §35 is asking for. */
export const MAX_IMMERSIVE_MOUNTED = MAX_IMMERSIVE_AHEAD + MAX_IMMERSIVE_BEHIND + 1;

function clampCount(value: unknown, fallback: number, ceiling: number) {
  const n = Math.trunc(Number(value));
  if (!Number.isFinite(n)) return fallback;
  return Math.max(0, Math.min(n, ceiling));
}

/**
 * The slice of the session that should be mounted right now.
 *
 * Returned in queue order, contiguous, and clamped to the queue's real bounds.
 *
 * The window is *not* slid to compensate for the clamp. At cursor 0 there is
 * nothing behind, and the obvious-looking improvement is to spend the unused
 * slot on one more item ahead. That would make the number of live decoders a
 * function of where the user happens to be standing, which is precisely the
 * property a bounded window exists to remove: the ceiling has to hold at the top
 * of the feed too, because the top of the feed is where a cold app with the most
 * else still in memory begins.
 *
 * Entries the session cannot key are skipped rather than mounted. A key is what
 * the list mounts on, and two unkeyable items would share `undefined` and be
 * reconciled as one — the same collapse the `kind:id` namespacing exists to stop,
 * arriving through the renderer instead of through the dedupe set.
 */
export function immersiveWindow(
  session: ImmersiveSession,
  options: ImmersiveWindowOptions = {}
): ImmersiveSlot[] {
  const queue = session?.queue || [];
  if (!queue.length) return [];

  const ahead = clampCount(options.ahead, DEFAULT_IMMERSIVE_AHEAD, MAX_IMMERSIVE_AHEAD);
  const behind = clampCount(options.behind, DEFAULT_IMMERSIVE_BEHIND, MAX_IMMERSIVE_BEHIND);
  const cursor = Math.max(0, Math.min(Math.trunc(Number(session.cursor)) || 0, queue.length - 1));

  const first = Math.max(0, cursor - behind);
  const last = Math.min(queue.length - 1, cursor + ahead);

  const slots: ImmersiveSlot[] = [];
  for (let index = first; index <= last; index += 1) {
    const entry = queue[index];
    const key = entryKey(entry);
    if (!key) continue;
    slots.push({
      entry,
      key,
      index,
      role: index === cursor ? "active" : index > cursor ? "preload" : "retained"
    });
  }
  return slots;
}

/**
 * The one item the engine wants playing, or null.
 *
 * Null when the session is empty or its current entry has no usable key — and
 * null rather than "the nearest playable thing", because the engine's guarantee
 * is that the cursor and the playing item are the same item. Quietly playing a
 * neighbour would make the overlay, the like button and the view count all
 * describe something other than what is on screen.
 */
export function activeImmersiveSlot(
  session: ImmersiveSession,
  options: ImmersiveWindowOptions = {}
): ImmersiveSlot | null {
  return immersiveWindow(session, options).find((slot) => slot.role === "active") || null;
}

/**
 * The keys that should be warming a first frame, in the order to warm them.
 *
 * Nearest-first, because a preload budget is spent in the order the user will
 * reach the items: if only one of the two ever finishes before the swipe, it
 * should be the one being swiped to.
 *
 * The active item is not in this list. It is not *pre*-loading — it is loading,
 * and it is the caller's first and unconditional job.
 */
export function immersivePreloadKeys(
  session: ImmersiveSession,
  options: ImmersiveWindowOptions = {}
): string[] {
  return immersiveWindow(session, options)
    .filter((slot) => slot.role === "preload")
    .map((slot) => slot.key);
}

/**
 * Whether a key that is currently mounted should now be torn down (§35).
 *
 * Expressed as a question about a key rather than as a diff of two windows so
 * that the caller — which knows what it actually has mounted, including things
 * mounted before a queue change — can ask about each one. A diff would only
 * describe items the previous window knew about, and the leak this guards
 * against is exactly the item that fell out of the queue without ever appearing
 * in a window comparison.
 */
export function shouldReleaseImmersiveKey(
  session: ImmersiveSession,
  key: string,
  options: ImmersiveWindowOptions = {}
): boolean {
  if (!key) return true;
  return !immersiveWindow(session, options).some((slot) => slot.key === key);
}
