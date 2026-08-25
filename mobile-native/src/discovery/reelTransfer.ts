/**
 * The handoff that makes "tap this reel, get this reel" literally true.
 *
 * ## The problem this exists to solve
 *
 * There is no endpoint that returns a single reel. `/api/pulse/reels/<id>`
 * exists for PATCH and DELETE only; `getReelDetail` reads the reel out of local
 * cache and fetches nothing but comments. So the player has exactly one way to
 * obtain reels — `/api/pulse/reels/feed` — and one way to honour a requested id:
 * `focusInitialReel`, which hoists the match to index 0 *if the id happens to be
 * on the page it just fetched*, and otherwise returns the page untouched.
 *
 * That "otherwise" is the bug the mission names. Two ways a user sees a reel
 * they did not tap:
 *
 *   1. The cached-snapshot path renders first, before the network call resolves.
 *      The snapshot is whatever was in the lane last time, so unless the tapped
 *      reel is in it, the first thing on screen is an unrelated reel.
 *   2. The tapped reel is not in the freshly fetched page — it was ranked in a
 *      different lane, or it has aged past the first page — and `focusInitialReel`
 *      silently gives up. The player opens on somebody else's reel with no error.
 *
 * ## Why a module slot instead of a navigation param
 *
 * The tap site already holds the whole `PulseReel`: the suggestions row was
 * built from a `listReels` response. So the reel does not need to be fetched at
 * all, it needs to be *carried*. Carrying it in `route.params` would work once
 * and then quietly rot — React Navigation keeps params alive for the life of the
 * route, and the Reels tab is never unmounted, so a stale reel object would sit
 * in the tab's params across every later visit and survive state persistence
 * into the next cold start. A one-shot slot that the player consumes on read has
 * the lifecycle the handoff actually has.
 *
 * Params still carry the `reelId` and a nonce, because those are small, they are
 * what the player keys its reload on, and they are what a deep link can supply
 * when there is no staged payload at all. The slot is an optimisation for the
 * in-app path and a correctness fix for the flash; a missing slot degrades to
 * exactly today's behaviour rather than to an error.
 */
import type { PulseReel } from "../api/reels";

/**
 * How long a staged reel stays takeable.
 *
 * The slot is consumed on read, so in the normal path the TTL never matters.
 * It matters when the navigation does not happen — the user long-presses,
 * a modal steals the tap, the screen unmounts mid-gesture — and the payload
 * would otherwise sit there waiting to seed some unrelated later visit to Reels
 * with a reel the user chose minutes ago and has forgotten.
 */
export const REEL_TRANSFER_TTL_MS = 60_000;

type Slot = { reelId: number; reel: PulseReel; stagedAt: number };

let slot: Slot | null = null;

/** Monotonic, so two taps on the same reel are still two distinct navigations. */
let nonceCounter = 0;

/**
 * Park a reel for the player to pick up, and return the nonce to pass in params.
 *
 * Returns null for a reel with no usable id: the caller should not navigate at
 * all in that case, because "open this exact reel" has no meaning without one.
 */
export function stageReelTransfer(reel: PulseReel, now: number = Date.now()): string | null {
  // `reel_id` first, matching `normalizeReel` and `buildReels`. The caller
  // navigates with `buildReels`' id, so keying the slot the other way round
  // parks the reel under an id the player will never ask for and the handoff
  // degrades to "some other reel" without an error.
  const reelId = Number(reel?.reel_id || reel?.id || 0);
  if (!Number.isFinite(reelId) || reelId <= 0) return null;
  slot = { reelId, reel, stagedAt: now };
  nonceCounter += 1;
  return `reel-transfer:${reelId}:${nonceCounter}`;
}

/** Read without consuming. For assertions and for a render that may be discarded. */
export function peekReelTransfer(reelId: number, now: number = Date.now()): PulseReel | null {
  if (!slot) return null;
  if (slot.reelId !== Number(reelId)) return null;
  if (now - slot.stagedAt > REEL_TRANSFER_TTL_MS) return null;
  return slot.reel;
}

/**
 * Take the staged reel for `reelId`, clearing the slot.
 *
 * Mismatched id clears too. If the player is opening reel B while reel A is
 * parked, A's navigation is never going to happen, and leaving it would let it
 * seed the *next* visit.
 */
export function takeReelTransfer(reelId: number, now: number = Date.now()): PulseReel | null {
  const reel = peekReelTransfer(reelId, now);
  slot = null;
  return reel;
}

/** Drop anything staged. Called on sign-out and by tests. */
export function clearReelTransfer(): void {
  slot = null;
}

/** Test-only: prove staging happened without consuming the slot. */
export function __reelTransferStaged(): boolean {
  return slot !== null;
}
