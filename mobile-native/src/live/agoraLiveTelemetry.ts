import { sanitizeLiveTelemetry } from "./liveTelemetryPrivacy";

type AgoraLiveEvent = {
  name: string;
  liveId?: number;
  uid?: number;
  code?: number | string;
  reason?: string;
  connectionState?: string;
  participantCount?: number;
  txQuality?: number;
  rxQuality?: number;
  audioBitrateKbps?: number;
  videoBitrateKbps?: number;
  videoFps?: number;
  packetLossPercent?: number;
  latencyMs?: number;
  width?: number;
  height?: number;
};

let lastQualityAt = 0;

/**
 * Emit one Live telemetry event.
 *
 * Stage 55. Sanitisation happens *here*, at the single chokepoint, rather than
 * at the twenty-odd call sites in `useAgoraLiveBroadcastRoom`. That placement is
 * the whole design: the `uid` these events carry is the caller's PulseSoc user
 * id (`_agora_uid(user_id) == user_id`), so a scheme that relied on every call
 * site remembering to pseudonymise it would leak the first time somebody added
 * an event in a hurry. Callers pass the uid they have; nothing but a tag ever
 * reaches the log. See `liveTelemetryPrivacy` for what the tag guarantees and
 * what it deliberately gives up.
 */
export function emitAgoraLiveEvent(event: AgoraLiveEvent, throttleQuality = false): void {
  if (throttleQuality) {
    const now = Date.now();
    if (now - lastQualityAt < 10_000) return;
    lastQualityAt = now;
  }
  console.log("PulseSocAgoraLive", sanitizeLiveTelemetry(event as Record<string, unknown>));
}
