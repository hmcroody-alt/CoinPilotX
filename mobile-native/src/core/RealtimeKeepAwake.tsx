/**
 * Mounts the screen keep-awake rule for real-time sessions.
 *
 * WHY THIS IS AN APP-LEVEL OBSERVER AND NOT AN EFFECT IN EACH SCREEN
 *
 * The obvious implementation is a `useKeepAwake()` inside CallScreen,
 * LiveScreen, LiveHostSessionScreen and ReelLiveViewerSurface. All four of
 * those files are protected paths under
 * `config/realtime-audio-protected-paths.json`, and this mission's subject is
 * the display, not audio — so per `docs/realtime_audio_change_policy.md` it must
 * not edit them. The policy's own guidance is to restructure instead, and that
 * it is usually the better design. It is here:
 *
 *   - Four copies of the same effect would be four chances to forget a cleanup
 *     path. This is one copy with one release.
 *   - A call deliberately OUTLIVES the Call screen (see `callSessionStore` — the
 *     session survives navigation and Minimize). A screen-scoped keep-awake
 *     would therefore release while the video call was still running.
 *
 * This component observes state that other modules already own and renders
 * nothing. It creates no lifecycle of its own, which is what keeps it from
 * becoming a second source of truth about whether a session is running.
 *
 * WHERE THE TWO SIGNALS COME FROM
 *
 *   - Calls: `useCallSession()`. `sessionActive` spans the whole call, from
 *     `beginCallSession` until an explicit hang-up, a terminal backend status,
 *     or an authoritative failure — which is exactly the window the brief asks
 *     for, including the failure and disconnect cases.
 *   - Live: the media-playback coordinator. Host, viewer and in-feed viewer all
 *     claim it with kind "live" and release it on end, leave, unmount, error and
 *     background, so one subscription covers all three roles and every teardown
 *     path without any of them knowing this feature exists.
 *
 * Backgrounding needs no handling here: the native module drops the idle timer
 * in `OnAppEntersBackground` and restores it in `OnAppEntersForeground` while
 * the tag is still held, so a background/foreground cycle cannot strand it on.
 */

import { useEffect, useState } from "react";
import { useCallSession } from "../calls/callSessionStore";
import { getActiveMediaPlayback, subscribeMediaPlayback } from "./mediaPlaybackCoordinator";
import { acquireKeepScreenAwake, releaseKeepScreenAwake, shouldKeepScreenAwake } from "./keepScreenAwake";

export function RealtimeKeepAwake() {
  const call = useCallSession();

  /*
   * Only the kind is tracked, never the owner object: `getActiveMediaPlayback`
   * builds a fresh object on every call, so holding the object would re-render
   * on every notify. A string collapses that to the transitions that matter.
   */
  const [mediaPlaybackKind, setMediaPlaybackKind] = useState<string | null>(
    () => getActiveMediaPlayback()?.kind ?? null
  );

  useEffect(() => {
    // `subscribeMediaPlayback` emits the current owner synchronously on
    // subscribe, so a session already running when this mounts is picked up.
    const unsubscribe = subscribeMediaPlayback((owner) => {
      setMediaPlaybackKind(owner?.kind ?? null);
    });
    return () => {
      unsubscribe();
    };
  }, []);

  const keepAwake = shouldKeepScreenAwake({
    callSessionActive: call.sessionActive,
    callType: call.callType,
    mediaPlaybackKind
  });

  useEffect(() => {
    if (!keepAwake) return;
    void acquireKeepScreenAwake();
    /*
     * The release is the cleanup rather than an `else` branch, so it runs on
     * every exit without any of them being enumerated here: session ends, Live
     * is left, the call fails, the user signs out, or the app tree unmounts.
     * A path nobody thought of still releases.
     */
    return () => {
      void releaseKeepScreenAwake();
    };
  }, [keepAwake]);

  return null;
}

export default RealtimeKeepAwake;
