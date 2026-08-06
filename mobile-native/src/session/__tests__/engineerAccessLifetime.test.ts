import { readFileSync } from "fs";
import { join } from "path";
import { authenticatedState, expiredState, fatalErrorState, recoverableErrorState, stateFor, unauthenticatedState } from "../auth";
import { clearEngineerAccess, hasEngineerAccess, setEngineerAccess } from "../../security/engineerAccessSession";

/**
 * §6: the engineer grant lasts for the current authenticated session and no
 * longer. Rather than testing each sign-out helper individually, these tests
 * target `stateFor` — the single constructor every auth transition funnels
 * through — because that is what makes "a stale grant survives an account
 * switch" structurally unreachable rather than merely handled in the branches
 * someone remembered.
 */

const OWNER = 4242;
const OTHER = 9001;

function grant() {
  return { token: "body.sig", expiresAt: Math.floor(Date.now() / 1000) + 1800, scope: ["business_os"] };
}

beforeEach(() => clearEngineerAccess());

describe("engineer grant lifetime is bound to the auth session", () => {
  it("survives while the same account stays authenticated", () => {
    setEngineerAccess(OWNER, grant());
    authenticatedState({ user_id: OWNER } as never);
    expect(hasEngineerAccess(OWNER)).toBe(true);
  });

  it("is dropped on sign-out", () => {
    setEngineerAccess(OWNER, grant());
    unauthenticatedState();
    expect(hasEngineerAccess()).toBe(false);
  });

  it("is dropped when the session expires", () => {
    setEngineerAccess(OWNER, grant());
    expiredState();
    expect(hasEngineerAccess()).toBe(false);
  });

  it("is dropped on a recoverable bootstrap error", () => {
    setEngineerAccess(OWNER, grant());
    recoverableErrorState();
    expect(hasEngineerAccess()).toBe(false);
  });

  it("is dropped on a fatal bootstrap error", () => {
    setEngineerAccess(OWNER, grant());
    fatalErrorState();
    expect(hasEngineerAccess()).toBe(false);
  });

  it("is dropped while the app is bootstrapping", () => {
    setEngineerAccess(OWNER, grant());
    stateFor("BOOTSTRAPPING");
    expect(hasEngineerAccess()).toBe(false);
  });

  it("is dropped on account switch", () => {
    // The dangerous case: signing into a different account must not inherit
    // the previous account's engineer access.
    setEngineerAccess(OWNER, grant());
    authenticatedState({ user_id: OTHER } as never);
    expect(hasEngineerAccess()).toBe(false);
  });

  it("is dropped when the authenticated user has no id", () => {
    setEngineerAccess(OWNER, grant());
    authenticatedState({} as never);
    expect(hasEngineerAccess()).toBe(false);
  });

  it("covers every non-authenticated phase", () => {
    const phases = ["UNAUTHENTICATED", "SESSION_EXPIRED", "RECOVERABLE_ERROR", "FATAL_ERROR", "BOOTSTRAPPING"] as const;
    for (const phase of phases) {
      setEngineerAccess(OWNER, grant());
      stateFor(phase);
      expect(hasEngineerAccess()).toBe(false);
    }
  });

  it("reconciles inside the single AuthState constructor", () => {
    // If a future edit adds a sign-out path that builds an AuthState by hand
    // instead of calling stateFor, this assertion is the thing that notices.
    const source = readFileSync(join(__dirname, "..", "auth.ts"), "utf8");
    const constructors = source.match(/=>\s*stateFor\(/g) || [];
    expect(constructors.length).toBeGreaterThanOrEqual(5);
    expect(source).toMatch(/reconcileEngineerAccessOwner/);
    expect(source).toMatch(/clearEngineerAccess/);
  });
});
