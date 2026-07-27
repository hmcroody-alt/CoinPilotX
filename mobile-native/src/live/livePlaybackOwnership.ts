import { claimMediaPlayback, releaseMediaPlayback } from "../core/mediaPlaybackCoordinator";

export type LivePlaybackScope = "viewer" | "feed" | "host";

export function livePlaybackOwnerId(scope: LivePlaybackScope, liveId: number | string) {
  return `live-${scope}:${liveId || "unknown"}`;
}

export async function claimLivePlaybackOwner(scope: LivePlaybackScope, liveId: number | string, onStop?: () => void | Promise<void>) {
  const ownerId = livePlaybackOwnerId(scope, liveId);
  return claimMediaPlayback({
    id: ownerId,
    kind: "live",
    pause: () => undefined,
    stop: onStop || (() => undefined)
  });
}

export async function releaseLivePlaybackOwner(scope: LivePlaybackScope, liveId: number | string) {
  await releaseMediaPlayback(livePlaybackOwnerId(scope, liveId)).catch(() => undefined);
}
