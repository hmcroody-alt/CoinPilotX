import {
  buildLiveStartPayload,
  elapsedLabel,
  formatViewerCount,
  normalizeGuestRequest,
  normalizeGuestRequests,
  normalizeLiveKitCredentials,
  normalizeLiveStartResult,
  pendingGuestRequests
} from "../liveSession";
import type { LiveStudioDraft } from "../liveStudioReadiness";

function draft(overrides: Partial<LiveStudioDraft> = {}): LiveStudioDraft {
  return {
    title: "My Live",
    description: "A test broadcast",
    liveType: "solo",
    audience: "public",
    allowComments: true,
    recordReplay: true,
    updatedAt: "",
    ...overrides
  };
}

describe("buildLiveStartPayload", () => {
  it("maps a solo public draft to the backend contract", () => {
    expect(buildLiveStartPayload(draft())).toEqual({
      title: "My Live",
      category: "Just Chatting",
      audience: "public",
      premium_only: false,
      allow_comments: true,
      record_replay: true,
      multi_guest: false,
      live_type: "solo",
      description: "A test broadcast",
      context_type: "native"
    });
  });

  it("marks subscriber audiences as premium and multi-guest types as multi_guest", () => {
    const payload = buildLiveStartPayload(draft({ liveType: "panel", audience: "subscribers" }));
    expect(payload.premium_only).toBe(true);
    expect(payload.multi_guest).toBe(true);
    expect(payload.category).toBe("Panel");
  });

  it("falls back to a default title and omits an empty description", () => {
    const payload = buildLiveStartPayload(draft({ title: "   ", description: "   " }));
    expect(payload.title).toBe("PulseSoc Live");
    expect(payload.description).toBeUndefined();
  });

  it("caps an overlong title at 120 chars", () => {
    const payload = buildLiveStartPayload(draft({ title: "x".repeat(200) }));
    expect(payload.title).toHaveLength(120);
  });
});

describe("normalizeLiveStartResult", () => {
  it("normalizes a full backend start response", () => {
    const result = normalizeLiveStartResult({
      live_id: 42,
      webrtc_room_id: "pulse-live-42",
      hls_url: "https://stream.mux.com/abc.m3u8",
      feed_post_id: 900,
      livekit: { room: "pulse-live-42", token_url: "/api/pulse/live/42/livekit/token" }
    });
    expect(result).toEqual({
      liveId: 42,
      room: "pulse-live-42",
      webrtcRoomId: "pulse-live-42",
      hlsUrl: "https://stream.mux.com/abc.m3u8",
      feedPostId: 900,
      tokenUrl: "/api/pulse/live/42/livekit/token"
    });
  });

  it("returns null when there is no usable live id", () => {
    expect(normalizeLiveStartResult({ ok: false, message: "denied" })).toBeNull();
    expect(normalizeLiveStartResult(null)).toBeNull();
  });
});

describe("normalizeLiveKitCredentials", () => {
  it("accepts livekit_url and coerces publish flag and numeric expiry", () => {
    const creds = normalizeLiveKitCredentials({
      token: "tok",
      livekit_url: "wss://livekit.example",
      room: "pulse-live-1",
      identity: "pulse-user-7",
      can_publish: true,
      role: "host",
      expires_at: 1700000000
    });
    expect(creds).toEqual({
      token: "tok",
      url: "wss://livekit.example",
      room: "pulse-live-1",
      identity: "pulse-user-7",
      canPublish: true,
      role: "host",
      expiresAt: "1700000000"
    });
  });

  it("accepts a plain url field and defaults role to viewer", () => {
    const creds = normalizeLiveKitCredentials({ token: "tok", url: "wss://x" });
    expect(creds?.role).toBe("viewer");
    expect(creds?.canPublish).toBe(false);
  });

  it("returns null when the token or url is missing", () => {
    expect(normalizeLiveKitCredentials({ token: "tok" })).toBeNull();
    expect(normalizeLiveKitCredentials({ url: "wss://x" })).toBeNull();
    expect(normalizeLiveKitCredentials(null)).toBeNull();
  });
});

describe("normalizeGuestRequest(s)", () => {
  it("normalizes a nested-user guest request", () => {
    const req = normalizeGuestRequest({
      request_id: 5,
      user: { user_id: 88, username: "nova", display_name: "Nova", avatar_url: "a.png" },
      status: "pending",
      camera_ready: 1,
      mic_ready: true,
      requested_at: "2026-07-19T10:00:00Z"
    });
    expect(req).toEqual({
      requestId: 5,
      userId: 88,
      displayName: "Nova",
      username: "nova",
      avatarUrl: "a.png",
      status: "pending",
      cameraReady: true,
      micReady: true,
      requestedAt: "2026-07-19T10:00:00Z"
    });
  });

  it("drops requests without a usable request or user id", () => {
    expect(normalizeGuestRequest({ user: { id: 1 } })).toBeNull();
    expect(normalizeGuestRequest({ request_id: 3 })).toBeNull();
  });

  it("dedupes by request id and sorts most recent first", () => {
    const list = normalizeGuestRequests([
      { request_id: 1, user_id: 1, requested_at: "2026-07-19T09:00:00Z" },
      { request_id: 2, user_id: 2, requested_at: "2026-07-19T11:00:00Z" },
      { request_id: 1, user_id: 1, requested_at: "2026-07-19T09:00:00Z" }
    ]);
    expect(list.map((item) => item.requestId)).toEqual([2, 1]);
  });

  it("keeps only pending/requested entries", () => {
    const list = normalizeGuestRequests([
      { request_id: 1, user_id: 1, status: "pending" },
      { request_id: 2, user_id: 2, status: "accepted" },
      { request_id: 3, user_id: 3, status: "requested" }
    ]);
    expect(pendingGuestRequests(list).map((item) => item.requestId).sort()).toEqual([1, 3]);
  });
});

describe("elapsedLabel", () => {
  it("formats minutes and seconds under an hour", () => {
    expect(elapsedLabel(0)).toBe("00:00");
    expect(elapsedLabel(65)).toBe("01:05");
  });

  it("adds an hours field past one hour and clamps negatives", () => {
    expect(elapsedLabel(3725)).toBe("1:02:05");
    expect(elapsedLabel(-10)).toBe("00:00");
  });
});

describe("formatViewerCount", () => {
  it("formats raw, thousands, and millions", () => {
    expect(formatViewerCount(0)).toBe("0");
    expect(formatViewerCount(950)).toBe("950");
    expect(formatViewerCount(1500)).toBe("1.5K");
    expect(formatViewerCount(2000)).toBe("2K");
    expect(formatViewerCount(1_500_000)).toBe("1.5M");
  });
});
