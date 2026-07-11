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

export type PulseGroupMember = {
  id: string;
  user_id?: number;
  public_player_id?: string;
  display_name: string;
  username?: string;
  avatar_url?: string;
  role: string;
  presence?: string;
  verified?: boolean;
  trust_state?: string;
  status?: string;
  joined_at?: string;
};

export type PulseGroupInvitation = {
  id: string;
  invitation_id?: string;
  user_id?: number;
  display_name: string;
  username?: string;
  avatar_url?: string;
  role?: string;
  status: string;
  created_at?: string;
  expires_at?: string;
};

export type PulseGroupAsset = {
  id: string;
  post_id?: number;
  message_id?: number;
  kind: "photo" | "video" | "file" | "audio" | "link" | "unknown";
  title: string;
  url?: string;
  thumbnail_url?: string;
  file_name?: string;
  file_size?: number;
  created_at?: string;
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
  owner_name?: string;
  notification_state?: string;
  privacy?: string;
  members?: PulseGroupMember[];
  invitations?: PulseGroupInvitation[];
  membership_requests?: PulseGroupInvitation[];
  media?: PulseGroupAsset[];
  files?: PulseGroupAsset[];
  links?: PulseGroupAsset[];
  posts?: PulseGroupPost[];
  url?: string;
};

export type PulseRoomParticipant = {
  id: string;
  user_id?: number;
  display_name: string;
  username?: string;
  avatar_url?: string;
  role: string;
  presence?: string;
  provider_state?: string;
};

export type PulseRoom = {
  id: string;
  room_id: string;
  conversation_id?: number;
  conversation_type?: string;
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
  provider?: string;
  provider_state?: string;
  room_type?: string;
  privacy?: string;
  host_name?: string;
  current_user_role?: string;
  participants?: PulseRoomParticipant[];
  activity?: PulseGroupAsset[];
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
    owner_name: String(group.owner_name || ""),
    notification_state: String(group.notification_state || ""),
    privacy: String(group.privacy || group.group_type || "public"),
    members: normalizeGroupMembers(group.members || []),
    invitations: normalizeGroupInvitations(group.invitations || []),
    membership_requests: normalizeGroupInvitations(group.membership_requests || []),
    media: normalizeGroupAssets(group.media || deriveAssetsFromPosts(group.posts || [], "media")),
    files: normalizeGroupAssets(group.files || []),
    links: normalizeGroupAssets(group.links || []),
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
      energy: Number(room.energy || 0),
      provider: String(room.provider || ""),
      provider_state: String(room.provider_state || (room.partial ? "provider_boundary" : "available")),
      room_type: String(room.room_type || room.conversation_type || "room"),
      privacy: String(room.privacy || "member"),
      host_name: String(room.host_name || ""),
      current_user_role: String(room.current_user_role || ""),
      participants: normalizeRoomParticipants(room.participants || []),
      activity: normalizeGroupAssets(room.activity || [])
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

export function normalizeGroupMembers(members: PulseGroupMember[]) {
  return (members || [])
    .map((member) => ({
      ...member,
      id: String(member.id || member.user_id || member.public_player_id || ""),
      user_id: Number(member.user_id || 0),
      display_name: String(member.display_name || member.username || "PulseSoc Member"),
      username: String(member.username || ""),
      avatar_url: String(member.avatar_url || ""),
      role: String(member.role || "member"),
      presence: String(member.presence || ""),
      trust_state: String(member.trust_state || ""),
      status: String(member.status || "active"),
      joined_at: String(member.joined_at || "")
    }))
    .filter((member) => member.id);
}

export function normalizeGroupInvitations(invitations: PulseGroupInvitation[]) {
  return (invitations || [])
    .map((invite) => ({
      ...invite,
      id: String(invite.id || invite.invitation_id || invite.user_id || ""),
      invitation_id: String(invite.invitation_id || invite.id || ""),
      user_id: Number(invite.user_id || 0),
      display_name: String(invite.display_name || invite.username || "PulseSoc Member"),
      username: String(invite.username || ""),
      avatar_url: String(invite.avatar_url || ""),
      role: String(invite.role || "member"),
      status: String(invite.status || "pending"),
      created_at: String(invite.created_at || ""),
      expires_at: String(invite.expires_at || "")
    }))
    .filter((invite) => invite.id);
}

export function normalizeRoomParticipants(participants: PulseRoomParticipant[]) {
  return (participants || [])
    .map((participant) => ({
      ...participant,
      id: String(participant.id || participant.user_id || ""),
      user_id: Number(participant.user_id || 0),
      display_name: String(participant.display_name || participant.username || "PulseSoc Member"),
      username: String(participant.username || ""),
      avatar_url: String(participant.avatar_url || ""),
      role: String(participant.role || "participant"),
      presence: String(participant.presence || ""),
      provider_state: String(participant.provider_state || "")
    }))
    .filter((participant) => participant.id);
}

export function normalizeGroupAssets(assets: PulseGroupAsset[]) {
  return (assets || [])
    .map((asset) => ({
      ...asset,
      id: String(asset.id || asset.post_id || asset.message_id || asset.url || ""),
      post_id: Number(asset.post_id || 0),
      message_id: Number(asset.message_id || 0),
      kind: normalizeAssetKind(asset.kind),
      title: String(asset.title || asset.file_name || asset.url || "Group asset"),
      url: String(asset.url || ""),
      thumbnail_url: String(asset.thumbnail_url || ""),
      file_name: String(asset.file_name || ""),
      file_size: Number(asset.file_size || 0),
      created_at: String(asset.created_at || "")
    }))
    .filter((asset) => asset.id);
}

function deriveAssetsFromPosts(posts: PulseGroupPost[], mode: "media") {
  if (mode !== "media") return [];
  return (posts || [])
    .filter((post) => post.media_url || post.thumbnail_url)
    .map((post) => ({
      id: `post-${post.id}`,
      post_id: post.id,
      kind: normalizeAssetKind(post.media_type || post.post_type),
      title: post.title || post.body || "Group media",
      url: post.media_url,
      thumbnail_url: post.thumbnail_url,
      created_at: post.created_at
    }));
}

function normalizeAssetKind(value?: string): PulseGroupAsset["kind"] {
  const kind = String(value || "").toLowerCase();
  if (kind.includes("image") || kind.includes("photo")) return "photo";
  if (kind.includes("video") || kind.includes("reel")) return "video";
  if (kind.includes("audio") || kind.includes("voice")) return "audio";
  if (kind.includes("file") || kind.includes("document") || kind.includes("pdf")) return "file";
  if (kind.includes("link") || kind.includes("url")) return "link";
  return "unknown";
}
