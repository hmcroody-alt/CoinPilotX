import { EventEmitter, requireOptionalNativeModule } from "expo-modules-core";

const NativeModule = requireOptionalNativeModule("PulseNowPlaying");
const emitter = NativeModule ? new EventEmitter(NativeModule) : null;

export const isNowPlayingSupported = Boolean(NativeModule);

export function setNowPlayingInfo(info) {
  if (!NativeModule) return;
  NativeModule.setNowPlayingInfo(info);
}

export function updatePlaybackProgress(positionSeconds, isPlaying, rate = 1) {
  if (!NativeModule) return;
  NativeModule.updatePlaybackProgress(positionSeconds, isPlaying, rate);
}

export function clearNowPlayingInfo() {
  if (!NativeModule) return;
  NativeModule.clearNowPlayingInfo();
}

export function subscribeRemoteCommand(listener) {
  if (!emitter) return null;
  return emitter.addListener("onRemoteCommand", listener);
}
