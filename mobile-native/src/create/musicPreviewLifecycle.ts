/**
 * Single source of truth for the composer's music-picker preview lifecycle.
 *
 * The product rule (see mission spec) is that at most one track may preview at a
 * time and preview playback MUST stop when: the same track is tapped again, a
 * different track starts, the picker closes, the track finishes, or the user
 * leaves the composer. Centralizing the state transitions here — as pure,
 * testable functions — keeps the imperative `Audio.Sound` calls in the composer
 * thin and prevents the "preview keeps playing after the picker closes" class of
 * bug, which happened because close/select paths only reset the panel flag.
 */

export type PreviewTransition = {
  /** Whether the currently-loaded preview sound must be stopped/unloaded first. */
  stopCurrent: boolean;
  /** Track id to begin previewing, or "" when nothing should play afterward. */
  nextTrackId: string;
};

/** Tapping a track's Preview/Stop control. */
export function resolvePreviewToggle(activeTrackId: string, tappedTrackId: string): PreviewTransition {
  if (activeTrackId && activeTrackId === tappedTrackId) {
    // Tapping the playing track again is an explicit stop.
    return { stopCurrent: true, nextTrackId: "" };
  }
  // Switching tracks (or starting from silence) always stops any prior preview.
  return { stopCurrent: true, nextTrackId: tappedTrackId };
}

/**
 * Any event that should silence the preview without starting another one:
 * closing the picker, selecting a track, the clip finishing, unmounting, or
 * clearing the draft. Returns whether a stop is actually needed.
 */
export function resolvePreviewStop(activeTrackId: string): PreviewTransition {
  return { stopCurrent: Boolean(activeTrackId), nextTrackId: "" };
}
