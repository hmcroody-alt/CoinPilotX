import { readFileSync } from "fs";
import { join } from "path";
import {
  clearEngineerAccess,
  engineerAccessDeviceId,
  engineerAccessScope,
  engineerAccessToken,
  hasEngineerAccess,
  reconcileEngineerAccessOwner,
  setEngineerAccess,
  subscribeToEngineerAccess
} from "../engineerAccessSession";

/**
 * The grant store is the only place on the client that remembers an engineer is
 * privileged. Everything here is about the ways that memory must NOT outlive
 * what authorized it: a different account, a sign-out, a session expiry.
 */

const OWNER = 4242;
const OTHER = 9001;

function grantFor(secondsFromNow: number) {
  return {
    token: "body.signature",
    expiresAt: Math.floor(Date.now() / 1000) + secondsFromNow,
    scope: ["business_os", "marketplace_selling"]
  };
}

beforeEach(() => clearEngineerAccess());

describe("engineer access session", () => {
  it("holds a grant for the account it was issued to", () => {
    setEngineerAccess(OWNER, grantFor(600));
    expect(hasEngineerAccess(OWNER)).toBe(true);
    expect(engineerAccessToken()).toBe("body.signature");
  });

  it("starts with no access", () => {
    expect(hasEngineerAccess()).toBe(false);
    expect(engineerAccessToken()).toBe("");
    expect(engineerAccessScope()).toEqual([]);
  });

  it("does not report access for a different account", () => {
    setEngineerAccess(OWNER, grantFor(600));
    expect(hasEngineerAccess(OTHER)).toBe(false);
  });

  it("drops an expired grant on read", () => {
    setEngineerAccess(OWNER, grantFor(-1));
    expect(hasEngineerAccess(OWNER)).toBe(false);
    expect(engineerAccessToken()).toBe("");
  });

  it("clears on account switch", () => {
    // The failure this guards against: a boolean 'isEngineer' flag would
    // survive the switch and hand the new account the old account's access.
    setEngineerAccess(OWNER, grantFor(600));
    reconcileEngineerAccessOwner(OTHER);
    expect(hasEngineerAccess()).toBe(false);
  });

  it("keeps the grant when the same account re-resolves", () => {
    setEngineerAccess(OWNER, grantFor(600));
    reconcileEngineerAccessOwner(OWNER);
    expect(hasEngineerAccess(OWNER)).toBe(true);
  });

  it("clears on sign-out", () => {
    setEngineerAccess(OWNER, grantFor(600));
    clearEngineerAccess();
    expect(hasEngineerAccess()).toBe(false);
  });

  it("notifies subscribers when access changes", () => {
    const listener = jest.fn();
    const unsubscribe = subscribeToEngineerAccess(listener);
    setEngineerAccess(OWNER, grantFor(600));
    expect(listener).toHaveBeenCalledTimes(1);
    clearEngineerAccess();
    expect(listener).toHaveBeenCalledTimes(2);
    unsubscribe();
    setEngineerAccess(OWNER, grantFor(600));
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("copies the scope array so callers cannot widen it in place", () => {
    setEngineerAccess(OWNER, grantFor(600));
    const scope = engineerAccessScope();
    scope.push("admin_everything");
    expect(engineerAccessScope()).not.toContain("admin_everything");
  });

  it("issues a stable device id within a launch", () => {
    expect(engineerAccessDeviceId()).toBe(engineerAccessDeviceId());
    expect(engineerAccessDeviceId()).toMatch(/^native-/);
  });

  it("persists nothing outside the JS runtime", () => {
    // §6 forbids the raw passcode or the grant reaching AsyncStorage,
    // SecureStore, or telemetry. The module imports none of them, which is a
    // stronger guarantee than asserting we never call them.
    // Comments are stripped first: the module names these sinks in prose to
    // explain why it avoids them, and that explanation must not trip the check.
    const source = readFileSync(join(__dirname, "..", "engineerAccessSession.ts"), "utf8");
    const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
    expect(code).not.toMatch(/AsyncStorage|SecureStore|expo-secure-store|analytics|track\(/);
  });
});
