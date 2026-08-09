jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn()
}));

const mockPulseApi = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

import {
  acceptCall,
  getCallStatus,
  normalizeCallPayload,
  requestCallJoinToken,
  startCall
} from "../calls";

describe("native call API contract normalization", () => {
  beforeEach(() => {
    mockPulseApi.mockReset();
  });

  it("keeps the LiveKit join token from canonical accept envelopes", async () => {
    mockPulseApi.mockResolvedValueOnce({
      ok: true,
      call: {
        public_id: "call_accepted",
        conversation_id: 42,
        call_type: "video",
        status: "connecting",
        participants: []
      },
      join: {
        ok: true,
        token: "livekit-token",
        livekit_url: "wss://livekit.example",
        room_name: "pulsesoc-call_accepted"
      }
    });

    const call = await acceptCall("call_accepted");

    expect(call.call_id).toBe("call_accepted");
    expect(call.call_type).toBe("video");
    expect(call.join?.token).toBe("livekit-token");
    expect(call.join?.livekit_url).toBe("wss://livekit.example");
    expect(call.room_name).toBe("pulsesoc-call_accepted");
  });

  it("reads join-token envelopes instead of treating them as direct join payloads", async () => {
    mockPulseApi.mockResolvedValueOnce({
      ok: true,
      call: { public_id: "call_join", status: "connecting" },
      join: {
        ok: true,
        token: "join-token",
        livekit_url: "wss://livekit.example",
        room_name: "pulsesoc-call_join"
      }
    });

    const join = await requestCallJoinToken("call_join");

    expect(join.token).toBe("join-token");
    expect(join.livekit_url).toBe("wss://livekit.example");
    expect(join.room_name).toBe("pulsesoc-call_join");
  });

  it("preserves provider-scoped Agora credentials without requiring LiveKit fields", async () => {
    mockPulseApi.mockResolvedValueOnce({
      ok: true,
      join: {
        provider: "agora",
        token: "redacted-agora-token",
        app_id: "public-app-id",
        channel_name: "pulsesoc-call_agora",
        uid: 42,
        room_name: "pulsesoc-call_agora"
      }
    });

    const join = await requestCallJoinToken("call_agora");

    expect(join.provider).toBe("agora");
    expect(join.app_id).toBe("public-app-id");
    expect(join.channel_name).toBe("pulsesoc-call_agora");
    expect(join.uid).toBe(42);
    expect(join.livekit_url).toBe("");
  });

  it("normalizes status envelopes without dropping the active call identity", async () => {
    mockPulseApi.mockResolvedValueOnce({
      ok: true,
      call: {
        public_id: "call_status",
        conversation_id: 7,
        call_type: "audio",
        status: "connected",
        room_name: "pulsesoc-call_status"
      },
      events: [{ id: 1, event_type: "client_connected" }]
    });

    const status = await getCallStatus("call_status");

    expect(status.call_id).toBe("call_status");
    expect(status.status).toBe("connected");
    expect(status.room_name).toBe("pulsesoc-call_status");
    expect(status.events).toHaveLength(1);
  });

  it("sends backend recipient IDs while retaining compatibility with old participant IDs", async () => {
    mockPulseApi.mockResolvedValueOnce({
      ok: true,
      public_id: "call_start",
      conversation_id: 8,
      call_type: "audio",
      status: "ringing"
    });

    await startCall({ conversation_id: 8, participant_user_ids: [44], call_type: "audio" });

    expect(mockPulseApi).toHaveBeenCalledWith("/api/calls/start", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: 8,
        participant_user_ids: [44],
        call_type: "audio",
        recipient_user_ids: [44],
        source: "native"
      })
    });
  });

  it("preserves legacy flat call payloads", () => {
    const call = normalizeCallPayload({
      public_id: "call_flat",
      call_type: "video",
      status: "ringing",
      join: {
        token: "flat-token",
        url: "wss://legacy-livekit.example",
        room_name: "pulsesoc-call_flat"
      }
    });

    expect(call.call_id).toBe("call_flat");
    expect(call.call_type).toBe("video");
    expect(call.join?.livekit_url).toBe("wss://legacy-livekit.example");
    expect(call.join?.token).toBe("flat-token");
  });
});
