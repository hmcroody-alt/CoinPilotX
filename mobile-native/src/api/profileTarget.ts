import { PULSE_API_BASE_URL } from "./config";

export type ProfileTargetInput =
  | string
  | number
  | null
  | undefined
  | {
      profileKey?: string | number;
      userId?: string | number;
      user_id?: string | number;
      id?: string | number;
      profileId?: string | number;
      profile_id?: string | number;
      publicPlayerId?: string;
      public_player_id?: string;
      publicPulseId?: string;
      public_pulse_id?: string;
      username?: string;
      handle?: string;
      url?: string;
      profile_url?: string;
      web_url?: string;
      target_url?: string;
      title?: string;
      display_name?: string;
      name?: string;
      source?: string;
      type?: string;
      author_public_player_id?: string;
      author_username?: string;
      author_name?: string;
      author?: Record<string, unknown>;
      user?: Record<string, unknown>;
      seller_public_player_id?: string;
      seller_username?: string;
      seller_name?: string;
      creator_name?: string;
    };

export type NativeProfileTarget = {
  profileKey: string;
  userId?: number;
  profileId?: string;
  publicPlayerId?: string;
  publicPulseId?: string;
  username?: string;
  title?: string;
  source?: string;
  cacheKey: string;
  webPath: string;
};

export function resolveProfileTarget(input: ProfileTargetInput): NativeProfileTarget | null {
  if (input === null || input === undefined) return null;
  if (typeof input === "string" || typeof input === "number") {
    const key = sanitizeProfileKey(String(input));
    return key ? buildProfileTarget({ profileKey: key, source: "string" }) : null;
  }

  const nestedAuthor = input.author || input.user || {};
  const userId = positiveNumber(
    input.userId ??
    input.user_id ??
    nestedValue(nestedAuthor, "user_id") ??
    nestedValue(nestedAuthor, "id") ??
    input.id
  );
  const publicPlayerId = sanitizeProfileKey(
    stringValue(input.publicPlayerId) ||
    stringValue(input.public_player_id) ||
    stringValue(nestedValue(nestedAuthor, "public_player_id")) ||
    stringValue(input.author_public_player_id) ||
    stringValue(input.seller_public_player_id)
  );
  const publicPulseId = sanitizeProfileKey(stringValue(input.publicPulseId) || stringValue(input.public_pulse_id));
  const username = sanitizeProfileKey(
    stringValue(input.username) ||
    stringValue(nestedValue(nestedAuthor, "username")) ||
    stringValue(nestedValue(nestedAuthor, "handle")) ||
    stringValue(input.handle) ||
    stringValue(input.author_username) ||
    stringValue(input.seller_username)
  );
  const routeKey = sanitizeProfileKey(stringValue(input.profileKey));
  const profileId = sanitizeProfileKey(stringValue(input.profileId) || stringValue(input.profile_id));
  const urlKey = profileKeyFromUrl(
    stringValue(input.url) ||
    stringValue(input.profile_url) ||
    stringValue(input.web_url) ||
    stringValue(input.target_url)
  );

  const profileKey = userId
    ? String(userId)
    : publicPlayerId || publicPulseId || username || routeKey || profileId || urlKey;
  if (!profileKey) return null;

  return buildProfileTarget({
    profileKey,
    userId: userId || undefined,
    profileId: profileId || undefined,
    publicPlayerId: publicPlayerId || publicPulseId || undefined,
    publicPulseId: publicPulseId ? `@${publicPulseId}` : publicPlayerId ? `@${publicPlayerId}` : undefined,
    username: username || undefined,
    title: stringValue(input.title) || stringValue(input.display_name) || stringValue(input.name) || stringValue(input.author_name) || stringValue(input.seller_name) || stringValue(input.creator_name) || stringValue(nestedValue(nestedAuthor, "display_name")) || undefined,
    source: stringValue(input.source) || stringValue(input.type) || "profile"
  });
}

export function profileTargetFromUrl(url: string) {
  return resolveProfileTarget({ url, source: "route" });
}

export function profileTargetFromAuthor(author?: Record<string, unknown>, fallback?: Record<string, unknown>) {
  return resolveProfileTarget({
    ...(fallback || {}),
    ...(author || {}),
    author,
    userId: stringValue(nestedValue(author, "user_id") || nestedValue(author, "id") || nestedValue(fallback, "user_id") || nestedValue(fallback, "id")),
    public_player_id: stringValue(nestedValue(author, "public_player_id")) || stringValue(nestedValue(fallback, "author_public_player_id")) || stringValue(nestedValue(fallback, "public_player_id")),
    username: stringValue(nestedValue(author, "username")) || stringValue(nestedValue(author, "handle")) || stringValue(nestedValue(fallback, "author_username")) || stringValue(nestedValue(fallback, "username")),
    profile_url: stringValue(nestedValue(author, "profile_url")) || stringValue(nestedValue(fallback, "profile_url")),
    display_name: stringValue(nestedValue(author, "display_name")) || stringValue(nestedValue(author, "name")) || stringValue(nestedValue(fallback, "author_name")) || stringValue(nestedValue(fallback, "display_name"))
  });
}

export function profileNavigationParams(target: NativeProfileTarget | null, title = "Profile") {
  if (!target) return undefined;
  return {
    profileKey: target.profileKey,
    userId: target.userId,
    profileId: target.profileId,
    publicPlayerId: target.publicPlayerId,
    username: target.username,
    title: target.title || title,
    source: target.source
  };
}

export function profileLookupKey(input: ProfileTargetInput | NativeProfileTarget) {
  const target = isNativeProfileTarget(input) ? input : resolveProfileTarget(input);
  return target?.profileKey || "";
}

export function profileCacheKey(input: ProfileTargetInput | NativeProfileTarget) {
  const target = isNativeProfileTarget(input) ? input : resolveProfileTarget(input);
  if (!target) return "";
  if (target.userId) return `user:${target.userId}`;
  if (/^\d+$/.test(target.profileKey)) return `user:${target.profileKey}`;
  if (target.publicPlayerId) return `public:${target.publicPlayerId.toLowerCase()}`;
  if (target.username) return `username:${target.username.toLowerCase()}`;
  return `public:${target.profileKey.toLowerCase()}`;
}

export function profileWebPath(input?: ProfileTargetInput | NativeProfileTarget) {
  if (!input) return "/pulse/profile";
  const target = isNativeProfileTarget(input) ? input : resolveProfileTarget(input);
  if (!target) return "/pulse/profile";
  const handle = target.publicPlayerId || target.username || (/^\d+$/.test(target.profileKey) ? "" : target.profileKey);
  if (handle) return `/pulse/@${encodeURIComponent(handle)}`;
  if (target.userId || /^\d+$/.test(target.profileKey)) return `/pulse/id/${encodeURIComponent(String(target.userId || target.profileKey))}`;
  return `/pulse/profile/${encodeURIComponent(target.profileKey)}`;
}

export function profileWebUrlForTarget(input?: ProfileTargetInput | NativeProfileTarget) {
  return `${PULSE_API_BASE_URL}${profileWebPath(input)}`;
}

function buildProfileTarget(input: Omit<NativeProfileTarget, "cacheKey" | "webPath">): NativeProfileTarget {
  const target = {
    ...input,
    profileKey: sanitizeProfileKey(input.profileKey) || "",
    publicPlayerId: sanitizeProfileKey(input.publicPlayerId || ""),
    username: sanitizeProfileKey(input.username || "")
  };
  const cacheKey = target.userId ? `user:${target.userId}` : target.publicPlayerId ? `public:${target.publicPlayerId.toLowerCase()}` : target.username ? `username:${target.username.toLowerCase()}` : `public:${target.profileKey.toLowerCase()}`;
  const webPath = profileWebPath({ ...target, cacheKey, webPath: "" });
  return { ...target, cacheKey, webPath };
}

function profileKeyFromUrl(raw: string): string {
  if (!raw) return "";
  let value = raw.trim();
  const lowered = value.toLowerCase();
  try {
    if (lowered.startsWith("http://") || lowered.startsWith("https://")) {
      const parsed = new URL(value);
      if (!["pulsesoc.com", "www.pulsesoc.com"].includes(parsed.hostname.toLowerCase())) return "";
      value = `${parsed.pathname}${parsed.search}${parsed.hash}`;
    } else if (lowered.startsWith("pulsesoc://")) {
      value = `/${value.slice("pulsesoc://".length).replace(/^\/+/, "")}`;
    } else if (lowered.startsWith("pulse://")) {
      value = `/${value.slice("pulse://".length).replace(/^\/+/, "")}`;
    }
  } catch {
    return "";
  }
  const match =
    value.match(/^\/pulse\/@([^/?#]+)/) ||
    value.match(/^\/pulse\/u\/([^/?#]+)/) ||
    value.match(/^\/pulse\/id\/([^/?#]+)/) ||
    value.match(/^\/pulse\/profile\/([^/?#]+)/);
  if (!match?.[1]) return "";
  const key = decodeProfileSegment(match[1]);
  if (key.toLowerCase() === "edit") return "";
  return sanitizeProfileKey(key);
}

function sanitizeProfileKey(value: string | number | undefined): string {
  let key = stringValue(value).trim();
  if (!key) return "";
  key = decodeProfileSegment(key);
  key = key.replace(/^@+/, "").trim();
  if (!key || /\s/.test(key)) return "";
  if (key.includes("/") || key.includes("\\") || key.includes("?") || key.includes("#")) {
    const fromUrl: string = profileKeyFromUrl(key);
    if (fromUrl) return fromUrl;
    return "";
  }
  if (/^(undefined|null|false|true)$/i.test(key)) return "";
  return key.slice(0, 160);
}

function decodeProfileSegment(value: string) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function positiveNumber(value: unknown) {
  const num = Number(value || 0);
  return Number.isFinite(num) && num > 0 ? num : 0;
}

function stringValue(value: unknown) {
  return value === null || value === undefined ? "" : String(value);
}

function nestedValue(obj: Record<string, unknown> | undefined, key: string) {
  return obj && Object.prototype.hasOwnProperty.call(obj, key) ? obj[key] : undefined;
}

function isNativeProfileTarget(input: ProfileTargetInput | NativeProfileTarget): input is NativeProfileTarget {
  return Boolean(input && typeof input === "object" && "cacheKey" in input && "webPath" in input);
}
