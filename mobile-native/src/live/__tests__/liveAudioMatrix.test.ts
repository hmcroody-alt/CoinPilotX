import type { LiveRole, LiveStageParticipant } from "../liveParticipantRegistry";
import {
  FORBIDDEN_SECOND_AUDIO_OWNERS,
  audibilityMatrix,
  guestArrivalRequiresAudioReconfiguration,
  inspectLiveAudioOwnership,
  isCustomAudioProcessing,
  resolveLiveAudioPlan,
  resolveLiveEchoControl,
  roleParticipatesInAudio,
  shouldHear,
  silentPublishers
} from "../liveAudioMatrix";

function person(overrides: Partial<LiveStageParticipant> = {}): LiveStageParticipant {
  const rtcUid = overrides.rtcUid ?? 1;
  return {
    rtcUid,
    userId: rtcUid,
    guestId: 0,
    key: `uid-${rtcUid}`,
    displayName: `User ${rtcUid}`,
    avatarUrl: "",
    role: "guest",
    roleLabel: "Guest",
    phase: "live",
    isLocal: false,
    isHost: false,
    hasVideo: true,
    hasAudio: true,
    audioMuted: false,
    speaking: false,
    layoutPosition: 0,
    unidentified: false,
    ...overrides
  };
}

/** A host plus `guests` guests, all live and unmuted. */
function stage(guests: number): LiveStageParticipant[] {
  return [
    person({ rtcUid: 1, key: "host", role: "host", isHost: true }),
    ...Array.from({ length: guests }, (_, index) =>
      person({ rtcUid: index + 2, key: `guest-${index + 1}`, role: "guest" })
    )
  ];
}

describe("audio plan", () => {
  const roles: LiveRole[] = ["host", "cohost", "guest", "audience"];

  it("makes every stage role a publisher and the audience a listener", () => {
    expect(roles.filter((role) => roleParticipatesInAudio(role))).toEqual(["host", "cohost", "guest"]);
  });

  it("never plays a client its own microphone back to itself", () => {
    for (const role of roles) {
      expect(resolveLiveAudioPlan(role, true).localAudioPlayback).toBe(false);
    }
  });

  it("subscribes every role to remote audio, because a Live is audible to everyone", () => {
    for (const role of roles) {
      expect(resolveLiveAudioPlan(role, true).subscribeRemoteAudio).toBe(true);
    }
  });

  it("never captures an audience member's microphone", () => {
    // The failure this catches: publishing configured from a local preference
    // instead of the server's role, so a viewer's mic is opened by a screen.
    expect(resolveLiveAudioPlan("audience", true)).toMatchObject({
      publishMicrophone: false,
      clientRole: "audience"
    });
  });

  it("refuses to publish for an unauthorized guest, so a client cannot self-promote", () => {
    expect(resolveLiveAudioPlan("guest", false).publishMicrophone).toBe(false);
    expect(resolveLiveAudioPlan("guest", false).clientRole).toBe("audience");
    expect(resolveLiveAudioPlan("guest", true).publishMicrophone).toBe(true);
  });
});

describe("audibility matrix", () => {
  it("makes everyone on stage audible to everyone else, at every population", () => {
    for (let guests = 1; guests <= 12; guests += 1) {
      const roster = stage(guests);
      const pairs = audibilityMatrix(roster);
      expect(pairs).toHaveLength(roster.length * (roster.length - 1));
      expect(pairs.every((pair) => pair.audible)).toBe(true);
    }
  });

  it("keeps guests audible to each other, not merely to the host", () => {
    // The classic multi-guest bug: a star topology through the host, so two
    // guests talk over each other because neither can hear the other.
    const roster = stage(3);
    expect(shouldHear(roster[1], roster[2])).toBe(true);
    expect(shouldHear(roster[2], roster[1])).toBe(true);
    expect(shouldHear(roster[3], roster[1])).toBe(true);
  });

  it("keeps the host and every guest mutually audible", () => {
    const roster = stage(4);
    for (const guest of roster.slice(1)) {
      expect(shouldHear(roster[0], guest)).toBe(true);
      expect(shouldHear(guest, roster[0])).toBe(true);
    }
  });

  it("makes an audience member a listener who is never a speaker", () => {
    const roster = [...stage(2), person({ rtcUid: 90, key: "viewer", role: "audience" })];
    const viewer = roster[roster.length - 1];
    expect(roster.slice(0, -1).every((speaker) => shouldHear(viewer, speaker))).toBe(true);
    expect(roster.slice(0, -1).some((listener) => shouldHear(listener, viewer))).toBe(false);
  });

  it("nobody hears themselves", () => {
    const roster = stage(3);
    expect(roster.every((participant) => shouldHear(participant, participant) === false)).toBe(true);
    expect(audibilityMatrix(roster).some((pair) => pair.listener === pair.speaker)).toBe(false);
  });

  it("silences a muted speaker for everyone, with no exception for the host", () => {
    const roster = stage(2);
    roster[1] = person({ ...roster[1], audioMuted: true });
    expect(roster.filter((listener) => shouldHear(listener, roster[1]))).toHaveLength(0);
  });

  it("does not leak a guest who has not reached the stage yet", () => {
    const roster = stage(2);
    roster[2] = person({ ...roster[2], phase: "joining" });
    expect(shouldHear(roster[0], roster[2])).toBe(false);
    // …and they become audible the moment they are live, with nothing else changing.
    expect(shouldHear(roster[0], person({ ...roster[2], phase: "live" }))).toBe(true);
  });

  it("reports who should be audible but is not, rather than repairing it", () => {
    const roster = stage(2);
    roster[1] = person({ ...roster[1], audioMuted: true });
    roster[2] = person({ ...roster[2], hasAudio: false });
    expect(silentPublishers(roster)).toEqual([
      { key: "guest-1", reason: "muted" },
      { key: "guest-2", reason: "no_audio_track" }
    ]);
  });

  it("survives an empty stage", () => {
    expect(audibilityMatrix([])).toEqual([]);
    expect(silentPublishers([])).toEqual([]);
  });
});

describe("single audio owner", () => {
  it("accepts exactly one engine and one microphone owner", () => {
    expect(inspectLiveAudioOwnership(["liveEngine"], ["liveEngine"])).toEqual([]);
  });

  it("rejects a second engine", () => {
    const violations = inspectLiveAudioOwnership(["liveEngine", "otherEngine"], ["liveEngine"]);
    expect(violations.map((violation) => violation.code)).toContain("MULTIPLE_LIVE_ENGINES");
  });

  it("rejects a second microphone owner, because a device has one microphone", () => {
    const violations = inspectLiveAudioOwnership(["liveEngine"], ["liveEngine", "guestMic"]);
    expect(violations.map((violation) => violation.code)).toContain("MULTIPLE_MICROPHONE_OWNERS");
  });

  it("names each forbidden alternate stack explicitly", () => {
    for (const name of FORBIDDEN_SECOND_AUDIO_OWNERS) {
      expect(inspectLiveAudioOwnership([name], [name]).map((v) => v.code)).toContain("FORBIDDEN_AUDIO_OWNER");
    }
  });

  it("treats a repeated owner id as one owner, not as a violation", () => {
    expect(inspectLiveAudioOwnership(["liveEngine", "liveEngine"], ["liveEngine"])).toEqual([]);
  });

  it("does not reconfigure audio when a guest arrives", () => {
    // The audio counterpart of the no-restart rule. Guest churn is invisible to
    // the microphone, the engine, and the audio session.
    expect(guestArrivalRequiresAudioReconfiguration()).toBe(false);
  });
});

describe("echo control", () => {
  const publisher = resolveLiveAudioPlan("host", true);
  const listener = resolveLiveAudioPlan("audience", true);

  it("turns on Agora's own processing for anyone publishing", () => {
    expect(resolveLiveEchoControl(publisher, 1)).toMatchObject({
      echoCancellation: true,
      noiseSuppression: true,
      automaticGainControl: true
    });
  });

  it("leaves a listener alone, because there is nothing to cancel", () => {
    expect(resolveLiveEchoControl(listener, 6)).toMatchObject({ listenerOnly: true, echoCancellation: false });
  });

  it("engages the chatroom scenario from the first guest, not the second", () => {
    // Two people on speakerphone is already a feedback loop. Waiting for a third
    // means the first guest's arrival is when the Live starts echoing.
    expect(resolveLiveEchoControl(publisher, 1).scenario).toBe("default");
    expect(resolveLiveEchoControl(publisher, 2).scenario).toBe("chatroom");
    expect(resolveLiveEchoControl(publisher, 6).scenario).toBe("chatroom");
  });

  it("recognises custom DSP by name, so it can be refused rather than reviewed", () => {
    for (const name of ["pulseEchoCanceller", "liveNoiseGate", "guestAudioFilter", "customDsp", "audioWorkletNode"]) {
      expect(isCustomAudioProcessing(name)).toBe(true);
    }
    expect(isCustomAudioProcessing("resolveLiveEchoControl")).toBe(false);
  });
});
