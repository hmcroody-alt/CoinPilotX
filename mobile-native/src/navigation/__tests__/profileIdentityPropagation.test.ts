/**
 * The global header holds its own copy of the signed-in member's identity.
 *
 * Both properties below are about where that copy comes from and how big it is,
 * neither of which a render assertion can pin: a header that fetches once and
 * never again shows the correct avatar in every test, and a header that renders
 * the full-resolution original looks identical to one rendering a thumbnail.
 * The failure shows up only in a real session — a changed photo that updates on
 * Profile and nowhere else, and a 4MB original decoded into a 32pt circle on
 * every screen. Both are properties of the source, so they are checked there,
 * following the same reasoning as navigation/__tests__/badgeSources.test.ts.
 */
import { readFileSync } from "fs";
import { join } from "path";

const source = readFileSync(join(__dirname, "..", "AppNavigator.tsx"), "utf8");

describe("global header identity", () => {
  it("re-reads the profile when the profile subsystem is invalidated", () => {
    expect(source).toMatch(/registerSyncInvalidation\(\s*["']profile["']/);
  });

  it("prefers the avatar thumbnail over the full-resolution original", () => {
    const avatarLine = source.split("\n").find((line) => line.includes("avatarUrl:"));
    expect(avatarLine).toBeDefined();
    const thumbnail = (avatarLine as string).indexOf("avatar_thumbnail_url");
    const original = (avatarLine as string).indexOf("profile?.avatar_url");
    expect(thumbnail).toBeGreaterThan(-1);
    expect(original).toBeGreaterThan(thumbnail);
  });

  // A failed *refresh* is not a sign-out. Blanking the header on one bad
  // response was how a dropped request became a logged-out-looking drawer.
  it("keeps the identity it already has when a refresh fails", () => {
    expect(source).not.toMatch(/getMyProfile\(\)[\s\S]{0,80}catch\(\(\)\s*=>\s*setProfile\(null\)\)/);
  });
});
