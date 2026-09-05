/**
 * The security client carries the server's refusals without translating them.
 *
 * The second lock's product IS its refusals: a 423 must arrive as LOCKED (never
 * as an entitlement problem), a cooldown must carry the server's own countdown,
 * and the one success path — /unlock answering with a grant — must stow that
 * grant in memory so the very next Office read presents it. Each case here pins
 * one of those translations.
 */

const mockPulseApi = jest.fn();

// The real `PulseApiError` is kept deliberately: `writeFailure` narrows with an
// `instanceof` test, and a stubbed class would decide these cases for reasons
// unrelated to the code under test.
jest.mock("../pulseApi", () => ({
  ...jest.requireActual("../pulseApi"),
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => undefined),
  deleteItemAsync: jest.fn(async () => undefined),
  AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY: "afterFirstUnlockThisDeviceOnly"
}));

import { PulseApiError } from "../pulseApi";
import {
  changeOfficePasscode,
  getOfficeSecurityStatus,
  getPrivateFacts,
  lockOffice,
  resetOfficePasscode,
  setupOfficePasscode,
  unlockOffice
} from "../privateOffice";
import {
  OFFICE_DEVICE_HEADER,
  OFFICE_GRANT_HEADER,
  __resetOfficeLockForTests,
  currentOfficeGrantToken,
  isOfficeUnlocked
} from "../../privateOffice/officeLock";

const USER = 9401;
const TOKEN = "grant-token-f6e5d4c3b2a1";

function apiError(
  status: number,
  details?: Record<string, unknown>,
  message = "refused"
): PulseApiError {
  return new PulseApiError(message, status, undefined, details);
}

function lastRequest(): { path: string; options: Record<string, any> } {
  const call = mockPulseApi.mock.calls[mockPulseApi.mock.calls.length - 1];
  return { path: call[0] as string, options: (call[1] ?? {}) as Record<string, any> };
}

beforeEach(() => {
  jest.clearAllMocks();
  __resetOfficeLockForTests();
});

describe("getPrivateFacts and the second lock", () => {
  it("maps a 423 to LOCKED before any entitlement word gets a say", async () => {
    // A body that ALSO claims NOT_ENTITLED must still land on LOCKED: the 423
    // carries the one instruction that matters — unlock, or set up.
    mockPulseApi.mockRejectedValueOnce(
      apiError(423, { state: "NOT_ENTITLED", minimum_tier: "gold", setup_required: false })
    );
    expect(await getPrivateFacts("finance")).toEqual({ state: "LOCKED", setupRequired: false });
  });

  it("recognises the lock by state word alone, and carries setup_required", async () => {
    mockPulseApi.mockRejectedValueOnce(
      apiError(403, { state: "PRIVATE_OFFICE_LOCKED", setup_required: true })
    );
    expect(await getPrivateFacts()).toEqual({ state: "LOCKED", setupRequired: true });
  });

  it("still names the other refusals when no lock is involved", async () => {
    mockPulseApi.mockRejectedValueOnce(
      apiError(403, { state: "NOT_ENTITLED", minimum_tier: "gold" })
    );
    expect(await getPrivateFacts()).toEqual({ state: "NOT_ENTITLED", minimumTier: "gold" });

    mockPulseApi.mockRejectedValueOnce(apiError(503, {}));
    expect(await getPrivateFacts()).toEqual({ state: "UNAVAILABLE" });

    mockPulseApi.mockRejectedValueOnce(new TypeError("network down"));
    expect(await getPrivateFacts()).toEqual({ state: "ERROR", message: "" });
  });

  it("sends the device header on every read, and the grant only once unlocked", async () => {
    mockPulseApi.mockResolvedValue({ facts: [], domain: "finance" });
    await getPrivateFacts("finance");
    const locked = lastRequest().options.headers as Record<string, string>;
    expect(locked[OFFICE_DEVICE_HEADER]).toBeTruthy();
    expect(locked[OFFICE_GRANT_HEADER]).toBeUndefined();

    mockPulseApi.mockResolvedValueOnce({
      grant_token: TOKEN,
      expires_at: new Date(Date.now() + 900_000).toISOString()
    });
    await unlockOffice("824913", USER);

    await getPrivateFacts("finance");
    const unlocked = lastRequest().options.headers as Record<string, string>;
    expect(unlocked[OFFICE_GRANT_HEADER]).toBe(TOKEN);
  });
});

describe("unlockOffice", () => {
  it("stows the server's grant so the office reads as unlocked for this user", async () => {
    mockPulseApi.mockResolvedValueOnce({
      grant_token: TOKEN,
      expires_at: new Date(Date.now() + 900_000).toISOString()
    });
    expect(await unlockOffice("824913", USER)).toEqual({ state: "UNLOCKED" });
    expect(isOfficeUnlocked(USER)).toBe(true);
    expect(isOfficeUnlocked(USER + 1)).toBe(false);
    expect(currentOfficeGrantToken()).toBe(TOKEN);
    expect(lastRequest().path).toBe("/api/private-office/security/unlock");
    expect(JSON.parse(lastRequest().options.body)).toEqual({ passcode: "824913" });
  });

  it("treats a success without a grant token as an error, and stays locked", async () => {
    mockPulseApi.mockResolvedValueOnce({ ok: true });
    expect(await unlockOffice("824913", USER)).toEqual({ state: "ERROR", message: "" });
    expect(isOfficeUnlocked()).toBe(false);
  });

  it("relays the server's cooldown clock rather than inventing one", async () => {
    mockPulseApi.mockRejectedValueOnce(
      apiError(429, { error: "cooldown", retry_after_seconds: 60 })
    );
    expect(await unlockOffice("824913", USER)).toEqual({
      state: "COOLDOWN",
      retryAfterSeconds: 60
    });
    expect(isOfficeUnlocked()).toBe(false);
  });

  it("names a wrong passcode as exactly that", async () => {
    mockPulseApi.mockRejectedValueOnce(apiError(401, { error: "wrong_passcode" }));
    expect(await unlockOffice("000000", USER)).toEqual({ state: "WRONG_PASSCODE" });
  });
});

describe("writeFailure mapping across the mutations", () => {
  it("maps policy refusals with the server's reason, and confirm_mismatch as POLICY", async () => {
    mockPulseApi.mockRejectedValueOnce(
      apiError(400, { error: "passcode_policy", reason: "too_short" })
    );
    expect(await setupOfficePasscode("12", "12")).toEqual({ state: "POLICY", reason: "too_short" });

    mockPulseApi.mockRejectedValueOnce(apiError(400, { error: "confirm_mismatch" }));
    expect(await setupOfficePasscode("824913", "824914")).toEqual({
      state: "POLICY",
      reason: "confirm_mismatch"
    });
  });

  it("maps already_set, not_set, and reverification_failed", async () => {
    mockPulseApi.mockRejectedValueOnce(apiError(409, { error: "passcode_already_set" }));
    expect(await setupOfficePasscode("824913", "824913")).toEqual({ state: "ALREADY_SET" });

    mockPulseApi.mockRejectedValueOnce(apiError(409, { error: "passcode_not_set" }));
    expect(await changeOfficePasscode("824913", "371049", "371049")).toEqual({
      state: "NOT_SET"
    });

    mockPulseApi.mockRejectedValueOnce(apiError(403, { error: "reverification_failed" }));
    expect(await resetOfficePasscode("account-pw", "371049", "371049")).toEqual({
      state: "REVERIFY_FAILED"
    });
  });

  it("maps a 503 to UNAVAILABLE and anything else to a plain error", async () => {
    mockPulseApi.mockRejectedValueOnce(apiError(503, {}));
    expect(await lockOffice(false)).toEqual({ state: "UNAVAILABLE" });

    mockPulseApi.mockRejectedValueOnce(apiError(500, {}, "boom"));
    expect(await lockOffice(false)).toEqual({ state: "ERROR", message: "boom" });

    mockPulseApi.mockRejectedValueOnce(new RangeError("nope"));
    expect(await lockOffice(false)).toEqual({ state: "ERROR", message: "" });
  });
});

describe("lockOffice request shape", () => {
  it("asks for all devices only when told to", async () => {
    mockPulseApi.mockResolvedValue({ ok: true });
    expect(await lockOffice(true)).toEqual({ state: "OK" });
    expect(JSON.parse(lastRequest().options.body)).toEqual({ all: true });

    expect(await lockOffice(false)).toEqual({ state: "OK" });
    expect(JSON.parse(lastRequest().options.body)).toEqual({});
  });
});

describe("getOfficeSecurityStatus", () => {
  it("parses the status body, narrowing the biometric preference", async () => {
    mockPulseApi.mockResolvedValueOnce({
      passcode_set: true,
      setup_required: false,
      cooldown_seconds: 30,
      biometric_preference: "ENABLED",
      unlocked: true
    });
    expect(await getOfficeSecurityStatus()).toEqual({
      state: "READY",
      passcodeSet: true,
      setupRequired: false,
      cooldownSeconds: 30,
      biometricPreference: "enabled",
      unlocked: true
    });
  });

  it("never throws — a failure to reach the server becomes UNAVAILABLE", async () => {
    mockPulseApi.mockRejectedValueOnce(new Error("network down"));
    expect(await getOfficeSecurityStatus()).toEqual({
      state: "UNAVAILABLE",
      passcodeSet: false,
      setupRequired: false,
      cooldownSeconds: 0,
      biometricPreference: "unset",
      unlocked: false
    });
  });

  it("keeps a 403 apart from UNAVAILABLE, because it is an answer and not a silence", async () => {
    // `_gate` spends three codes on three different truths: 503 for "we could
    // not look", 404 for "there is nothing to sell yet", and 403 only for a
    // real capability out of reach. Collapsing the 403 into UNAVAILABLE is what
    // once told a member whose membership had lapsed that the network was down,
    // under a "Try again" button that could never succeed.
    mockPulseApi.mockRejectedValueOnce(apiError(403, { state: "NOT_ENTITLED" }));
    expect(await getOfficeSecurityStatus()).toEqual({
      state: "UPGRADE_REQUIRED",
      passcodeSet: false,
      setupRequired: false,
      cooldownSeconds: 0,
      biometricPreference: "unset",
      unlocked: false
    });
  });

  it("leaves the server's other refusals on the UNAVAILABLE path", async () => {
    // 503 is the degraded resolve the gate warns about, and it is exactly the
    // case where retrying IS the right offer. It must not drift into an upgrade
    // prompt: billing a member for an outage would be the mirror of the bug.
    mockPulseApi.mockRejectedValueOnce(apiError(503, {}));
    expect((await getOfficeSecurityStatus()).state).toBe("UNAVAILABLE");

    mockPulseApi.mockRejectedValueOnce(apiError(500, {}, "boom"));
    expect((await getOfficeSecurityStatus()).state).toBe("UNAVAILABLE");

    mockPulseApi.mockRejectedValueOnce(apiError(401, {}));
    expect((await getOfficeSecurityStatus()).state).toBe("UNAVAILABLE");
  });
});
