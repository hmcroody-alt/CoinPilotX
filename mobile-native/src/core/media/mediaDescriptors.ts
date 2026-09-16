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
import { resolveReelAudioPolicy } from "../attachedMusicAudioPolicy";
import type { PulseReelAudio } from "../../api/reels";

type WithMedia = { media?: CanonicalMediaRecord[] | null };

type WithAudio = WithMedia & { audio?: PulseReelAudio | null };

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
 * A reel's painted media, carrying the track that plays over it (§29).
 *
 * Reels are the one surface where the visible asset is not the whole of what
 * has to be ready: the attached music is a second network fetch, and a reel
 * whose video is warm but whose track is cold still opens with silence over
 * moving picture. "It is useless for video to start instantly if music starts
 * 800ms later."
 *
 * The track is folded onto the descriptor rather than passed as a second array
 * because the planner walks one window; a parallel array would have to stay
 * index-aligned with it through every filter, and would eventually not.
 *
 * `resolveReelAudioPolicy` is reused rather than reading `attached_audio_url`
 * directly so that the prewarm and the player agree on what "has music" means.
 * It already resolves baked-in audio to no track at all -- and prewarming a
 * track for a reel that will never play one is pure waste.
 */
export function reelPrefetchMediaOf(reel: WithAudio | null | undefined): MediaDescriptor | null {
  const record = primaryMediaOf(reel);
  if (!record) return null;
  const policy = resolveReelAudioPolicy(reel?.audio);
  if (!policy.hasAttachedMusic || !policy.musicUrl) return record;
  return { ...record, attached_audio_url: policy.musicUrl };
}

export function reelPrefetchMediaList(
  reels: readonly (WithAudio | null | undefined)[]
): (MediaDescriptor | null)[] {
  return reels.map((reel) => reelPrefetchMediaOf(reel));
}

/**
 * Every media record on an item, for surfaces that really do show all of them
 * (a carousel the user is already paging through). Not used by the list
 * planners -- see the note above about spending a feed's budget on one card.
 */
export function allMediaOf(item: WithMedia | null | undefined): MediaDescriptor[] {
  return (item?.media ?? []).map((record) => record as MediaDescriptor);
}
