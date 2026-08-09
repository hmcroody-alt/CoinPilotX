const mockGetSessionCookie = jest.fn(async () => "");
const mockGetSessionEnvelope = jest.fn(async () => null);

jest.mock("../../session/sessionStore", () => ({
  clearNativeSessionCredentials: jest.fn(),
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

import { pulseApi } from "../pulseApi";

describe("pulseApi in-flight read coalescing", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (mockGetSessionCookie as jest.Mock).mockResolvedValue("");
    (mockGetSessionEnvelope as jest.Mock).mockResolvedValue(null);
  });

  it("shares simultaneous identical canonical GET requests", async () => {
    let resolveFetch!: (value: Response) => void;
    const fetchPromise = new Promise<Response>((resolve) => { resolveFetch = resolve; });
    global.fetch = jest.fn(() => fetchPromise) as jest.Mock;

    const first = pulseApi<{ value: number }>("/api/pulse/profile/7");
    const second = pulseApi<{ value: number }>("/api/pulse/profile/7");
    await Promise.resolve();
    await Promise.resolve();
    expect(global.fetch).toHaveBeenCalledTimes(1);

    resolveFetch(new Response(JSON.stringify({ value: 7 }), { status: 200 }));
    await expect(Promise.all([first, second])).resolves.toEqual([{ value: 7 }, { value: 7 }]);
  });

  it("never combines writes", async () => {
    global.fetch = jest.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 })) as jest.Mock;
    await Promise.all([
      pulseApi("/api/pulse/save", { method: "POST", body: "{}" }),
      pulseApi("/api/pulse/save", { method: "POST", body: "{}" })
    ]);
    expect(global.fetch).toHaveBeenCalledTimes(2);
  });

  it("attaches the current mobile bearer token to post writes immediately after login", async () => {
    (mockGetSessionEnvelope as jest.Mock).mockResolvedValue({
      version: 1,
      userId: 42,
      accessToken: "native-access-token",
      accessTokenExpiresAt: Date.now() + 60_000,
      refreshToken: "native-refresh-token",
      refreshTokenExpiresAt: Date.now() + 3600_000
    });
    global.fetch = jest.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 })) as jest.Mock;

    await pulseApi("/api/pulse/posts", { method: "POST", body: "{}" });

    const headers = (global.fetch as jest.Mock).mock.calls[0][1].headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer native-access-token");
  });
});
