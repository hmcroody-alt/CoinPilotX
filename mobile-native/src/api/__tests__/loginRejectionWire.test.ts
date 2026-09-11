/**
 * The login screen now branches on `PulseApiError.code`. That is only sound if
 * the backend's discriminator actually survives the trip, so this pins the wire
 * contract rather than the screen's reading of it.
 *
 * Why it needs its own file: every other test of this mapping builds a
 * `PulseApiError` by hand. A hand-built error asserts the shape we *hope*
 * arrives -- it cannot catch the failure where the field never becomes `.code`
 * at all, because the constructor is being handed the answer. That failure has
 * real precedent here: `errorCodeOf` reads `error_code` and `error`, and a
 * backend answering only `code` collapses every state to generic while every
 * hand-built test stays green.
 *
 * So these bodies are copied from what `bot.py` emits, not invented:
 *   api_error(msg, 401)                               -> no discriminator at all
 *   api_error(msg, 403, error="login_challenge_required", challenge={...})
 *   api_error(msg, 429, error="login_rate_limited", challenge={})
 *   api_error(msg, 403, error="email_not_confirmed")
 *   api_error(msg, 403, error="account_restricted")
 *
 * `api_error` always includes ok/success/message/trace_id, so they are here too.
 */
jest.mock("../../session/sessionStore", () => ({
  clearNativeSessionCredentials: jest.fn(),
  getSessionCookie: jest.fn(async () => ""),
  getSessionEnvelope: jest.fn(async () => null),
  setCachedSessionUser: jest.fn(),
  setSessionCookie: jest.fn(),
  setSessionEnvelope: jest.fn()
}));
jest.mock("../../session/qaTemporaryAccount", () => ({ shouldRejectTemporaryQaUser: jest.fn(() => false) }));
jest.mock("../../core/perfTrace", () => ({ startSpan: jest.fn(() => ({ end: jest.fn() })) }));
jest.mock("react-native", () => ({ Platform: { OS: "ios" } }));
jest.mock("../config", () => ({ PULSE_API_BASE_URL: "https://pulse.test" }));

import { PulseApiError, pulseApi } from "../pulseApi";

const LOGIN_PATH = "/api/mobile/auth/login";
const SUBMIT = { method: "POST", body: JSON.stringify({ identifier: "a@b.c", password: "x" }) };

function answerWith(status: number, body: Record<string, unknown>) {
  global.fetch = jest.fn(
    async () =>
      new Response(JSON.stringify({ ok: false, success: false, trace_id: "abc123", ...body }), {
        status,
        headers: { "Content-Type": "application/json" }
      })
  ) as unknown as typeof fetch;
}

async function rejectionFrom(status: number, body: Record<string, unknown>) {
  answerWith(status, body);
  try {
    await pulseApi(LOGIN_PATH, SUBMIT);
  } catch (error) {
    return error as PulseApiError;
  }
  throw new Error("pulseApi resolved a rejection");
}

afterEach(() => {
  jest.restoreAllMocks();
});

describe("login rejections arriving from the backend", () => {
  it("carries `error` through as PulseApiError.code for every security state", async () => {
    const states: Array<[number, string, string]> = [
      [403, "login_challenge_required", "Complete the security challenge to continue."],
      [429, "login_rate_limited", "Too many failed login attempts. Try again after the cooldown or reset your password."],
      [403, "email_not_confirmed", "Please confirm your email before logging in."],
      [403, "account_restricted", "This account is not active. Please contact support@pulsesoc.com."]
    ];

    for (const [status, code, message] of states) {
      const error = await rejectionFrom(status, { message, error: code });
      expect(error).toBeInstanceOf(PulseApiError);
      expect(error.code).toBe(code);
      expect(error.status).toBe(status);
      // The server's own sentence must remain reachable. A state this build
      // does not recognise is shown verbatim rather than guessed at, so losing
      // `message` would turn a future rejection back into a generic one.
      expect(error.message).toBe(message);
    }
  });

  it("leaves code undefined when the backend sends no discriminator", async () => {
    // The bare 401 is deliberate on the server: unknown account and wrong
    // password answer identically so the endpoint cannot be used to enumerate
    // accounts. The client must not invent a distinction the server withheld.
    const error = await rejectionFrom(401, { message: "Email or password is incorrect." });
    expect(error.code).toBeUndefined();
    expect(error.status).toBe(401);
  });

  it("keeps the challenge payload reachable on the error", async () => {
    // `details` is what a challenge UI would need; dropping it would make the
    // challenge state unactionable even once it is named correctly.
    const error = await rejectionFrom(403, {
      message: "Complete the security challenge to continue.",
      error: "login_challenge_required",
      challenge: { kind: "math", prompt: "3 + 4", token: "ch_1" }
    });
    expect(error.details?.challenge).toEqual({ kind: "math", prompt: "3 + 4", token: "ch_1" });
  });

  it("does not attempt a session refresh for a login rejection", async () => {
    // A 401 on most routes means "our access token aged out" and triggers
    // refresh-and-replay. On the login route there is no session to refresh,
    // and a replay would spend a second failed attempt against the velocity
    // gate -- three of which is what trips the challenge in the first place.
    await rejectionFrom(401, { message: "Email or password is incorrect." });
    const calls = (global.fetch as jest.Mock).mock.calls.map((call) => String(call[0]));
    expect(calls.filter((url) => url.includes("/auth/refresh"))).toHaveLength(0);
    expect(calls).toHaveLength(1);
  });
});
