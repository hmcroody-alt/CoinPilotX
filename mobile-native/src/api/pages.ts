import { pulseApi } from "./pulseApi";
import type { PulsePost } from "./feed";
import { normalizePost } from "./feed";

/**
 * Page OS client — the ONE canonical page system (artist, business,
 * organization pages all speak this API). PERSON ≠ PAGE ≠ STORE: a page is an
 * identity a signed-in user acts as, never a second login.
 *
 * Server truth lives in `services/pulsesoc_pages.py`; this file only mirrors
 * its public shapes. Anything management-only (members, links, analytics)
 * comes exclusively from `/manage` and is typed separately from `PulsePage`
 * so a public surface cannot compile against private fields.
 */

export const PAGE_TYPES = [
  "ARTIST",
  "CREATOR",
  "PUBLIC_FIGURE",
  "BUSINESS",
  "BRAND",
  "STORE",
  "RESTAURANT",
  "PROFESSIONAL_SERVICE",
  "LOCAL_BUSINESS",
  "NONPROFIT",
  "ORGANIZATION",
  "MEDIA",
  "SPORTS_TEAM",
  "VENUE",
  "EDUCATION",
  "OTHER"
] as const;
export type PageType = (typeof PAGE_TYPES)[number];

export type PageStatus = "ACTIVE" | "PAUSED" | "UNPUBLISHED" | "DEACTIVATED";
export type PageRole =
  | "OWNER"
  | "ADMIN"
  | "MANAGER"
  | "CONTENT_MANAGER"
  | "ADVERTISING_MANAGER"
  | "MARKETPLACE_MANAGER"
  | "ANALYST";

/** The server's public_view shape — safe to render anywhere. */
export type PulsePage = {
  id: number;
  page_type: PageType;
  category: string;
  subcategory: string;
  name: string;
  handle: string;
  avatar_url: string;
  cover_url: string;
  description: string;
  genre: string;
  website: string;
  email: string;
  location: string;
  hours: Record<string, string>;
  status: PageStatus;
  verification_status: string;
  verified: boolean;
  followers_count: number;
  posts_count: number;
  /** Server-decided tab set for this page type. Render ONLY these. */
  tabs: string[];
  created_at?: string;
  viewer?: { role: PageRole | null; following: boolean };
  /** Present only on "my pages" rows. */
  role?: PageRole;
  can_post?: boolean;
};

export type PageIdentity = {
  kind: "personal" | "page";
  id: number;
  name: string;
  handle: string;
  avatar_url: string;
  page_type?: PageType;
  role?: PageRole;
  verified?: boolean;
};

export type PageIdentities = { personal: PageIdentity; pages: PageIdentity[] };

export type PageMember = {
  user_id: number;
  role: PageRole;
  status: string;
  username?: string;
  display_name?: string;
  avatar_url?: string;
  invite_expires_at?: string;
};

export type PageLink = { link_type: string; ref_id: string; created_at?: string };

/**
 * Growth windows are measured server-side from real follow/post timestamps —
 * never estimated. Completeness is derived from actual profile fields and is
 * management-only: it never appears in a public payload.
 */
export type PageAnalytics = {
  followers: number;
  posts: number;
  team_members: number;
  followers_7d?: number;
  followers_30d?: number;
  posts_30d?: number;
  note?: string;
};

export type PageCompletenessItem = { key: string; label: string; done: boolean };
export type PageCompleteness = { percent: number; items: PageCompletenessItem[] };

export type PageManageView = {
  page: PulsePage;
  role: PageRole;
  capabilities: string[];
  owner_user_id: number;
  phone?: string;
  links: PageLink[];
  members?: PageMember[];
  analytics?: PageAnalytics;
  completeness?: PageCompleteness;
};

export type HandleCheck = { candidate: string; handle: string; available: boolean; reason: string };

export type CreatePagePayload = {
  page_type: PageType;
  name: string;
  handle: string;
  category?: string;
  subcategory?: string;
  description?: string;
  genre?: string;
  email?: string;
  phone?: string;
  website?: string;
  location?: string;
  avatar_url?: string;
  cover_url?: string;
  /** The owner-confirmation step of the creation flow. Required by the server. */
  confirm_owner: boolean;
};

export async function listMyPages() {
  const data = await pulseApi<{ ok: boolean; pages: PulsePage[] }>("/api/pages");
  return data.pages || [];
}

export async function searchPages(query: string, limit = 20) {
  const data = await pulseApi<{ ok: boolean; pages: PulsePage[] }>(
    `/api/pages?q=${encodeURIComponent(query)}&limit=${limit}`
  );
  return data.pages || [];
}

export async function listPageIdentities(): Promise<PageIdentities> {
  const data = await pulseApi<{ ok: boolean } & PageIdentities>("/api/pages/identities");
  return { personal: data.personal, pages: data.pages || [] };
}

export async function checkPageHandle(handle: string): Promise<HandleCheck> {
  const data = await pulseApi<{ ok: boolean } & HandleCheck>(
    `/api/pages/handle-check?handle=${encodeURIComponent(handle)}`
  );
  return data;
}

export async function createPage(payload: CreatePagePayload) {
  const data = await pulseApi<{ ok: boolean; page: PulsePage; message?: string }>("/api/pages", {
    method: "POST",
    body: JSON.stringify(payload)
  });
  return data.page;
}

export async function getPageByHandle(handle: string) {
  const clean = handle.replace(/^@+/, "");
  const data = await pulseApi<{ ok: boolean; page: PulsePage }>(
    `/api/pages/by-handle/${encodeURIComponent(clean)}`
  );
  return data.page;
}

export async function getPage(pageId: number) {
  const data = await pulseApi<{ ok: boolean; page: PulsePage }>(`/api/pages/${pageId}`);
  return data.page;
}

export async function updatePage(pageId: number, patch: Partial<CreatePagePayload>) {
  const data = await pulseApi<{ ok: boolean; page: PulsePage }>(`/api/pages/${pageId}`, {
    method: "PATCH",
    body: JSON.stringify(patch)
  });
  return data.page;
}

export async function getPageManageView(pageId: number) {
  const data = await pulseApi<{ ok: boolean } & PageManageView>(`/api/pages/${pageId}/manage`);
  return data;
}

export async function setPageStatus(pageId: number, status: PageStatus) {
  const data = await pulseApi<{ ok: boolean; page: PulsePage }>(`/api/pages/${pageId}/status`, {
    method: "POST",
    body: JSON.stringify({ status })
  });
  return data.page;
}

export async function requestPageVerification(pageId: number) {
  return pulseApi<{ ok: boolean; verification_status: string; message?: string }>(
    `/api/pages/${pageId}/verification`,
    { method: "POST", body: JSON.stringify({}) }
  );
}

export async function listPageMembers(pageId: number) {
  const data = await pulseApi<{ ok: boolean; members: PageMember[] }>(`/api/pages/${pageId}/members`);
  return data.members || [];
}

export async function invitePageMember(
  pageId: number,
  target: { user_id?: number; handle?: string },
  role: PageRole
) {
  return pulseApi<{ ok: boolean; invite: { token?: string; role: PageRole; expires_at?: string } }>(
    `/api/pages/${pageId}/members`,
    { method: "POST", body: JSON.stringify({ ...target, role }) }
  );
}

export async function acceptPageInvite(token: string) {
  return pulseApi<{ ok: boolean; membership: { page_id: number; role: PageRole } }>(
    "/api/pages/invites/accept",
    { method: "POST", body: JSON.stringify({ token }) }
  );
}

export async function changePageMemberRole(pageId: number, memberUserId: number, role: PageRole) {
  return pulseApi<{ ok: boolean }>(`/api/pages/${pageId}/members/${memberUserId}`, {
    method: "PATCH",
    body: JSON.stringify({ role })
  });
}

export async function removePageMember(pageId: number, memberUserId: number) {
  return pulseApi<{ ok: boolean }>(`/api/pages/${pageId}/members/${memberUserId}`, {
    method: "DELETE"
  });
}

/**
 * Ownership transfer — server requires the OWNER role AND the literal
 * confirmation phrase. There is no client-side shortcut, by design.
 */
export async function transferPageOwnership(pageId: number, newOwnerUserId: number, confirm: string) {
  return pulseApi<{ ok: boolean; message?: string }>(`/api/pages/${pageId}/transfer`, {
    method: "POST",
    body: JSON.stringify({ new_owner_user_id: newOwnerUserId, confirm })
  });
}

export async function togglePageFollow(pageId: number) {
  return pulseApi<{ ok: boolean; following: boolean; followers_count: number }>(
    `/api/pages/${pageId}/follow`,
    { method: "POST", body: JSON.stringify({}) }
  );
}

export async function listPagePosts(pageId: number, params: { limit?: number; offset?: number } = {}) {
  const limit = params.limit || 20;
  const offset = params.offset || 0;
  const data = await pulseApi<{
    ok: boolean;
    posts: PulsePost[];
    has_more?: boolean;
    next_offset?: number;
  }>(`/api/pages/${pageId}/posts?limit=${limit}&offset=${offset}`);
  return {
    posts: (data.posts || []).map((post) => normalizePost(post)),
    has_more: Boolean(data.has_more),
    next_offset: Number(data.next_offset ?? offset + (data.posts || []).length)
  };
}

/**
 * Publish a post AS the page. Goes through `/api/pages/:id/posts`, which runs
 * the role check server-side and then hands off to the ONE canonical content
 * system (`pulse_feed_engine.create_post` with `page_id`) — page posts land in
 * the same `pulse_posts` table and the same feed as personal posts.
 */
export async function createPagePost(
  pageId: number,
  payload: {
    body?: string;
    title?: string;
    post_type?: string;
    visibility?: string;
    media_ids?: number[];
    tags?: string[];
  }
) {
  return pulseApi<{ ok: boolean; post_id?: number; message?: string }>(`/api/pages/${pageId}/posts`, {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function listPageLinks(pageId: number, type?: string) {
  const suffix = type ? `?type=${encodeURIComponent(type)}` : "";
  const data = await pulseApi<{ ok: boolean; links: PageLink[] }>(`/api/pages/${pageId}/links${suffix}`);
  return data.links || [];
}

export async function setPageLink(pageId: number, linkType: string, refId: string) {
  return pulseApi<{ ok: boolean; link: PageLink }>(`/api/pages/${pageId}/links`, {
    method: "POST",
    body: JSON.stringify({ link_type: linkType, ref_id: refId })
  });
}

/** Human-readable label for a page type, used across page surfaces. */
export function pageTypeLabel(pageType?: string) {
  const text = String(pageType || "OTHER").replace(/_/g, " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}
