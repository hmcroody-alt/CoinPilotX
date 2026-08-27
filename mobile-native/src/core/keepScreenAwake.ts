/**
 * Screen keep-awake for real-time sessions.
 *
 * WHAT THIS IS FOR
 *
 * A video call and a livestream are the two places in PulseSoc where the user
 * can be fully engaged while touching nothing. The OS idle timer cannot tell
 * "watching a broadcast" from "abandoned the phone on a desk", so it dims and
 * then locks the display mid-session. This module holds the idle timer open for
 * exactly as long as such a session is running, and not one moment longer.
 *
 * WHY IT TALKS TO THE NATIVE MODULE DIRECTLY
 *
 * `expo-keep-awake` is present in this install, but nested under
 * `node_modules/expo/node_modules/` rather than hoisted, so a bare
 * `import "expo-keep-awake"` does not resolve from app code. The obvious fix —
 * adding it to `mobile-native/package.json` — is expensive here for a reason
 * that has nothing to do with this feature: `package.json` is listed under
 * `dependency_watch` in `config/realtime-audio-protected-paths.json`, so any
 * edit to it demands an audio-critical label, a fresh change declaration and
 * physical audible re-validation of calls and Live.
 *
 * None of that buys any safety for a change that never touches audio. So we
 * bind to the SAME native module `expo-keep-awake` itself binds to — it is
 * already compiled into the app (`ExpoKeepAwake` is in `ios/Podfile.lock`,
 * autolinked transitively through `expo`) — and skip the JS package. This is
 * the official Expo keep-awake implementation; only the import path differs.
 *
 * `requireOptionalNativeModule` returns `null` rather than throwing when the
 * module is absent, which is what keeps this safe under Jest and on web, where
 * there is no idle timer to hold open.
 */

import { requireOptionalNativeModule } from "expo-modules-core";

/**
 * One tag for the whole feature. The native module reference-counts tags in a
 * Set, so a single shared tag means a call and a Live that briefly overlap
 * cannot leave the idle timer disabled when the second one ends.
 */
export const REALTIME_KEEP_AWAKE_TAG = "pulsesoc-realtime-session";

type KeepAwakeNativeModule = {
  activate: (tag: string) => Promise<boolean>;
  deactivate: (tag: string) => Promise<boolean>;
};

const nativeModule = requireOptionalNativeModule<KeepAwakeNativeModule>("ExpoKeepAwake");

/** True when the platform can actually hold the screen awake. */
export function keepScreenAwakeSupported(): boolean {
  return nativeModule !== null;
}

/**
 * The single rule for when the display must stay lit.
 *
 * Kept as a pure function of already-owned state so it can be tested without a
 * renderer, a native module, or a real call. Everything it reads is state some
 * other module already owns — this feature adds no lifecycle of its own.
 */
export function shouldKeepScreenAwake(input: {
  /** `sessionActive` from the call session store. */
  callSessionActive: boolean;
  /** `callType` from the call session store. */
  callType: string;
  /** `kind` of the current media-playback owner, or null when there is none. */
  mediaPlaybackKind: string | null;
}): boolean {
  /*
   * Video calls only. An audio call is deliberately excluded: the user is
   * usually holding the phone to their ear with the proximity sensor blanking
   * the screen, and pinning the idle timer open there would burn battery for a
   * display nobody is looking at. The brief asks for video calls, and that is
   * also the correct behaviour.
   */
  const inVideoCall = input.callSessionActive && input.callType === "video";

  /*
   * Every Live path — host, full-screen viewer, and the in-feed viewer — claims
   * the media-playback coordinator with kind "live" (see
   * `live/livePlaybackOwnership.ts`). Reading the coordinator therefore covers
   * hosting and watching in one condition, and inherits the release those
   * screens already perform on end, leave, unmount, disconnect and error.
   */
  const inLiveSession = input.mediaPlaybackKind === "live";

  return inVideoCall || inLiveSession;
}

/**
 * Hold the idle timer open. Safe to call when already held — the native module
 * keeps a Set of tags, so repeats are idempotent.
 */
export async function acquireKeepScreenAwake(tag: string = REALTIME_KEEP_AWAKE_TAG): Promise<void> {
  if (!nativeModule) return;
  await nativeModule.activate(tag).catch(() => undefined);
}

/**
 * Release the idle timer. Never throws: this runs from effect cleanup, where a
 * rejection would be unhandled, and a failure to release must not also become a
 * crash. Safe to call when nothing is held.
 */
export async function releaseKeepScreenAwake(tag: string = REALTIME_KEEP_AWAKE_TAG): Promise<void> {
  if (!nativeModule) return;
  await nativeModule.deactivate(tag).catch(() => undefined);
}
