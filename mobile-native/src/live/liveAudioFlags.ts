/**
 * Livestream audio V2 rollout gate.
 *
 * The decision is made ENTIRELY on the server and delivered on the LiveKit
 * token response the client already fetches for every broadcast. The client
 * never computes eligibility, so the kill switch takes effect on the next token
 * fetch with no app release. There is deliberately no local override: a
 * client-side flag would not be a kill switch.
 *
 * Default is OFF. Anything other than an explicit server `true` runs the legacy
 * path, so a malformed, truncated, or absent field is fail-safe.
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
 * Host/co-host capture is held on the physically verified legacy publisher
 * path until the server explicitly opts that session into publisher V2. A
 * viewer may continue using V2 because it never owns or records the microphone.
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
