/**
 * Which reel, if any, carries a commerce chip — and nothing else.
 *
 * ## Why this is keyed by reel id and not by index
 *
 * The obvious implementation is `index % interval === 0`, and it is wrong here
 * in a way that only shows up on a real device. The Reels list is not a fixed
 * array: pull-to-refresh prepends, pagination appends, and a removed or blocked
 * reel is filtered out of the middle. Every one of those shifts the index of
 * every reel after it, so an index-keyed chip silently jumps to a *different
 * video* — one the ranker never scored it against, and one the user may have
 * already scrolled past. Binding to the reel's own id means the chip is
 * attached to the reel, not to the slot number the reel happened to occupy when
 * the list was last built.
 *
 * ## Why a dismissed slot stays empty
 *
 * Same rule as `commerceRows.ts`, for the same reason: hiding a chip must not
 * cause another product to appear on the next reel a moment later. A slot spent
 * on a dismissed placement is spent.
 *
 * ## What this module structurally cannot do
 *
 * It cannot pause a reel, mute one, reorder the list, or drop a reel. Its entire
 * output is a `Map<reelId, placement>` — a lookup the renderer consults and the
 * player never sees. The hard rule that a chip never interferes with playback is
 * enforced by there being no playback in scope, rather than by remembering not
 * to touch it.
 */
import type { CommercePlacement } from "../api/commerceDiscovery";

export type ReelSlotOptions = {
  /** Reels the user watches before the first chip is eligible. Server: 4. */
  leadIn?: number;
  /** Reels between chips once the lead-in is spent. Server: 10. */
  interval?: number;
  /** Hard cap for this list. Server sends 1 for reels. */
  maxChips?: number;
  /** Placements hidden this session, before the refetch catches up. */
  dismissedPlacementIds?: ReadonlySet<string>;
  /** Sellers the user asked us to stop recommending, same window. */
  dismissedSellerIds?: ReadonlySet<number>;
  /** Injected so binding stays a pure function of its arguments. */
  now?: number;
};

/**
 * Mirrors `services/commerce_discovery/config.py` and is used only until the
 * first response lands — which, since the response also carries the placements,
 * is before any chip can exist. The server's numbers win whenever it sends them.
 */
export const REELS_LEAD_IN = 4;
export const REELS_INTERVAL = 10;
export const REELS_MAX_CHIPS = 1;

function expired(placement: CommercePlacement, now: number): boolean {
  if (!placement.expiresAt) return false;
  const expiry = Date.parse(placement.expiresAt);
  if (Number.isNaN(expiry)) return false;
  return expiry <= now;
}

function renderable(placement: CommercePlacement, now: number): boolean {
  if (!placement?.placementId || !placement.impressionToken) return false;
  if (!placement.product?.listingId || !placement.product.title) return false;
  return !expired(placement, now);
}

function dismissed(placement: CommercePlacement, options: ReelSlotOptions): boolean {
  if (options.dismissedPlacementIds?.has(placement.placementId)) return true;
  const sellerId = placement.product.sellerUserId;
  return Boolean(sellerId && options.dismissedSellerIds?.has(sellerId));
}

/**
 * Which reels are *offered* a slot, in order — the cadence, with no placements.
 *
 * Split out of `bindReelCommerce` because the answer is useful before any
 * placement exists. The caller that needs it is the fetch: §8 wants the chip
 * matched to the reel it sits on, and since this arithmetic depends only on the
 * id list and the cadence, the surface can name that reel and send *its* topic
 * as the ranking context — rather than sending a generic signal and hoping the
 * chip lands somewhere it happens to fit.
 *
 * `bindReelCommerce` consumes this rather than repeating the loop, so a change
 * to the rhythm cannot make the reel we asked about and the reel we bind to
 * drift apart.
 *
 * Returned ids are deduped: a repeated id would otherwise hand two reels the
 * same chip, and reels lists do repeat across pagination boundaries. Positions
 * are raw array indices, so a duplicate still consumes its position.
 */
export function reelCommerceSlots(
  reelIds: readonly string[],
  options: Pick<ReelSlotOptions, "leadIn" | "interval" | "maxChips"> = {}
): string[] {
  const slots: string[] = [];
  if (!Array.isArray(reelIds)) return slots;

  const leadIn = Math.max(options.leadIn ?? REELS_LEAD_IN, 1);
  const interval = Math.max(options.interval ?? REELS_INTERVAL, 1);
  const maxChips = Math.max(options.maxChips ?? REELS_MAX_CHIPS, 0);
  if (maxChips === 0) return slots;

  const seenIds = new Set<string>();
  for (let position = 0; position < reelIds.length; position += 1) {
    const reelId = String(reelIds[position] || "");
    if (!reelId || seenIds.has(reelId)) continue;
    seenIds.add(reelId);

    if (slots.length >= maxChips) break;
    if (position < leadIn) continue;
    if ((position - leadIn) % interval !== 0) continue;

    slots.push(reelId);
  }

  return slots;
}

/**
 * Bind placements to reels.
 *
 * Returns an empty map — never a partial or a guess — whenever there is nothing
 * to place, which is the common case: the reels relevance floor is the strictest
 * in the system (`min_score("reels")` = base + 0.20) precisely so that the
 * engine answers "nothing" rather than "something mediocre". A chip that is not
 * clearly worth the frame it occupies must not be drawn at all.
 */
export function bindReelCommerce(
  reelIds: readonly string[],
  placements: readonly CommercePlacement[],
  options: ReelSlotOptions = {}
): Map<string, CommercePlacement> {
  const bound = new Map<string, CommercePlacement>();
  if (!Array.isArray(reelIds) || !Array.isArray(placements) || placements.length === 0) {
    return bound;
  }

  const now = options.now ?? Date.now();
  const usable = placements.filter((placement) => renderable(placement, now));
  if (usable.length === 0) return bound;

  // Slots are *offered*, not drawn — a slot whose placement the user dismissed
  // is still spent, which is why this indexes `usable` by slot position rather
  // than consuming the next undismissed placement.
  const slots = reelCommerceSlots(reelIds, options);
  for (let slot = 0; slot < slots.length; slot += 1) {
    const placement = usable[slot];
    if (!placement) break;
    if (dismissed(placement, options)) continue;
    bound.set(slots[slot], placement);
  }

  return bound;
}
