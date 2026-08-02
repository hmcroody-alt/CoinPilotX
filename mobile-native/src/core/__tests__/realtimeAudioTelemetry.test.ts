import {
  emitRealtimeAudioEvent,
  setRealtimeAudioTelemetrySink
} from "../realtimeAudioTelemetry";

describe("realtimeAudioTelemetry", () => {
  afterEach(() => setRealtimeAudioTelemetrySink(null));

  it("emits correlation-safe structured session diagnostics", () => {
    const events: any[] = [];
    setRealtimeAudioTelemetrySink((event) => events.push(event));

    emitRealtimeAudioEvent({
      name: "microphone_published",
      correlationId: "corr-123",
      sessionId: "private-room-name",
      roomType: "audio_call",
      participantRole: "caller",
      outcome: "published",
      audioTrackCount: 1
    });

    expect(events).toEqual([
      expect.objectContaining({
        name: "microphone_published",
        correlationId: "corr-123",
        roomType: "audio_call",
        participantRole: "caller",
        outcome: "published",
        audioTrackCount: 1
      })
    ]);
    expect(events[0].sessionHash).not.toContain("private-room-name");
  });

  it("redacts tokens, authorization headers, endpoints, and opaque secrets", () => {
    const events: any[] = [];
    setRealtimeAudioTelemetrySink((event) => events.push(event));
    const jwt = "eyJabcdefghijk.abcdefghijklmnopqrstuvwxyz.abcdefghijklmnopqrstuvwxyz";

    emitRealtimeAudioEvent({
      name: "microphone_publish_failed",
      correlationId: `Bearer ${jwt}`,
      roomType: `wss://livekit.example?token=${jwt}`,
      participantRole: "viewer",
      failureCategory: jwt
    });

    const serialized = JSON.stringify(events[0]);
    expect(serialized).not.toContain(jwt);
    expect(serialized).not.toContain("livekit.example");
    expect(serialized).toContain("[redacted]");
  });
});
