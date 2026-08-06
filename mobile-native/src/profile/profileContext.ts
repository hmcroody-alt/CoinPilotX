/**
 * Canonical Profile OS context.
 *
 * Every Profile OS destination is *about* exactly one account: the profile that
 * was open when the tile was tapped. That account is `profileOwnerId`. The
 * signed-in account is `viewerUserId` and is only the subject when the two are
 * the same id.
 *
 * The bug this module exists to prevent: a tile opens Maria's Media and the
 * screen calls `getMyProfile()` / `/api/me/...`, so Roody — the viewer — sees
 * his own media under Maria's name. Screens must therefore never infer their
 * subject from the auth store, a global "me" selector, or whatever profile
 * happened to be open last. They read it from route params, through
 * `resolveRouteProfileContext`.
 *
 * IDs are authoritative. `displayName` / `username` / `avatarUrl` ride along as
 * presentation hints so a destination can paint its header before the fetch
 * lands, but they must never be used to decide *whose* data to load.
 */

import { NativeProfileTarget, ProfileTargetInput, resolveProfileTarget } from "../api/profileTarget";

export const PROFILE_OS_ENTRY = "PROFILE_OS" as const;

export type ProfileEntryPoint = typeof PROFILE_OS_ENTRY;

/**
 * What this viewer is allowed to see about this profile owner.
 *
 * Authored by the server (`profile.viewer_permissions`). The client uses these
 * to decide what to *render*; it is not the enforcement boundary. Section 8 of
 * the mission is explicit: the backend must enforce access, and the client must
 * not fetch private data and merely hide it.
 */
export type ProfileViewerPermissions = {
  canViewPublicProfile: boolean;
  canViewFollowerContent: boolean;
  canViewFriendContent: boolean;
  canViewPublicMedia: boolean;
  canViewPublicMusic: boolean;
  canViewPublicActivity: boolean;
  canViewPublicCollections: boolean;
  canViewPublicCommunities: boolean;
  canViewMarketplace: boolean;
  canViewBusiness: boolean;
  canViewEvents: boolean;
  canViewPublicMemories: boolean;
  canMessage: boolean;
  canReport: boolean;
  canBlock: boolean;
};

export type ProfileContext = {
  /** Signed-in account. Never the subject unless it equals profileOwnerId. */
  viewerUserId: string;
  /** The account this screen is about. Authoritative. */
  profileOwnerId: string;
  /** Key safe to interpolate into an API path (numeric id, @handle or username). */
  lookupKey: string;
  isOwnProfile: boolean;
  permissions: ProfileViewerPermissions;
  source: ProfileEntryPoint;
  /** Presentation only. */
  displayName: string;
  username: string;
  avatarUrl: string;
};

/**
 * Route params every Profile OS destination receives.
 *
 * `sourceProfileId` is the profile the user was standing on when they tapped
 * the tile. It is normally identical to `profileOwnerId`, but it is carried
 * separately so back-navigation can return to the *source* profile rather than
 * guessing (mission section 7).
 */
export type ProfileOsRouteParams = {
  profileOwnerId: string;
  sourceProfileId: string;
  isOwnProfile: boolean;
  entryPoint: ProfileEntryPoint;
  profileLookupKey?: string;
  displayName?: string;
  username?: string;
  avatarUrl?: string;
};

/**
 * Deny-by-default. Used when a payload predates `viewer_permissions`, so an
 * older/degraded server response can never widen what a visitor sees.
 */
export const NO_PERMISSIONS: ProfileViewerPermissions = {
  canViewPublicProfile: false,
  canViewFollowerContent: false,
  canViewFriendContent: false,
  canViewPublicMedia: false,
  canViewPublicMusic: false,
  canViewPublicActivity: false,
  canViewPublicCollections: false,
  canViewPublicCommunities: false,
  canViewMarketplace: false,
  canViewBusiness: false,
  canViewEvents: false,
  canViewPublicMemories: false,
  canMessage: false,
  canReport: false,
  canBlock: false
};

/** The owner of a profile can see everything on it; that is what ownership means. */
export const OWNER_PERMISSIONS: ProfileViewerPermissions = {
  canViewPublicProfile: true,
  canViewFollowerContent: true,
  canViewFriendContent: true,
  canViewPublicMedia: true,
  canViewPublicMusic: true,
  canViewPublicActivity: true,
  canViewPublicCollections: true,
  canViewPublicCommunities: true,
  canViewMarketplace: true,
  canViewBusiness: true,
  canViewEvents: true,
  canViewPublicMemories: true,
  // An account cannot message, report or block itself.
  canMessage: false,
  canReport: false,
  canBlock: false
};

const PERMISSION_KEYS = Object.keys(NO_PERMISSIONS) as Array<keyof ProfileViewerPermissions>;

/** snake_case (server) -> camelCase (client). */
const SERVER_PERMISSION_KEYS: Record<keyof ProfileViewerPermissions, string> = {
  canViewPublicProfile: "can_view_public_profile",
  canViewFollowerContent: "can_view_follower_content",
  canViewFriendContent: "can_view_friend_content",
  canViewPublicMedia: "can_view_public_media",
  canViewPublicMusic: "can_view_public_music",
  canViewPublicActivity: "can_view_public_activity",
  canViewPublicCollections: "can_view_public_collections",
  canViewPublicCommunities: "can_view_public_communities",
  canViewMarketplace: "can_view_marketplace",
  canViewBusiness: "can_view_business",
  canViewEvents: "can_view_events",
  canViewPublicMemories: "can_view_public_memories",
  canMessage: "can_message",
  canReport: "can_report",
  canBlock: "can_block"
};

export function normalizePermissions(raw: unknown, isOwnProfile = false): ProfileViewerPermissions {
  const base = isOwnProfile ? OWNER_PERMISSIONS : NO_PERMISSIONS;
  if (!raw || typeof raw !== "object") return { ...base };
  const source = raw as Record<string, unknown>;
  const next = { ...base };
  for (const key of PERMISSION_KEYS) {
    const value = source[key] ?? source[SERVER_PERMISSION_KEYS[key]];
    if (value !== undefined) next[key] = Boolean(value);
  }
  return next;
}

type ProfileLike = {
  user_id?: string | number;
  display_name?: string;
  username?: string;
  avatar_url?: string;
  public_player_id?: string;
  canonical_profile_key?: string;
  is_self?: boolean;
  viewer_permissions?: unknown;
};

/**
 * Build the context for a profile that has finished loading.
 *
 * Ownership is decided by comparing ids, and only when both are actually known.
 * A missing viewer id must not read as "this is mine" — that is precisely the
 * failure mode the mission describes, so an unknown viewer resolves to visitor.
 */
export function buildProfileContext(input: {
  viewerUserId?: string | number | null;
  profile?: ProfileLike | null;
  target?: NativeProfileTarget | ProfileTargetInput | null;
}): ProfileContext {
  const viewerUserId = idString(input.viewerUserId);
  const profile = input.profile || null;
  const target = input.target ? asTarget(input.target) : null;

  const profileOwnerId =
    idString(profile?.user_id) ||
    idString(target?.userId) ||
    (target?.profileKey && /^\d+$/.test(target.profileKey) ? target.profileKey : "") ||
    target?.profileKey ||
    // No target at all means the screen was opened on the viewer's own profile
    // (e.g. the Profile tab), so the viewer is the subject.
    viewerUserId;

  const lookupKey =
    target?.profileKey ||
    profile?.canonical_profile_key ||
    profile?.public_player_id ||
    profile?.username ||
    profileOwnerId;

  const isOwnProfile = resolveIsOwnProfile({
    viewerUserId,
    profileOwnerId,
    hasTarget: Boolean(target),
    serverIsSelf: profile?.is_self
  });

  return {
    viewerUserId,
    profileOwnerId,
    lookupKey,
    isOwnProfile,
    permissions: normalizePermissions(profile?.viewer_permissions, isOwnProfile),
    source: PROFILE_OS_ENTRY,
    displayName: String(profile?.display_name || target?.title || ""),
    username: String(profile?.username || target?.username || ""),
    avatarUrl: String(profile?.avatar_url || "")
  };
}

/**
 * The server's `is_self` wins when present — it compared real ids with real
 * auth. The local comparison is the fallback for cached/offline payloads.
 */
function resolveIsOwnProfile(input: {
  viewerUserId: string;
  profileOwnerId: string;
  hasTarget: boolean;
  serverIsSelf?: boolean;
}): boolean {
  if (typeof input.serverIsSelf === "boolean") return input.serverIsSelf;
  // No navigation target => the Profile tab => own profile.
  if (!input.hasTarget) return true;
  if (!input.viewerUserId || !input.profileOwnerId) return false;
  return input.viewerUserId === input.profileOwnerId;
}

/** Params to hand to `navigation.navigate` for any Profile OS destination. */
export function profileOsRouteParams(context: ProfileContext, extra?: Record<string, unknown>): ProfileOsRouteParams & Record<string, unknown> {
  return {
    ...(extra || {}),
    profileOwnerId: context.profileOwnerId,
    sourceProfileId: context.profileOwnerId,
    isOwnProfile: context.isOwnProfile,
    entryPoint: PROFILE_OS_ENTRY,
    profileLookupKey: context.lookupKey,
    displayName: context.displayName,
    username: context.username,
    avatarUrl: context.avatarUrl
  };
}

/**
 * Read a destination's subject back out of its route params.
 *
 * `isOwnProfile` arriving in params is a rendering hint only — it is re-derived
 * from the ids here, because a client boolean is not trustworthy (section 8:
 * "Do not trust isOwnProfile from a client boolean alone"). The server's
 * permissions on the fetched payload remain the real gate.
 */
export function resolveRouteProfileContext(
  params: Partial<ProfileOsRouteParams> | null | undefined,
  viewerUserId?: string | number | null
): ProfileContext {
  const viewer = idString(viewerUserId);
  const routeOwnerId = idString(params?.profileOwnerId);
  // Absent params means the screen was reached outside Profile OS (a tab, a
  // deep link to a personal surface), where the viewer is legitimately the
  // subject. Present-but-different means a visitor destination.
  const profileOwnerId = routeOwnerId || viewer;
  const isOwnProfile = routeOwnerId ? Boolean(viewer) && routeOwnerId === viewer : true;

  return {
    viewerUserId: viewer,
    profileOwnerId,
    lookupKey: String(params?.profileLookupKey || routeOwnerId || viewer || ""),
    isOwnProfile,
    permissions: isOwnProfile ? { ...OWNER_PERMISSIONS } : { ...NO_PERMISSIONS },
    source: PROFILE_OS_ENTRY,
    displayName: String(params?.displayName || ""),
    username: String(params?.username || ""),
    avatarUrl: String(params?.avatarUrl || "")
  };
}

/** Fold server-authored permissions into a context once the payload arrives. */
export function withServerPermissions(context: ProfileContext, profile?: ProfileLike | null): ProfileContext {
  if (!profile) return context;
  const isOwnProfile = typeof profile.is_self === "boolean" ? profile.is_self : context.isOwnProfile;
  return {
    ...context,
    isOwnProfile,
    profileOwnerId: idString(profile.user_id) || context.profileOwnerId,
    permissions: normalizePermissions(profile.viewer_permissions, isOwnProfile),
    displayName: String(profile.display_name || context.displayName),
    username: String(profile.username || context.username),
    avatarUrl: String(profile.avatar_url || context.avatarUrl)
  };
}

/**
 * Cache key for any profile-scoped data.
 *
 * Always includes the owner id, so Roody's media and Maria's media cannot land
 * in the same bucket (mission section 6).
 */
export function profileScopedKey(namespace: string, context: Pick<ProfileContext, "profileOwnerId">): [string, string] {
  return [namespace, context.profileOwnerId || "unknown"];
}

export function profileScopedCacheId(namespace: string, context: Pick<ProfileContext, "profileOwnerId">): string {
  return profileScopedKey(namespace, context).join(":");
}

/** "Maria" -> "Maria's"; "Chris" -> "Chris'". */
export function possessive(name: string): string {
  const trimmed = (name || "").trim();
  if (!trimmed) return "";
  return /s$/i.test(trimmed) ? `${trimmed}'` : `${trimmed}'s`;
}

/** Short label for the subject, for headers and empty states. */
export function subjectName(context: Pick<ProfileContext, "displayName" | "username" | "isOwnProfile">): string {
  if (context.isOwnProfile) return "You";
  const name = (context.displayName || "").trim();
  if (name) return name.split(/\s+/)[0];
  const handle = (context.username || "").trim();
  return handle ? `@${handle}` : "This profile";
}

/**
 * Destination header title. Owner sees "My Media"; a visitor sees "Maria's
 * Media" — never "My"/"Your" on someone else's screen (mission section 4).
 */
export function destinationTitle(context: Pick<ProfileContext, "displayName" | "username" | "isOwnProfile">, noun: string): string {
  if (context.isOwnProfile) return `My ${noun}`;
  const owner = possessive(subjectName(context));
  return owner ? `${owner} ${noun}` : noun;
}

/** Empty-state copy that names the right person. */
export function emptyStateText(
  context: Pick<ProfileContext, "displayName" | "username" | "isOwnProfile">,
  ownerCopy: string,
  visitorCopy: (name: string) => string
): string {
  return context.isOwnProfile ? ownerCopy : visitorCopy(subjectName(context));
}

export const PRIVATE_CONTENT_MESSAGE = "This content is not available to you.";
export const BLOCKED_PROFILE_MESSAGE = "This profile is unavailable.";

function idString(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text = String(value).trim();
  if (!text || /^(undefined|null|false|0)$/i.test(text)) return "";
  return text;
}

function asTarget(input: NativeProfileTarget | ProfileTargetInput): NativeProfileTarget | null {
  if (input && typeof input === "object" && "cacheKey" in input && "nativePath" in input) {
    return input as NativeProfileTarget;
  }
  return resolveProfileTarget(input as ProfileTargetInput);
}
