/**
 * Where each Profile OS tile goes, for the owner and for a visitor.
 *
 * Before this registry existed, `ProfileScreen.handleModule` sent every tile to
 * a single global destination — `Music`, `TrustCenter`, `SafetyHub`,
 * `BusinessOs`, the Saved/Groups/Marketplace tabs — all of which are personal
 * surfaces scoped to the signed-in account. Tapping "Music" on Maria's profile
 * opened Roody's music library under a header that said Maria. Twelve of the
 * fourteen tiles behaved that way.
 *
 * The fix is a lookup that is explicit about the visitor case for every tile.
 * A tile is only reachable from someone else's profile if there is a real
 * destination that takes the profile owner as its subject. Where no such
 * destination exists yet, the tile resolves to `unsupported` and is hidden
 * rather than opening the viewer's own data — showing the wrong person's
 * library is worse than showing nothing, and it is what the mission forbids.
 *
 * Adding a visitor destination later is a two-line change here plus the screen
 * itself; nothing else needs to know.
 */

import { ProfileModuleKey } from "../components/ProfileHeader";
import type { RootStackParamList } from "../navigation/types";
import { ProfileContext, ProfileViewerPermissions, profileOsRouteParams } from "./profileContext";

/**
 * Route names are checked against the navigator rather than left as `string`.
 * This registry is the one place tile destinations are written down, so a typo
 * here would be a dead tile discovered only by tapping it on a device.
 */
export type ProfileOsRouteName = keyof RootStackParamList;

export type ProfileOsDestination =
  /** Push a stack route. `params` already carries the Profile OS context. */
  | { kind: "route"; name: ProfileOsRouteName; params?: Record<string, unknown> }
  /** Switch a tab on the profile screen itself; the subject is already correct. */
  | { kind: "tab"; tab: "posts" | "media" | "about" }
  /** No profile-scoped destination exists for this tile yet. */
  | { kind: "unsupported"; tile: ProfileModuleKey };

type TileDef = {
  key: ProfileModuleKey;
  /** Noun used to build the destination header, e.g. "Media" -> "Maria's Media". */
  noun: string;
  /** Permission a visitor needs before the tile is offered at all. */
  permission?: keyof ProfileViewerPermissions;
  /** Where the tile goes on your own profile. Preserves existing owner workflows. */
  owner: (context: ProfileContext) => ProfileOsDestination;
  /** Where it goes on someone else's profile. `null` => not built yet. */
  visitor: ((context: ProfileContext) => ProfileOsDestination) | null;
};

const TILES: Record<ProfileModuleKey, TileDef> = {
  identity: {
    key: "identity",
    noun: "Pulse Identity",
    permission: "canViewPublicProfile",
    owner: (context) => route("PulseIdentity", context, { title: "Pulse Identity" }),
    // The identity screen is profile-aware: it renders the public identity card
    // for a visitor and the full owner surface (QR, linked accounts) for you.
    visitor: (context) => route("PulseIdentity", context, { title: "Pulse Identity" })
  },
  media: {
    key: "media",
    noun: "Media",
    permission: "canViewPublicMedia",
    // Media is a tab on the profile itself, so its subject is whatever profile
    // is loaded. This tile was already correct for visitors.
    owner: () => ({ kind: "tab", tab: "media" }),
    visitor: () => ({ kind: "tab", tab: "media" })
  },
  music: {
    key: "music",
    noun: "Music",
    permission: "canViewPublicMusic",
    owner: (context) => route("Music", context, { title: "Music" }),
    // `Music` is the viewer's own library and upload surface. No public artist
    // page keyed by profile owner exists yet.
    visitor: null
  },
  trust: {
    key: "trust",
    noun: "Trust",
    owner: (context) => route("TrustCenter", context, { title: "Trust Center" }),
    // TrustCenter holds the viewer's own verification documents and appeals.
    visitor: null
  },
  safety: {
    key: "safety",
    noun: "Safety",
    owner: (context) => route("SafetyHub", context, { title: "Safety Hub", section: "overview" }),
    // A visitor's Safety tile must be interaction controls *about* this profile
    // (report / block / mute), never the owner's private safety dashboard.
    // That surface does not exist yet, and SafetyHub is the viewer's own.
    visitor: null
  },
  pulse_dna: {
    key: "pulse_dna",
    noun: "Pulse DNA",
    owner: (context) => route("IntelligenceCenter", context, { title: "Pulse DNA" }),
    visitor: null
  },
  achievements: {
    key: "achievements",
    noun: "Achievements",
    owner: (context) => route("GrowthCenter", context, { contentType: "profile", title: "Achievements" }),
    visitor: null
  },
  activity: {
    key: "activity",
    noun: "Activity",
    permission: "canViewPublicActivity",
    owner: (context) => route("ActivityInbox", context, { title: "Activity" }),
    // ActivityInbox is the viewer's notification inbox — not a public activity
    // feed for the profile owner.
    visitor: null
  },
  collections: {
    key: "collections",
    noun: "Collections",
    permission: "canViewPublicCollections",
    owner: (context) => route("Saved", context),
    // `Saved` is the viewer's private saved items.
    visitor: null
  },
  communities: {
    key: "communities",
    noun: "Communities",
    permission: "canViewPublicCommunities",
    owner: (context) => ({ kind: "route", name: "Tabs", params: { screen: "Groups" } }),
    visitor: null
  },
  marketplace: {
    key: "marketplace",
    noun: "Marketplace",
    permission: "canViewMarketplace",
    owner: (context) => ({ kind: "route", name: "Tabs", params: { screen: "Marketplace" } }),
    // The Marketplace tab is a global browse surface and `SellerStore` ignores
    // its `sellerId` param, so there is no seller storefront keyed by owner yet.
    visitor: null
  },
  events: {
    key: "events",
    noun: "Events",
    permission: "canViewEvents",
    owner: (context) => route("Events", context, { mode: "events", title: "Events" }),
    visitor: null
  },
  business: {
    key: "business",
    noun: "Business",
    permission: "canViewBusiness",
    // Business OS is the owner's management surface: store, seller tools,
    // advertising, payouts.
    owner: (context) => route("BusinessOs", context, { title: "Business OS" }),
    // BusinessBuyerPreview is genuinely buyer-facing and already loads a public
    // profile from `sellerUserId`, so a visitor gets the storefront view rather
    // than any Business OS control.
    visitor: (context) => route("BusinessBuyerPreview", context, { sellerUserId: numericId(context.profileOwnerId) })
  },
  memories: {
    key: "memories",
    noun: "Memories",
    permission: "canViewPublicMemories",
    owner: (context) => ({ kind: "route", name: "Tabs", params: { screen: "Status" } }),
    visitor: null
  },
  progress: {
    key: "progress",
    noun: "Progress",
    // No `title` override: unlike the tiles above, Progress has a localized
    // header in `common:screens.progressCenter`, and an English literal here
    // would win over it in all eleven languages.
    owner: (context) => route("ProgressCenter", context),
    /**
     * `null` here is not a "not built yet" like the tiles above it — it is the
     * feature's privacy requirement expressed in the one place tile visibility
     * is decided.
     *
     * The Founding Member Challenge exposes how many people someone invited,
     * which of them qualified, what they have earned and whether anything is
     * under review. None of that may reach another member, and a visitor
     * destination that "just shows less" would be one refactor away from
     * leaking a count. So there is no visitor destination at all, and
     * `visibleProfileOsTiles` drops the tile from the grid entirely rather than
     * rendering something tappable that then refuses.
     *
     * The server agrees independently: no Progress route accepts a target user,
     * so even a client that reached this screen for someone else would only
     * ever be shown its own progress.
     */
    visitor: null
  }
};

export const PROFILE_OS_TILE_ORDER: ProfileModuleKey[] = [
  "identity",
  "media",
  "music",
  "trust",
  "safety",
  "pulse_dna",
  "achievements",
  "activity",
  "collections",
  "communities",
  "marketplace",
  "events",
  "business",
  "memories",
  // Last in the grid, first thing a member is likely to open: the tile carries
  // live state, so it is the only one whose label changes as they make progress.
  "progress"
];

export function tileNoun(key: ProfileModuleKey): string {
  return TILES[key]?.noun || "";
}

/**
 * Resolve a tile tap.
 *
 * On your own profile every tile keeps its existing destination. On someone
 * else's, a tile only resolves when it has a visitor destination *and* the
 * server said this viewer may see it.
 */
export function profileOsDestination(key: ProfileModuleKey, context: ProfileContext): ProfileOsDestination {
  const tile = TILES[key];
  if (!tile) return { kind: "unsupported", tile: key };
  if (context.isOwnProfile) return tile.owner(context);
  if (!tile.visitor) return { kind: "unsupported", tile: key };
  if (tile.permission && !context.permissions[tile.permission]) return { kind: "unsupported", tile: key };
  return tile.visitor(context);
}

/**
 * Tiles to render for this context.
 *
 * A visitor sees only tiles that lead somewhere about this profile owner. The
 * rest are hidden — a grid of tiles that either error or quietly show your own
 * data is worse than a shorter, honest grid.
 */
export function visibleProfileOsTiles(context: ProfileContext): ProfileModuleKey[] {
  if (context.isOwnProfile) return PROFILE_OS_TILE_ORDER;
  return PROFILE_OS_TILE_ORDER.filter((key) => profileOsDestination(key, context).kind !== "unsupported");
}

function route(name: ProfileOsRouteName, context: ProfileContext, extra?: Record<string, unknown>): ProfileOsDestination {
  return { kind: "route", name, params: profileOsRouteParams(context, extra) };
}

function numericId(value: string): number | undefined {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}
