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
  audioSharedPathEnabled?: unknown;
  audioV2Enabled?: unknown;
};

export const LIVE_AUDIO_SHARED_PATH_FLAG_KEY = "audioSharedPathEnabled";
export const LIVE_AUDIO_V2_FLAG_KEY = "audioV2Enabled";

export function isLiveAudioV2Enabled(source: LiveAudioFlagSource | null | undefined): boolean {
  if (source?.audioSharedPathEnabled !== undefined) return source.audioSharedPathEnabled === true;
  return source?.audioV2Enabled === true;
}

/**
 * Normalise the raw server field into a strict boolean at the API boundary, so
 * no truthy-but-not-true value ("false", 0, "0") can enable the new path.
 */
export function normalizeLiveAudioV2Flag(raw: unknown): boolean {
  return raw === true;
}

export type LiveAudioPathName = "shared_governed" | "legacy_fallback";

export function resolveLiveAudioPath(source: LiveAudioFlagSource | null | undefined): LiveAudioPathName {
  return isLiveAudioV2Enabled(source) ? "shared_governed" : "legacy_fallback";
}
