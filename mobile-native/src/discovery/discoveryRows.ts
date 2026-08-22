/**
 * The one row type and the one placement rule for Home's discovery suggestions.
 *
 * ## Why a second module instead of widening `injectAds`
 *
 * `feed/injectAds.ts` answers a narrower question — where do *sponsored* cards
 * go — and it answers it against a cadence Advertising owns. Discovery rows have
 * different eligibility, different rotation and a dismissal rule ads do not
 * have, and if both lived in one function every change to one would be a change
 * to the other's blast radius. So this module composes *after* it: `injectAds`
 * produces the post/ad rows exactly as it does today, and `injectDiscoveryRows`
 * takes that list and threads suggestions between the post groups. With the
 * feature flag off the second call never happens and Home's row list is
 * identical to what it was.
 *
 * ## Why the row type is one union rather than one type per module
 *
 * Seven modules rendered by seven `renderItem` branches is seven places to
 * forget a key, a dismissal check or an analytics call. Everything here is a
 * `DiscoveryRow` carrying a discriminated `module`, so Home's renderer has one
 * discovery branch, and adding an eighth module is a case in one `switch` that
 * TypeScript will point at if it is missing.
 *
 * ## What this module deliberately does not do
 *
 * No fetching, no flags, no navigation, no clock. Everything is a pure function
 * of its arguments, which is what lets the placement rules below be asserted as
 * arithmetic instead of inferred from a rendered screen. Flag gating happens at
 * the call site (`discovery/flags.ts`), because a module the operator turned off
 * should never even be built, let alone placed and then filtered.
 */
import type { PulseReel } from "../api/reels";
import type { FeedRow } from "../feed/injectAds";

export type DiscoveryModuleKind =
  | "reels"
  | "people"
  | "statuses"
  | "groups"
  | "creators"
  | "topics"
  | "sponsored";

/**
 * A reel that can be opened *exactly*.
 *
 * `reelId` is required and is not optional-with-a-fallback on purpose: the whole
 * point of the suggested-reels row is that the tap lands on the reel in the
 * thumbnail. A suggestion without an id has no destination, so the adapter drops
 * it rather than shipping a card that opens whatever the feed happens to return
 * first.
 */
export type ReelSuggestion = {
  reelId: number;
  title: string;
  posterUrl?: string | null;
  authorName?: string | null;
  viewCount?: number | null;
  /**
   * Playable URL for the card's muted 3–4 second preview, or absent.
   *
   * Resolved by the adapter, not by the card, for the same reason `reelId` is:
   * the view should not be able to invent a source. It is absent — rather than
   * present-and-broken — whenever the reel is not playable yet (still
   * transcoding, no media), which is what lets the card fall back to its poster
   * without first mounting a player against a URL that will fail.
   */
  previewVideoUrl?: string | null;
  /**
   * The reel exactly as the API returned it, carried for the transfer slot.
   *
   * Not render data — nothing below the card reads it. It exists because
   * `reelTransfer.ts` stages a whole `PulseReel` so the player can render the
   * tapped reel on its first frame instead of showing whatever the feed returns
   * while it looks for the right one. The row was built from a `listReels`
   * response, so the reel is already in hand; dropping it here would mean
   * re-fetching something we already had. `import type` keeps this a
   * compile-time reference, so this module still pulls in no API code.
   */
  source?: PulseReel;
};

/**
 * A person or creator.
 *
 * The destination is `profileKey`, not a user id. That is not a preference — it
 * is what the server can address: `/api/pulse/friends` builds people through
 * `pulse_person_public_payload`, which returns `username`, `public_player_id`,
 * `display_name` and `avatar_url` and **no `user_id`**. A `userId: number` field
 * here would be `undefined` for every card the real endpoint produces, so every
 * tap would resolve to nothing. `profileKey` is what `resolveProfileTarget` and
 * the `ProfileDetail` route already accept.
 *
 * `rank` is the server's own identity label. Fields the payload does not
 * contain — mutual-connection counts, follower counts — are absent rather than
 * optional-and-always-undefined, so a card cannot render a relevance reason the
 * backend never supplied.
 */
export type PersonSuggestion = {
  profileKey: string;
  username: string;
  displayName: string;
  avatarUrl?: string | null;
  publicPlayerId?: string | null;
  /** Server-computed identity label ("Creator", "Member"). Never derived client-side. */
  rank?: string | null;
  verified?: boolean;
};

export type StatusSuggestion = {
  statusId: number;
  title: string;
  authorName?: string | null;
  previewUrl?: string | null;
  seen?: boolean;
};

export type GroupSuggestion = {
  slug: string;
  name: string;
  memberCount?: number | null;
  coverUrl?: string | null;
  isMember?: boolean;
};

export type TopicSuggestion = {
  topic: string;
  label: string;
  postCount?: number | null;
};

export type SponsoredSuggestion = {
  campaignId: string;
  creativeId: string;
  headline: string;
  imageUrl?: string | null;
  destinationUrl?: string | null;
};

/**
 * A built module: a kind, its items, and the i18n key for its heading.
 *
 * `titleKey` is a key, never a string. Hardcoded copy fails the i18n gate in CI,
 * and a heading is the one part of a carousel that is guaranteed to be read.
 */
export type DiscoveryModule = { titleKey: string } & (
  | { kind: "reels"; items: ReelSuggestion[] }
  | { kind: "people"; items: PersonSuggestion[] }
  | { kind: "creators"; items: PersonSuggestion[] }
  | { kind: "statuses"; items: StatusSuggestion[] }
  | { kind: "groups"; items: GroupSuggestion[] }
  | { kind: "topics"; items: TopicSuggestion[] }
  | { kind: "sponsored"; items: SponsoredSuggestion[] }
);

export type DiscoveryRow = {
  type: "discovery";
  key: string;
  /** 0-based position among discovery rows in this feed, for analytics. */
  slot: number;
  module: DiscoveryModule;
};

/** What Home's FlatList actually renders once discovery is on. */
export type HomeRow<TPost> = FeedRow<TPost> | DiscoveryRow;

export type DiscoveryPlacementOptions = {
  /** Organic posts that must render before the first suggestion row. */
  leadIn?: number;
  /** Organic posts between suggestion rows after the first. */
  interval?: number;
  /** Hard cap on suggestion rows per feed page. */
  maxRows?: number;
  /** A module with fewer items than this is skipped: a two-card carousel reads as an error. */
  minItems?: number;
  /** Kinds the user dismissed this session. */
  dismissed?: ReadonlySet<DiscoveryModuleKind>;
  /**
   * Which module starts the rotation. Home advances this on pull-to-refresh so
   * the same kind is not pinned to the same scroll depth forever. Any integer;
   * it is reduced modulo the eligible-module count.
   */
  rotationOffset?: number;
};

/** Defaults chosen to sit *between* ad slots (`injectAds` uses leadIn 3, interval 5 on Home). */
export const DISCOVERY_LEAD_IN = 5;
export const DISCOVERY_INTERVAL = 7;
export const DISCOVERY_MIN_ITEMS = 3;
export const DISCOVERY_MAX_ROWS = 4;

/** Stable, unique, and readable in a `keyExtractor` crash log. */
export function discoveryRowKey(kind: DiscoveryModuleKind, slot: number): string {
  return `discovery:${kind}:${slot}`;
}

/**
 * Thread discovery rows through an existing post/ad row list.
 *
 * Three placement invariants, each of which has a test:
 *
 *  1. **A suggestion never splits a post from its ad.** `injectAds` puts an ad
 *     row immediately after the post it was earned by; inserting between them
 *     would read as the ad belonging to the carousel. So insertion happens after
 *     the whole post group.
 *  2. **Two suggestion rows are never adjacent.** Guaranteed structurally by
 *     counting only organic posts and requiring `interval >= 1`, not by a
 *     lookback — a lookback is the kind of check that survives a refactor as a
 *     no-op.
 *  3. **The feed is never mostly suggestions.** `maxRows` caps them per page,
 *     and modules are consumed round-robin so one kind cannot take every slot.
 *
 * Pure and deterministic: same arguments, same rows, every time.
 */
export function injectDiscoveryRows<TPost>(
  rows: FeedRow<TPost>[],
  modules: DiscoveryModule[],
  options: DiscoveryPlacementOptions = {}
): HomeRow<TPost>[] {
  const leadIn = Math.max(options.leadIn ?? DISCOVERY_LEAD_IN, 1);
  const interval = Math.max(options.interval ?? DISCOVERY_INTERVAL, 1);
  const maxRows = Math.max(options.maxRows ?? DISCOVERY_MAX_ROWS, 0);
  const minItems = Math.max(options.minItems ?? DISCOVERY_MIN_ITEMS, 1);
  const dismissed = options.dismissed;

  const eligible = modules.filter(
    (module) => module.items.length >= minItems && !dismissed?.has(module.kind)
  );

  if (eligible.length === 0 || maxRows === 0) return [...rows];

  // Normalised so a negative or huge offset still lands inside the list. `%` in
  // JS keeps the sign of the dividend, hence the second add.
  const offset = ((Math.trunc(options.rotationOffset ?? 0) % eligible.length) + eligible.length) % eligible.length;

  const out: HomeRow<TPost>[] = [];
  let organicCount = 0;
  let placed = 0;

  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index];
    out.push(row);
    if (row.type !== "post") continue;

    organicCount += 1;

    // Keep the ad that belongs to this post with it, and do not let it count
    // toward the organic cadence.
    while (index + 1 < rows.length && rows[index + 1].type !== "post") {
      index += 1;
      out.push(rows[index]);
    }

    if (placed >= maxRows) continue;
    if (organicCount < leadIn) continue;
    if ((organicCount - leadIn) % interval !== 0) continue;

    // Never place a suggestion row as the last thing in the list: a carousel
    // with nothing under it looks like the feed ended.
    if (index + 1 >= rows.length) continue;

    const module = eligible[(offset + placed) % eligible.length];
    out.push({ type: "discovery", key: discoveryRowKey(module.kind, placed), slot: placed, module });
    placed += 1;
  }

  return out;
}
