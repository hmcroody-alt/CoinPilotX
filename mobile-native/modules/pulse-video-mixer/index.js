import { requireOptionalNativeModule } from "expo-modules-core";

const NativeModule = requireOptionalNativeModule("PulseVideoMixer");

export const isPulseVideoMixerSupported = Boolean(NativeModule);

export async function mixVideoWithMusic(options) {
  if (!NativeModule) throw new Error("Digital video mixing requires the native iOS build.");
  return NativeModule.mixVideoWithMusic(options);
}
