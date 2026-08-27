/**
 * Keep-awake lifecycle during real-time sessions.
 *
 * The feature fails in two directions and both are silent:
 *
 *   - It never engages. The screen dims mid-call and nothing in the logs says
 *     so; the only symptom is a user complaining their video call went dark.
 *   - It never releases. The idle timer stays disabled for the rest of the
 *     process, the phone never sleeps again, and the battery drains overnight.
 *     Nothing crashes, no test fails, and the cause is invisible.
 *
 * The release direction is the dangerous one, so most of what follows is about
 * teardown rather than setup: every way a session can end gets its own case,
 * because a cleanup that only covers the happy path is the exact bug.
 *
 * The seam here is `keepScreenAwake`'s own exports, not the native module. The
 * import graph reaches `expo-av` and `expo-secure-store` through the call
 * store, and replacing `expo-modules-core` would take the jest-expo preset's
 * `requireNativeModule` with it. The tag-level contract is covered next door in
 * `keepScreenAwake.test.ts`.
 */

import React from "react";
import { act, render } from "@testing-library/react-native";

// The call store reaches `expo-av` through the voice-message player, and there
// is no `ExponentAV` under Jest. This stub is only about making the import
// graph resolve — nothing in this file touches audio, and the real audio path
// is untouched by the feature under test.
jest.mock("expo-av", () => ({
  Audio: {
    setAudioModeAsync: jest.fn().mockResolvedValue(undefined),
    Sound: { createAsync: jest.fn() }
  },
  InterruptionModeAndroid: { DoNotMix: 1 },
  InterruptionModeIOS: { DoNotMix: 1 }
}));

import { RealtimeKeepAwake } from "../RealtimeKeepAwake";
import * as keepScreenAwake from "../keepScreenAwake";
import * as callSessionStore from "../../calls/callSessionStore";
import * as mediaPlaybackCoordinator from "../mediaPlaybackCoordinator";

type CallSnapshot = { sessionActive: boolean; callType: string };
type PlaybackOwner = { id: string; kind: string } | null;

let acquire: jest.SpyInstance;
let release: jest.SpyInstance;

let callSnapshot: CallSnapshot;
let playbackOwner: PlaybackOwner;
const callListeners = new Set<() => void>();
const playbackListeners = new Set<(owner: PlaybackOwner) => void>();

/** Drive the call store the way a real call transition would. */
function setCall(next: Partial<CallSnapshot>) {
  callSnapshot = { ...callSnapshot, ...next };
  act(() => {
    callListeners.forEach((listener) => listener());
  });
}

/** Drive the media coordinator the way a Live claim/release would. */
function setPlayback(owner: PlaybackOwner) {
  playbackOwner = owner;
  act(() => {
    playbackListeners.forEach((listener) => listener(owner));
  });
}

beforeEach(() => {
  callSnapshot = { sessionActive: false, callType: "audio" };
  playbackOwner = null;
  callListeners.clear();
  playbackListeners.clear();

  acquire = jest.spyOn(keepScreenAwake, "acquireKeepScreenAwake").mockResolvedValue(undefined);
  release = jest.spyOn(keepScreenAwake, "releaseKeepScreenAwake").mockResolvedValue(undefined);

  jest.spyOn(callSessionStore, "useCallSession").mockImplementation(() =>
    React.useSyncExternalStore(
      (listener: () => void) => {
        callListeners.add(listener);
        return () => callListeners.delete(listener);
      },
      () => callSnapshot,
      () => callSnapshot
    ) as never
  );

  jest.spyOn(mediaPlaybackCoordinator, "getActiveMediaPlayback").mockImplementation(
    () => playbackOwner as never
  );
  jest
    .spyOn(mediaPlaybackCoordinator, "subscribeMediaPlayback")
    .mockImplementation((listener: (owner: never) => void) => {
      playbackListeners.add(listener as (owner: PlaybackOwner) => void);
      // The real coordinator emits the current owner on subscribe. Without
      // this, a session already running when the observer mounts is missed.
      listener(playbackOwner as never);
      // The real coordinator returns `Set.delete`, i.e. a boolean. Matching
      // that exactly keeps the mock honest about the contract the component
      // consumes.
      return () => playbackListeners.delete(listener as (owner: PlaybackOwner) => void);
    });
});

afterEach(() => {
  jest.restoreAllMocks();
});

describe("a video call", () => {
  it("holds the screen awake for the duration and releases when it ends", () => {
    render(<RealtimeKeepAwake />);
    expect(acquire).not.toHaveBeenCalled();

    setCall({ sessionActive: true, callType: "video" });
    expect(acquire).toHaveBeenCalledTimes(1);

    // The conjunction is the test: engaging is only half of it, and a feature
    // that engages and never lets go is worse than one that never engages.
    setCall({ sessionActive: false });
    expect(release).toHaveBeenCalledTimes(1);
  });

  /**
   * Hang-up, remote hang-up, a terminal backend status and an authoritative
   * connect failure all land on the same store field. Whichever one happened,
   * the release is the same — which is the point of tying this to
   * `sessionActive` rather than to a screen or to a success path.
   */
  it("releases when the call fails rather than ends cleanly", () => {
    render(<RealtimeKeepAwake />);
    setCall({ sessionActive: true, callType: "video" });
    acquire.mockClear();

    setCall({ sessionActive: false });
    expect(release).toHaveBeenCalledTimes(1);
    expect(acquire).not.toHaveBeenCalled();
  });

  it("does not hold the screen awake for an audio call", () => {
    render(<RealtimeKeepAwake />);
    setCall({ sessionActive: true, callType: "audio" });
    expect(acquire).not.toHaveBeenCalled();
  });
});

describe("a livestream", () => {
  it.each(["host", "viewer", "feed"])("holds and releases for the %s role", (scope) => {
    render(<RealtimeKeepAwake />);

    setPlayback({ id: `live-${scope}:1`, kind: "live" });
    expect(acquire).toHaveBeenCalledTimes(1);

    setPlayback(null);
    expect(release).toHaveBeenCalledTimes(1);
  });

  /**
   * Leaving a Live for a reel is a release, not a handover. Without this the
   * idle timer would stay disabled for the whole scroll session because
   * *something* still owned playback.
   */
  it("releases when Live is replaced by ordinary media", () => {
    render(<RealtimeKeepAwake />);
    setPlayback({ id: "live-viewer:1", kind: "live" });
    release.mockClear();

    setPlayback({ id: "reel:9", kind: "reel" });
    expect(release).toHaveBeenCalledTimes(1);
  });

  it("picks up a Live that was already running when it mounted", () => {
    // Cold-start into a Live, or a remount of the app tree. The coordinator
    // emits synchronously on subscribe, and this is what proves it is used.
    playbackOwner = { id: "live-host:7", kind: "live" };
    render(<RealtimeKeepAwake />);
    expect(acquire).toHaveBeenCalledTimes(1);
  });
});

describe("cleanup that must never leak", () => {
  it("releases when the app tree unmounts mid-session", () => {
    const view = render(<RealtimeKeepAwake />);
    setCall({ sessionActive: true, callType: "video" });
    release.mockClear();

    view.unmount();
    expect(release).toHaveBeenCalledTimes(1);
  });

  it("unsubscribes from the coordinator on unmount", () => {
    const view = render(<RealtimeKeepAwake />);
    expect(playbackListeners.size).toBe(1);
    view.unmount();
    // A retained listener would keep the component's state setter alive and
    // leak a little more on every remount.
    expect(playbackListeners.size).toBe(0);
  });

  /**
   * A call ending while a Live is running must NOT release: the Live still
   * needs the display. One shared tag plus a single derived boolean is what
   * makes the overlap safe, and this is the case that would catch a refactor
   * to per-source tags that forgot the interaction.
   */
  it("keeps the screen awake when one source ends but another is still live", () => {
    render(<RealtimeKeepAwake />);
    setPlayback({ id: "live-host:1", kind: "live" });
    setCall({ sessionActive: true, callType: "video" });
    release.mockClear();

    setCall({ sessionActive: false });
    expect(release).not.toHaveBeenCalled();

    setPlayback(null);
    expect(release).toHaveBeenCalledTimes(1);
  });

  it("does not thrash the native module while a session continues", () => {
    render(<RealtimeKeepAwake />);
    setCall({ sessionActive: true, callType: "video" });
    expect(acquire).toHaveBeenCalledTimes(1);

    // Unrelated store churn (a status poll, a participant joining) must not
    // re-acquire; a release/acquire pair per poll tick would be a real bug.
    setCall({ sessionActive: true, callType: "video" });
    expect(acquire).toHaveBeenCalledTimes(1);
    expect(release).not.toHaveBeenCalled();
  });
});
