import {
  applyRemoteAudioEnabled,
  countPublishedAudioTracks,
  countSubscribedRemoteAudioTracks,
  ensureMicrophonePublished,
  resolveRealtimeAudioConfiguration,
  restoreRealtimeRoomAudio,
  resumeRealtimeAudioSession,
  selectRealtimeSpeakerOutput,
  setLocalMicrophoneEnabled,
  startRealtimeAudioSession,
  stopRealtimeAudioSession
} from "../realtimeAudioEngine";

function sdkTrack() {
  const calls: boolean[] = [];
  return {
    kind: "audio",
    setEnabledCalls: calls,
    setEnabled(value: boolean) {
      calls.push(value);
      return Promise.resolve();
    }
  };
}

function participantWithAudioPublications(publications: any[]) {
  return {
    audioTrackPublications: new Map(publications.map((publication, index) => [String(index), publication]))
  };
}

describe("shared realtime audio engine", () => {
  it("keeps call and Live publisher audio on the same record-capable profile", () => {
    expect(resolveRealtimeAudioConfiguration("interactive")).toEqual({
      audioCategory: "playAndRecord",
      audioMode: "videoChat",
      audioCategoryOptions: ["allowBluetooth", "allowBluetoothA2DP", "allowAirPlay", "defaultToSpeaker"]
    });
  });

  it("keeps listen-only Live viewers on playback without a microphone dependency", () => {
    expect(resolveRealtimeAudioConfiguration("listener")).toEqual({
      audioCategory: "playback",
      audioMode: "moviePlayback",
      audioCategoryOptions: ["allowBluetooth", "allowBluetoothA2DP", "allowAirPlay"]
    });
  });

  it("activates and restores the same native audio session configuration", async () => {
    const session = {
      setAppleAudioConfiguration: jest.fn().mockResolvedValue(undefined),
      configureAudio: jest.fn().mockResolvedValue(undefined),
      startAudioSession: jest.fn().mockResolvedValue(undefined),
      stopAudioSession: jest.fn().mockResolvedValue(undefined)
    };
    const native = { registerGlobals: jest.fn(), AudioSession: session };
    const runtime = await startRealtimeAudioSession(native, "interactive");
    await resumeRealtimeAudioSession(runtime.session, runtime.configuration, "ios");
    await stopRealtimeAudioSession(runtime);

    expect(native.registerGlobals).toHaveBeenCalledWith({ autoConfigureAudioSession: false });
    expect(session.setAppleAudioConfiguration).toHaveBeenLastCalledWith(
      expect.objectContaining({ audioCategory: "playAndRecord", audioMode: "videoChat" })
    );
    expect(session.startAudioSession).toHaveBeenCalledTimes(2);
    expect(session.stopAudioSession).toHaveBeenCalledTimes(1);
  });

  it("retries microphone publication and verifies the resulting track", async () => {
    let enabledCalls = 0;
    const participant = {
      audioTrackPublications: new Map(),
      setMicrophoneEnabled: jest.fn(async (enabled: boolean) => {
        if (enabled && ++enabledCalls >= 2) participant.audioTrackPublications.set("mic", { track: sdkTrack() });
      })
    };
    await expect(ensureMicrophonePublished({ localParticipant: participant }, 0)).resolves.toBe(1);
    expect(participant.setMicrophoneEnabled).toHaveBeenCalledWith(false);
  });

  it("uses the verified publication for mute and unmute", async () => {
    const participant = {
      audioTrackPublications: new Map([["mic", { track: sdkTrack() }]]),
      setMicrophoneEnabled: jest.fn().mockResolvedValue(undefined)
    };
    const room = { localParticipant: participant };
    await expect(setLocalMicrophoneEnabled(room, false)).resolves.toBe(1);
    await expect(setLocalMicrophoneEnabled(room, true)).resolves.toBe(1);
    expect(participant.setMicrophoneEnabled).toHaveBeenNthCalledWith(1, false);
    expect(participant.setMicrophoneEnabled).toHaveBeenNthCalledWith(2, true);
  });

  it("counts and drives every subscribed remote audio track", async () => {
    const first = sdkTrack();
    const second = sdkTrack();
    const room = {
      remoteParticipants: new Map([
        ["host", participantWithAudioPublications([{ track: first }])],
        ["guest", participantWithAudioPublications([{ track: second }, { track: sdkTrack(), isSubscribed: false }])]
      ])
    };
    expect(countSubscribedRemoteAudioTracks(room)).toBe(2);
    expect(countPublishedAudioTracks(room.remoteParticipants.get("host"))).toBe(1);
    await expect(applyRemoteAudioEnabled(room, false)).resolves.toBe(2);
    await expect(applyRemoteAudioEnabled(room, true)).resolves.toBe(2);
    expect(first.setEnabledCalls).toEqual([false, true]);
    expect(second.setEnabledCalls).toEqual([false, true]);
  });

  it("restores publisher capture and viewer playback from desired state", async () => {
    const remoteTrack = sdkTrack();
    const localParticipant = {
      audioTrackPublications: new Map([["mic", { track: sdkTrack() }]]),
      setMicrophoneEnabled: jest.fn().mockResolvedValue(undefined)
    };
    const room = {
      localParticipant,
      remoteParticipants: new Map([
        ["host", participantWithAudioPublications([{ track: remoteTrack }])]
      ])
    };
    await expect(
      restoreRealtimeRoomAudio(room, {
        publishMicrophone: true,
        microphoneEnabled: true,
        remoteAudioEnabled: false
      })
    ).resolves.toEqual({ localAudioTrackCount: 1, remoteAudioTrackCount: 1 });
    expect(localParticipant.setMicrophoneEnabled).toHaveBeenCalledWith(true);
    expect(remoteTrack.setEnabledCalls).toEqual([false]);
  });

  it("routes speaker and Bluetooth selection through the shared native session", async () => {
    const runtime = {
      session: { selectAudioOutput: jest.fn().mockResolvedValue(undefined) },
      configuration: resolveRealtimeAudioConfiguration("interactive")
    };
    await selectRealtimeSpeakerOutput(runtime, true, "ios");
    await selectRealtimeSpeakerOutput(runtime, false, "android");
    expect(runtime.session.selectAudioOutput).toHaveBeenNthCalledWith(1, "force_speaker");
    expect(runtime.session.selectAudioOutput).toHaveBeenNthCalledWith(2, "earpiece");
  });
});
