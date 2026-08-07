/**
 * Livestream audio V2 rollout flag — now a TELEMETRY COHORT LABEL.
 *
 * The client runs the single unified call-grade audio path for every live
 * session; this server-delivered flag no longer selects an audio code path in
 * `useLiveBroadcastRoom`. It is still parsed strictly (only an explicit `true`
 * counts) and still labels telemetry events (`audioPath`, `audioV2`) so the
 * server rollout state stays observable per session.
 */

export type LiveAudioFlagSource = {
  audioV2Enabled?: unknown;
  /** Emergency publisher gate. Missing is OFF, even when general V2 is on. */
  publisherAudioV2Enabled?: unknown;
};

export const LIVE_AUDIO_V2_FLAG_KEY = "audioV2Enabled";

export function isLiveAudioV2Enabled(source: LiveAudioFlagSource | null | undefined): boolean {
  return source?.audioV2Enabled === true;
}

/**
 * Cohort resolution for a session: publishers count as V2 only when the server
 * additionally sets the publisher flag. Behaviourally the client is unified;
 * this distinction exists so telemetry can attribute sessions to the server's
 * rollout buckets.
 */
export function isLiveAudioV2EnabledForSession(
  source: LiveAudioFlagSource | null | undefined,
  publish: boolean
): boolean {
  if (!isLiveAudioV2Enabled(source)) return false;
  return !publish || source?.publisherAudioV2Enabled === true;
}

/**
 * Normalise the raw server field into a strict boolean at the API boundary, so
 * no truthy-but-not-true value ("false", 0, "0") can enable the new path.
 */
export function normalizeLiveAudioV2Flag(raw: unknown): boolean {
  return raw === true;
}

export type LiveAudioPathName = "v2_isolated" | "v1_legacy";

export function resolveLiveAudioPath(source: LiveAudioFlagSource | null | undefined): LiveAudioPathName {
  return isLiveAudioV2Enabled(source) ? "v2_isolated" : "v1_legacy";
}

export function resolveLiveAudioPathForSession(
  source: LiveAudioFlagSource | null | undefined,
  publish: boolean
): LiveAudioPathName {
  return isLiveAudioV2EnabledForSession(source, publish) ? "v2_isolated" : "v1_legacy";
}
