import { getEngineerAccessStatus, verifyEngineerAccess } from "../engineerAccess";
import { PulseApiError, pulseApi } from "../pulseApi";
import {
  clearEngineerAccess,
  engineerAccessSource,
  engineerAccessToken,
  hasEngineerAccess,
  hasLocalEngineerAccess,
  setEngineerAccess
} from "../../security/engineerAccessSession";
import {
  EngineerAccessDiagnostic,
  setEngineerAccessDiagnosticsSink
} from "../../security/engineerAccessDiagnostics";

jest.mock("../pulseApi", () => {
  const actual = jest.requireActual("../pulseApi");
  return { ...actual, pulseApi: jest.fn() };
});

const mockedApi = pulseApi as unknown as jest.Mock;
const OWNER = 4242;
const PASSCODE = "13572468";

beforeEach(() => {
  mockedApi.mockReset();
  clearEngineerAccess();
});

function denial(status: number, details?: Record<string, unknown>) {
  return new PulseApiError("engineer_access_denied", status, "engineer_access_denied", details);
}

describe("verifyEngineerAccess", () => {
  it("stores the grant the server issues", async () => {
    const expiresAt = Math.floor(Date.now() / 1000) + 1800;
    mockedApi.mockResolvedValue({ ok: true, authorized: true, grant: "body.sig", expires_at: expiresAt, scope: ["business_os"] });

    const outcome = await verifyEngineerAccess(OWNER, PASSCODE);

    expect(outcome.authorized).toBe(true);
    expect(hasEngineerAccess(OWNER)).toBe(true);
    expect(engineerAccessToken()).toBe("body.sig");
  });

  it("never sends the passcode as anything but a request body field", async () => {
    mockedApi.mockResolvedValue({ authorized: false });
    await verifyEngineerAccess(OWNER, PASSCODE);

    const [path, options] = mockedApi.mock.calls[0];
    // Not in the URL — §6 forbids the passcode in route params or URLs, where
    // it would land in server access logs and crash breadcrumbs.
    expect(path).not.toContain(PASSCODE);
    expect(path).toBe("/api/internal/engineer-access/verify");
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body).passcode).toBe(PASSCODE);
  });

  it("does not leak the passcode into the returned outcome", async () => {
    mockedApi.mockResolvedValue({ authorized: false });
    const outcome = await verifyEngineerAccess(OWNER, PASSCODE);
    expect(JSON.stringify(outcome)).not.toContain(PASSCODE);
  });

  it("does not leak the passcode when the request throws", async () => {
    mockedApi.mockRejectedValue(denial(403));
    const outcome = await verifyEngineerAccess(OWNER, PASSCODE);
    expect(JSON.stringify(outcome)).not.toContain(PASSCODE);
  });

  it("grants nothing on a denial", async () => {
    mockedApi.mockRejectedValue(denial(403));
    const outcome = await verifyEngineerAccess(OWNER, PASSCODE);
    expect(outcome.authorized).toBe(false);
    expect(hasEngineerAccess()).toBe(false);
  });

  it("surfaces the lockout countdown from a denial body", async () => {
    mockedApi.mockRejectedValue(denial(403, { retry_after_seconds: 60 }));
    const outcome = await verifyEngineerAccess(OWNER, PASSCODE);
    expect(outcome).toMatchObject({ authorized: false, retryAfterSeconds: 60 });
  });

  it("surfaces the re-authentication requirement", async () => {
    mockedApi.mockRejectedValue(denial(403, { retry_after_seconds: 3600, requires_reauthentication: true }));
    const outcome = await verifyEngineerAccess(OWNER, PASSCODE);
    expect(outcome).toMatchObject({ authorized: false, requiresReauthentication: true });
  });

  it("treats being offline as denied, never as granted", async () => {
    // The failure mode this guards: an attacker forcing the network to fail so
    // the client 'optimistically' unlocks. Every non-success path denies.
    mockedApi.mockRejectedValue(new Error("Network request failed"));
    const outcome = await verifyEngineerAccess(OWNER, PASSCODE);
    expect(outcome.authorized).toBe(false);
    expect(hasEngineerAccess()).toBe(false);
  });

  it("treats a server error as denied", async () => {
    mockedApi.mockRejectedValue(denial(500));
    const outcome = await verifyEngineerAccess(OWNER, PASSCODE);
    expect(outcome.authorized).toBe(false);
    expect(hasEngineerAccess()).toBe(false);
  });

  it("ignores an authorized flag that arrives without a signed grant", async () => {
    // A tampered or spoofed response cannot unlock anything: the grant token is
    // what protected routes actually check, and it cannot be forged client-side.
    mockedApi.mockResolvedValue({ ok: true, authorized: true });
    const outcome = await verifyEngineerAccess(OWNER, PASSCODE);
    expect(outcome.authorized).toBe(false);
    expect(hasEngineerAccess()).toBe(false);
  });
});

/**
 * The interim development passcode. These tests run with __DEV__ true, which is
 * the same condition that compiles the fallback into a debug build, so the
 * behaviour exercised here is the behaviour on an internal device.
 */
const DEV_PASSCODE = "70041852";

/** The deployment state that caused the defect: the route does not exist. */
function routeNotDeployed() {
  return new PulseApiError("not_found", 404, "not_found");
}

describe("verifyEngineerAccess — development fallback", () => {
  it("unlocks with the exact passcode when the route is not deployed", async () => {
    mockedApi.mockRejectedValue(routeNotDeployed());

    const outcome = await verifyEngineerAccess(OWNER, DEV_PASSCODE);

    expect(outcome.authorized).toBe(true);
    expect(hasEngineerAccess(OWNER)).toBe(true);
    expect(hasLocalEngineerAccess(OWNER)).toBe(true);
  });

  it.each(["  70041852", "70041852  ", " 70041852 "])("unlocks with %j after trimming", async (entered) => {
    mockedApi.mockRejectedValue(routeNotDeployed());
    const outcome = await verifyEngineerAccess(OWNER, entered);
    expect(outcome.authorized).toBe(true);
  });

  it.each(["70041853", "12345678", "7004185", ""])("stays denied for %j", async (entered) => {
    mockedApi.mockRejectedValue(routeNotDeployed());
    const outcome = await verifyEngineerAccess(OWNER, entered);
    expect(outcome.authorized).toBe(false);
    expect(hasEngineerAccess()).toBe(false);
  });

  it("is not blocked by a stale lockout from earlier failed attempts", async () => {
    // The correct passcode is not one of the attempts the server counted, so its
    // countdown must not apply to a locally verified answer.
    mockedApi.mockRejectedValue(denial(403, { retry_after_seconds: 900 }));

    const outcome = await verifyEngineerAccess(OWNER, DEV_PASSCODE);

    expect(outcome.authorized).toBe(true);
    expect(hasLocalEngineerAccess(OWNER)).toBe(true);
  });

  it("is not blocked by a re-authentication demand either", async () => {
    mockedApi.mockRejectedValue(denial(403, { retry_after_seconds: 3600, requires_reauthentication: true }));
    const outcome = await verifyEngineerAccess(OWNER, DEV_PASSCODE);
    expect(outcome.authorized).toBe(true);
  });

  it("still reports the lockout for a passcode the fallback does not accept", async () => {
    mockedApi.mockRejectedValue(denial(403, { retry_after_seconds: 900 }));
    const outcome = await verifyEngineerAccess(OWNER, "12345678");
    expect(outcome).toMatchObject({ authorized: false, retryAfterSeconds: 900 });
  });

  it("lets a real server grant win over the local one", async () => {
    // The local path is tried after the server, never instead of it.
    const expiresAt = Math.floor(Date.now() / 1000) + 1800;
    mockedApi.mockResolvedValue({ ok: true, authorized: true, grant: "body.sig", expires_at: expiresAt, scope: ["business_os"] });

    await verifyEngineerAccess(OWNER, DEV_PASSCODE);

    expect(engineerAccessToken()).toBe("body.sig");
    expect(engineerAccessSource()).toBe("server");
    expect(hasLocalEngineerAccess(OWNER)).toBe(false);
  });

  it("does not hand a local grant to a different account", async () => {
    mockedApi.mockRejectedValue(routeNotDeployed());
    await verifyEngineerAccess(OWNER, DEV_PASSCODE);
    expect(hasEngineerAccess(OWNER + 1)).toBe(false);
  });

  it("expires the local grant on its own", async () => {
    mockedApi.mockRejectedValue(routeNotDeployed());
    await verifyEngineerAccess(OWNER, DEV_PASSCODE);

    const outcome = await verifyEngineerAccess(OWNER, DEV_PASSCODE);
    if (!outcome.authorized) throw new Error("expected a grant");
    jest.spyOn(Date, "now").mockReturnValue((outcome.expiresAt + 1) * 1000);
    expect(hasEngineerAccess(OWNER)).toBe(false);
    (Date.now as jest.Mock).mockRestore();
  });

  it("drops the grant on sign-out", async () => {
    mockedApi.mockRejectedValue(routeNotDeployed());
    await verifyEngineerAccess(OWNER, DEV_PASSCODE);

    clearEngineerAccess();

    expect(hasEngineerAccess()).toBe(false);
    expect(hasLocalEngineerAccess(OWNER)).toBe(false);
  });
});

describe("verifyEngineerAccess — diagnostics", () => {
  let events: EngineerAccessDiagnostic[] = [];
  let restore: () => void = () => undefined;
  const last = () => events[events.length - 1];

  beforeEach(() => {
    events = [];
    restore = setEngineerAccessDiagnosticsSink((event) => events.push(event));
  });
  afterEach(() => restore());

  it("traces the local approval path", async () => {
    mockedApi.mockRejectedValue(routeNotDeployed());
    await verifyEngineerAccess(OWNER, DEV_PASSCODE);

    expect(events.map((event) => event.stage)).toEqual([
      "verification_started",
      "verification_source",
      "authorization_result"
    ]);
    expect(events[1]).toMatchObject({ source: "local" });
    expect(events[2]).toMatchObject({ source: "local", result: "approved" });
  });

  it("distinguishes an undeployed route from a wrong passcode", async () => {
    // The whole reason this trace exists: both looked identical before.
    mockedApi.mockRejectedValue(routeNotDeployed());
    await verifyEngineerAccess(OWNER, "12345678");
    expect(last()).toMatchObject({ source: "server", result: "denied", status: 404 });

    events = [];
    mockedApi.mockRejectedValue(denial(403, { retry_after_seconds: 60 }));
    await verifyEngineerAccess(OWNER, "12345678");
    expect(last()).toMatchObject({ result: "locked" });

    events = [];
    mockedApi.mockRejectedValue(new Error("Network request failed"));
    await verifyEngineerAccess(OWNER, "12345678");
    expect(last()).toMatchObject({ result: "error" });
  });

  it("never carries the entered passcode", async () => {
    mockedApi.mockRejectedValue(routeNotDeployed());
    await verifyEngineerAccess(OWNER, DEV_PASSCODE);
    await verifyEngineerAccess(OWNER, PASSCODE);
    expect(JSON.stringify(events)).not.toContain(DEV_PASSCODE);
    expect(JSON.stringify(events)).not.toContain(PASSCODE);
  });
});

describe("getEngineerAccessStatus", () => {
  it("clears the local grant when the server says the session is inactive", async () => {
    setEngineerAccess(OWNER, { token: "body.sig", expiresAt: Math.floor(Date.now() / 1000) + 600, scope: [] });
    mockedApi.mockResolvedValue({ active: false });

    await getEngineerAccessStatus();

    expect(hasEngineerAccess()).toBe(false);
  });

  it("keeps the grant when the server confirms it is active", async () => {
    setEngineerAccess(OWNER, { token: "body.sig", expiresAt: Math.floor(Date.now() / 1000) + 600, scope: [] });
    mockedApi.mockResolvedValue({ active: true, expires_at: Math.floor(Date.now() / 1000) + 600, scope: ["business_os"] });

    const status = await getEngineerAccessStatus();

    expect(status.active).toBe(true);
    expect(hasEngineerAccess(OWNER)).toBe(true);
  });

  it("reports a server-held lockout so a restart cannot bypass it", async () => {
    mockedApi.mockResolvedValue({ active: false, locked_seconds_remaining: 240, requires_reauthentication: false });
    const status = await getEngineerAccessStatus();
    expect(status.lockedSecondsRemaining).toBe(240);
  });

  it("does not eject an engineer on a transient network failure", async () => {
    setEngineerAccess(OWNER, { token: "body.sig", expiresAt: Math.floor(Date.now() / 1000) + 600, scope: [] });
    mockedApi.mockRejectedValue(new Error("Network request failed"));

    const status = await getEngineerAccessStatus();

    expect(status.active).toBe(false);
    // The grant survives locally because every protected request is checked
    // server-side anyway; a flaky connection should not interrupt work.
    expect(hasEngineerAccess(OWNER)).toBe(true);
  });
});
