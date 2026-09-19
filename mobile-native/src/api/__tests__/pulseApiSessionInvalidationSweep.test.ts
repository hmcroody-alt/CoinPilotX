/**
 * A session can end without anyone pressing "Sign out", and when it does the
 * account's cached data has to leave with it.
 *
 * `session/auth` pairs its credential clear with `clearUserScopedMediaState()`,
 * and that pairing is load-bearing rather than tidy. Most of what this app
 * caches is stored under a BARE key, so it is not isolated by account at rest.
 * `core/storageScope` states the consequence directly: profiles, the activity
 * inbox, the saved library, recent searches and every composer draft "survived a
 * sign-out under a bare key and were read straight back by the next account."
 * The sweep is the whole boundary.
 *
 * `performNativeSessionRefresh` reaches its own `"invalid"` outcome on three
 * paths, none of which pass through `signOut`, and all three used to clear the
 * credentials and stop -- half a sign-out. The sharpest was the envelope/refresh
 * `userId` disagreement, which IS an account switch and is the precise scenario
 * `clearUserScopedMediaState` was written for. The 401/403 path matters for a
 * duller reason: refresh-reuse detection revokes an entire token family on
 * benign desync, so it is reached in ordinary use, not only under attack.
 *
 * These tests pin the pairing. The three positive cases would all pass if the
 * sweep were called unconditionally, so two controls below require it NOT to run
 * when the session survives -- without them this file would be satisfied by a
 * change that wipes the user's drafts on every successful token refresh.
 */
const mockGetSessionCookie = jest.fn(async () => "");
const mockGetSessionEnvelope = jest.fn(async () => null as unknown);
const mockClearCredentials = jest.fn(async () => undefined);
const mockClearUserScopedMediaState = jest.fn(async () => undefined);
const mockShouldRejectTemporaryQaUser = jest.fn((_user?: unknown) => false);

jest.mock("../../session/sessionStore", () => ({
  clearNativeSessionCredentials: () => mockClearCredentials(),
  getSessionCookie: () => mockGetSessionCookie(),
  getSessionEnvelope: () => mockGetSessionEnvelope(),
  setCachedSessionUser: jest.fn(),
  setSessionCookie: jest.fn(),
  setSessionEnvelope: jest.fn()
}));
jest.mock("../../media/mediaSessionCleanup", () => ({
  clearUserScopedMediaState: () => mockClearUserScopedMediaState()
}));
jest.mock("../../session/qaTemporaryAccount", () => ({
  shouldRejectTemporaryQaUser: (user: unknown) => mockShouldRejectTemporaryQaUser(user)
}));
jest.mock("../../core/perfTrace", () => ({ startSpan: jest.fn(() => ({ end: jest.fn() })) }));
jest.mock("react-native", () => ({ Platform: { OS: "ios" } }));
jest.mock("../config", () => ({ PULSE_API_BASE_URL: "https://pulse.test" }));

import { recoverNativeSession } from "../pulseApi";

const SIGNED_IN_AS_ONE = {
  version: 1,
  userId: 1,
  accessToken: "",
  accessTokenExpiresAt: 0,
  refreshToken: "stored-refresh",
  refreshTokenExpiresAt: Date.now() + 3_600_000
};

function respondWith(body: unknown, status = 200) {
  global.fetch = jest.fn(async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;
}

function tokensFor(userId: number) {
  return {
    authenticated: true,
    user: { user_id: userId },
    access_token: "fresh-access",
    access_token_expires_in: 900,
    refresh_token: "fresh-refresh",
    refresh_token_expires_in: 3600
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockShouldRejectTemporaryQaUser.mockReturnValue(false);
  (mockGetSessionCookie as jest.Mock).mockResolvedValue("pulsesoc_session=abc");
  (mockGetSessionEnvelope as jest.Mock).mockResolvedValue(SIGNED_IN_AS_ONE);
});

describe("a session that dies outside the sign-out button", () => {
  /**
   * MUTATION: drop the `clearUserScopedMediaState()` call from
   * `abandonInvalidSession`, or restore the bare `clearNativeSessionCredentials`
   * + `setCachedSessionUser` pair at the 401/403 branch.
   */
  it("sweeps the account's cached data when the server rejects the refresh token", async () => {
    respondWith({ error: "invalid_grant" }, 401);

    await expect(recoverNativeSession()).resolves.toBe("invalid");

    expect(mockClearCredentials).toHaveBeenCalledTimes(1);
    expect(mockClearUserScopedMediaState).toHaveBeenCalledTimes(1);
  });

  /**
   * MUTATION: restore the bare credential clear at the `shouldRejectTemporaryQaUser`
   * branch so it stops routing through `abandonInvalidSession`.
   */
  it("sweeps when a rejected QA account is what came back", async () => {
    mockShouldRejectTemporaryQaUser.mockReturnValue(true);
    respondWith(tokensFor(1));

    await expect(recoverNativeSession()).resolves.toBe("invalid");

    expect(mockClearUserScopedMediaState).toHaveBeenCalledTimes(1);
  });

  /**
   * MUTATION: restore the bare credential clear at the `envelope.userId !== userId`
   * branch.
   *
   * This is the case the whole file exists for. The device believed it was signed
   * in as user 1 and the server just said user 2, which is an account switch
   * discovered mid-request. Leaving user 1's bare-keyed caches in place here
   * hands them to user 2.
   */
  it("sweeps when the refreshed identity disagrees with the stored one", async () => {
    respondWith(tokensFor(2));

    await expect(recoverNativeSession()).resolves.toBe("invalid");

    expect(mockClearCredentials).toHaveBeenCalledTimes(1);
    expect(mockClearUserScopedMediaState).toHaveBeenCalledTimes(1);
  });
});

describe("the controls -- a surviving session must keep its data", () => {
  /**
   * MUTATION: call `clearUserScopedMediaState()` unconditionally in
   * `performNativeSessionRefresh` instead of only on the invalid paths.
   *
   * Without this test the three above are satisfied by a change that wipes every
   * draft, saved item and recent search on each successful token refresh -- which
   * happens routinely, in the background, to a user who is doing nothing wrong.
   */
  it("does not sweep when the refresh succeeds for the same user", async () => {
    respondWith(tokensFor(1));

    await expect(recoverNativeSession()).resolves.toBe("refreshed");

    expect(mockClearUserScopedMediaState).not.toHaveBeenCalled();
    expect(mockClearCredentials).not.toHaveBeenCalled();
  });

  /**
   * MUTATION: widen the invalid branch from `401 || 403` to any non-ok status.
   *
   * A 500 is the server having a bad minute. Treating it as an invalid session
   * would sign the user out and destroy their local data over a transient fault,
   * which is why `temporary()` exists as a separate outcome.
   */
  it("does not sweep when the refresh fails temporarily", async () => {
    respondWith({ error: "upstream" }, 500);

    await expect(recoverNativeSession()).resolves.not.toBe("invalid");

    expect(mockClearUserScopedMediaState).not.toHaveBeenCalled();
    expect(mockClearCredentials).not.toHaveBeenCalled();
  });
});

describe("anti-vacuity", () => {
  /**
   * Everything above asserts on a mock reached through a lazy `require` inside
   * `abandonInvalidSession`, chosen to break the cycle
   * `pulseApi -> mediaSessionCleanup -> messengerMediaAccess -> pulseApi`.
   *
   * If that require ever resolved to something other than this mock -- a renamed
   * module, a moved file, a jest config change -- every positive assertion above
   * would fail rather than silently pass, so this test is belt and braces. What
   * it really pins is that the mocked export is callable and that the suite is
   * exercising a real code path rather than a typo'd one that throws.
   */
  it("reaches the real sweep binding, not a silently absent one", async () => {
    respondWith({ error: "invalid_grant" }, 401);
    await recoverNativeSession();

    const { clearUserScopedMediaState } = require("../../media/mediaSessionCleanup") as {
      clearUserScopedMediaState: () => Promise<void>;
    };
    expect(typeof clearUserScopedMediaState).toBe("function");
    expect(mockClearUserScopedMediaState.mock.calls.length).toBeGreaterThan(0);
  });
});
