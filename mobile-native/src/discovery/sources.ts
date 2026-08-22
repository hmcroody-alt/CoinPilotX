/**
 * Where Home's discovery modules get their data.
 *
 * `discoveryRows.ts` decides *where* a suggestion row goes; this module decides
 * *what is in it*. They are separate because placement is pure arithmetic that
 * can be asserted directly, while sourcing is network I/O with a different
 * failure mode for every endpoint.
 *
 * ## Three rules this file exists to enforce
 *
 * 1. **A card is only built when it has a destination.** Every adapter drops
 *    items missing the identifier its route needs — a reel with no id, a group
 *    with no slug, a person with no profile key. A card that cannot open
 *    anything is worse than an absent card, because the user blames the tap.
 * 2. **One dead endpoint is not a dead feed.** Each source is awaited
 *    independently through `settle`, so Groups being down removes the Groups row
 *    and nothing else. Failures are returned, not swallowed, so §14 can report a
 *    recommendation failure reason instead of silence.
 * 3. **Nothing is invented.** Where PulseSoc has no source — creators, topics,
 *    sponsored carousels — this module returns nothing at all rather than
 *    synthesising a plausible-looking card. §9 says not to invent inactive
 *    destinations and §10 says to leave sponsored disabled without an approved
 *    source; both are honoured by absence, which is why there is no adapter for
 *    them below.
 */
import { listReels, reelIsPlayable, reelVideoUrl } from "../api/reels";
import { listStatuses } from "../api/status";
import { listGroups } from "../api/groups";
import { listSuggestedPeople } from "../api/friends";
import {
  DiscoveryModule,
  DiscoveryModuleKind,
  GroupSuggestion,
  PersonSuggestion,
  ReelSuggestion,
  StatusSuggestion
} from "./discoveryRows";
import {
  discoveryGroupsEnabled,
  discoveryPeopleEnabled,
  discoveryReelsEnabled,
  discoveryStatusesEnabled
} from "./flags";

/** How many cards to request per module. Enough to fill a carousel, bounded for §16. */
const SOURCE_LIMIT = 12;

export type DiscoveryLoadOptions = {
  /** Reel ids already visible in the Home feed, so a suggestion never duplicates one. */
  excludeReelIds?: ReadonlySet<number>;
  /** Status ids already on Home's status rail — the rail is not a discovery surface. */
  excludeStatusIds?: ReadonlySet<number>;
};

export type DiscoveryLoadResult = {
  modules: DiscoveryModule[];
  /** kind → reason, for analytics. Empty when everything that was enabled loaded. */
  failures: Partial<Record<DiscoveryModuleKind, string>>;
};

function failureReason(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return "unknown_error";
}

/** Await a source without letting its rejection cancel the others. */
async function settle<T>(
  kind: DiscoveryModuleKind,
  enabled: boolean,
  build: () => Promise<T | null>
): Promise<{ kind: DiscoveryModuleKind; value: T | null; error?: string }> {
  if (!enabled) return { kind, value: null };
  try {
    return { kind, value: await build() };
  } catch (error) {
    return { kind, value: null, error: failureReason(error) };
  }
}

/**
 * Reels.
 *
 * `reel_id` is preferred over `id` because the Reels route and the transfer slot
 * are both keyed on it. Anything unplayable is excluded up front (§13): removed,
 * deleted, moderated or still transcoding. A card that opens a "this reel is
 * unavailable" screen is a broken recommendation, not a graceful one.
 */
async function buildReels(exclude?: ReadonlySet<number>): Promise<DiscoveryModule | null> {
  const { reels } = await listReels({ lane: "for_you", limit: SOURCE_LIMIT });
  const seen = new Set<number>();
  const items: ReelSuggestion[] = [];

  for (const reel of reels || []) {
    const reelId = Number(reel.reel_id || reel.id || 0);
    if (!reelId || seen.has(reelId) || exclude?.has(reelId)) continue;
    if (reel.is_removed || reel.deleted_at) continue;
    if (reel.moderation_status && reel.moderation_status !== "approved") continue;
    if (reel.availability && reel.availability !== "available") continue;

    seen.add(reelId);
    items.push({
      reelId,
      title: String(reel.title || reel.caption || reel.body || "").trim(),
      posterUrl: reel.poster_url || reel.media?.[0]?.thumbnail_url || null,
      authorName: reel.author?.display_name || reel.author?.name || reel.author?.username || null,
      viewCount: typeof reel.view_count === "number" ? reel.view_count : null,
      // Resolved here so the card cannot mount a player against a reel that is
      // still transcoding: `reelIsPlayable` is the same gate the Reels player
      // uses, and a null result means the card shows its poster and nothing else.
      previewVideoUrl: reelIsPlayable(reel) ? reelVideoUrl(reel) || null : null,
      // Carried, not re-fetched: the player seeds its first frame from this.
      source: reel
    });
  }

  return items.length ? { kind: "reels", titleKey: "social:feed.discovery.reelsTitle", items } : null;
}

/**
 * People you may know.
 *
 * `listSuggestedPeople` has already removed existing friends, accounts the
 * viewer follows, and pending requests, because only the raw graph response can
 * do that subtraction.
 */
async function buildPeople(): Promise<DiscoveryModule | null> {
  const people = await listSuggestedPeople();
  const items: PersonSuggestion[] = people.map((person) => ({
    profileKey: person.profileKey,
    username: person.username,
    displayName: person.displayName,
    avatarUrl: person.avatarUrl || null,
    publicPlayerId: person.publicPlayerId || null,
    rank: person.rank || null,
    verified: person.premiumVerified
  }));

  return items.length ? { kind: "people", titleKey: "social:feed.discovery.peopleTitle", items } : null;
}

/**
 * Statuses.
 *
 * Home already renders a status rail, so anything on it is excluded — a
 * suggestion the user can see two rows up is noise. Expired statuses are dropped
 * against the wall clock: the rail payload can outlive its own contents, and §6
 * requires that an expired status must not open.
 */
async function buildStatuses(exclude?: ReadonlySet<number>): Promise<DiscoveryModule | null> {
  const { items: statuses } = await listStatuses({ lane: "for_you" });
  const now = Date.now();
  const seen = new Set<number>();
  const items: StatusSuggestion[] = [];

  for (const status of statuses || []) {
    const statusId = Number(status.status_id || status.id || 0);
    if (!statusId || seen.has(statusId) || exclude?.has(statusId)) continue;

    if (status.expires_at) {
      const expiresAt = Date.parse(status.expires_at);
      if (Number.isFinite(expiresAt) && expiresAt <= now) continue;
    }
    if (status.fixture_state && status.fixture_state !== "ready") continue;
    if (status.visibility === "private") continue;

    seen.add(statusId);
    items.push({
      statusId,
      title: String(status.body || "").trim(),
      authorName: status.author?.display_name || status.author_name || null,
      previewUrl: status.media?.[0]?.thumbnail_url || status.author_avatar_url || null,
      seen: Boolean(status.viewed)
    });
  }

  return items.length ? { kind: "statuses", titleKey: "social:feed.discovery.statusesTitle", items } : null;
}

/**
 * Groups.
 *
 * Already-joined groups are excluded (§7). `slug` is required because
 * `GroupDetail` is keyed on it — a group with an id but no slug has no route.
 */
async function buildGroups(): Promise<DiscoveryModule | null> {
  const { groups } = await listGroups({ limit: SOURCE_LIMIT });
  const seen = new Set<string>();
  const items: GroupSuggestion[] = [];

  for (const group of groups || []) {
    const slug = String(group.slug || "").trim();
    if (!slug || seen.has(slug)) continue;
    if (group.joined) continue;
    if (group.status && group.status !== "active") continue;

    seen.add(slug);
    items.push({
      slug,
      name: String(group.name || "").trim() || slug,
      memberCount: typeof group.member_count === "number" ? group.member_count : null,
      coverUrl: group.cover_image_url || null,
      isMember: Boolean(group.joined)
    });
  }

  return items.length ? { kind: "groups", titleKey: "social:feed.discovery.groupsTitle", items } : null;
}

/**
 * Build every enabled module, in parallel, tolerating individual failures.
 *
 * The returned order is the order modules are offered to the placement engine;
 * rotation then decides which one lands in which slot, so this is a preference,
 * not a guarantee of position.
 */
export async function loadDiscoveryModules(options: DiscoveryLoadOptions = {}): Promise<DiscoveryLoadResult> {
  const settled = await Promise.all([
    settle("reels", discoveryReelsEnabled(), () => buildReels(options.excludeReelIds)),
    settle("people", discoveryPeopleEnabled(), () => buildPeople()),
    settle("statuses", discoveryStatusesEnabled(), () => buildStatuses(options.excludeStatusIds)),
    settle("groups", discoveryGroupsEnabled(), () => buildGroups())
  ]);

  const modules: DiscoveryModule[] = [];
  const failures: Partial<Record<DiscoveryModuleKind, string>> = {};

  for (const result of settled) {
    if (result.error) failures[result.kind] = result.error;
    if (result.value) modules.push(result.value);
  }

  return { modules, failures };
}
