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

/** Safe test telemetry: identifiers and media statistics only, never credentials. */
export function emitAgoraLiveEvent(event: AgoraLiveEvent, throttleQuality = false): void {
  if (throttleQuality) {
    const now = Date.now();
    if (now - lastQualityAt < 10_000) return;
    lastQualityAt = now;
  }
  console.log("PulseSocAgoraLive", event);
}
