/**
 * The invariant under test: a Profile OS destination's subject is the profile
 * that was open when the tile was tapped, never the signed-in viewer.
 *
 * Roody (user 7) is signed in throughout. Maria (user 8) is the profile being
 * visited. Any assertion that lets Roody's id or data surface under Maria is a
 * regression of the bug this module was written to remove.
 */

import {
  NO_PERMISSIONS,
  OWNER_PERMISSIONS,
  buildProfileContext,
  destinationTitle,
  emptyStateText,
  normalizePermissions,
  possessive,
  profileOsRouteParams,
  profileScopedCacheId,
  profileScopedKey,
  resolveRouteProfileContext,
  subjectName,
  withServerPermissions
} from "../profileContext";

const ROODY = { user_id: 7, display_name: "Roody Cherie", username: "roodycherie", is_self: true };
const MARIA = { user_id: 8, display_name: "Maria Cherie", username: "mariacherie", is_self: false };
const MARIA_TARGET = { profileKey: "8", userId: 8, cacheKey: "user:8", nativePath: "/pulse/id/8" };

describe("buildProfileContext", () => {
  it("makes the viewed profile the subject, not the viewer", () => {
    const context = buildProfileContext({ viewerUserId: 7, profile: MARIA, target: MARIA_TARGET });
    expect(context.profileOwnerId).toBe("8");
    expect(context.viewerUserId).toBe("7");
    expect(context.isOwnProfile).toBe(false);
  });

  it("treats a profile with no navigation target as the viewer's own", () => {
    const context = buildProfileContext({ viewerUserId: 7, profile: ROODY, target: null });
    expect(context.profileOwnerId).toBe("7");
    expect(context.isOwnProfile).toBe(true);
  });

  it("uses the server's is_self when you reach your own profile through a target", () => {
    const context = buildProfileContext({
      viewerUserId: 7,
      profile: { ...ROODY, is_self: true },
      target: { profileKey: "7", userId: 7, cacheKey: "user:7", nativePath: "/pulse/id/7" }
    });
    expect(context.isOwnProfile).toBe(true);
  });

  it("does not read an unknown viewer as ownership", () => {
    // A signed-out or not-yet-restored session must resolve to visitor, never
    // to "this is mine" — the permissive direction is the dangerous one.
    const context = buildProfileContext({ viewerUserId: null, profile: { ...MARIA, is_self: undefined }, target: MARIA_TARGET });
    expect(context.isOwnProfile).toBe(false);
    expect(context.permissions).toEqual(NO_PERMISSIONS);
  });

  it("carries display fields for presentation without using them as the subject", () => {
    const context = buildProfileContext({ viewerUserId: 7, profile: MARIA, target: MARIA_TARGET });
    expect(context.displayName).toBe("Maria Cherie");
    expect(context.username).toBe("mariacherie");
    // The id, not the name, decides whose data loads.
    expect(context.profileOwnerId).toBe("8");
  });
});

describe("route params", () => {
  it("sends profileOwnerId, sourceProfileId, isOwnProfile and entryPoint to every destination", () => {
    const context = buildProfileContext({ viewerUserId: 7, profile: MARIA, target: MARIA_TARGET });
    const params = profileOsRouteParams(context, { title: "Media" });
    expect(params).toMatchObject({
      profileOwnerId: "8",
      sourceProfileId: "8",
      isOwnProfile: false,
      entryPoint: "PROFILE_OS",
      title: "Media"
    });
  });

  it("round-trips the subject back out on the destination screen", () => {
    const context = buildProfileContext({ viewerUserId: 7, profile: MARIA, target: MARIA_TARGET });
    const resolved = resolveRouteProfileContext(profileOsRouteParams(context), 7);
    expect(resolved.profileOwnerId).toBe("8");
    expect(resolved.isOwnProfile).toBe(false);
  });

  it("re-derives ownership from ids and ignores a spoofed isOwnProfile flag", () => {
    // Section 8: do not trust isOwnProfile from a client boolean alone.
    const resolved = resolveRouteProfileContext(
      { profileOwnerId: "8", sourceProfileId: "8", isOwnProfile: true, entryPoint: "PROFILE_OS" },
      7
    );
    expect(resolved.isOwnProfile).toBe(false);
    expect(resolved.permissions).toEqual(NO_PERMISSIONS);
  });

  it("treats a destination opened without params as the viewer's own surface", () => {
    // Reached from a tab or a personal deep link, where the viewer really is
    // the subject. It must not inherit a previously visited profile.
    const resolved = resolveRouteProfileContext(undefined, 7);
    expect(resolved.profileOwnerId).toBe("7");
    expect(resolved.isOwnProfile).toBe(true);
  });
});

describe("permissions", () => {
  it("denies everything when the server sent no permissions for a visitor", () => {
    expect(normalizePermissions(undefined, false)).toEqual(NO_PERMISSIONS);
  });

  it("reads the server's snake_case flags", () => {
    const permissions = normalizePermissions({ can_view_public_media: true, can_message: true }, false);
    expect(permissions.canViewPublicMedia).toBe(true);
    expect(permissions.canMessage).toBe(true);
    expect(permissions.canViewBusiness).toBe(false);
  });

  it("gives the owner full read of their own profile", () => {
    expect(normalizePermissions(undefined, true)).toEqual(OWNER_PERMISSIONS);
  });

  it("lets the server payload correct a context built optimistically", () => {
    const context = resolveRouteProfileContext({ profileOwnerId: "8", entryPoint: "PROFILE_OS" }, 7);
    const corrected = withServerPermissions(context, { ...MARIA, viewer_permissions: { can_view_public_media: true } });
    expect(corrected.permissions.canViewPublicMedia).toBe(true);
    expect(corrected.permissions.canViewBusiness).toBe(false);
    expect(corrected.isOwnProfile).toBe(false);
  });
});

describe("cache isolation", () => {
  it("keys every profile-scoped cache entry by owner id", () => {
    const roody = buildProfileContext({ viewerUserId: 7, profile: ROODY, target: null });
    const maria = buildProfileContext({ viewerUserId: 7, profile: MARIA, target: MARIA_TARGET });
    expect(profileScopedKey("profile-media", maria)).toEqual(["profile-media", "8"]);
    // The whole point: two profiles cannot collide in one bucket.
    expect(profileScopedCacheId("profile-media", roody)).not.toBe(profileScopedCacheId("profile-media", maria));
  });

  it("keeps distinct keys across a rapid profile switch", () => {
    const ids = [MARIA_TARGET, { profileKey: "9", userId: 9, cacheKey: "user:9", nativePath: "/pulse/id/9" }, MARIA_TARGET].map(
      (target) => profileScopedCacheId("profile-media", buildProfileContext({ viewerUserId: 7, target }))
    );
    expect(ids[0]).toBe("profile-media:8");
    expect(ids[1]).toBe("profile-media:9");
    expect(ids[2]).toBe("profile-media:8");
  });
});

describe("subject-aware copy", () => {
  const maria = buildProfileContext({ viewerUserId: 7, profile: MARIA, target: MARIA_TARGET });
  const roody = buildProfileContext({ viewerUserId: 7, profile: ROODY, target: null });

  it("names the profile owner in visitor headers", () => {
    expect(destinationTitle(maria, "Media")).toBe("Maria's Media");
    expect(destinationTitle(maria, "Achievements")).toBe("Maria's Achievements");
  });

  it("never says 'My' or 'Your' on someone else's screen", () => {
    for (const noun of ["Media", "Music", "Marketplace", "Business", "Collections"]) {
      expect(destinationTitle(maria, noun)).not.toMatch(/\b(My|Your)\b/);
    }
  });

  it("still says 'My' on your own screen", () => {
    expect(destinationTitle(roody, "Media")).toBe("My Media");
  });

  it("writes empty states about the right person", () => {
    expect(emptyStateText(maria, "You have not added any music yet.", (name) => `${name} has not shared any music yet.`)).toBe(
      "Maria has not shared any music yet."
    );
    expect(emptyStateText(roody, "You have not added any music yet.", (name) => `${name} has not shared any music yet.`)).toBe(
      "You have not added any music yet."
    );
  });

  it("handles possessives for names ending in s", () => {
    expect(possessive("Maria")).toBe("Maria's");
    expect(possessive("Chris")).toBe("Chris'");
  });

  it("falls back to the handle when a display name is missing", () => {
    const context = buildProfileContext({ viewerUserId: 7, profile: { user_id: 8, username: "mariacherie" }, target: MARIA_TARGET });
    expect(subjectName(context)).toBe("@mariacherie");
  });
});
