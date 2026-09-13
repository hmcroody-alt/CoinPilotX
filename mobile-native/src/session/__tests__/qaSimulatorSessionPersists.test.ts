/**
 * The QA auto-login has to leave behind a session, not just a cookie.
 *
 * `createQaSimulatorLocalSession` registers a throwaway account with its own
 * `fetch` rather than going through `signIn`, so it is on its own hook for the
 * two writes that make a sign-in durable: the token envelope (pulseApi reads
 * its bearer from there) and the cached user (bootstrapSession rebuilds
 * identity from it). It originally did neither, and the resulting failure was
 * invisible from every angle that looked healthy:
 *
 *   - the register POST returned 200 and really did create the user
 *   - the returned AuthState really was `signedIn`
 *   - and the app still sat on the login form, 401ing every request, minting a
 *     brand-new throwaway user on each remount -- eight of them in one launch
 *
 * A test that only asserted on the returned AuthState would have passed
 * throughout. So these assert on what was WRITTEN, which is the part that was
 * missing, and one case pins the negative that made it hard to see: a cookie
 * alone is not enough.
 */

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

const mockSetSessionEnvelope = jest.fn();
const mockSetCachedSessionUser = jest.fn();
const mockSetSessionCookie = jest.fn();

jest.mock("../sessionStore", () => ({
  setSessionEnvelope: (...args: unknown[]) => mockSetSessionEnvelope(...args),
  setCachedSessionUser: (...args: unknown[]) => mockSetCachedSessionUser(...args),
  setSessionCookie: (...args: unknown[]) => mockSetSessionCookie(...args),
  getSessionCookie: jest.fn(async () => ""),
  getSessionEnvelope: jest.fn(async () => null),
  getCachedSessionUser: jest.fn(async () => null),
  clearNativeSessionCredentials: jest.fn(async () => undefined)
}));

// The gate this path lives behind is a loopback API base plus two opt-in flags.
jest.mock("../qaTemporaryAccount", () => ({
  canUseTemporaryQaAccount: () => true,
  isLocalApiBaseUrl: () => true,
  shouldRejectTemporaryQaUser: () => false,
  isTemporaryQaUser: () => true
}));

jest.mock("../../api/config", () => ({ PULSE_API_BASE_URL: "http://127.0.0.1:8000" }));

import { createQaSimulatorLocalSession } from "../qaSimulatorAuth";

const QA_USER = { user_id: 42, username: "nativeqa_1234567890", full_name: "PulseSoc Native QA" };

/** The real shape of a 200 from `POST /api/mobile/auth/register`. */
function registerOk(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    status: 200,
    headers: { get: () => "pulse_session=abc; Path=/; HttpOnly" },
    json: async () => ({
      ok: true,
      authenticated: true,
      user: QA_USER,
      access_token: "access-tok",
      access_token_expires_in: 900,
      refresh_token: "psr_refresh-tok",
      refresh_token_expires_in: 2592000,
      ...overrides
    })
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  (global as { fetch?: unknown }).fetch = jest.fn(async () => registerOk());
});

describe("QA simulator auto-login persistence", () => {
  it("returns a signed-in state", async () => {
    const state = await createQaSimulatorLocalSession();
    expect(state.status).toBe("signedIn");
    expect(state.user).toEqual(QA_USER);
  });

  it("persists the token envelope, so pulseApi has a bearer to attach", async () => {
    await createQaSimulatorLocalSession();
    expect(mockSetSessionEnvelope).toHaveBeenCalledTimes(1);
    expect(mockSetSessionEnvelope).toHaveBeenCalledWith(
      expect.objectContaining({
        userId: 42,
        accessToken: "access-tok",
        refreshToken: "psr_refresh-tok"
      })
    );
  });

  it("caches the user, so the next bootstrap restores instead of re-registering", async () => {
    await createQaSimulatorLocalSession();
    expect(mockSetCachedSessionUser).toHaveBeenCalledWith(QA_USER);
  });

  // The original bug in one assertion: this call happened, the two above did not,
  // and a cookie on its own survives neither a bootstrap nor an authed request.
  it("does not settle for having written only the cookie", async () => {
    await createQaSimulatorLocalSession();
    expect(mockSetSessionCookie).toHaveBeenCalled();
    expect(mockSetSessionEnvelope).toHaveBeenCalled();
    expect(mockSetCachedSessionUser).toHaveBeenCalled();
  });

  it("writes nothing when the backend does not return a session", async () => {
    (global as { fetch?: unknown }).fetch = jest.fn(async () => ({
      ok: true,
      status: 200,
      headers: { get: () => null },
      json: async () => ({ ok: true, authenticated: false, requires_email_confirmation: true })
    }));
    const state = await createQaSimulatorLocalSession();
    expect(state.status).toBe("signedOut");
    expect(mockSetSessionEnvelope).not.toHaveBeenCalled();
    expect(mockSetCachedSessionUser).not.toHaveBeenCalled();
  });

  // A 200 carrying a user but no refresh token is not a session either. The
  // envelope writer drops it on the floor; the point here is that the cached
  // user is not left behind pointing at credentials that were never stored.
  it("does not store an envelope for a session with no refresh token", async () => {
    (global as { fetch?: unknown }).fetch = jest.fn(async () =>
      registerOk({ refresh_token: undefined, refresh_token_expires_in: undefined })
    );
    await createQaSimulatorLocalSession();
    expect(mockSetSessionEnvelope).not.toHaveBeenCalled();
  });
});
