/**
 * The state these pin is "signed in, and silently unable to write".
 *
 * Two credentials travel on every native request: a session cookie, which
 * authenticates, and a bearer from the stored envelope, which is additionally
 * what the server's CSRF gate needs before it will accept a write. They are
 * stored in the same keychain but written at different times, and `setSecureValue`
 * deliberately swallows a failed keychain write rather than crash the app. So the
 * envelope can be absent while the cookie still works -- and when it is, the app
 * looks completely normal. Reads succeed and are attributed to the right user;
 * only writes fail, with a 403 that no amount of retrying clears, because the
 * recovery path keys on 401.
 *
 * Measured on the CJ staging simulator before this fix: 45 minutes of
 * authenticated GETs at `user_id=1`, an access token that had expired at minute
 * zero, *zero* refresh attempts, and every write refused. The server had
 * supported cookie-only refresh the whole time; the client's precondition was
 * stricter than the function it guarded, so the one state where recovery was
 * both possible and necessary was the state it excluded.
 */
const mockGetSessionCookie = jest.fn(async () => "");
const mockGetSessionEnvelope = jest.fn(async () => null as unknown);
const mockSetSessionEnvelope = jest.fn();

jest.mock("../../session/sessionStore", () => ({
  clearNativeSessionCredentials: jest.fn(),
  getSessionCookie: () => mockGetSessionCookie(),
  getSessionEnvelope: () => mockGetSessionEnvelope(),
  setCachedSessionUser: jest.fn(),
  setSessionCookie: jest.fn(),
  setSessionEnvelope: (envelope: unknown) => mockSetSessionEnvelope(envelope)
}));
jest.mock("../../session/qaTemporaryAccount", () => ({ shouldRejectTemporaryQaUser: jest.fn(() => false) }));
jest.mock("../../core/perfTrace", () => ({ startSpan: jest.fn(() => ({ end: jest.fn() })) }));
jest.mock("react-native", () => ({ Platform: { OS: "ios" } }));
jest.mock("../config", () => ({ PULSE_API_BASE_URL: "https://pulse.test" }));

import { pulseApi } from "../pulseApi";

const WRITE_PATH = "/api/business-os/suppliers/cj/connect";
const WRITE = { method: "POST", body: JSON.stringify({ api_key: "x" }) };
const REFRESH = "/api/mobile/auth/refresh";

function refreshCalls() {
  return (global.fetch as jest.Mock).mock.calls.filter((call) => String(call[0]).includes(REFRESH));
}
function writeCalls() {
  return (global.fetch as jest.Mock).mock.calls.filter((call) => !String(call[0]).includes(REFRESH));
}
function headerOf(call: any[], name: string) {
  const headers = call[1].headers;
  return headers instanceof Headers ? headers.get(name) : headers?.[name];
}

function freshTokens() {
  return new Response(
    JSON.stringify({
      authenticated: true,
      user: { user_id: 1 },
      access_token: "recovered-access",
      access_token_expires_in: 900,
      refresh_token: "recovered-refresh",
      refresh_token_expires_in: 3600
    }),
    { status: 200 }
  );
}

/** `refresh` answers the refresh endpoint; every other call is served in order. */
function routeFetch(refresh: () => Response, ...responses: Array<() => Response>) {
  let served = 0;
  global.fetch = jest.fn(async (url: string) => {
    if (String(url).includes(REFRESH)) return refresh();
    const next = responses[Math.min(served, responses.length - 1)];
    served += 1;
    return next();
  }) as unknown as typeof fetch;
}

const ok = () => new Response(JSON.stringify({ ok: true }), { status: 200 });
const csrf403 = () =>
  new Response(JSON.stringify({ ok: false, code: "csrf", error_code: "csrf" }), { status: 403 });

beforeEach(() => {
  jest.clearAllMocks();
  jest.resetModules();
  (mockGetSessionEnvelope as jest.Mock).mockResolvedValue(null);
  (mockGetSessionCookie as jest.Mock).mockResolvedValue("");
});

describe("a cookie that outlives its envelope", () => {
  it("recovers a bearer from the cookie alone, rather than sending none at all", async () => {
    (mockGetSessionCookie as jest.Mock).mockResolvedValue("pulsesoc_session=abc");
    routeFetch(freshTokens, ok);
    // The refresh writes an envelope; the retried read of it must find one, or
    // the request goes out bare and the test would pass for the wrong reason.
    mockSetSessionEnvelope.mockImplementation((envelope) => {
      (mockGetSessionEnvelope as jest.Mock).mockResolvedValue(envelope);
    });

    await expect(pulseApi(WRITE_PATH, WRITE)).resolves.toMatchObject({ ok: true });

    expect(refreshCalls()).toHaveLength(1);
    expect(headerOf(writeCalls()[0], "Authorization")).toBe("Bearer recovered-access");
  });

  it("sends no Authorization header at all when there is nothing to recover from", async () => {
    routeFetch(freshTokens, ok);

    await expect(pulseApi(WRITE_PATH, WRITE)).resolves.toMatchObject({ ok: true });

    // No cookie and no envelope is a genuinely signed-out device. Attempting a
    // refresh there would be a wasted round trip on every single request.
    expect(refreshCalls()).toHaveLength(0);
    expect(headerOf(writeCalls()[0], "Authorization")).toBeNull();
  });

  it("does not send an empty Cookie header, which would suppress the platform jar", async () => {
    (mockGetSessionEnvelope as jest.Mock).mockResolvedValue({
      version: 1,
      userId: 1,
      accessToken: "",
      accessTokenExpiresAt: 0,
      refreshToken: "stored-refresh",
      refreshTokenExpiresAt: Date.now() + 3_600_000
    });
    routeFetch(freshTokens, ok);

    await pulseApi(WRITE_PATH, WRITE).catch(() => undefined);

    // `Cookie: ""` is not the same as omitting it: an explicit empty header
    // overrides what the platform would otherwise attach, which on the recovery
    // path is the whole credential.
    expect(headerOf(refreshCalls()[0], "Cookie")).toBeUndefined();
  });
});

describe("a CSRF refusal, which no 401 ever announces", () => {
  it("is recovered from once and replayed, instead of standing forever", async () => {
    (mockGetSessionCookie as jest.Mock).mockResolvedValue("pulsesoc_session=abc");
    routeFetch(freshTokens, csrf403, ok);
    mockSetSessionEnvelope.mockImplementation((envelope) => {
      (mockGetSessionEnvelope as jest.Mock).mockResolvedValue(envelope);
    });

    await expect(pulseApi(WRITE_PATH, WRITE)).resolves.toMatchObject({ ok: true });
    expect(writeCalls()).toHaveLength(2);
  });

  it("recovers even when nothing at all is readable locally, because the server just vouched", async () => {
    // The keychain reads empty -- no cookie, no envelope -- yet the request was
    // authenticated, so a session cookie reached the server from the platform
    // jar. This is the device the whole fix exists for, and gating recovery on
    // JS-readable state would skip exactly it.
    (mockGetSessionCookie as jest.Mock).mockResolvedValue("");
    (mockGetSessionEnvelope as jest.Mock).mockResolvedValue(null);
    routeFetch(freshTokens, csrf403, ok);
    mockSetSessionEnvelope.mockImplementation((envelope) => {
      (mockGetSessionEnvelope as jest.Mock).mockResolvedValue(envelope);
    });

    await expect(pulseApi(WRITE_PATH, WRITE)).resolves.toMatchObject({ ok: true });

    expect(refreshCalls()).toHaveLength(1);
    // The recovered envelope must be persisted, or the next write repeats all
    // of this instead of simply carrying the bearer.
    expect(mockSetSessionEnvelope).toHaveBeenCalledWith(
      expect.objectContaining({ refreshToken: "recovered-refresh", userId: 1 })
    );
    expect(headerOf(writeCalls()[1], "Authorization")).toBe("Bearer recovered-access");
  });

  it("is surfaced, not retried forever, when the fresh token does not fix it", async () => {
    (mockGetSessionCookie as jest.Mock).mockResolvedValue("pulsesoc_session=abc");
    routeFetch(freshTokens, csrf403);
    mockSetSessionEnvelope.mockImplementation((envelope) => {
      (mockGetSessionEnvelope as jest.Mock).mockResolvedValue(envelope);
    });

    await expect(pulseApi(WRITE_PATH, WRITE)).rejects.toMatchObject({ status: 403, code: "csrf" });
    // The replay is issued with refresh disabled, so a CSRF failure a new token
    // cannot fix costs exactly one extra attempt -- never a loop.
    expect(writeCalls()).toHaveLength(2);
  });
});

describe("a cookie-only recovery that keeps failing", () => {
  it("backs off instead of posting a refresh for every request on the screen", async () => {
    (mockGetSessionCookie as jest.Mock).mockResolvedValue("pulsesoc_session=abc");
    // 500 is `temporary`: it does not clear the cookie, so the precondition
    // stays true and nothing else stops this repeating.
    routeFetch(() => new Response("", { status: 500 }), ok);

    await pulseApi("/api/calls/active").catch(() => undefined);
    await pulseApi("/api/calls/active").catch(() => undefined);
    await pulseApi("/api/calls/active").catch(() => undefined);

    expect(refreshCalls()).toHaveLength(1);
  });
});
