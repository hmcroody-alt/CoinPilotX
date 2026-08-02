import { createLiveAudioTrace, setLiveAudioTraceSink } from "../liveAudioTrace";

afterEach(() => setLiveAudioTraceSink(null));

describe("QA-only ordered Live audio trace", () => {
  it("does nothing when the server gate is disabled", () => {
    const sink = jest.fn();
    setLiveAudioTraceSink(sink);
    const trace = createLiveAudioTrace({
      enabled: false,
      correlationId: "corr-1",
      room: "private-room-name",
      participantIdentity: "private-user-id",
      participantRole: "host"
    });
    expect(trace.emit("live_start_requested")).toBeNull();
    expect(trace.snapshot()).toEqual([]);
    expect(sink).not.toHaveBeenCalled();
  });

  it("emits a complete ordered, privacy-safe timeline schema", () => {
    const sink = jest.fn();
    setLiveAudioTraceSink(sink);
    const trace = createLiveAudioTrace({
      enabled: true,
      correlationId: "corr-2",
      room: "private-room-name",
      participantIdentity: "private-user-id",
      participantRole: "viewer"
    });
    const first = trace.emit("viewer_room_connected", { room_state: "connected" });
    const second = trace.emit("remote_audio_energy_detected", {
      room_state: "connected",
      participantIdentity: "host-user-id",
      audioLevel: 0.321,
      enabled: true,
      muted: false,
      subscription_state: "subscribed",
      output_route: "speaker"
    });

    expect(first?.sequence).toBe(1);
    expect(second?.sequence).toBe(2);
    expect(second).toEqual(expect.objectContaining({
      correlation_id: "corr-2",
      session_id: expect.stringMatching(/^hash:/),
      room_name: expect.stringMatching(/^hash:/),
      participant_identity: expect.stringMatching(/^hash:/),
      participant_role: "viewer",
      room_state: "connected",
      audio_owner: "none",
      track_sid: "none",
      publication_sid: "none",
      muted: false,
      enabled: true,
      subscription_state: "subscribed",
      output_route: "speaker",
      error_category: "none",
      audio_level: 32,
      audio_profile: "unknown",
      engine_state: "unknown"
    }));
    expect(JSON.stringify(trace.snapshot())).not.toContain("private-room-name");
    expect(JSON.stringify(trace.snapshot())).not.toContain("private-user-id");
    expect(sink).toHaveBeenCalledTimes(2);
  });

  it("redacts token-shaped values before the sink receives them", () => {
    const sink = jest.fn();
    setLiveAudioTraceSink(sink);
    const trace = createLiveAudioTrace({
      enabled: true,
      correlationId: "Bearer secret-token",
      room: "room",
      participantIdentity: "viewer",
      participantRole: "viewer"
    });
    trace.emit("invariant_failed", { error_category: "eyJabcdefghijk.secret.signature" });
    expect(JSON.stringify(sink.mock.calls)).not.toContain("secret-token");
    expect(JSON.stringify(sink.mock.calls)).not.toContain("eyJabcdefghijk");
  });
});
