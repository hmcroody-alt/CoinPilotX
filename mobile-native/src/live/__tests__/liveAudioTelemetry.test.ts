import {
  buildLiveAudioEvent,
  emitLiveAudioEvent,
  hashIdentifier,
  normalizeRole,
  redact,
  setLiveAudioTelemetrySink
} from "../liveAudioTelemetry";

// A realistic-looking LiveKit JWT. Not a real credential - three base64url
// segments so the redactor is tested against the shape it must catch.
const FAKE_JWT =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." +
  "eyJpc3MiOiJBUElrZXkiLCJzdWIiOiJwdWxzZS11c2VyLTQyIiwidmlkZW8iOnt9fQ." +
  "cUJqZmxUdVJnV3NBcUpxNWRnRm9wR2haWjJscGRXWQ";

describe("secret redaction", () => {
  it("removes a JWT wherever it appears", () => {
    expect(redact(FAKE_JWT)).not.toContain("eyJ");
    expect(redact(`connect failed with token ${FAKE_JWT}`)).not.toContain("eyJ");
    expect(redact(FAKE_JWT)).toContain("[redacted]");
  });

  it("removes bearer headers and long opaque blobs", () => {
    expect(redact("Authorization: Bearer sk_live_abcdef123456")).not.toContain("sk_live");
    expect(redact("sig=a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f6")).not.toContain("a1b2c3d4e5f6");
  });

  it("removes endpoints, which can carry credentials in the query string", () => {
    const line = redact("failed to reach wss://pulse.livekit.cloud/rtc?access_token=abc123");
    expect(line).not.toContain("access_token");
    expect(line).not.toContain("livekit.cloud");
  });

  it("keeps ordinary diagnostic text readable", () => {
    expect(redact("  microphone   publish timed out ")).toBe("microphone publish timed out");
  });

  it("bounds field length so one event cannot flood the log", () => {
    expect(redact("word ".repeat(200)).length).toBeLessThanOrEqual(120);
  });
});

describe("identifier hashing", () => {
  it("is stable for the same input and different for different inputs", () => {
    expect(hashIdentifier("pulse-live-7")).toBe(hashIdentifier("pulse-live-7"));
    expect(hashIdentifier("pulse-live-7")).not.toBe(hashIdentifier("pulse-live-8"));
  });

  it("never echoes the raw identifier", () => {
    const room = "pulse-live-7";
    expect(hashIdentifier(room)).not.toContain(room);
  });

  it("handles empty input without throwing", () => {
    expect(hashIdentifier("")).toBe("none");
    expect(hashIdentifier(undefined)).toBe("none");
  });
});

describe("role normalisation", () => {
  it("accepts the roles the backend mints", () => {
    expect(normalizeRole("host")).toBe("host");
    expect(normalizeRole("GUEST")).toBe("guest");
    expect(normalizeRole("co-host")).toBe("cohost");
    expect(normalizeRole("viewer")).toBe("viewer");
  });

  it("never passes an unrecognised role through verbatim", () => {
    expect(normalizeRole("pulse-user-42")).toBe("unknown");
    expect(normalizeRole(undefined)).toBe("unknown");
  });
});

describe("event construction", () => {
  it("defaults to the legacy path, so an unlabelled event is never miscredited to V2", () => {
    expect(buildLiveAudioEvent({ name: "live_audio_path_selected" }).path).toBe("v1_legacy");
  });

  it("omits absent optional fields instead of emitting undefined", () => {
    const event = buildLiveAudioEvent({ name: "live_audio_session_released", room: "pulse-live-7" });
    expect(Object.keys(event).sort()).toEqual(["name", "path", "role", "roomHash"]);
  });

  it("carries publish measurements through", () => {
    const event = buildLiveAudioEvent({
      name: "live_audio_publish_settled",
      path: "v2_isolated",
      role: "host",
      room: "pulse-live-7",
      outcome: "published",
      audioTrackCount: 1,
      duplicatesRemoved: 0,
      durationMs: 412.7
    });
    expect(event.outcome).toBe("published");
    expect(event.audioTrackCount).toBe(1);
    expect(event.duplicatesRemoved).toBe(0);
    expect(event.durationMs).toBe(413);
  });

  it("drops non-finite numbers rather than emitting NaN", () => {
    const event = buildLiveAudioEvent({ name: "live_audio_publish_timeout", durationMs: NaN, attempt: Infinity });
    expect(event.durationMs).toBeUndefined();
    expect(event.attempt).toBeUndefined();
  });

  it("SECURITY: a token smuggled into any free-text field never survives", () => {
    const event = buildLiveAudioEvent({
      name: "live_audio_token_refresh_failed",
      room: "pulse-live-7",
      reason: FAKE_JWT,
      detail: `refresh returned ${FAKE_JWT}`,
      outcome: FAKE_JWT
    });
    const serialized = JSON.stringify(event);
    expect(serialized).not.toContain("eyJ");
    expect(serialized).not.toContain(FAKE_JWT.slice(0, 24));
  });

  it("SECURITY: the raw room name never reaches the event", () => {
    const event = buildLiveAudioEvent({ name: "live_audio_session_claimed", room: "pulse-live-7" });
    expect(JSON.stringify(event)).not.toContain("pulse-live-7");
  });
});

describe("emission", () => {
  afterEach(() => setLiveAudioTelemetrySink(null));

  it("forwards the built event to the configured sink", () => {
    const seen: any[] = [];
    setLiveAudioTelemetrySink((event) => seen.push(event));
    emitLiveAudioEvent({ name: "live_audio_route_reapplied", path: "v2_isolated", reason: "oldDeviceUnavailable" });
    expect(seen).toHaveLength(1);
    expect(seen[0].name).toBe("live_audio_route_reapplied");
    expect(seen[0].reason).toBe("oldDeviceUnavailable");
  });

  it("never lets a failing sink break the broadcast", () => {
    setLiveAudioTelemetrySink(() => {
      throw new Error("analytics is down");
    });
    expect(() => emitLiveAudioEvent({ name: "live_audio_publish_started" })).not.toThrow();
  });
});
