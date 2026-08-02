/**
 * Media quality V2 rollout gate.
 *
 * Mirrors liveAudioFlags.ts deliberately, because the property that made that
 * flag trustworthy is the one that matters here too: the decision is made
 * ENTIRELY on the server and arrives on the LiveKit token response the client
 * already fetches. There is no local override, no cached preference, and no
 * developer toggle. A flag the client can set for itself is not a kill switch.
 *
 * Every flag defaults OFF. A malformed, truncated, absent, or partially
 * migrated response therefore runs the verified stable configuration. The
 * failure mode of this file is "quality stays exactly as it is today", which is
 * the only acceptable failure mode for a layer sitting next to working audio.
 *
 * Environment variable names, from the mission brief:
 *   REALTIME_MEDIA_QUALITY_V2_ENABLED   -> realtimeMediaQualityV2Enabled
 *   REALTIME_MEDIA_QUALITY_V2_QA_ONLY   -> realtimeMediaQualityV2QaOnly
 *   AUDIO_QUALITY_PROFILE               -> audioQualityProfile
 *   VIDEO_QUALITY_PROFILE               -> videoQualityProfile
 *   LIVE_ELITE_VIDEO_ENABLED            -> liveEliteVideoEnabled
 *   LIVE_ELITE_AUDIO_ENABLED            -> liveEliteAudioEnabled
 *   CALL_ELITE_AUDIO_ENABLED            -> callEliteAudioEnabled
 *   VIDEO_CALL_ELITE_QUALITY_ENABLED    -> videoCallEliteQualityEnabled
 */

import {
  normalizeContentMode,
  normalizeMediaQualityFlag,
  normalizeProfileName,
  type MediaQualityFlagSource,
  type MediaQualityProfileName
} from "./mediaQualityPolicy";

/**
 * The exact keys read off the token response. Named so the backend contract
 * test and the client agree on one list rather than two hand-copied ones.
 */
export const MEDIA_QUALITY_FLAG_KEYS = Object.freeze([
  "realtimeMediaQualityV2Enabled",
  "realtimeMediaQualityV2QaOnly",
  "audioQualityProfile",
  "videoQualityProfile",
  "liveEliteVideoEnabled",
  "liveEliteAudioEnabled",
  "callEliteAudioEnabled",
  "videoCallEliteQualityEnabled",
  "mediaContentMode",
  "qaCohort"
] as const);

/**
 * The state the codebase ships in, and the state any parse failure falls back
 * to. Written out in full rather than as an empty object so that "what is the
 * default" is answerable by reading, not by inferring from absent keys.
 */
export const DEFAULT_MEDIA_QUALITY_FLAGS: Readonly<Required<MediaQualityFlagSource>> = Object.freeze({
  realtimeMediaQualityV2Enabled: false,
  realtimeMediaQualityV2QaOnly: true,
  audioQualityProfile: "stable" as MediaQualityProfileName,
  videoQualityProfile: "stable" as MediaQualityProfileName,
  liveEliteVideoEnabled: false,
  liveEliteAudioEnabled: false,
  callEliteAudioEnabled: false,
  videoCallEliteQualityEnabled: false,
  mediaContentMode: "auto",
  qaCohort: false
});

/**
 * Normalise at the API boundary, exactly once, so that no downstream module
 * ever sees a raw server value. Booleans become strict booleans, profile names
 * become one of four known strings, and anything unrecognised becomes the
 * conservative default rather than being passed through.
 *
 * Note that QA-only defaults to TRUE while the master switch defaults to FALSE.
 * That asymmetry is intentional: if the server sends the master switch but
 * omits the cohort scoping — the shape of a half-finished rollout config — the
 * result is a QA-gated rollout, not a general one.
 */
/**
 * The wire spelling for each flag. The backend speaks snake_case everywhere
 * else on the token response (`realtime_audio_v2_enabled`), so it speaks
 * snake_case here too; the camelCase name is accepted as well so that a JSON
 * payload written either way parses identically rather than silently falling
 * back to stable and looking like a rollout that "didn't work".
 */
const WIRE_ALIASES: Record<string, string> = {
  realtimeMediaQualityV2Enabled: "realtime_media_quality_v2_enabled",
  realtimeMediaQualityV2QaOnly: "realtime_media_quality_v2_qa_only",
  audioQualityProfile: "audio_quality_profile",
  videoQualityProfile: "video_quality_profile",
  liveEliteVideoEnabled: "live_elite_video_enabled",
  liveEliteAudioEnabled: "live_elite_audio_enabled",
  callEliteAudioEnabled: "call_elite_audio_enabled",
  videoCallEliteQualityEnabled: "video_call_elite_quality_enabled",
  mediaContentMode: "media_content_mode",
  qaCohort: "qa_cohort"
};

function pick(source: Record<string, unknown>, key: string): unknown {
  const camel = source[key];
  if (camel !== undefined) return camel;
  return source[WIRE_ALIASES[key]];
}

export function parseMediaQualityFlags(raw: unknown): Required<MediaQualityFlagSource> {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return { ...DEFAULT_MEDIA_QUALITY_FLAGS };
  }
  const source = raw as Record<string, unknown>;
  const qaOnly = pick(source, "realtimeMediaQualityV2QaOnly");

  return {
    realtimeMediaQualityV2Enabled: normalizeMediaQualityFlag(pick(source, "realtimeMediaQualityV2Enabled")),
    realtimeMediaQualityV2QaOnly: qaOnly === undefined ? true : normalizeMediaQualityFlag(qaOnly),
    audioQualityProfile: normalizeProfileName(pick(source, "audioQualityProfile")),
    videoQualityProfile: normalizeProfileName(pick(source, "videoQualityProfile")),
    liveEliteVideoEnabled: normalizeMediaQualityFlag(pick(source, "liveEliteVideoEnabled")),
    liveEliteAudioEnabled: normalizeMediaQualityFlag(pick(source, "liveEliteAudioEnabled")),
    callEliteAudioEnabled: normalizeMediaQualityFlag(pick(source, "callEliteAudioEnabled")),
    videoCallEliteQualityEnabled: normalizeMediaQualityFlag(pick(source, "videoCallEliteQualityEnabled")),
    mediaContentMode: normalizeContentMode(pick(source, "mediaContentMode")),
    qaCohort: normalizeMediaQualityFlag(pick(source, "qaCohort"))
  };
}

/**
 * A one-line, non-identifying description of the flag state, for telemetry and
 * for the QA harness. Contains no token, no room name, and no user identifier —
 * only the eight rollout decisions, so it is safe to log verbatim.
 */
export function describeMediaQualityFlags(flags: Required<MediaQualityFlagSource>): string {
  return [
    `v2=${flags.realtimeMediaQualityV2Enabled ? 1 : 0}`,
    `qa=${flags.realtimeMediaQualityV2QaOnly ? 1 : 0}`,
    `cohort=${flags.qaCohort ? 1 : 0}`,
    `a=${flags.audioQualityProfile}`,
    `v=${flags.videoQualityProfile}`,
    `lv=${flags.liveEliteVideoEnabled ? 1 : 0}`,
    `la=${flags.liveEliteAudioEnabled ? 1 : 0}`,
    `ca=${flags.callEliteAudioEnabled ? 1 : 0}`,
    `vc=${flags.videoCallEliteQualityEnabled ? 1 : 0}`
  ].join(" ");
}
