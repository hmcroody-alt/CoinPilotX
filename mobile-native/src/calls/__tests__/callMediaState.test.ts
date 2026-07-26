import {
  callAudioSessionConfiguration,
  shouldSurfaceVideoAudioWarning,
  summarizeCallMediaState
} from "../callMediaState";

function publication(track = {}, overrides: Record<string, unknown> = {}) {
  return { track, isSubscribed: true, isMuted: false, ...overrides };
}

function roomFixture(overrides: Record<string, unknown> = {}) {
  return {
    localParticipant: {
      audioTrackPublications: new Map([["mic", publication({ kind: "audio" })]]),
      videoTrackPublications: new Map([["camera", publication({ kind: "video" })]])
    },
    remoteParticipants: new Map([
      [
        "remote",
        {
          audioTrackPublications: new Map([["remote-mic", publication({ kind: "audio" })]]),
          videoTrackPublications: new Map([["remote-camera", publication({ kind: "video" })]])
        }
      ]
    ]),
    ...overrides
  };
}

describe("summarizeCallMediaState", () => {
  it("separates local microphone publication from remote audio subscription", () => {
    const summary = summarizeCallMediaState(roomFixture());

    expect(summary.localAudioPublished).toBe(true);
    expect(summary.localAudioMuted).toBe(false);
    expect(summary.localVideoPublished).toBe(true);
    expect(summary.remoteAudioSubscribed).toBe(true);
    expect(summary.remoteAudioMuted).toBe(false);
    expect(summary.remoteVideoSubscribed).toBe(true);
    expect(summary.remoteAudioParticipantCount).toBe(1);
  });

  it("detects the broken video-call shape where video exists but audio is absent", () => {
    const summary = summarizeCallMediaState(
      roomFixture({
        localParticipant: {
          audioTrackPublications: new Map(),
          videoTrackPublications: new Map([["camera", publication({ kind: "video" })]])
        },
        remoteParticipants: new Map([
          [
            "remote",
            {
              audioTrackPublications: new Map(),
              videoTrackPublications: new Map([["remote-camera", publication({ kind: "video" })]])
            }
          ]
        ])
      })
    );

    expect(summary.localVideoPublished).toBe(true);
    expect(summary.remoteVideoSubscribed).toBe(true);
    expect(summary.localAudioPublished).toBe(false);
    expect(summary.remoteAudioSubscribed).toBe(false);
  });

  it("treats muted remote audio as subscribed but not currently audible", () => {
    const summary = summarizeCallMediaState(
      roomFixture({
        remoteParticipants: new Map([
          [
            "remote",
            {
              audioTrackPublications: new Map([["remote-mic", publication({ kind: "audio" }, { isMuted: true })]]),
              videoTrackPublications: new Map()
            }
          ]
        ])
      })
    );

    expect(summary.remoteAudioSubscribed).toBe(true);
    expect(summary.remoteAudioMuted).toBe(true);
  });
});

describe("shouldSurfaceVideoAudioWarning", () => {
  it("warns only for connected video calls with missing local or remote audio", () => {
    expect(shouldSurfaceVideoAudioWarning({ callType: "video", connected: true, localAudioPublished: false, remoteParticipantCount: 1, remoteAudioSubscribed: true })).toBe(true);
    expect(shouldSurfaceVideoAudioWarning({ callType: "video", connected: true, localAudioPublished: true, remoteParticipantCount: 1, remoteAudioSubscribed: false })).toBe(true);
  });

  it("does not warn for audio calls, disconnected calls, or waiting-for-peer states", () => {
    expect(shouldSurfaceVideoAudioWarning({ callType: "audio", connected: true, localAudioPublished: false, remoteParticipantCount: 1, remoteAudioSubscribed: false })).toBe(false);
    expect(shouldSurfaceVideoAudioWarning({ callType: "video", connected: false, localAudioPublished: false, remoteParticipantCount: 1, remoteAudioSubscribed: false })).toBe(false);
    expect(shouldSurfaceVideoAudioWarning({ callType: "video", connected: true, localAudioPublished: true, remoteParticipantCount: 0, remoteAudioSubscribed: false })).toBe(false);
  });
});

describe("callAudioSessionConfiguration", () => {
  it("keeps audio and video on the same existing call-compatible iOS category/mode", () => {
    expect(callAudioSessionConfiguration("video")).toMatchObject({ audioCategory: "playAndRecord", audioMode: "videoChat" });
    expect(callAudioSessionConfiguration("audio")).toMatchObject({ audioCategory: "playAndRecord", audioMode: "videoChat" });
  });
});
