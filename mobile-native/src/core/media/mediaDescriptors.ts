/**
 * Adapters from the surface types to the one descriptor shape the foundation
 * understands.
 *
 * Reels, posts and Statuses each carry a `media` array of the same canonical
 * record, so these are thin by design. They exist so the prefetch call in each
 * screen is one line and so the "which media does this item actually show"
 * question is answered in one place -- a carousel post shows its first item,
 * and a planner that silently prefetched all ten would spend a feed's worth of
 * budget on one card.
 */

import type { CanonicalMediaRecord } from "../../media/mediaContract";
import type { MediaDescriptor } from "./mediaIdentity";

type WithMedia = { media?: CanonicalMediaRecord[] | null };

/**
 * The record a card paints first.
 *
 * Returns null rather than an empty object for text-only items: the planner
 * treats null as "nothing to warm", whereas an empty object would fall through
 * to mediaIdentityOf and produce a null identity anyway, one layer later and
 * less obviously.
 */
export function primaryMediaOf(item: WithMedia | null | undefined): MediaDescriptor | null {
  const record = (item?.media ?? [])[0];
  return record ? (record as MediaDescriptor) : null;
}

export function primaryMediaList(items: readonly (WithMedia | null | undefined)[]): (MediaDescriptor | null)[] {
  return items.map((item) => primaryMediaOf(item));
}

/**
 * Every media record on an item, for surfaces that really do show all of them
 * (a carousel the user is already paging through). Not used by the list
 * planners -- see the note above about spending a feed's budget on one card.
 */
export function allMediaOf(item: WithMedia | null | undefined): MediaDescriptor[] {
  return (item?.media ?? []).map((record) => record as MediaDescriptor);
}
