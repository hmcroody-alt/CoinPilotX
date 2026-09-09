/**
 * A 401 is not self-describing: it says a credential was rejected, not *which*
 * one. `pulseApi` reads a bare 401 as "our access token aged out" and recovers
 * by refreshing and replaying, which is right for almost every route and wrong
 * for a supplier route, where the rejected credential belongs to the provider.
 *
 * These pin the split. The cost of getting it wrong is not a wasted round trip:
 * the replay spends a second call against CJ's tightest-quota auth endpoint and
 * comes back rate-limited, so a merchant whose CJ key was answered about clearly
 * ("APIkey is wrong") is instead shown "your supplier isn't responding".
 */
const mockGetSessionCookie = jest.fn(async () => "");
const mockGetSessionEnvelope = jest.fn(async () => null as unknown);
const mockClearNativeSessionCredentials = jest.fn();

jest.mock("../../session/sessionStore", () => ({
  clearNativeSessionCredentials: () => mockClearNativeSessionCredentials(),
  getSessionCookie: () => mockGetSessionCookie(),
  getSessionEnvelope: () => mockGetSessionEnvelope(),
  setCachedSessionUser: jest.fn(),
  setSessionCookie: jest.fn(),
  setSessionEnvelope: jest.fn()
}));
jest.mock("../../session/qaTemporaryAccount", () => ({ shouldRejectTemporaryQaUser: jest.fn(() => false) }));
jest.mock("../../core/perfTrace", () => ({ startSpan: jest.fn(() => ({ end: jest.fn() })) }));
jest.mock("react-native", () => ({ Platform: { OS: "ios" } }));
jest.mock("../config", () => ({ PULSE_API_BASE_URL: "https://pulse.test" }));

import { PulseApiError, pulseApi } from "../pulseApi";

const SUPPLIER_PATH = "/api/business-os/suppliers/cj/discover-shops";
const WRITE = { method: "POST", body: JSON.stringify({ api_key: "x" }) };

/** A session that is perfectly healthy -- so a refresh would in fact succeed. */
function healthySession() {
  (mockGetSessionEnvelope as jest.Mock).mockResolvedValue({
    version: 1,
    userId: 1,
    accessToken: "access",
    accessTokenExpiresAt: Date.now() + 60_000,
    refreshToken: "refresh",
    refreshTokenExpiresAt: Date.now() + 3_600_000
  });
}

/**
 * Answers the refresh endpoint successfully and everything else with `first`,
 * then `second`. A test that never reaches `second` is the point of most of
 * these: the second answer stands in for the replayed provider call.
 */
function routeFetch(first: () => Response, second: () => Response) {
  let served = 0;
  global.fetch = jest.fn(async (url: string) => {
    if (String(url).includes("/api/mobile/auth/refresh")) {
      return new Response(
        JSON.stringify({
          authenticated: true,
          user: { user_id: 1 },
          access_token: "access2",
          access_token_expires_in: 900,
          refresh_token: "refresh2",
          refresh_token_expires_in: 3600
        }),
        { status: 200 }
      );
    }
    served += 1;
    return served === 1 ? first() : second();
  }) as unknown as typeof fetch;
}

function providerCalls() {
  return (global.fetch as jest.Mock).mock.calls.filter(
    (call) => !String(call[0]).includes("/api/mobile/auth/refresh")
  );
}

describe("a 401 that is not about this device's PulseSoc session", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (mockGetSessionCookie as jest.Mock).mockResolvedValue("");
    healthySession();
  });

  it("is surfaced to the caller instead of being replayed against the provider", async () => {
    routeFetch(
      () => new Response(JSON.stringify({ ok: false, code: "REAUTH_REQUIRED", error_code: "REAUTH_REQUIRED" }), { status: 401 }),
      () => new Response(JSON.stringify({ ok: false, error_code: "RATE_LIMITED" }), { status: 429 })
    );

    await expect(pulseApi(SUPPLIER_PATH, WRITE)).rejects.toMatchObject({
      status: 401,
      code: "REAUTH_REQUIRED"
    });
    expect(providerCalls()).toHaveLength(1);
  });

  it("does not even attempt a session refresh, so the session is never touched", async () => {
    routeFetch(
      () => new Response(JSON.stringify({ ok: false, error_code: "reauth_required" }), { status: 401 }),
      () => new Response(JSON.stringify({ ok: true }), { status: 200 })
    );

    await expect(pulseApi(SUPPLIER_PATH, WRITE)).rejects.toBeInstanceOf(PulseApiError);
    expect(
      (global.fetch as jest.Mock).mock.calls.some((call) => String(call[0]).includes("/api/mobile/auth/refresh"))
    ).toBe(false);
    expect(mockClearNativeSessionCredentials).not.toHaveBeenCalled();
  });

  it.each(["auth_expired", "credential_missing", "supplier_disconnected", "connection_not_found", "not_connected", "invalid_api_key"])(
    "treats %s the same way, since each names a credential other than the session",
    async (code) => {
      routeFetch(
        () => new Response(JSON.stringify({ ok: false, error_code: code }), { status: 401 }),
        () => new Response(JSON.stringify({ ok: true }), { status: 200 })
      );

      await expect(pulseApi(SUPPLIER_PATH, WRITE)).rejects.toMatchObject({ status: 401, code });
      expect(providerCalls()).toHaveLength(1);
    }
  );
});

describe("a 401 that is about the PulseSoc session still recovers", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (mockGetSessionCookie as jest.Mock).mockResolvedValue("");
    healthySession();
  });

  it("refreshes and replays when the server names the session as the problem", async () => {
    routeFetch(
      () => new Response(JSON.stringify({ ok: false, error_code: "login_required" }), { status: 401 }),
      () => new Response(JSON.stringify({ ok: true, data: { shops: [] } }), { status: 200 })
    );

    await expect(pulseApi(SUPPLIER_PATH, WRITE)).resolves.toMatchObject({ ok: true });
    expect(providerCalls()).toHaveLength(2);
  });

  it("refreshes and replays when the 401 carries no code at all", async () => {
    // The deny-list is deliberately not an allow-list: older routes answer 401
    // with an empty body, and those must keep recovering exactly as before.
    routeFetch(
      () => new Response("", { status: 401 }),
      () => new Response(JSON.stringify({ ok: true }), { status: 200 })
    );

    await expect(pulseApi("/api/pulse/profile/1")).resolves.toMatchObject({ ok: true });
    expect(providerCalls()).toHaveLength(2);
  });
});
