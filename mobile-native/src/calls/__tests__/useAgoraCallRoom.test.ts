/**
 * The hook is now a thin binding over the module-scope callSessionStore, whose
 * import graph reaches native modules (expo-av cues, CallKit, API layer). Mock
 * those leaves so this suite keeps exercising the same pure participant
 * tracking helpers it always protected.
 */
jest.mock("../callSignalMedia", () => ({
  playCallCue: jest.fn(async () => undefined),
  stopCallTone: jest.fn(async () => undefined)
}));
jest.mock("../../core/voiceMessagePlayback", () => ({
  stopVoiceMessagePlayback: jest.fn(async () => undefined)
}));
jest.mock("../callKitBridge", () => ({
  markCallKitConnected: jest.fn(),
  endCallKitCall: jest.fn()
}));
jest.mock("../../core/mediaPlaybackCoordinator", () => ({
  claimMediaPlayback: jest.fn(async () => undefined),
  releaseMediaPlayback: jest.fn(async () => undefined)
}));
jest.mock("../../api/calls", () => ({
  getCallStatus: jest.fn(),
  endCall: jest.fn(),
  requestCallJoinToken: jest.fn(),
  markCallConnected: jest.fn()
}));
jest.mock("../../api/presenceSession", () => ({
  reportPresenceActivity: jest.fn(async () => undefined)
}));

import { addAgoraRemoteUid, removeAgoraRemoteUid } from "../useAgoraCallRoom";

describe("Agora group call participant tracking", () => {
  it("keeps every unique remote participant in join order", () => {
    expect(addAgoraRemoteUid([101, 202], 303)).toEqual([101, 202, 303]);
    expect(addAgoraRemoteUid([101, 202], 202)).toEqual([101, 202]);
  });

  it("removes only the participant who left", () => {
    expect(removeAgoraRemoteUid([101, 202, 303], 202)).toEqual([101, 303]);
    expect(removeAgoraRemoteUid([101, 303], 999)).toEqual([101, 303]);
  });
});
