import {
  clearNowPlayingInfo,
  isNowPlayingSupported,
  NowPlayingInfo,
  RemoteCommandEvent,
  setNowPlayingInfo,
  subscribeRemoteCommand,
  updatePlaybackProgress
} from "pulse-now-playing";

// Thin, defensive wrapper around the native `pulse-now-playing` Expo module.
// The native module only exists on iOS (bare Android/web builds resolve to
// `null` inside the module and every export here becomes a safe no-op), so
// callers never need to platform-check before using this bridge.
export type { NowPlayingInfo, RemoteCommandEvent };

export const nowPlayingSupported = isNowPlayingSupported;

export function pushNowPlayingInfo(info: NowPlayingInfo) {
  try {
    setNowPlayingInfo(info);
  } catch {
    // Lock-screen metadata is a presentation nicety; never let a native
    // bridge error interrupt audio playback.
  }
}

export function pushNowPlayingProgress(positionSeconds: number, isPlaying: boolean, rate = 1) {
  try {
    updatePlaybackProgress(positionSeconds, isPlaying, rate);
  } catch {
    // ignore
  }
}

export function clearNowPlaying() {
  try {
    clearNowPlayingInfo();
  } catch {
    // ignore
  }
}

export function onRemoteCommand(listener: (event: RemoteCommandEvent) => void) {
  const subscription = subscribeRemoteCommand(listener);
  return () => subscription?.remove();
}
