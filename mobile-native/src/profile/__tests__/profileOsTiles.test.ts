/**
 * Tile routing: no Profile OS tile may send a visitor to a surface scoped to
 * the signed-in account.
 *
 * The regression these tests lock down is concrete. `handleModule` used to be a
 * switch that opened `Music`, `TrustCenter`, `SafetyHub`, `IntelligenceCenter`,
 * `GrowthCenter`, `ActivityInbox`, `Saved`, `BusinessOs` and three tabs with no
 * subject at all — every one of them the viewer's own. Visiting Maria and
 * tapping any tile showed Roody's data under Maria's name.
 */

import { PROFILE_OS_TILE_ORDER, profileOsDestination, visibleProfileOsTiles } from "../profileOsTiles";
import { buildProfileContext, normalizePermissions, ProfileContext } from "../profileContext";

const ALL_PUBLIC = {
  can_view_public_profile: true,
  can_view_public_media: true,
  can_view_public_music: true,
  can_view_public_activity: true,
  can_view_public_collections: true,
  can_view_public_communities: true,
  can_view_marketplace: true,
  can_view_business: true,
  can_view_events: true,
  can_view_public_memories: true,
  can_message: true,
  can_report: true,
  can_block: true
};

const roody: ProfileContext = buildProfileContext({
  viewerUserId: 7,
  profile: { user_id: 7, display_name: "Roody Cherie", username: "roodycherie", is_self: true },
  target: null
});

function visiting(permissions: Record<string, boolean> = ALL_PUBLIC): ProfileContext {
  const context = buildProfileContext({
    viewerUserId: 7,
    profile: { user_id: 8, display_name: "Maria Cherie", username: "mariacherie", is_self: false },
    target: { profileKey: "8", userId: 8, cacheKey: "user:8", nativePath: "/pulse/id/8" }
  });
  return { ...context, permissions: normalizePermissions(permissions, false) };
}

/** Routes that are scoped to the signed-in account and must never open for a visitor. */
const VIEWER_OWNED_ROUTES = [
  "Music",
  "TrustCenter",
  "SafetyHub",
  "IntelligenceCenter",
  "GrowthCenter",
  "ActivityInbox",
  "Saved",
  "BusinessOs",
  "Tabs"
];

describe("owner profile", () => {
  it("keeps every tile available", () => {
    expect(visibleProfileOsTiles(roody)).toEqual(PROFILE_OS_TILE_ORDER);
  });

  it("routes each tile somewhere", () => {
    for (const tile of PROFILE_OS_TILE_ORDER) {
      expect(profileOsDestination(tile, roody).kind).not.toBe("unsupported");
    }
  });

  it("still opens Business OS on your own profile", () => {
    expect(profileOsDestination("business", roody)).toMatchObject({ kind: "route", name: "BusinessOs" });
  });
});

describe("visiting another profile", () => {
  it("never opens a viewer-scoped route", () => {
    const maria = visiting();
    for (const tile of PROFILE_OS_TILE_ORDER) {
      const destination = profileOsDestination(tile, maria);
      if (destination.kind !== "route") continue;
      expect(VIEWER_OWNED_ROUTES).not.toContain(destination.name);
    }
  });

  it("stamps the profile owner onto every route it does open", () => {
    const maria = visiting();
    for (const tile of PROFILE_OS_TILE_ORDER) {
      const destination = profileOsDestination(tile, maria);
      if (destination.kind !== "route") continue;
      expect(destination.params).toMatchObject({
        profileOwnerId: "8",
        sourceProfileId: "8",
        isOwnProfile: false,
        entryPoint: "PROFILE_OS"
      });
    }
  });

  it("hides tiles that have no destination about this profile owner", () => {
    const visible = visibleProfileOsTiles(visiting());
    // Rather than opening the viewer's own library under Maria's name, these
    // are omitted until a profile-scoped destination exists for each.
    expect(visible).not.toContain("music");
    expect(visible).not.toContain("collections");
    expect(visible).not.toContain("activity");
    expect(visible).not.toContain("safety");
  });

  it("keeps the tiles that are genuinely profile-scoped", () => {
    const visible = visibleProfileOsTiles(visiting());
    expect(visible).toEqual(expect.arrayContaining(["identity", "media", "business"]));
  });

  it("sends Business to the buyer-facing storefront, not Business OS", () => {
    const destination = profileOsDestination("business", visiting());
    expect(destination).toMatchObject({ kind: "route", name: "BusinessBuyerPreview" });
    // The storefront must load the profile owner's shop, not the viewer's.
    expect((destination as { params: Record<string, unknown> }).params.sellerUserId).toBe(8);
  });

  it("keeps Media on the profile's own tab, whose subject is already correct", () => {
    expect(profileOsDestination("media", visiting())).toEqual({ kind: "tab", tab: "media" });
  });
});

describe("permission gating", () => {
  it("withholds a tile the server did not authorise", () => {
    const noBusiness = visiting({ ...ALL_PUBLIC, can_view_business: false });
    expect(profileOsDestination("business", noBusiness).kind).toBe("unsupported");
    expect(visibleProfileOsTiles(noBusiness)).not.toContain("business");
  });

  it("shows no tiles at all when the server grants no permissions", () => {
    // A blocked or private profile: no counts, no metadata, no destinations.
    // Media is gated too. It only switches a tab on the profile already on
    // screen, so leaving it would be defensible — but every tile deferring to
    // the server's answer is the rule that keeps this module honest, and an
    // empty grid is the correct read of "you may see nothing about this person".
    const denied = visiting({});
    expect(visibleProfileOsTiles(denied)).toEqual([]);
  });
});
