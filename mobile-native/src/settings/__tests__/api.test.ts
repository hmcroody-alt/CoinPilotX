/**
 * The settings transport layer.
 *
 * Two rules in `api.ts` are load-bearing enough to be worth pinning, and both
 * are the kind that a well-meaning refactor quietly reverses.
 *
 * **The read/list asymmetry.** `fetchRemotePreferences` must never throw, and
 * the list reads must always throw. That looks inconsistent until you ask what
 * an empty result *claims*. A stale preference snapshot is still a truthful
 * answer to "what are my settings". An empty blocked list is a positive
 * assertion — "you have blocked nobody" — and an empty session list says "no
 * other device is signed in to your account". Swallowing a network error on a
 * security surface turns "we could not check" into "you are safe", so the
 * asymmetry is asserted here in both directions rather than left to a comment.
 *
 * **The permanence classification.** `toSyncError` decides whether the store
 * rolls a value back or keeps retrying. Getting 429 or 408 wrong means a user
 * who hit a rate limit sees their change reverted rather than retried; getting
 * 5xx wrong means a change is discarded because a server restarted. The
 * boundaries are checked at each edge (399/400/408/429/499/500) rather than at
 * a representative value in the middle, because every bug in a range check
 * lives at its edges.
 *
 * `pulseApi` is the only mock. The normalizers run for real.
 */

import { PulseApiError } from "../../api/pulseApi";

const mockPulseApi = jest.fn();

jest.mock("../../api/pulseApi", () => {
  class MockPulseApiError extends Error {
    status: number;
    code?: string;
    constructor(message: string, status: number, code?: string) {
      super(message);
      this.name = "PulseApiError";
      this.status = status;
      this.code = code;
    }
  }
  return {
    __esModule: true,
    PulseApiError: MockPulseApiError,
    pulseApi: (...args: unknown[]) => mockPulseApi(...args)
  };
});

import {
  fetchActiveSessions,
  fetchBlockedUsers,
  fetchMutedUsers,
  fetchRemotePreferences,
  PreferenceSyncError,
  pushPreferencePatch,
  revokeSession,
  setBlocked,
  setMuted,
  __testing
} from "../api";
import { DEFAULT_PREFERENCES } from "../schema";

const { toSyncError, SETTINGS_PATH, settingsMessageFor } = __testing;

/** Build the error the real `pulseApi` would throw, via the mocked class. */
function apiError(status: number, message = "boom"): Error {
  return new PulseApiError(message, status);
}

/** The parsed JSON body of the nth `pulseApi` call. */
function bodyOf(callIndex = 0): Record<string, unknown> {
  const [, options] = mockPulseApi.mock.calls[callIndex] as [string, RequestInit];
  return JSON.parse(String(options.body));
}

beforeEach(() => {
  mockPulseApi.mockReset();
});

/* -------------------------------------------------------------------------- */

describe("error permanence", () => {
  // A permanent error rolls the user's change back. A transient one keeps it and
  // retries. Misclassifying either direction loses data or spins forever.
  it.each([
    [400, true, "a malformed payload will be malformed on the retry too"],
    [401, true, "the session is gone; retrying the same request cannot fix it"],
    [403, true, "a forbidden change stays forbidden"],
    [404, true, "the endpoint is not there"],
    [422, true, "a rejected value is the whole point of rolling back"],
    [499, true, "still client-side"],
    [408, false, "a timeout is the canonical retryable 4xx"],
    [429, false, "rate limited — the same request succeeds later"],
    [500, false, "the server broke, not the payload"],
    [502, false, "a bad gateway is a deploy, not a defect in the change"],
    [503, false, "maintenance ends"]
  ])("treats %i as permanent=%s (%s)", (status, permanent) => {
    expect(toSyncError(apiError(status as number)).permanent).toBe(permanent);
  });

  it("treats a status below 400 as transient", () => {
    // Not reachable through a normal success path, but a 399 arriving here means
    // something upstream is confused — retrying is the non-destructive guess.
    expect(toSyncError(apiError(399)).permanent).toBe(false);
  });

  it("treats a non-API throw as an offline transient failure", () => {
    // A TypeError from fetch, a DNS failure, an aborted request. None of these
    // say anything about the payload, so the change must survive.
    const error = toSyncError(new TypeError("Network request failed"));
    expect(error.permanent).toBe(false);
    expect(error.status).toBe(0);
    expect(error.message).toMatch(/offline/i);
  });

  it("keeps the server's message so the user is told why, not just that", () => {
    expect(toSyncError(apiError(422, "Quiet hours must be under 24 hours.")).message).toBe(
      "Quiet hours must be under 24 hours."
    );
  });

  it("substitutes a message when the server sent none", () => {
    // An empty string would render as a blank error row.
    expect(toSyncError(apiError(500, "")).message).toBeTruthy();
  });

  it("preserves the status, so a 401 can be told from a 422 downstream", () => {
    expect(toSyncError(apiError(401)).status).toBe(401);
  });

  it("is a PreferenceSyncError, which is what the store branches on", () => {
    // The store uses `instanceof`. A structurally identical object would be
    // silently downgraded to a transient failure.
    expect(toSyncError(apiError(422))).toBeInstanceOf(PreferenceSyncError);
  });
});

/* -------------------------------------------------------------------------- */

describe("fetchRemotePreferences", () => {
  it("never throws — a failure returns null so Settings still renders", async () => {
    mockPulseApi.mockRejectedValue(apiError(500));
    await expect(fetchRemotePreferences()).resolves.toBeNull();
  });

  it("returns null even on 401, rather than surfacing an error mid-screen", async () => {
    // Session expiry is handled globally by the API layer; Settings' job here is
    // to keep showing the values already on the device.
    mockPulseApi.mockRejectedValue(apiError(401));
    await expect(fetchRemotePreferences()).resolves.toBeNull();
  });

  it("reads the documented envelope shape", async () => {
    mockPulseApi.mockResolvedValue({
      preferences: { appearance: { theme: "dark" } },
      revision: 7,
      updated_at: "2026-01-01T00:00:00Z"
    });
    const result = await fetchRemotePreferences();
    expect(result?.preferences.appearance.theme).toBe("dark");
    expect(result?.revision).toBe(7);
    expect(result?.updatedAt).toBe("2026-01-01T00:00:00Z");
  });

  it("also reads a flattened body, so the client survives that change", async () => {
    // `toEnvelope` accepts a bare preferences object deliberately; this pins that
    // the fallback actually parses rather than yielding defaults.
    mockPulseApi.mockResolvedValue({ appearance: { theme: "light_futuristic" } });
    const result = await fetchRemotePreferences();
    expect(result?.preferences.appearance.theme).toBe("light_futuristic");
  });

  it("accepts `version` as an alias for `revision`", async () => {
    mockPulseApi.mockResolvedValue({ preferences: {}, version: "12" });
    expect((await fetchRemotePreferences())?.revision).toBe(12);
  });

  it("falls back to revision 0 when the server sends nonsense", async () => {
    // Revision 0 means "no server state yet", which makes the next PATCH
    // unconditional. That is the safe reading of an unparseable revision.
    mockPulseApi.mockResolvedValue({ preferences: {}, revision: "not-a-number" });
    expect((await fetchRemotePreferences())?.revision).toBe(0);
  });

  it("rejects a negative revision rather than propagating it", async () => {
    mockPulseApi.mockResolvedValue({ preferences: {}, revision: -4 });
    expect((await fetchRemotePreferences())?.revision).toBe(0);
  });

  it("returns a complete preference set even from an empty body", async () => {
    // The screens index into every group unconditionally; a partial object here
    // would crash a render rather than show a wrong value.
    mockPulseApi.mockResolvedValue({});
    const result = await fetchRemotePreferences();
    expect(Object.keys(result?.preferences ?? {}).sort()).toEqual(Object.keys(DEFAULT_PREFERENCES).sort());
  });

  it("survives a null body", async () => {
    mockPulseApi.mockResolvedValue(null);
    expect((await fetchRemotePreferences())?.preferences).toEqual(DEFAULT_PREFERENCES);
  });

  it("normalizes a hostile value instead of trusting the server", async () => {
    // The server is not the only thing that can answer this URL — a captive
    // portal or a stale proxy can. Normalizing on read makes that a no-op.
    mockPulseApi.mockResolvedValue({ preferences: { appearance: { theme: "chartreuse" } } });
    const result = await fetchRemotePreferences();
    expect(result?.preferences.appearance.theme).toBe(DEFAULT_PREFERENCES.appearance.theme);
  });

  it("uses the settings path with GET", async () => {
    mockPulseApi.mockResolvedValue({});
    await fetchRemotePreferences();
    expect(mockPulseApi).toHaveBeenCalledWith(SETTINGS_PATH, { method: "GET" });
  });
});

/* -------------------------------------------------------------------------- */

describe("pushPreferencePatch", () => {
  it("sends only the groups it was given", async () => {
    // Sending the whole document would make two devices editing different
    // sections overwrite each other.
    mockPulseApi.mockResolvedValue({ preferences: {}, revision: 3 });
    await pushPreferencePatch({ appearance: { ...DEFAULT_PREFERENCES.appearance, theme: "dark" } }, 2);
    expect(Object.keys(bodyOf().preferences as object)).toEqual(["appearance"]);
  });

  it("carries the revision, which is what makes a stale write detectable", async () => {
    mockPulseApi.mockResolvedValue({ preferences: {}, revision: 3 });
    await pushPreferencePatch({ privacy: DEFAULT_PREFERENCES.privacy }, 11);
    expect(bodyOf().revision).toBe(11);
  });

  it("PATCHes rather than PUTs, because the body is partial", async () => {
    mockPulseApi.mockResolvedValue({ preferences: {}, revision: 1 });
    await pushPreferencePatch({}, 0);
    const [path, options] = mockPulseApi.mock.calls[0] as [string, RequestInit];
    expect(path).toBe(SETTINGS_PATH);
    expect(options.method).toBe("PATCH");
  });

  it("returns the server's envelope, which becomes the confirmed state", async () => {
    mockPulseApi.mockResolvedValue({ preferences: { appearance: { theme: "dark" } }, revision: 9 });
    const envelope = await pushPreferencePatch({}, 8);
    expect(envelope.revision).toBe(9);
    expect(envelope.preferences.appearance.theme).toBe("dark");
  });

  it("throws, because the store cannot roll back what it never hears about", async () => {
    mockPulseApi.mockRejectedValue(apiError(422, "Nope."));
    await expect(pushPreferencePatch({}, 1)).rejects.toBeInstanceOf(PreferenceSyncError);
  });

  it("throws a classified error rather than the raw transport error", async () => {
    mockPulseApi.mockRejectedValue(apiError(503));
    await expect(pushPreferencePatch({}, 1)).rejects.toMatchObject({ permanent: false, status: 503 });
  });
});

/* -------------------------------------------------------------------------- */

describe("relationship lists", () => {
  it.each([
    ["blocked", fetchBlockedUsers],
    ["muted", fetchMutedUsers]
  ])("%s throws rather than claiming the list is empty", async (_name, fetcher) => {
    // The whole point of the asymmetry with the preference read.
    mockPulseApi.mockRejectedValue(apiError(500));
    await expect(fetcher()).rejects.toBeInstanceOf(PreferenceSyncError);
  });

  it("reads the `users` key", async () => {
    mockPulseApi.mockResolvedValue({ users: [{ id: 4, username: "ada" }] });
    expect(await fetchBlockedUsers()).toHaveLength(1);
  });

  it("reads the `items` key, and a bare array", async () => {
    mockPulseApi.mockResolvedValueOnce({ items: [{ id: 4 }] });
    expect(await fetchBlockedUsers()).toHaveLength(1);
    mockPulseApi.mockResolvedValueOnce([{ id: 5 }]);
    expect(await fetchMutedUsers()).toHaveLength(1);
  });

  it("drops an entry with no usable id instead of rendering a dead row", async () => {
    // A row with id 0 cannot be unblocked — the unblock call would target nobody.
    mockPulseApi.mockResolvedValue({ users: [{ id: 0 }, { username: "ghost" }, { id: -1 }, { id: 7 }] });
    const users = await fetchBlockedUsers();
    expect(users.map((user) => user.id)).toEqual([7]);
  });

  it("gives a nameless user a stable label rather than an empty row", async () => {
    mockPulseApi.mockResolvedValue({ users: [{ id: 7 }] });
    expect((await fetchBlockedUsers())[0].displayName).toBe("User 7");
  });

  it("prefers the display name, then the username", async () => {
    mockPulseApi.mockResolvedValue({
      users: [
        { id: 1, username: "ada", display_name: "Ada L." },
        { id: 2, username: "grace" }
      ]
    });
    const users = await fetchBlockedUsers();
    expect(users[0].displayName).toBe("Ada L.");
    expect(users[1].displayName).toBe("grace");
  });

  it("accepts both snake_case and camelCase, since two backends answer these", async () => {
    mockPulseApi.mockResolvedValue({ users: [{ user_id: 3, username: "x", avatarUrl: "/a.png", created_at: "2026-01-01" }] });
    const [user] = await fetchBlockedUsers();
    expect(user).toMatchObject({ id: 3, avatarUrl: "/a.png", since: "2026-01-01" });
  });

  it("returns an empty list for a body it cannot read at all", async () => {
    // Distinct from the error case: the server answered, it just had nothing.
    mockPulseApi.mockResolvedValue({ nope: true });
    expect(await fetchBlockedUsers()).toEqual([]);
  });

  it.each([
    [true, "POST"],
    [false, "DELETE"]
  ])("setBlocked(%s) uses %s", async (blocked, method) => {
    mockPulseApi.mockResolvedValue({});
    await setBlocked(42, blocked as boolean);
    const [path, options] = mockPulseApi.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/api/pulse/mobile/settings/blocked");
    expect(options.method).toBe(method);
    expect(bodyOf().user_id).toBe(42);
  });

  it.each([
    [true, "POST"],
    [false, "DELETE"]
  ])("setMuted(%s) uses %s", async (muted, method) => {
    mockPulseApi.mockResolvedValue({});
    await setMuted(9, muted as boolean);
    const [path, options] = mockPulseApi.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/api/pulse/mobile/settings/muted");
    expect(options.method).toBe(method);
  });

  it("surfaces a failed block, so the switch does not stay flipped on a lie", async () => {
    mockPulseApi.mockRejectedValue(apiError(500));
    await expect(setBlocked(1, true)).rejects.toBeInstanceOf(PreferenceSyncError);
  });
});

/* -------------------------------------------------------------------------- */

describe("sessions", () => {
  it("throws rather than reporting that no other device is signed in", async () => {
    // The single most consequential empty list in the app.
    mockPulseApi.mockRejectedValue(apiError(500));
    await expect(fetchActiveSessions()).rejects.toBeInstanceOf(PreferenceSyncError);
  });

  it("throws on a network failure too, not only on an HTTP error", async () => {
    mockPulseApi.mockRejectedValue(new TypeError("Network request failed"));
    await expect(fetchActiveSessions()).rejects.toBeInstanceOf(PreferenceSyncError);
  });

  it("reads the `sessions` key and a bare array", async () => {
    mockPulseApi.mockResolvedValueOnce({ sessions: [{ id: "a" }] });
    expect(await fetchActiveSessions()).toHaveLength(1);
    mockPulseApi.mockResolvedValueOnce([{ id: "b" }]);
    expect(await fetchActiveSessions()).toHaveLength(1);
  });

  it("drops a session with no id, which could not be revoked anyway", async () => {
    mockPulseApi.mockResolvedValue({ sessions: [{ device_name: "iPhone" }, { id: "  " }, { id: "keep" }] });
    const sessions = await fetchActiveSessions();
    expect(sessions.map((session) => session.id)).toEqual(["keep"]);
  });

  it("labels an unnamed device rather than showing a blank row", async () => {
    mockPulseApi.mockResolvedValue({ sessions: [{ id: "a" }] });
    expect((await fetchActiveSessions())[0].deviceName).toBe("Unknown device");
  });

  it("defaults `current` to false, so no session is wrongly protected", async () => {
    // The screen hides "revoke" on the current session. Defaulting to true would
    // make an attacker's session unrevokable.
    mockPulseApi.mockResolvedValue({ sessions: [{ id: "a" }] });
    expect((await fetchActiveSessions())[0].current).toBe(false);
  });

  it("reads `is_current` as well as `current`", async () => {
    mockPulseApi.mockResolvedValue({ sessions: [{ id: "a", is_current: true }] });
    expect((await fetchActiveSessions())[0].current).toBe(true);
  });

  it("keeps IP and last-active when present and nulls them when absent", async () => {
    mockPulseApi.mockResolvedValue({
      sessions: [
        { id: "a", ip: "10.0.0.1", lastActiveAt: "2026-02-02T00:00:00Z" },
        { id: "b" }
      ]
    });
    const [first, second] = await fetchActiveSessions();
    expect(first).toMatchObject({ ipAddress: "10.0.0.1", lastActiveAt: "2026-02-02T00:00:00Z" });
    expect(second).toMatchObject({ ipAddress: null, lastActiveAt: null, location: null });
  });

  it("posts the session id when revoking", async () => {
    mockPulseApi.mockResolvedValue({});
    await revokeSession("sess-1");
    const [path, options] = mockPulseApi.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/api/pulse/mobile/settings/sessions/revoke");
    expect(options.method).toBe("POST");
    expect(bodyOf().session_id).toBe("sess-1");
  });

  it("surfaces a failed revoke, because silence would read as success", async () => {
    mockPulseApi.mockRejectedValue(apiError(403));
    await expect(revokeSession("sess-1")).rejects.toBeInstanceOf(PreferenceSyncError);
  });
});

/* -------------------------------------------------------------------------- */

/**
 * What the user is actually told.
 *
 * The banner a tester photographed thirteen times — "The requested PulseSoc
 * service was not found." — is the generic 404 body from `bot.py`, written for
 * whoever is holding the failing request. That reader is a developer. Passing it
 * through unedited put an accurate, unactionable sentence in front of somebody
 * who had done nothing but tap a switch, and it appeared identically on every
 * settings screen, which is why the whole surface read as "static UI".
 *
 * Two properties are pinned here. Each status gets its own text, so a support
 * report can be told apart from a bug report by reading the screenshot. And the
 * 404 text says plainly that the change was not saved and that this is not
 * something the user can fix — softening it into "try again later" would hide a
 * missing deployment behind an invitation to retry forever.
 */
describe("user-facing messages", () => {
  const STATUSES = [401, 403, 404, 409, 422, 429, 500, 503];

  it("no longer shows the backend's generic 404 prose", () => {
    expect(settingsMessageFor(404, "The requested PulseSoc service was not found.")).not.toMatch(
      /requested PulseSoc service/i
    );
  });

  it("gives every status its own wording", () => {
    const messages = STATUSES.map((status) => settingsMessageFor(status, ""));
    expect(new Set(messages).size).toBe(new Set(STATUSES.map((s) => (s >= 500 ? 500 : s))).size);
    messages.forEach((message) => expect(message.length).toBeGreaterThan(10));
  });

  it("tells a signed-out user the one thing that will help", () => {
    expect(settingsMessageFor(401, "")).toMatch(/sign in/i);
  });

  it("says a forbidden change is about permission, not about the network", () => {
    expect(settingsMessageFor(403, "")).toMatch(/allowed/i);
  });

  it("states that a 404 lost the change and is not the user's doing", () => {
    const message = settingsMessageFor(404, "");
    expect(message).toMatch(/not saved/i);
    expect(message).not.toMatch(/try again|retry/i);
  });

  it("names the other device on a conflict, and says how to resolve it", () => {
    expect(settingsMessageFor(409, "")).toMatch(/another device/i);
    expect(settingsMessageFor(409, "")).toMatch(/refresh/i);
  });

  it("prefers the server's own words for a validation failure", () => {
    // 422 is the one status where the backend knows something the client does
    // not — which field, and why.
    expect(settingsMessageFor(422, "Quiet hours must be under 24 hours.")).toBe(
      "Quiet hours must be under 24 hours."
    );
    expect(settingsMessageFor(422, "")).toBeTruthy();
  });

  it("promises a retry only where one will actually happen", () => {
    // 429 and 5xx are the transient classes; the store really does retry those.
    expect(settingsMessageFor(429, "")).toMatch(/moment|retry|finish/i);
    expect(settingsMessageFor(500, "")).toMatch(/retry/i);
    expect(settingsMessageFor(503, "")).toMatch(/retry/i);
  });

  it("never leaves a blank message, whatever the server sent", () => {
    [0, 100, 302, 400, 418, 451, 507].forEach((status) => {
      expect(settingsMessageFor(status, "").trim().length).toBeGreaterThan(0);
    });
  });

  it("carries the same text through toSyncError", () => {
    const spy = jest.spyOn(console, "error").mockImplementation(() => undefined);
    STATUSES.forEach((status) => {
      expect(toSyncError(apiError(status, "")).message).toBe(settingsMessageFor(status, ""));
    });
    spy.mockRestore();
  });

  it("logs a 404 as a deployment defect, because it is invisible in the logs otherwise", () => {
    // A missing endpoint and an ordinary rejected write are indistinguishable in
    // aggregate error counts. This line is what makes the difference greppable.
    const spy = jest.spyOn(console, "error").mockImplementation(() => undefined);
    toSyncError(apiError(404, "The requested PulseSoc service was not found."));
    expect(spy).toHaveBeenCalledTimes(1);
    expect(String(spy.mock.calls[0][0])).toMatch(/deployment defect/i);
    spy.mockRestore();
  });

  it("does not log for an ordinary rejected write", () => {
    const spy = jest.spyOn(console, "error").mockImplementation(() => undefined);
    [401, 403, 409, 422, 429, 500].forEach((status) => toSyncError(apiError(status, "")));
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});
