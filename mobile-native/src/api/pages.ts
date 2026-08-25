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
  videos_count?: number;
  /** Marketplace seller behind the shop/merch/menu tab; 0 when unlinked. */
  shop_seller_id?: number;
  /** Server-decided tab set for this page type. Render ONLY these. */
  tabs: string[];
  /** Which optional modules have real backing data. The server hides unbacked
   * tabs from the public and keeps them for the team as setup prompts. */
  modules?: Record<string, boolean>;
  /**
   * Whether this presence's operations continue into Business OS, decided by
   * the server from `BUSINESS_PAGE_TYPES`. Optional because an older server
   * does not send it, and a missing field must read as "no" — offering a door
   * onto nothing is worse than a shorter card.
   */
  business_os_capable?: boolean;
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

/**
 * One person on a presence's team, as the server actually sends them.
 *
 * These field names are not cosmetic. This type previously declared
 * `username`/`display_name`; the server has always sent `name`/`handle`. Both
 * were optional, so TypeScript was satisfied, the render fell through to its
 * `Member ${user_id}` fallback, and every team member in the app displayed as
 * a number. Nothing errored — which is why it survived. Renaming a field here
 * without renaming it in `list_members` reproduces exactly that.
 *
 * The `can_*` flags are decided server-side from the same permission table the
 * mutating calls check. The client must not re-derive them from `role`: an
 * offer the server refuses is worse than no offer.
 */
export type PageMember = {
  user_id: number;
  name: string;
  handle: string;
  avatar_url: string;
  role: PageRole;
  status: string;
  invited_by?: number | null;
  invite_expires_at?: string | null;
  since?: string | null;
  is_owner?: boolean;
  is_you?: boolean;
  can_change_role?: boolean;
  can_remove?: boolean;
  can_receive_ownership?: boolean;
};

export type PageTeam = {
  page_id: number;
  role: PageRole;
  owner_user_id: number;
  can_manage_members: boolean;
  can_transfer_ownership: boolean;
  /** Server-supplied: OWNER is deliberately absent. Never hardcode this list. */
  assignable_roles: PageRole[];
  /** Server-supplied literal the owner must type to confirm a transfer. */
  transfer_confirm_phrase: string;
  members: PageMember[];
};

export type PageLink = { link_type: string; ref_id: string; created_at?: string };

/**
 * One thing a presence could be connected to — a shop, ad account, community
 * or music catalogue the member (or the page's owner) actually holds.
 *
 * `label` is what the member recognises it by. `ref_id` is carried so the
 * connect call can name it, and is never something a member has to know or
 * type: a raw id in a text box is both unusable and the shape that made
 * connecting *other people's* resources so easy before the server started
 * checking.
 */
export type PageLinkOption = { ref_id: string; label: string };

export type PageLinkSlot = {
  link_type: string;
  label: string;
  /** The page permission the server requires to change this connection. */
  permission: string;
  /** Whether this caller's role may change it. Decided server-side. */
  can_manage: boolean;
  /** "" when nothing is connected. */
  connected_ref_id: string;
  /** Empty when `can_manage` is false — withheld, not merely disabled. */
  options: PageLinkOption[];
};

export type PageLinkOptions = { page_id: number; role: PageRole; links: PageLinkSlot[] };

/**
 * Growth windows are measured server-side from real follow/post timestamps —
 * never estimated. Completeness is derived from actual profile fields and is
 * management-only: it never appears in a public payload.
 *
 * These are the raw counts. Nothing renders them directly: the hub draws
 * `PageOverview`, which the server builds from exactly this data and which
 * decides — once, server-side — which numbers are presentable, what a window
 * is called, and when a delta exists at all. Formatting these here instead
 * would put that judgement in a second place, and the two would drift.
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

/**
 * One area of the management surface, as the server decided it.
 *
 * Three separate facts, which the client must not collapse into one:
 *
 *  - **absence** — a section this page type does not have is not in the array
 *    at all. There is no disabled shop on a media page; there is no shop. The
 *    page type never reaches the client as a decision, so nothing here should
 *    ever be greyed out on the strength of it.
 *  - **`permitted`** — whether *this caller's role* may act here, read from the
 *    same permission table the mutating endpoints check. Re-deriving this from
 *    `capabilities` client-side is how a screen drifts into offering buttons
 *    that 403.
 *  - **`ready`** — whether anything is behind it yet. A section that is not
 *    ready is still shown, with `setup` naming the single missing thing, so the
 *    team can see what to fill in. Empty is a state; hidden is a dead end.
 *
 * `count` is present only where a real number was measured. A section without
 * one has no number — not zero, and never an estimate.
 */
export type PageSection = {
  key: string;
  label: string;
  hint: string;
  permission: string;
  permitted: boolean;
  ready: boolean;
  /** Empty when `ready`. The one missing thing, in words, when not. */
  setup: string;
  count?: number;
};

/**
 * One number on the Overview, and the proof that it was counted.
 *
 * `value` is a total measured from rows. Zero is a result: a presence with no
 * followers has none, and suppressing the metric until it flatters would make
 * it mean "at least one".
 *
 * `delta` and `window` travel together and are present only where the server
 * measured a window. `delta` is the count of things that happened *inside*
 * `window`, not a rate and not a projection — which is why `window` must be
 * rendered next to it. A `delta` of 0 is a measurement, so test for the key,
 * never for truthiness.
 */
export type PageOverviewMetric = {
  key: string;
  label: string;
  value: number;
  delta?: number;
  window?: string;
};

/**
 * The Overview section's contents, decided server-side.
 *
 * Nothing here is modelled, projected or estimated. Reach and engagement have
 * no source wired, so they are absent and `note` says so rather than a
 * plausible number standing in for them.
 *
 * `status` and `verification` arrive as words. They used to be rendered as the
 * raw column values — "Status: ACTIVE · unverified" — which is a database row
 * read aloud.
 *
 * `pending` is the labels of sections this caller may act on that have nothing
 * behind them yet. It comes from the same `sections` array the tiles do, so it
 * cannot name work that is not offered or hide work that is.
 */
export type PageOverview = {
  status: string;
  verification: string;
  metrics: PageOverviewMetric[];
  pending: string[];
  completeness_percent: number;
  note: string;
};

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
  /**
   * Optional because an older server does not send it. The hub then shows no
   * sections rather than falling back to a guessed set — a management surface
   * assembled client-side is precisely what this replaced.
   */
  sections?: PageSection[];
  /**
   * Optional for the same reason, and handled the same way: an older server
   * means no Overview block, not an Overview assembled from whatever the client
   * happens to hold. Summing things locally is how a screen starts reporting a
   * number nobody measured.
   */
  overview?: PageOverview;
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

/**
 * `pageId` excludes that page from the uniqueness check. Without it, a page
 * being edited collides with itself and its own handle reports as "taken by
 * another page" — so an owner cannot save any change to a form that also
 * carries the handle they already have. The server gates the exclusion on
 * `edit_page`, so it cannot be used to probe another page's handle.
 */
export async function checkPageHandle(handle: string, pageId?: number): Promise<HandleCheck> {
  const scope = pageId ? `&page_id=${pageId}` : "";
  const data = await pulseApi<{ ok: boolean } & HandleCheck>(
    `/api/pages/handle-check?handle=${encodeURIComponent(handle)}${scope}`
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

/**
 * The wire shape of `/api/pages/:id/manage`. The server builds it as
 * `public_view(...)` and then merges the management fields into that same
 * dict, so `role`, `capabilities`, `links`, `members`, `analytics`,
 * `completeness`, `sections` and `overview` arrive nested inside `page` — NOT
 * beside it.
 */
type PageManageWire = PulsePage & Omit<PageManageView, "page">;

/**
 * Reading those fields off the top level of the response returns `undefined`
 * for every one of them, which is how the whole management surface came to be
 * invisible to the people who own the page: `capabilities` was always `[]` and
 * `role === "OWNER"` was always false, so the owner status controls,
 * verification request, analytics, completeness meter, team list, Advertising,
 * Marketplace and Payments never rendered for anybody.
 *
 * Both of those read fail-closed, which is why nothing looked broken — it just
 * looked empty. The normalization below keeps that property and adds no
 * defaults that would grant anything: an absent `capabilities` stays an empty
 * list rather than becoming a guess at what the caller may do.
 *
 * A field the server sends and this function does not name is not a type error
 * — it survives into the rest element, lands on `page`, and reads as
 * `undefined` at the name the screens use. `sections` was added server-side and
 * missed here, and the hub, which renders one tile per section, drew none. So
 * every management field the server sends is destructured explicitly, and
 * `pagesManageView.test.ts` asserts each one arrives; a fixture built from a
 * real response is what turns an omission back into a failing test.
 */
export async function getPageManageView(pageId: number): Promise<PageManageView> {
  const data = await pulseApi<{ ok: boolean; page: PageManageWire }>(`/api/pages/${pageId}/manage`);
  const {
    role,
    capabilities,
    owner_user_id,
    phone,
    links,
    members,
    analytics,
    completeness,
    sections,
    overview,
    ...page
  } = data.page || ({} as PageManageWire);
  return {
    page,
    role,
    capabilities: capabilities || [],
    owner_user_id: Number(owner_user_id || 0),
    phone: phone || "",
    links: links || [],
    members,
    analytics,
    completeness,
    // Passed through as sent, `undefined` included. An older server means the
    // hub renders no sections and no Overview, which is the honest outcome;
    // defaulting to `[]` here would be indistinguishable from a server that
    // genuinely offers none, and defaulting the Overview to a zeroed object
    // would put unmeasured numbers on screen.
    sections,
    overview
  };
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

/**
 * The roster plus what this caller may do to it, in one read.
 *
 * Same endpoint as `listPageMembers` — the server answers both questions at
 * once so a screen never has to infer permission from a role name.
 */
export async function getPageTeam(pageId: number): Promise<PageTeam> {
  const data = await pulseApi<{ ok: boolean } & PageTeam>(`/api/pages/${pageId}/members`);
  return {
    page_id: Number(data.page_id || pageId),
    role: data.role,
    owner_user_id: Number(data.owner_user_id || 0),
    can_manage_members: Boolean(data.can_manage_members),
    can_transfer_ownership: Boolean(data.can_transfer_ownership),
    assignable_roles: data.assignable_roles || [],
    transfer_confirm_phrase: data.transfer_confirm_phrase || "",
    members: data.members || []
  };
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

export type PageInvite = {
  /** The credential. Only ever returned to the person it was issued to. */
  token: string;
  role: PageRole;
  expires_at: string;
  expired: boolean;
  invited_at: string;
  page_id: number;
  page_name: string;
  page_handle: string;
  page_avatar_url: string;
  page_type: PageType;
  invited_by_name: string;
};

/**
 * The invites waiting on you.
 *
 * `invitePageMember` returns the token to the *inviter*, and nothing is pushed
 * or mailed to the invitee — so without this read, joining a team meant someone
 * pasting a secret to you by hand. Scoped server-side to the caller; there is
 * no page id to pass, deliberately.
 */
export async function listMyPageInvites(): Promise<PageInvite[]> {
  const data = await pulseApi<{ ok: boolean; invites: PageInvite[] }>("/api/pages/invites");
  return data.invites || [];
}

export async function acceptPageInvite(token: string) {
  return pulseApi<{ ok: boolean; membership: { page_id: number; role: PageRole } }>(
    "/api/pages/invites/accept",
    { method: "POST", body: JSON.stringify({ token }) }
  );
}

/**
 * Refuse an invite. Removal is gated on `manage_members`, which an invitee does
 * not have — so without this the only way out of an unwanted invite is to
 * accept it and ask to be removed.
 */
export async function declinePageInvite(token: string) {
  return pulseApi<{ ok: boolean; page_id: number; status: string }>(
    "/api/pages/invites/decline",
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

export async function listPagePosts(
  pageId: number,
  params: { limit?: number; offset?: number; kind?: "videos" } = {}
) {
  const limit = params.limit || 20;
  const offset = params.offset || 0;
  const kind = params.kind ? `&kind=${params.kind}` : "";
  const data = await pulseApi<{
    ok: boolean;
    posts: PulsePost[];
    has_more?: boolean;
    next_offset?: number;
  }>(`/api/pages/${pageId}/posts?limit=${limit}&offset=${offset}${kind}`);
  return {
    posts: (data.posts || []).map((post) => normalizePost(post)),
    has_more: Boolean(data.has_more),
    next_offset: Number(data.next_offset ?? offset + (data.posts || []).length)
  };
}

export type PageTrack = {
  id: string;
  title: string;
  artist: string;
  genre?: string;
  cover_art_url?: string;
  audio_url?: string;
  duration_seconds?: number;
};

/**
 * Tracks for an artist presence, read lazily when the Music tab opens. The
 * records live in the canonical music catalogue; the presence only stores a
 * pointer, so an unlinked presence returns an empty list rather than a guess.
 */
export async function listPageMusic(pageId: number, limit = 24) {
  const data = await pulseApi<{
    ok: boolean;
    artist?: string;
    tracks?: PageTrack[];
    linked?: boolean;
  }>(`/api/pages/${pageId}/music?limit=${limit}`);
  return {
    artist: data.artist || "",
    tracks: data.tracks || [],
    linked: Boolean(data.linked)
  };
}

/**
 * One upcoming date, in the shape a visitor is allowed to see it.
 *
 * Deliberately narrower than the record Business OS stores. There is no
 * organiser id, no owning business id, no attendee list and no sales figure —
 * a tier reports `sold_out` rather than how many are left, because "how well
 * is this selling" is the organiser's business and not the audience's. The
 * server builds this from an allowlist, so a column added to the events table
 * later stays invisible here until somebody decides it is public.
 */
export type PageEventTier = {
  ticket_type_id: string;
  name: string;
  price_cents: number;
  sold_out: boolean;
};

export type PageEvent = {
  event_id: string;
  title: string;
  description?: string;
  venue?: string;
  starts_at?: string;
  ends_at?: string;
  status?: string;
  currency?: string;
  ticket_types?: PageEventTier[];
};

/**
 * Upcoming dates for a presence, read lazily when the Events tab opens.
 *
 * `enabled` and `linked` come back separately and mean different things.
 * `enabled: false` is the events domain being switched off for this
 * environment — nobody can fix that from the app, so the empty state must not
 * ask them to. `linked: false` is this presence not having been pointed at the
 * business that runs its dates, which the owner *can* fix and should be
 * offered. Collapsing them into one "no events" would send half the owners who
 * see it to do work that would not help.
 */
export async function listPageEvents(pageId: number, limit = 12) {
  const data = await pulseApi<{
    ok: boolean;
    enabled?: boolean;
    linked?: boolean;
    events?: PageEvent[];
  }>(`/api/pages/${pageId}/events?limit=${limit}`);
  return {
    enabled: Boolean(data.enabled),
    linked: Boolean(data.linked),
    events: data.events || []
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

/**
 * The connectable inventory for this presence. Options are a convenience, not
 * an authorization: the server re-derives entitlement when the connection is
 * actually made, so a stale option fails at `setPageLink` rather than
 * succeeding on the strength of having been offered.
 */
export async function getPageLinkOptions(pageId: number): Promise<PageLinkOptions> {
  const data = await pulseApi<{ ok: boolean } & PageLinkOptions>(`/api/pages/${pageId}/link-options`);
  return { page_id: Number(data.page_id || pageId), role: data.role, links: data.links || [] };
}

export async function setPageLink(pageId: number, linkType: string, refId: string) {
  return pulseApi<{ ok: boolean; link: PageLink }>(`/api/pages/${pageId}/links`, {
    method: "POST",
    body: JSON.stringify({ link_type: linkType, ref_id: refId })
  });
}

/**
 * Point this presence at nothing of this kind.
 *
 * One URL serves three acts here — read, connect, disconnect — separated only
 * by the verb, so the subject of the write goes in the same place for all
 * three. A query string for this one verb would be a second convention on a
 * route that already has to be read carefully.
 *
 * The server also accepts `?type=` on DELETE, because a DELETE body has no
 * defined semantics in HTTP and an intermediary is within its rights to drop
 * it. That is a fallback for something going wrong in transit, not a second
 * interface — this client always sends the body.
 */
export async function clearPageLink(pageId: number, linkType: string) {
  return pulseApi<{ ok: boolean; link: PageLink }>(`/api/pages/${pageId}/links`, {
    method: "DELETE",
    body: JSON.stringify({ link_type: linkType })
  });
}

/** Human-readable label for a page type, used across page surfaces. */
export function pageTypeLabel(pageType?: string) {
  const text = String(pageType || "OTHER").replace(/_/g, " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * A role name is a wire constant; this is how it reads to a person.
 *
 * Sentence case, not title case: "Content manager" is a description of a job,
 * "Content Manager" reads like a product feature. Derived rather than mapped so
 * a role the server adds still renders as words instead of ADVERTISING_MANAGER.
 *
 * Lives here rather than in a screen because the team screen and the invite
 * inbox both name roles, and a second copy is a second thing to get wrong.
 */
export function pageRoleLabel(role?: string) {
  const words = String(role || "").split("_").join(" ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** What a role can actually do, in the terms of the thing being handed over. */
export const PAGE_ROLE_SUMMARY: Record<string, string> = {
  OWNER: "Full control, including ownership and deletion.",
  ADMIN: "Everything except transferring ownership.",
  MANAGER: "Edit the page, post, and manage connections.",
  CONTENT_MANAGER: "Post and manage content. No settings or team changes.",
  ADVERTISING_MANAGER: "Run campaigns from the connected ad account.",
  MARKETPLACE_MANAGER: "Manage the connected shop and its listings.",
  ANALYST: "Read-only. Sees insights, changes nothing."
};
