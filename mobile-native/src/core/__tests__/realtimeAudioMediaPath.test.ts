import {
  claimRealtimeAudioPath,
  releaseRealtimeAudioPath,
  startPublishingAudio
} from "../realtimeAudioMediaPath";
import {
  claimRealtimeAudioSession,
  resetRealtimeAudioOwnership
} from "../realtimeAudioEngine";

function connectedPublishingRoom() {
  const listeners = new Set<(publication: any) => void>();
  const publications = new Map<string, any>();
  const room = {
    state: "connected",
    on: (_event: string, listener: (publication: any) => void) => listeners.add(listener),
    off: (_event: string, listener: (publication: any) => void) => listeners.delete(listener),
    localParticipant: {
      audioTrackPublications: publications,
      setMicrophoneEnabled: jest.fn(async (enabled: boolean) => {
        if (!enabled || publications.size > 0) return;
        const publication = { kind: "audio", track: { kind: "audio", sid: "mic-1" } };
        publications.set("mic-1", publication);
        listeners.forEach((listener) => listener(publication));
      }),
      unpublishTrack: jest.fn()
    }
  };
  return room;
}

describe("governed realtime audio media path", () => {
  beforeEach(async () => {
    await resetRealtimeAudioOwnership();
  });

  it("publishes a Live host only through the current host lease", async () => {
    const room = connectedPublishingRoom();
    const lease = claimRealtimeAudioSession("live_host", "live:host:42");

    const result = await startPublishingAudio({
      room,
      lease,
      feature: "livestream",
      role: "host",
      canPublishMicrophone: true,
      context: { sessionId: "42", roomType: "livestream", participantRole: "host" }
    });

    expect(result.outcome).toBe("published");
    expect(result.audioTrackCount).toBe(1);
    expect(room.localParticipant.setMicrophoneEnabled).toHaveBeenCalledTimes(1);
  });

  it("rejects a delayed publication that carries a stale lease generation", async () => {
    const room = connectedPublishingRoom();
    const stale = claimRealtimeAudioSession("live_host", "live:host:42");
    claimRealtimeAudioSession("live_host", "live:host:42");

    await expect(startPublishingAudio({
      room,
      lease: stale,
      feature: "livestream",
      role: "host",
      canPublishMicrophone: true,
      context: { sessionId: "42", roomType: "livestream", participantRole: "host" }
    })).rejects.toMatchObject({ code: "REALTIME_AUDIO_STALE_OR_WRONG_OWNER" });
    expect(room.localParticipant.setMicrophoneEnabled).not.toHaveBeenCalled();
  });

  it("allows a server-approved Live guest through the same publisher", async () => {
    const room = connectedPublishingRoom();
    const lease = claimRealtimeAudioSession("live_guest", "live:guest:42");

    const result = await startPublishingAudio({
      room,
      lease,
      feature: "livestream",
      role: "approved_guest",
      canPublishMicrophone: true,
      context: { sessionId: "42", roomType: "livestream", participantRole: "approved_guest" }
    });

    expect(result.audioTrackCount).toBe(1);
    expect(room.localParticipant.setMicrophoneEnabled).toHaveBeenCalledTimes(1);
  });

  it("rejects publication before the room is connected", async () => {
    const room = connectedPublishingRoom();
    room.state = "connecting";
    const lease = claimRealtimeAudioSession("live_host", "live:host:42");

    await expect(startPublishingAudio({
      room,
      lease,
      feature: "livestream",
      role: "host",
      canPublishMicrophone: true,
      context: { sessionId: "42", roomType: "livestream", participantRole: "host" }
    })).rejects.toMatchObject({ code: "REALTIME_AUDIO_ROOM_NOT_CONNECTED" });
  });

  it("never lets a Live viewer publish a microphone", async () => {
    const room = connectedPublishingRoom();
    const lease = claimRealtimeAudioSession("live_viewer", "live:viewer:42");

    await expect(startPublishingAudio({
      room,
      lease,
      feature: "livestream",
      role: "viewer",
      canPublishMicrophone: false,
      context: { sessionId: "42", roomType: "livestream", participantRole: "viewer" }
    })).rejects.toMatchObject({ code: "REALTIME_AUDIO_PUBLISH_FORBIDDEN" });
  });

  it("prohibits legacy and shared paths from activating in one room", () => {
    const room = connectedPublishingRoom();
    claimRealtimeAudioPath(room, "legacy_fallback");
    expect(() => claimRealtimeAudioPath(room, "shared_governed")).toThrow(
      expect.objectContaining({ code: "REALTIME_AUDIO_PATH_CONFLICT" })
    );
    releaseRealtimeAudioPath(room);
    expect(() => claimRealtimeAudioPath(room, "shared_governed")).not.toThrow();
  });
});
