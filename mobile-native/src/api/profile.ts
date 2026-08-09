import AsyncStorage from "@react-native-async-storage/async-storage";
import { PULSE_API_BASE_URL } from "./config";
import { FeedResponse, listFeed, normalizePosts, PulsePost } from "./feed";
import { PulseApiError, pulseApi } from "./pulseApi";
import {
  NativeProfileTarget,
  ProfileTargetInput,
  profileCacheKey,
  profileLookupKey,
  profileTargetFromAuthor,
  profileWebUrlForTarget,
  resolveProfileTarget
} from "./profileTarget";

const PROFILE_CACHE_PREFIX = "pulsesoc.native.profile.";
const PROFILE_THEME_CACHE_KEY = "pulsesoc.native.profile.theme";

export type PulseProfileTheme = {
  theme_key?: string;
  accent_color?: string;
  background_style?: string;
  active?: boolean;
  layout_key?: string;
  modules?: string[];
  modules_json?: string;
  motion_level?: "subtle" | "balanced" | "reduced";
};

export type PulseProfile = {
  user_id: number;
  pulse_id?: string;
  email?: string;
  username?: string;
  display_name: string;
  full_name?: string;
  public_player_id?: string;
  avatar_url?: string;
  avatar_thumbnail_url?: string;
  cover_url?: string;
  banner_url?: string;
  bio?: string;
  social_links?: string;
  social_links_json?: string;
  expertise_tags?: string;
  expertise_tags_json?: string;
  profile_visibility?: "public" | "private";
  account_status?: string;
  profile_state?: "available" | "private" | "blocked" | "restricted" | "deactivated" | "deleted" | "not_found" | "unavailable";
  canonical_profile_key?: string;
  premium_status?: string;
  verification_status?: string;
  verified_badge?: boolean | number;
  follower_count?: number;
  following_count?: number;
  post_count?: number;
  media_count?: number;
  badges?: string[];
  theme?: PulseProfileTheme;
  viewer_follows?: boolean;
  is_self?: boolean;
  /**
   * Server-authored answer to what this viewer may see about this profile
   * owner, keyed snake_case as the API returns it. Profile OS destinations gate
   * on these rather than on a client-side ownership guess — see
   * `src/profile/profileContext.ts` and `services/profile_viewer_permissions.py`.
   */
  viewer_permissions?: Record<string, boolean>;
};

export type ProfileUpdatePayload = {
  display_name: string;
  username?: string;
  bio?: string;
  social_links?: string;
  expertise_tags?: string;
  profile_visibility?: "public" | "private";
};

export type ProfileMediaUploadInput = {
  uri: string;
  name: string;
  mimeType: string;
};

export async function getMyProfile() {
  const data = await pulseApi<{ ok?: boolean; user?: PulseProfile; items?: PulseProfile[] }>("/api/pulse/profile/me");
  const profile = normalizeProfile(data.user || data.items?.[0] || {});
  const theme = await getProfileTheme().catch(() => null);
  const next = { ...profile, theme: theme || profile.theme };
  await cacheProfile("me", next);
  return next;
}

export type ProfileErrorState = {
  title: string;
  body: string;
  status?: number;
  retryable: boolean;
  offline: boolean;
  canOpenWebFallback: boolean;
};

export async function getPublicProfile(input: ProfileTargetInput | NativeProfileTarget) {
  const target = resolveProfileTarget(input);
  const lookupKey = target ? profileLookupKey(target) : "";
  if (!target || !lookupKey) {
    throw new PulseApiError("PulseSoc could not resolve this profile target.", 400, "profile_target_unresolved");
  }
  const data = await pulseApi<{ ok?: boolean; profile?: PulseProfile }>(`/api/pulse/profile/${encodeURIComponent(lookupKey)}`);
  const profile = normalizeProfile(data.profile || {});
  await cacheProfileAliases(target, profile);
  return profile;
}

export async function updateProfile(payload: ProfileUpdatePayload) {
  const data = await pulseApi<{ ok?: boolean; message?: string } & Partial<PulseProfile>>("/api/pulse/profile/update", {
    method: "POST",
    body: JSON.stringify(payload)
  });
  const current: Partial<PulseProfile> = (await loadCachedProfile("me")) || {};
  const profile = normalizeProfile({ ...current, ...payload, ...data });
  await cacheProfile("me", profile);
  return profile;
}

export async function uploadProfileAvatar(input: ProfileMediaUploadInput) {
  const form = new FormData();
  form.append("avatar", {
    uri: input.uri,
    name: input.name,
    type: input.mimeType
  } as unknown as Blob);
  const data = await pulseApi<{ ok?: boolean; avatar_url?: string; avatar_url_cache_busted?: string; avatar_thumbnail_url?: string }>(
    "/api/pulse/profile/avatar",
    { method: "POST", body: form }
  );
  const current: Partial<PulseProfile> = (await loadCachedProfile("me")) || {};
  const profile = normalizeProfile({
    ...current,
    avatar_url: data.avatar_url_cache_busted || data.avatar_url || current.avatar_url,
    avatar_thumbnail_url: data.avatar_thumbnail_url || data.avatar_url_cache_busted || data.avatar_url || current.avatar_thumbnail_url
  });
  await cacheProfile("me", profile);
  return profile;
}

export async function uploadProfileCover(input: ProfileMediaUploadInput) {
  const form = new FormData();
  form.append("cover", {
    uri: input.uri,
    name: input.name,
    type: input.mimeType
  } as unknown as Blob);
  const data = await pulseApi<{ ok?: boolean; cover_url?: string; cover_url_cache_busted?: string; banner_url?: string }>(
    "/api/pulse/profile/cover",
    { method: "POST", body: form }
  );
  const current: Partial<PulseProfile> = (await loadCachedProfile("me")) || {};
  const profile = normalizeProfile({
    ...current,
    cover_url: data.cover_url_cache_busted || data.cover_url || data.banner_url || current.cover_url,
    banner_url: data.banner_url || data.cover_url_cache_busted || data.cover_url || current.banner_url
  });
  await cacheProfile("me", profile);
  return profile;
}

export async function removeProfileAvatar() {
  await pulseApi<{ ok?: boolean; avatar_url?: string }>("/api/pulse/profile/avatar/remove", {
    method: "POST",
    body: JSON.stringify({})
  });
  const current: Partial<PulseProfile> = (await loadCachedProfile("me")) || {};
  const profile = normalizeProfile({ ...current, avatar_url: "", avatar_thumbnail_url: "" });
  await cacheProfile("me", profile);
  return profile;
}

export async function removeProfileCover() {
  await pulseApi<{ ok?: boolean; cover_url?: string }>("/api/pulse/profile/cover/remove", {
    method: "POST",
    body: JSON.stringify({})
  });
  const current = (await loadCachedProfile("me")) || {};
  const profile = normalizeProfile({ ...current, cover_url: "", banner_url: "" });
  await cacheProfile("me", profile);
  return profile;
}

export async function getProfileTheme() {
  const data = await pulseApi<{ ok?: boolean; theme?: PulseProfileTheme }>("/api/pulse/premium/profile-theme");
  const theme = data.theme || {};
  await AsyncStorage.setItem(PROFILE_THEME_CACHE_KEY, JSON.stringify(theme));
  return theme;
}

export async function updateProfileTheme(theme: PulseProfileTheme) {
  const data = await pulseApi<{ ok?: boolean; theme_key?: string; layout_key?: string; motion_level?: PulseProfileTheme["motion_level"]; modules?: string[]; message?: string }>("/api/pulse/premium/profile-theme", {
    method: "POST",
    body: JSON.stringify({
      theme_key: theme.theme_key || "deep_space",
      accent_color: theme.accent_color || "#32e6b3",
      layout_key: theme.layout_key || "classic",
      motion_level: theme.motion_level || "balanced",
      modules: theme.modules || []
    })
  });
  const next = { ...theme, ...data, theme_key: data.theme_key || theme.theme_key };
  await AsyncStorage.setItem(PROFILE_THEME_CACHE_KEY, JSON.stringify(next));
  return next;
}

export async function toggleProfileFollow(profile: PulseProfile) {
  return pulseApi<{ ok?: boolean; following?: boolean; message?: string }>("/api/pulse/follows/toggle", {
    method: "POST",
    body: JSON.stringify({
      followed_user_id: profile.user_id,
      public_player_id: profile.public_player_id || profile.username || "",
      followed_public_player_id: profile.public_player_id || profile.username || ""
    })
  });
}

export async function listPublicProfilePosts(input: ProfileTargetInput | NativeProfileTarget, options: { limit?: number; offset?: number; mediaOnly?: boolean } = {}) {
  const target = resolveProfileTarget(input);
  const lookupKey = target?.userId
    ? String(target.userId)
    : target?.publicPlayerId || target?.username || target?.profileKey || "";
  if (!target || !lookupKey) return { ok: true, posts: [], feed: [], next_offset: 0, has_more: false };
  const fallbackLookupKey = target.publicPlayerId || target.username || target.profileKey || lookupKey;
  const fallbackToFeedProfile = async () => {
    const fallback = await listFeed({
      feed: "for_you",
      profile: fallbackLookupKey,
      limit: options.limit || 20,
      offset: options.offset || 0
    });
    const fallbackPosts = options.mediaOnly
      ? (fallback.posts || []).filter((post) => post.media?.length || post.media_assets?.length || post.attachments?.length)
      : fallback.posts || [];
    return {
      ...fallback,
      posts: fallbackPosts,
      feed: fallbackPosts,
      next_offset: Number(fallback.next_offset ?? (options.offset || 0) + fallbackPosts.length),
      has_more: Boolean(fallback.has_more)
    };
  };
  const query = new URLSearchParams();
  query.set("limit", String(options.limit || 20));
  query.set("offset", String(options.offset || 0));
  if (options.mediaOnly) query.set("media", "1");
  try {
    const data = await pulseApi<FeedResponse>(`/api/pulse/profile/${encodeURIComponent(lookupKey)}/posts?${query.toString()}`);
    const posts = normalizePosts(data.posts || data.feed || []);
    if (!posts.length && !data.has_more && fallbackLookupKey) return fallbackToFeedProfile();
    return {
      ...data,
      posts,
      next_offset: Number(data.next_offset ?? (options.offset || 0) + posts.length),
      has_more: Boolean(data.has_more)
    };
  } catch (error) {
    if (fallbackLookupKey) return fallbackToFeedProfile();
    throw error;
  }
}

export async function loadCachedProfile(cacheKey: string | NativeProfileTarget = "me") {
  const resolvedCacheKey = typeof cacheKey === "string" ? cacheKey : profileCacheKey(cacheKey);
  try {
    const cached = await AsyncStorage.getItem(`${PROFILE_CACHE_PREFIX}${resolvedCacheKey}`);
    if (!cached) return null;
    return normalizeProfile(JSON.parse(cached) as PulseProfile);
  } catch {
    await AsyncStorage.removeItem(`${PROFILE_CACHE_PREFIX}${resolvedCacheKey}`).catch(() => undefined);
    return null;
  }
}

export async function cacheProfile(cacheKey: string, profile: PulseProfile) {
  await AsyncStorage.setItem(`${PROFILE_CACHE_PREFIX}${cacheKey}`, JSON.stringify(profile));
}

export async function cacheProfileAliases(target: NativeProfileTarget, profile: PulseProfile) {
  const aliases = new Set<string>([profileCacheKey(target)]);
  if (profile.user_id) aliases.add(`user:${profile.user_id}`);
  if (profile.public_player_id) aliases.add(`public:${String(profile.public_player_id).toLowerCase()}`);
  if (profile.username) aliases.add(`username:${String(profile.username).toLowerCase()}`);
  if (target.profileKey && !/\s/.test(target.profileKey)) aliases.add(`public:${target.profileKey.toLowerCase()}`);
  await Promise.all(Array.from(aliases).filter(Boolean).map((key) => cacheProfile(key, profile)));
}

export function normalizeProfile(input: Partial<PulseProfile>): PulseProfile {
  const profile = input || {};
  const display = profile.display_name || profile.full_name || profile.username || profile.public_player_id || "PulseSoc member";
  return {
    ...profile,
    user_id: Number(profile.user_id || 0),
    pulse_id: String(profile.pulse_id || "").toUpperCase(),
    display_name: display,
    username: String(profile.username || "").replace(/^@/, ""),
    avatar_url: absoluteProfileUrl(profile.avatar_url || profile.avatar_thumbnail_url || ""),
    avatar_thumbnail_url: absoluteProfileUrl(profile.avatar_thumbnail_url || profile.avatar_url || ""),
    cover_url: absoluteProfileUrl(profile.cover_url || profile.banner_url || ""),
    banner_url: absoluteProfileUrl(profile.banner_url || profile.cover_url || ""),
    bio: profile.bio || "",
    social_links: profile.social_links || profile.social_links_json || "",
    expertise_tags: profile.expertise_tags || profile.expertise_tags_json || "",
    profile_visibility: profile.profile_visibility === "private" ? "private" : "public",
    account_status: profile.account_status || "active",
    profile_state: profile.profile_state || "available",
    canonical_profile_key: profile.canonical_profile_key || profile.public_player_id || profile.username || String(profile.user_id || ""),
    follower_count: Number(profile.follower_count || 0),
    following_count: Number(profile.following_count || 0),
    post_count: Number(profile.post_count || 0),
    media_count: Number(profile.media_count || 0),
    badges: profile.badges || [],
    theme: normalizeTheme(profile.theme || {}),
    viewer_follows: Boolean(profile.viewer_follows),
    is_self: Boolean(profile.is_self)
  };
}

function normalizeTheme(theme: PulseProfileTheme): PulseProfileTheme {
  let modules = theme.modules || [];
  if (!modules.length && theme.modules_json) {
    try {
      const parsed = JSON.parse(theme.modules_json);
      if (Array.isArray(parsed)) modules = parsed.map(String);
    } catch {
      modules = [];
    }
  }
  return {
    ...theme,
    theme_key: theme.theme_key || "deep_space",
    accent_color: theme.accent_color || "#32e6b3",
    layout_key: theme.layout_key || "classic",
    motion_level: theme.motion_level || "balanced",
    modules
  };
}

export function profileKeyFromPost(post: PulsePost) {
  return profileTargetFromPost(post)?.profileKey || "";
}

export function profileTargetFromPost(post: PulsePost) {
  return profileTargetFromAuthor(post.author as Record<string, unknown> | undefined, post as unknown as Record<string, unknown>);
}

export function profileWebUrl(profileKey?: ProfileTargetInput | NativeProfileTarget) {
  return profileKey ? profileWebUrlForTarget(profileKey) : `${PULSE_API_BASE_URL}/pulse/profile`;
}

export function profileErrorState(error: unknown): ProfileErrorState {
  if (error instanceof PulseApiError) {
    if (error.status === 401) {
      return {
        title: "Session expired",
        body: "Sign in again to open this PulseSoc profile.",
        status: error.status,
        retryable: true,
        offline: false,
        canOpenWebFallback: false
      };
    }
    if (error.status === 403) {
      return {
        title: "Private or restricted profile",
        body: "This PulseSoc profile exists, but your account does not currently have permission to view it.",
        status: error.status,
        retryable: false,
        offline: false,
        canOpenWebFallback: false
      };
    }
    if (error.status === 404) {
      return {
        title: "Profile not found",
        body: "PulseSoc could not match this canonical profile identity. Try again from search or open the web profile as a fallback.",
        status: error.status,
        retryable: false,
        offline: false,
        canOpenWebFallback: true
      };
    }
    if (error.status === 410) {
      return {
        title: "Account inactive",
        body: "This PulseSoc account is deactivated or deleted and cannot be opened natively.",
        status: error.status,
        retryable: false,
        offline: false,
        canOpenWebFallback: false
      };
    }
    if (error.status === 429) {
      return {
        title: "Profile lookup is cooling down",
        body: "PulseSoc is rate-limiting this profile lookup. Wait a moment and retry.",
        status: error.status,
        retryable: true,
        offline: false,
        canOpenWebFallback: false
      };
    }
    if (error.status >= 500 || error.code === "request_unreachable") {
      return {
        title: error.code === "request_unreachable" ? "Connection issue" : "Profile service temporarily unavailable",
        body: "The native profile route could not reach PulseSoc. Retry without losing your place.",
        status: error.status,
        retryable: true,
        offline: error.code === "request_unreachable",
        canOpenWebFallback: true
      };
    }
  }
  return {
    title: "Profile could not load",
    body: "PulseSoc could not load this profile through the native route. Retry the request.",
    retryable: true,
    offline: false,
    canOpenWebFallback: true
  };
}

function absoluteProfileUrl(url: string) {
  if (!url) return "";
  if (/^https?:\/\//i.test(url)) return url;
  if (url.startsWith("/")) return `${PULSE_API_BASE_URL}${url}`;
  return `${PULSE_API_BASE_URL}/${url}`;
}
