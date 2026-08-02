import {
  buildLiveStartPayload,
  elapsedLabel,
  formatViewerCount,
  normalizeGuestRequest,
  normalizeGuestRequests,
  normalizeLiveGuest,
  normalizeLiveGuests,
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
      can_subscribe: true,
      can_publish_data: true,
      can_update_own_metadata: true,
      room_join: true,
      role: "host",
      guest_id: 0,
      request_id: 0,
      participant_name: "Roody",
      trace_id: "trace-1",
      expires_at: 1700000000
    });
    expect(creds).toEqual({
      token: "tok",
      url: "wss://livekit.example",
      room: "pulse-live-1",
      identity: "pulse-user-7",
      canPublish: true,
      canSubscribe: true,
      canPublishData: true,
      canUpdateOwnMetadata: true,
      roomJoin: true,
      role: "host",
      guestId: 0,
      requestId: 0,
      participantName: "Roody",
      traceId: "trace-1",
      expiresAt: "1700000000",
      // Absent from this payload, so the rollout gate stays OFF and the legacy
      // fallback stays available.
      audioV2Enabled: false,
      audioV2FallbackEnabled: true,
      audioTraceEnabled: false
    });
  });

  describe("QA-only audio trace gate", () => {
    const base = { token: "tok", livekit_url: "wss://livekit.example" };

    it("is OFF when omitted or expressed as a non-boolean", () => {
      for (const raw of [undefined, false, "true", 1, "1"]) {
        expect(normalizeLiveKitCredentials({ ...base, audio_trace_enabled: raw })?.audioTraceEnabled).toBe(false);
      }
    });

    it("is ON only when the server explicitly authorizes the account", () => {
      expect(normalizeLiveKitCredentials({ ...base, audio_trace_enabled: true })?.audioTraceEnabled).toBe(true);
    });
  });

  describe("livestream audio V2 rollout gate", () => {
    const base = { token: "tok", livekit_url: "wss://livekit.example" };

    it("is OFF when the server omits the field, so an older backend runs the legacy path", () => {
      expect(normalizeLiveKitCredentials(base)?.audioV2Enabled).toBe(false);
    });

    it("is ON only for an explicit server true", () => {
      expect(normalizeLiveKitCredentials({ ...base, audio_v2_enabled: true })?.audioV2Enabled).toBe(true);
      expect(normalizeLiveKitCredentials({ ...base, audio_shared_path_enabled: true })?.audioV2Enabled).toBe(true);
    });

    it("treats the canonical shared-path kill switch as authoritative over the legacy alias", () => {
      expect(normalizeLiveKitCredentials({
        ...base,
        audio_shared_path_enabled: false,
        audio_v2_enabled: true
      })?.audioV2Enabled).toBe(false);
    });

    it("KILL SWITCH: any non-true value runs the legacy path", () => {
      for (const raw of [false, "true", "false", 1, 0, "1", "0", null, undefined, {}]) {
        expect(normalizeLiveKitCredentials({ ...base, audio_v2_enabled: raw })?.audioV2Enabled).toBe(false);
      }
    });

    it("keeps the legacy fallback available unless the server explicitly disables it", () => {
      expect(normalizeLiveKitCredentials(base)?.audioV2FallbackEnabled).toBe(true);
      expect(normalizeLiveKitCredentials({ ...base, audio_v2_fallback_enabled: true })?.audioV2FallbackEnabled).toBe(true);
      expect(normalizeLiveKitCredentials({ ...base, audio_v2_fallback_enabled: false })?.audioV2FallbackEnabled).toBe(false);
    });
  });

  it("accepts a plain url field and defaults role to viewer", () => {
    const creds = normalizeLiveKitCredentials({ token: "tok", url: "wss://x" });
    expect(creds?.role).toBe("viewer");
    expect(creds?.canPublish).toBe(false);
  });

  it("normalizes server-verified co-host publishing claims", () => {
    const creds = normalizeLiveKitCredentials({
      token: "cohost-token",
      livekit_url: "wss://livekit.example",
      room: "pulse-live-44",
      identity: "pulse-live-guest-8",
      role: "cohost",
      can_publish: true,
      can_subscribe: true,
      can_publish_data: true,
      can_update_own_metadata: true,
      room_join: true,
      guest_id: 91,
      request_id: 77,
      participant_name: "Nova",
      trace_id: "cohost-trace",
      expires_at: 1800000000
    });

    expect(creds).toMatchObject({
      role: "cohost",
      canPublish: true,
      canSubscribe: true,
      canPublishData: true,
      canUpdateOwnMetadata: true,
      roomJoin: true,
      guestId: 91,
      requestId: 77,
      participantName: "Nova",
      traceId: "cohost-trace"
    });
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

describe("normalizeLiveGuest(s)", () => {
  it("normalizes an active guest payload from the backend", () => {
    const guest = normalizeLiveGuest({
      id: 12,
      user_id: 88,
      display_name: "Nova",
      avatar_url: "a.png",
      role: "cohost",
      role_label: "Co-host",
      status: "active",
      audio_muted: 1,
      video_enabled: true,
      joined_at: "2026-07-20T10:00:00Z"
    });
    expect(guest).toEqual({
      guestId: 12,
      userId: 88,
      requestId: 0,
      displayName: "Nova",
      avatarUrl: "a.png",
      role: "cohost",
      roleLabel: "Co-host",
      status: "active",
      audioMuted: true,
      videoEnabled: true,
      joinedAt: "2026-07-20T10:00:00Z"
    });
  });

  it("drops guests without a usable guest id and dedupes by id", () => {
    expect(normalizeLiveGuest({ user_id: 5 })).toBeNull();
    const list = normalizeLiveGuests([
      { id: 1, user_id: 1 },
      { id: 2, user_id: 2 },
      { id: 1, user_id: 1 }
    ]);
    expect(list.map((item) => item.guestId)).toEqual([1, 2]);
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
