jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn()
}));

const mockPulseApi = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

const mockGetPushInstallationId = jest.fn();

jest.mock("../installationId", () => ({
  getPushInstallationId: () => mockGetPushInstallationId()
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
    mockGetPushInstallationId.mockReset();
    mockGetPushInstallationId.mockResolvedValue("native-ios-device-0123456789");
  });

  it("keeps the Agora join token from canonical accept envelopes", async () => {
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
        token: "agora-token",
        app_id: "agora-app-id",
        room_name: "pulsesoc-call_accepted"
      }
    });

    const call = await acceptCall("call_accepted");

    expect(call.call_id).toBe("call_accepted");
    expect(call.call_type).toBe("video");
    expect(call.join?.token).toBe("agora-token");
    expect(call.join?.app_id).toBe("agora-app-id");
    expect(call.room_name).toBe("pulsesoc-call_accepted");
  });

  it("reads join-token envelopes instead of treating them as direct join payloads", async () => {
    mockPulseApi.mockResolvedValueOnce({
      ok: true,
      call: { public_id: "call_join", status: "connecting" },
      join: {
        ok: true,
        token: "join-token",
        app_id: "agora-app-id",
        room_name: "pulsesoc-call_join"
      }
    });

    const join = await requestCallJoinToken("call_join");

    expect(join.token).toBe("join-token");
    expect(join.app_id).toBe("agora-app-id");
    expect(join.room_name).toBe("pulsesoc-call_join");
  });

  it("preserves provider-scoped Agora credentials", async () => {
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

  /**
   * Answering has to say *which device* answered, or the phone cancels its own call.
   *
   * Accepting moves the call off the `ringing` edge, and that edge fans an
   * `answered_elsewhere` VoIP cancel to every device of the answering user — the point
   * being to clear a second device left showing a full-screen CallKit UI for a call
   * somebody already took. The cancel carries the *same* call UUID as the answered call,
   * so `_voip_stop_ringing` spares the device that answered; but the only thing telling it
   * which device that is, is `_answering_device_ids` reading the accept body.
   *
   * With no id in the body the exclusion set is empty, the answering phone receives its
   * own cancel, `AppDelegate` calls `endCall(withUUID:reason:)` on the UUID it is
   * connected on, and CallKit tears the UI down as `answeredElsewhere` while Agora keeps
   * running underneath — the call is live and the system says it ended. That is the
   * lock-screen "PulseSoc Audio ended" report, and it is a *body* bug, invisible to every
   * assertion about the accept *response*.
   */
  describe("accept names the answering device", () => {
    const acceptEnvelope = {
      ok: true,
      call: { public_id: "call_answer", status: "connecting", call_type: "audio" }
    };

    /** The accept body as the server receives it. */
    async function acceptBody() {
      const [, options] = mockPulseApi.mock.calls[0] as [string, { body: string }];
      return JSON.parse(options.body) as Record<string, unknown>;
    }

    it("sends the push installation id under both keys the backend reads", async () => {
      // `_answering_device_ids` accepts either key. Sending both is not belt-and-braces
      // for its own sake: the two names exist because registration and accept grew apart,
      // and a backend that only ever learned one of them must still be able to spare this
      // device. Sending one key would make the fix depend on which name that is.
      mockPulseApi.mockResolvedValueOnce(acceptEnvelope);

      await acceptCall("call_answer");

      expect(await acceptBody()).toEqual({
        source: "native",
        device_id: "native-ios-device-0123456789",
        installation_id: "native-ios-device-0123456789"
      });
    });

    it("names the same id the VoIP registration filed under", async () => {
      // The exclusion is a string match against the ids the *push registrations* stored.
      // An id minted here, or read from anywhere but `getPushInstallationId`, would name a
      // device the server has never heard of — the body would look correct and the phone
      // would still cancel itself. This pins the single source rather than the value.
      mockPulseApi.mockResolvedValueOnce(acceptEnvelope);

      await acceptCall("call_answer");

      expect(mockGetPushInstallationId).toHaveBeenCalledTimes(1);
      const body = await acceptBody();
      const registeredId = await mockGetPushInstallationId.mock.results[0].value;
      expect(body.device_id).toBe(registeredId);
      expect(body.installation_id).toBe(registeredId);
    });

    it("omits the keys entirely when the device id cannot be read", async () => {
      // A locked keychain makes `getPushInstallationId` return empty rather than mint an
      // id that matches no registration. Forwarding that empty string would name a device
      // the server cannot match, which is strictly worse than saying nothing: the backend
      // would carry a junk entry in the exclusion set and still cancel this device, while
      // the body claimed to have answered the question. Omission keeps the failure honest.
      mockGetPushInstallationId.mockResolvedValue("");
      mockPulseApi.mockResolvedValueOnce(acceptEnvelope);

      await acceptCall("call_answer");

      const body = await acceptBody();
      expect(body).toEqual({ source: "native" });
      expect(body).not.toHaveProperty("device_id");
      expect(body).not.toHaveProperty("installation_id");
    });

    it("still answers the call when the device id lookup throws", async () => {
      // Positive control for the omission path. Answering is the one thing that must not
      // be contingent on the keychain: a rejection here propagating would turn a degraded
      // `answered_elsewhere` race into a call that cannot be picked up at all — trading a
      // rare wrong-looking teardown for a total failure of the feature.
      mockGetPushInstallationId.mockRejectedValue(new Error("keychain unavailable"));
      mockPulseApi.mockResolvedValueOnce(acceptEnvelope);

      const call = await acceptCall("call_answer");

      expect(call.call_id).toBe("call_answer");
      expect(await acceptBody()).toEqual({ source: "native" });
    });
  });

  it("preserves flat Agora call payloads", () => {
    const call = normalizeCallPayload({
      public_id: "call_flat",
      call_type: "video",
      status: "ringing",
      join: {
        token: "flat-token",
        provider: "agora",
        app_id: "agora-app-id",
        channel_name: "pulsesoc-call_flat",
        uid: 42,
        room_name: "pulsesoc-call_flat"
      }
    });

    expect(call.call_id).toBe("call_flat");
    expect(call.call_type).toBe("video");
    expect(call.join?.app_id).toBe("agora-app-id");
    expect(call.join?.token).toBe("flat-token");
  });
});
