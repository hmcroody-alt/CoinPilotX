jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

// getSession is the server session probe; the rest of api/auth is unused here
// but must exist so the auth module imports cleanly under the mock.
jest.mock("../../api/auth", () => ({
  getSession: jest.fn(),
  login: jest.fn(),
  logout: jest.fn(),
  logoutAll: jest.fn(),
  signup: jest.fn()
}));

// Provide a REAL PulseApiError class from the mock so `instanceof` checks in
// restoreSession resolve against the same constructor the tests throw with.
jest.mock("../../api/pulseApi", () => {
  class PulseApiError extends Error {
    status: number;
    code?: string;
    constructor(message: string, status: number, code?: string) {
      super(message);
      this.name = "PulseApiError";
      this.status = status;
      this.code = code;
    }
  }
  return { PulseApiError, recoverNativeSession: jest.fn() };
});

const store = {
  cookie: null as string | null,
  envelope: null as { refreshToken?: string; userId?: number } | null,
  cachedUser: null as unknown
};

jest.mock("../sessionStore", () => ({
  getSessionCookie: jest.fn(async () => store.cookie),
  getSessionEnvelope: jest.fn(async () => store.envelope),
  getCachedSessionUser: jest.fn(async () => store.cachedUser),
  setCachedSessionUser: jest.fn(async (user: unknown) => {
    store.cachedUser = user;
  }),
  clearNativeSessionCredentials: jest.fn(async () => {
    store.cookie = null;
    store.envelope = null;
  })
}));

jest.mock("../qaTemporaryAccount", () => ({
  shouldRejectTemporaryQaUser: () => false
}));

import { restoreSession } from "../auth";
import { getSession } from "../../api/auth";
import { PulseApiError, recoverNativeSession } from "../../api/pulseApi";

const getSessionMock = getSession as jest.Mock;
const recoverMock = recoverNativeSession as jest.Mock;

const user = { user_id: 5, username: "alex" };

beforeEach(() => {
  getSessionMock.mockReset();
  recoverMock.mockReset();
  store.cookie = null;
  store.envelope = null;
  store.cachedUser = null;
});

describe("restoreSession deterministic bootstrap phases", () => {
  it("AUTHENTICATED when the server returns a valid live session", async () => {
    getSessionMock.mockResolvedValue({ authenticated: true, user });
    const state = await restoreSession();
    expect(state.phase).toBe("AUTHENTICATED");
    expect(state.status).toBe("signedIn");
    expect(state.user).toEqual(expect.objectContaining(user));
  });

  it("UNAUTHENTICATED on a clean first launch (no session, nothing to recover)", async () => {
    getSessionMock.mockResolvedValue({ authenticated: false, user: null });
    recoverMock.mockResolvedValue("unavailable");
    const state = await restoreSession();
    expect(state.phase).toBe("UNAUTHENTICATED");
    expect(state.status).toBe("signedOut");
  });

  it("SESSION_EXPIRED when stored credentials exist but the server rejects them", async () => {
    store.cookie = "session=abc";
    getSessionMock.mockResolvedValue({ authenticated: false, user: null });
    recoverMock.mockResolvedValue("invalid");
    const state = await restoreSession();
    expect(state.phase).toBe("SESSION_EXPIRED");
    expect(state.status).toBe("signedOut");
  });

  it("AUTHENTICATED after a successful silent refresh", async () => {
    store.envelope = { refreshToken: "r", userId: 5 };
    getSessionMock.mockResolvedValueOnce({ authenticated: false, user: null }).mockResolvedValueOnce({ authenticated: true, user });
    recoverMock.mockResolvedValue("refreshed");
    const state = await restoreSession();
    expect(state.phase).toBe("AUTHENTICATED");
    expect(state.user).toEqual(expect.objectContaining(user));
  });

  it("RECOVERABLE_ERROR when the network is unreachable and no valid cache exists", async () => {
    getSessionMock.mockRejectedValue(new PulseApiError("offline", 503, "request_unreachable"));
    const state = await restoreSession();
    expect(state.phase).toBe("RECOVERABLE_ERROR");
    expect(state.status).toBe("signedOut");
  });

  it("AUTHENTICATED from cache when the network fails but valid credentials are cached", async () => {
    store.cookie = "session=abc";
    store.cachedUser = user;
    getSessionMock.mockRejectedValue(new PulseApiError("offline", 503, "request_unreachable"));
    const state = await restoreSession();
    expect(state.phase).toBe("AUTHENTICATED");
    expect(state.user).toEqual(expect.objectContaining(user));
  });

  it("FATAL_ERROR on an unexpected non-transient failure with no cache", async () => {
    getSessionMock.mockRejectedValue(new Error("boom"));
    const state = await restoreSession();
    expect(state.phase).toBe("FATAL_ERROR");
    expect(state.status).toBe("signedOut");
  });
});
