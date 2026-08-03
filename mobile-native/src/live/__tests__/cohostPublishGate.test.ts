import { canConnectAsCohostPublisher } from "../liveSession";
import type { LiveKitCredentials } from "../liveSession";

function creds(overrides: Partial<LiveKitCredentials> = {}): LiveKitCredentials {
  return {
    token: "tok-abc",
    url: "wss://live.example",
    room: "room-1",
    identity: "user-9",
    canPublish: true,
    canSubscribe: true,
    canPublishData: true,
    canUpdateOwnMetadata: false,
    roomJoin: true,
    role: "cohost",
    guestId: 42,
    requestId: 7,
    participantName: "Guest",
    traceId: "trace-1",
    expiresAt: "2026-01-01T00:00:00Z",
    audioV2Enabled: false,
    publisherAudioV2Enabled: false,
    audioV2FallbackEnabled: true,
    audioTraceEnabled: false,
    ...overrides
  };
}

describe("canConnectAsCohostPublisher (Issue 5: guest joining fails)", () => {
  it("allows connecting when the token grants publish AND is bound to a real guest slot", () => {
    expect(canConnectAsCohostPublisher(creds())).toBe(true);
  });

  it("refuses a null/undefined credential set", () => {
    expect(canConnectAsCohostPublisher(null)).toBe(false);
    expect(canConnectAsCohostPublisher(undefined)).toBe(false);
  });

  it("refuses a viewer token that cannot publish (the silent on-stage bubble bug)", () => {
    expect(canConnectAsCohostPublisher(creds({ canPublish: false }))).toBe(false);
  });

  it("refuses a publish-capable token that is not bound to a guest slot", () => {
    expect(canConnectAsCohostPublisher(creds({ guestId: 0 }))).toBe(false);
    expect(canConnectAsCohostPublisher(creds({ guestId: -1 }))).toBe(false);
  });

  it("refuses when the token or url is missing", () => {
    expect(canConnectAsCohostPublisher(creds({ token: "" }))).toBe(false);
    expect(canConnectAsCohostPublisher(creds({ url: "" }))).toBe(false);
  });
});
