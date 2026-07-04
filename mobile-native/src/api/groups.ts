import { readJsonCache, writeJsonCache } from "../core/cache";
import { pulseApi } from "./pulseApi";

const GROUPS_CACHE_KEY = "pulsesoc.native.groups.browse";
const groupDetailCacheKey = (slug: string) => `pulsesoc.native.groups.detail.${slug}`;

export type PulseGroupPost = {
  id: number;
  group_id?: number;
  user_id?: number;
  author_name?: string;
  title?: string;
  body?: string;
  post_type?: string;
  media_url?: string;
  thumbnail_url?: string;
  media_type?: string;
  pinned?: boolean;
  created_at?: string;
  updated_at?: string;
};

export type PulseGroup = {
  id: number;
  group_id: number;
  slug: string;
  name: string;
  description?: string;
  category?: string;
  group_type?: string;
  rules?: string;
  status?: string;
  trust_level?: string;
  featured?: boolean;
  cover_image_url?: string;
  member_count?: number;
  post_count?: number;
  viewer_role?: string;
  joined?: boolean;
  can_manage?: boolean;
  posts?: PulseGroupPost[];
  url?: string;
};

export type PulseRoom = {
  id: string;
  room_id: string;
  conversation_id?: number;
  name: string;
  title?: string;
  description?: string;
  pinned_notice?: string;
  online_count?: number;
  unread_count?: number;
  last_message?: string;
  last_message_at?: string;
  energy?: number;
  partial?: boolean;
};

export type GroupsResponse = {
  ok?: boolean;
  groups?: PulseGroup[];
  items?: PulseGroup[];
  rooms?: PulseRoom[];
  has_more?: boolean;
  next_offset?: number;
  message?: string;
};

export type GroupDetailResponse = {
  ok?: boolean;
  group?: PulseGroup;
  posts?: PulseGroupPost[];
  message?: string;
};

export async function listGroups(params: { query?: string; category?: string; limit?: number; offset?: number } = {}) {
  const query = new URLSearchParams({
    q: params.query || "",
    category: params.category || "",
    limit: String(params.limit || 40),
    offset: String(params.offset || 0)
  });
  const data = await pulseApi<GroupsResponse>(`/api/pulse/groups?${query.toString()}`);
  const groups = normalizeGroups(data.groups || data.items || []);
  const rooms = normalizeRooms(data.rooms || []);
  if (!params.offset) await cacheGroups({ ...data, groups, rooms }).catch(() => undefined);
  return { ...data, groups, items: groups, rooms, next_offset: Number(params.offset || 0) + groups.length, has_more: Boolean(data.has_more) };
}

export async function getGroupDetail(slug: string) {
  const data = await pulseApi<GroupDetailResponse>(`/api/pulse/groups/${encodeURIComponent(slug)}`);
  const group = data.group ? normalizeGroup(data.group) : undefined;
  const posts = normalizeGroupPosts(data.posts || group?.posts || []);
  const detail = { ...data, group: group ? { ...group, posts } : undefined, posts };
  if (group?.slug) await writeJsonCache(groupDetailCacheKey(group.slug), detail).catch(() => undefined);
  return detail;
}

export async function joinGroup(slug: string) {
  return pulseApi<{ ok?: boolean; joined?: boolean; pending_review?: boolean; member_count?: number; message?: string }>(
    `/api/pulse/groups/${encodeURIComponent(slug)}/join`,
    { method: "POST", body: JSON.stringify({}) }
  );
}

export async function leaveGroup(slug: string) {
  return pulseApi<{ ok?: boolean; left?: boolean; member_count?: number; message?: string }>(`/api/pulse/groups/${encodeURIComponent(slug)}/leave`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function openGroupChat(slug: string) {
  return pulseApi<{ ok?: boolean; conversation_id?: number; next_url?: string; message?: string }>(
    `/api/pulse/groups/${encodeURIComponent(slug)}/chat/open`,
    { method: "POST", body: JSON.stringify({}) }
  );
}

export async function reportGroup(slug: string, reason = "Needs review") {
  return pulseApi<{ ok?: boolean; message?: string }>(`/api/pulse/groups/${encodeURIComponent(slug)}/report`, {
    method: "POST",
    body: JSON.stringify({ reason })
  });
}

export async function listRooms() {
  const data = await pulseApi<{ ok?: boolean; rooms?: PulseRoom[]; items?: PulseRoom[] }>("/api/pulse/communications/rooms");
  return normalizeRooms(data.rooms || data.items || []);
}

export async function joinRoom(roomId: string) {
  return pulseApi<{ ok?: boolean; conversation_id?: number; next_url?: string; message?: string }>(
    `/api/pulse/messages/rooms/${encodeURIComponent(roomId)}/join`,
    { method: "POST", body: JSON.stringify({}) }
  );
}

export async function loadCachedGroups() {
  return readJsonCache<GroupsResponse>(GROUPS_CACHE_KEY, (data) => ({
    ...data,
    groups: normalizeGroups(data.groups || data.items || []),
    rooms: normalizeRooms(data.rooms || [])
  }));
}

export async function loadCachedGroupDetail(slug: string) {
  return readJsonCache<GroupDetailResponse>(groupDetailCacheKey(slug), (data) => {
    const group = data.group ? normalizeGroup(data.group) : undefined;
    return { ...data, group, posts: normalizeGroupPosts(data.posts || group?.posts || []) };
  });
}

export async function cacheGroups(data: GroupsResponse) {
  await writeJsonCache(GROUPS_CACHE_KEY, { ...data, groups: (data.groups || []).slice(0, 100), rooms: data.rooms || [] });
}

export function normalizeGroups(groups: PulseGroup[]) {
  return (groups || []).map(normalizeGroup).filter((group) => group.id > 0);
}

export function normalizeGroup(group: PulseGroup): PulseGroup {
  const id = Number(group.group_id || group.id || 0);
  return {
    ...group,
    id,
    group_id: id,
    slug: String(group.slug || id),
    name: String(group.name || "PulseSoc Group"),
    description: String(group.description || ""),
    category: String(group.category || "Community"),
    group_type: String(group.group_type || "public"),
    rules: String(group.rules || ""),
    trust_level: String(group.trust_level || "standard"),
    member_count: Number(group.member_count || 0),
    post_count: Number(group.post_count || 0),
    joined: Boolean(group.joined || group.viewer_role),
    can_manage: Boolean(group.can_manage),
    posts: normalizeGroupPosts(group.posts || []),
    url: group.url || `/pulse/groups/${group.slug || id}`
  };
}

export function normalizeRooms(rooms: PulseRoom[]) {
  return (rooms || [])
    .map((room) => ({
      ...room,
      id: String(room.room_id || room.id || ""),
      room_id: String(room.room_id || room.id || ""),
      conversation_id: Number(room.conversation_id || 0),
      name: String(room.name || room.title || "PulseSoc Room"),
      title: String(room.title || room.name || "PulseSoc Room"),
      description: String(room.description || ""),
      pinned_notice: String(room.pinned_notice || ""),
      online_count: Number(room.online_count || 0),
      unread_count: Number(room.unread_count || 0),
      energy: Number(room.energy || 0)
    }))
    .filter((room) => room.id);
}

export function normalizeGroupPosts(posts: PulseGroupPost[]) {
  return (posts || [])
    .map((post) => ({
      ...post,
      id: Number(post.id || 0),
      group_id: Number(post.group_id || 0),
      body: String(post.body || ""),
      title: String(post.title || ""),
      author_name: String(post.author_name || "PulseSoc Member"),
      post_type: String(post.post_type || "text"),
      media_url: String(post.media_url || ""),
      thumbnail_url: String(post.thumbnail_url || "")
    }))
    .filter((post) => post.id > 0);
}
