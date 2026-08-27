/**
 * The keep-awake rule and its native binding.
 *
 * WHY THIS FILE MOCKS `expo-modules-core` AND ITS SIBLING DOES NOT
 *
 * Replacing `expo-modules-core` wholesale also replaces `requireNativeModule`,
 * which the jest-expo preset supplies for every Expo package in the tree. That
 * is harmless HERE, because `keepScreenAwake.ts` imports nothing else — but it
 * is fatal the moment a test reaches `expo-av` or `expo-secure-store`. So the
 * component lifecycle lives in `RealtimeKeepAwake.test.tsx`, which pulls in the
 * call store and therefore leaves the preset's mock alone.
 */

const mockActivate = jest.fn(async (_tag: string) => true);
const mockDeactivate = jest.fn(async (_tag: string) => true);

// The real `ExpoKeepAwake` is not present under Jest. Standing it up here is
// what lets "did the idle timer get released" be an assertion, not an inference.
//
// The two mocks are read through a wrapper rather than passed by reference:
// `jest.mock` is hoisted above the `const`s, and `requireOptionalNativeModule`
// runs at import time, so a direct reference captures `undefined` and the
// module ends up holding an object whose `activate` is not a function.
jest.mock("expo-modules-core", () => ({
  requireOptionalNativeModule: () => ({
    activate: (tag: string) => mockActivate(tag),
    deactivate: (tag: string) => mockDeactivate(tag)
  })
}));

import {
  REALTIME_KEEP_AWAKE_TAG,
  acquireKeepScreenAwake,
  keepScreenAwakeSupported,
  releaseKeepScreenAwake,
  shouldKeepScreenAwake
} from "../keepScreenAwake";

beforeEach(() => {
  mockActivate.mockClear();
  mockDeactivate.mockClear();
});

describe("the rule for when the screen stays lit", () => {
  const base = { callSessionActive: false, callType: "audio", mediaPlaybackKind: null };

  it("keeps the screen awake in a video call", () => {
    expect(shouldKeepScreenAwake({ ...base, callSessionActive: true, callType: "video" })).toBe(true);
  });

  /**
   * Not an oversight. On an audio call the phone is usually at the user's ear
   * with the proximity sensor blanking the display, so pinning the idle timer
   * open would drain the battery for a screen nobody is looking at.
   */
  it("leaves an audio call alone", () => {
    expect(shouldKeepScreenAwake({ ...base, callSessionActive: true, callType: "audio" })).toBe(false);
  });

  it("ignores the call type when no session is running", () => {
    // `callType` survives the end of a call in the store. Reading it without
    // `sessionActive` would hold the display open after every video hang-up.
    expect(shouldKeepScreenAwake({ ...base, callSessionActive: false, callType: "video" })).toBe(false);
  });

  it("keeps the screen awake for any Live role", () => {
    // Host, full-screen viewer and in-feed viewer all claim the coordinator
    // with kind "live", so one condition covers hosting and watching.
    expect(shouldKeepScreenAwake({ ...base, mediaPlaybackKind: "live" })).toBe(true);
  });

  it("ignores media that is not a call or a Live", () => {
    // A reel or the radio must not hold the display open — that is the
    // "permanent global keep-awake" the brief forbids, arrived at by accident.
    ["reel", "radio", "music_preview", "voice", "feed", "status"].forEach((kind) => {
      expect(shouldKeepScreenAwake({ ...base, mediaPlaybackKind: kind })).toBe(false);
    });
  });

  it("stays off when nothing is happening", () => {
    expect(shouldKeepScreenAwake(base)).toBe(false);
  });
});

describe("the native binding", () => {
  it("reports support when the module resolved", () => {
    expect(keepScreenAwakeSupported()).toBe(true);
  });

  it("passes the shared tag through to the module", async () => {
    // One tag for the whole feature: the native module reference-counts tags in
    // a Set, so an overlapping call and Live cannot strand the idle timer.
    await acquireKeepScreenAwake();
    expect(mockActivate).toHaveBeenCalledWith(REALTIME_KEEP_AWAKE_TAG);
    await releaseKeepScreenAwake();
    expect(mockDeactivate).toHaveBeenCalledWith(REALTIME_KEEP_AWAKE_TAG);
  });

  it("never rejects when the native call fails", async () => {
    // This runs from effect cleanup, where a rejection is unhandled. A failure
    // to release must not also become a crash.
    mockActivate.mockRejectedValueOnce(new Error("no native module"));
    mockDeactivate.mockRejectedValueOnce(new Error("no native module"));
    await expect(acquireKeepScreenAwake()).resolves.toBeUndefined();
    await expect(releaseKeepScreenAwake()).resolves.toBeUndefined();
  });
});
