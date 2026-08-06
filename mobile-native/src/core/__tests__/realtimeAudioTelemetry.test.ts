import {
  emitRealtimeAudioEvent,
  setRealtimeAudioTelemetrySink,
  setRealtimeAudioTelemetryVerbose
} from "../realtimeAudioTelemetry";

describe("realtimeAudioTelemetry", () => {
  afterEach(() => {
    setRealtimeAudioTelemetrySink(null);
    setRealtimeAudioTelemetryVerbose(false);
  });

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

  // The whole point of the native bridge is the enabled/running split and
  // WebRTC's own error string. Both are longer than the 96-character cap that
  // applies to the short identity fields, and concatenating them into `outcome`
  // is what silently destroyed the first attempt at explaining `engine=false`.
  it("keeps native engine state and native error intact past the outcome cap", () => {
    const events: any[] = [];
    setRealtimeAudioTelemetrySink((event) => events.push(event));
    const engineState =
      "nativeIn=en/stop;nativeOut=dis/stop;nativeInit=play:false,rec:true;" +
      "alwaysPrepared=true;muteMode=0;inputMuted=false;manualRender=false";
    const nativeError =
      "nativeLogs=[3]Failed to start engine: -10875 | [2]No input path. | " +
      "[2]ReconfigureEngine: Failed to recover engine state, error: -10868 (+7 dropped)";

    emitRealtimeAudioEvent({
      name: "audio_engine_guard_failed",
      correlationId: "rt-abc-1",
      roomType: "livestream",
      participantRole: "host",
      engineState,
      nativeError,
      failureStage: "camera_start",
      interruption: "none",
      recoveryAttempt: 2
    });

    expect(engineState.length).toBeGreaterThan(96);
    expect(nativeError.length).toBeGreaterThan(96);
    expect(events[0].engineState).toBe(engineState);
    expect(events[0].nativeError).toBe(nativeError);
    expect(events[0].failureStage).toBe("camera_start");
    expect(events[0].interruption).toBe("none");
    expect(events[0].recoveryAttempt).toBe(2);
  });

  // A wider cap must not become a wider leak: redaction runs before truncation,
  // so a token pasted into a native log line is still removed.
  it("still redacts secrets inside the wide native fields", () => {
    const events: any[] = [];
    setRealtimeAudioTelemetrySink((event) => events.push(event));
    const jwt = "eyJabcdefghijk.abcdefghijklmnopqrstuvwxyz.abcdefghijklmnopqrstuvwxyz";

    emitRealtimeAudioEvent({
      name: "audio_engine_guard_failed",
      correlationId: "rt-abc-2",
      roomType: "livestream",
      participantRole: "host",
      nativeError: `nativeLogs=[3]Failed to start engine for wss://live.example?token=${jwt}`
    });

    expect(events[0].nativeError).not.toContain(jwt);
    expect(events[0].nativeError).not.toContain("live.example");
    expect(events[0].nativeError).toContain("[redacted]");
  });

  // The native fields are optional. An event that carries none of them must not
  // gain empty strings, or every log line grows noise that hides the one line
  // that does carry a reading.
  it("omits native fields entirely when the caller supplies none", () => {
    const events: any[] = [];
    setRealtimeAudioTelemetrySink((event) => events.push(event));

    emitRealtimeAudioEvent({
      name: "microphone_published",
      correlationId: "rt-abc-3",
      roomType: "livestream",
      participantRole: "host"
    });

    expect(events[0]).not.toHaveProperty("engineState");
    expect(events[0]).not.toHaveProperty("nativeError");
    expect(events[0]).not.toHaveProperty("failureStage");
    expect(events[0]).not.toHaveProperty("interruption");
    expect(events[0]).not.toHaveProperty("recoveryAttempt");
  });
});

describe("realtimeAudioTelemetry default sink severity", () => {
  let errorSpy: jest.SpyInstance;
  let logSpy: jest.SpyInstance;

  beforeEach(() => {
    setRealtimeAudioTelemetrySink(null);
    errorSpy = jest.spyOn(console, "error").mockImplementation(() => undefined);
    logSpy = jest.spyOn(console, "log").mockImplementation(() => undefined);
  });

  afterEach(() => {
    setRealtimeAudioTelemetryVerbose(false);
    errorSpy.mockRestore();
    logSpy.mockRestore();
  });

  const context = { correlationId: "rt-sev-1", roomType: "livestream", participantRole: "host" } as const;

  // Logging a healthy transition at error level trains every reader - human and
  // crash reporter alike - to ignore the channel. During this incident the real
  // `audio_engine_guard_failed` line was indistinguishable from the healthy
  // lines around it, because all of them were console.error.
  it("logs normal transitions below error level", () => {
    emitRealtimeAudioEvent({ name: "microphone_published", ...context });
    expect(logSpy).toHaveBeenCalledTimes(1);
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it.each([
    "audio_engine_guard_failed",
    "audio_owner_rejected",
    "microphone_publish_failed",
    "invariant_violation"
  ] as const)("logs %s at error level", (name) => {
    emitRealtimeAudioEvent({ name, ...context });
    expect(errorSpy).toHaveBeenCalledTimes(1);
    expect(logSpy).not.toHaveBeenCalled();
  });

  // iOS os_log drops info/debug in Release, so a device capture would otherwise
  // see only the failures and none of the transitions leading to them. Opt-in,
  // so ordinary Release builds keep an honest severity mapping.
  it("raises every event to error level while a verbose capture is running", () => {
    setRealtimeAudioTelemetryVerbose(true);
    emitRealtimeAudioEvent({ name: "microphone_published", ...context });
    expect(errorSpy).toHaveBeenCalledTimes(1);
    expect(logSpy).not.toHaveBeenCalled();
  });
});
