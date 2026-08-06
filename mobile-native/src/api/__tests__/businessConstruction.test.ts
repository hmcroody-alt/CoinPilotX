import { readFileSync } from "fs";
import { join } from "path";
import * as businessConstruction from "../businessConstruction";
import { CONSTRUCTION_LOCKED } from "../businessConstruction";

/**
 * This module used to export `authenticatedOwnerAccess`, a client-side check
 * keyed on two hardcoded owner email addresses. It was removed because strings
 * in the JS bundle are readable by anyone who unzips the IPA: the check both
 * disclosed the owner accounts and could be satisfied by patching a single
 * comparison. These tests are the regression guard — they fail if any form of
 * client-side identity authorization creeps back in.
 */
describe("business construction access is server-resolved", () => {
  it("exposes no client-side owner check", () => {
    expect((businessConstruction as Record<string, unknown>).authenticatedOwnerAccess).toBeUndefined();
    expect((businessConstruction as Record<string, unknown>).CANONICAL_OWNER_EMAILS).toBeUndefined();
  });

  it("ships no owner email or identity allowlist in the bundled source", () => {
    const source = readFileSync(join(__dirname, "..", "businessConstruction.ts"), "utf8");
    // Any literal email address in this module would be an identity allowlist.
    expect(source).not.toMatch(/[\w.+-]+@[\w-]+\.[a-z]{2,}/i);
  });

  it("fails closed: the default access shape grants nothing", () => {
    expect(CONSTRUCTION_LOCKED.ok).toBe(false);
    expect(CONSTRUCTION_LOCKED.can_access_private_business_os).toBe(false);
    expect(CONSTRUCTION_LOCKED.developer_mode).toBe(false);
    expect(CONSTRUCTION_LOCKED.developer_badge).toBe(false);
    expect(CONSTRUCTION_LOCKED.engineer_access).toBeUndefined();
  });
});
