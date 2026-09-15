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
  getPushInstallationId: (...args: unknown[]) => mockGetPushInstallationId(...args)
}));

import { registerVoipPushToken, unregisterVoipPushToken } from "../calls";

const INSTALLATION_ID = "native-ios-m1abcd-xyz123456789";

function bodyOf(call: unknown[]) {
  const options = call[1] as { body: string };
  return JSON.parse(options.body) as Record<string, unknown>;
}

beforeEach(() => {
  mockPulseApi.mockReset();
  mockPulseApi.mockResolvedValue({ ok: true });
  mockGetPushInstallationId.mockReset();
  mockGetPushInstallationId.mockResolvedValue(INSTALLATION_ID);
});

// The backend's `register_voip_token` returns `missing_device_id` (400) when the device id
// is absent, so a client that omits it never registers at all — and because registration
// failure is swallowed, the symptom is not an error but a phone that quietly never rings
// through CallKit and keeps using the alert-push fallback forever.
describe("VoIP token registration wire contract", () => {
  it("sends the installation id the backend requires", async () => {
    await registerVoipPushToken("voip-token-abc");

    const body = bodyOf(mockPulseApi.mock.calls[0]);
    expect(mockPulseApi.mock.calls[0][0]).toBe("/api/calls/voip-token");
    expect(body.token).toBe("voip-token-abc");
    expect(body.device_id).toBe(INSTALLATION_ID);
    expect(body.installation_id).toBe(INSTALLATION_ID);
  });

  it("files the VoIP token under the SAME id as the alert-push registration", async () => {
    // This is the join the alert-push suppression is built on. `/api/push/subscribe` files
    // under `getPushInstallationId()`, and the backend silences the incoming-call alert for
    // exactly those device ids that hold an active VoIP token. A VoIP registration under a
    // different id would not error — it would ring through CallKit *and* deliver the alert
    // banner to the same handset, which is the duplicate ring the whole design avoids.
    await registerVoipPushToken("voip-token-abc");
    const body = bodyOf(mockPulseApi.mock.calls[0]);
    expect(mockGetPushInstallationId).toHaveBeenCalled();
    expect(body.device_id).toBe(INSTALLATION_ID);
  });

  it("reports no environment, leaving the APNs host to the server", async () => {
    // The client cannot distinguish a sandbox entitlement from a production one at runtime
    // (`__DEV__` is false in a Release build that still carries `aps-environment:
    // development`), so any value sent here would be a guess that is wrong exactly when it
    // matters. The server's `default_environment()` follows the same `APNS_USE_SANDBOX`
    // switch as the alert sender, which keeps the two from targeting different APNs hosts.
    await registerVoipPushToken("voip-token-abc");
    const body = bodyOf(mockPulseApi.mock.calls[0]);
    expect(body.environment).toBeUndefined();
  });

  it("marks the registration as iOS VoIP so it lands in the right token table", async () => {
    await registerVoipPushToken("voip-token-abc");
    const body = bodyOf(mockPulseApi.mock.calls[0]);
    expect(body.platform).toBe("ios");
    expect(body.provider).toBe("apns_voip");
  });

  it("still sends a device id when the secure store read fails", async () => {
    mockGetPushInstallationId.mockRejectedValueOnce(new Error("keychain locked"));
    await expect(registerVoipPushToken("voip-token-abc")).resolves.toBeDefined();
    expect(bodyOf(mockPulseApi.mock.calls[0]).device_id).toBe("");
  });
});

describe("VoIP token revocation wire contract", () => {
  it("identifies the device by installation id, not only by token", async () => {
    // PushKit hands JS a token through a `register` event that fires once per launch, so a
    // user who signs out without having received a call in that session has no token to
    // send. Revoking by device id is what makes sign-out work in that — very common — case.
    await unregisterVoipPushToken({ reason: "logout" });

    const body = bodyOf(mockPulseApi.mock.calls[0]);
    expect(mockPulseApi.mock.calls[0][0]).toBe("/api/calls/voip-token/revoke");
    expect(body.device_id).toBe(INSTALLATION_ID);
    expect(body.reason).toBe("logout");
  });

  it("names the token as well when one is known", async () => {
    await unregisterVoipPushToken({ token: "voip-token-abc", reason: "logout" });
    expect(bodyOf(mockPulseApi.mock.calls[0]).token).toBe("voip-token-abc");
  });

  it("omits an empty token rather than asking the server to match on it", async () => {
    // `revoke_token` builds its WHERE clause from whichever identifiers are present. An
    // empty string is falsy on the server too, but sending `""` relies on that; omitting
    // the key states the intent directly.
    await unregisterVoipPushToken({ token: "", reason: "logout" });
    expect(bodyOf(mockPulseApi.mock.calls[0]).token).toBeUndefined();
  });
});
