/**
 * The commerce-inbox model layer.
 *
 * The Messages surface (Business "Sections" card #6) is not a generic chat list.
 * Every row is about a money object — an offer, an order, a pickup, a listing
 * question, a completed sale — and the inbox surfaces that object on the row via
 * a context chip so a seller can triage money-relevant threads at a glance. This
 * module owns everything the screen renders that is derived rather than drawn:
 * the unified row model, the context-chip data contract and its batched resolver,
 * filter counts, the reply-time stat, away-mode state, the inbox tool counts and
 * the expiring-offer banner.
 *
 * ## Backend truth (read before trusting anything here)
 *
 * The live conversation surface is `/api/pulse/communications/v2/conversations`
 * (see `./messenger`). It returns conversations — participants, last message,
 * unread count, timestamps, presence, typing — and NOTHING about commerce. There
 * is no join from a conversation to an offer / order / listing anywhere in the
 * live API, and (per `./marketplaceOffers`) there is no offers backend at all.
 *
 * So the defining feature of this screen — the context chip — has no server-side
 * source today. Two honest consequences, both enforced here rather than faked:
 *
 *   1. A chip renders from a REAL association when the conversation actually
 *      carries one (`conversation.commerce_link`, future-proofed). That path is
 *      always live.
 *   2. For design review, a deterministic MOCK association can be turned on with
 *      `EXPO_PUBLIC_MESSAGES_MOCK_CHIPS`. Off by default. When off and no real
 *      association exists, the row simply has no chip — never a fabricated one.
 *
 * Everything else with no live field (reply-time stat, away mode, saved-reply /
 * spam / blocked counts, starred/archived state) is tagged in
 * `INBOX_MOCK_DATA_GAPS` and gated or clearly marked, never silently invented.
 *
 * ## Real-time
 *
 * There is no websocket. `subscribeConversationUpdates` (in `./messenger`) is an
 * in-process listener fired when a thread writes the local cache; the thread view
 * polls. The inbox therefore rides that listener for in-place updates and offers
 * pull-to-refresh as the manual path. Typing and presence exist as conversation
 * fields but have no live push, so both are flag-gated — off by default the inbox
 * is a correct pull-to-refresh list.
 */

import {
  MessengerConversation,
  listConversations,
  loadCachedConversations
} from "./messenger";
import { ConversationDomain, conversationSplitEnabled } from "./conversationDomain";
import {
  MarketplaceOffer,
  MARKETPLACE_OFFERS_ENABLED,
  offerExpiresAt,
  resolveExpiry
} from "./marketplaceOffers";
import { MESSAGES_AVATAR_GRADIENTS, MessagesChipKind } from "../theme/messagesLight";

/* ------------------------------------------------------------------ *
 * Feature flags — all read at call time so tests can toggle them.
 * ------------------------------------------------------------------ */

function flagOn(name: string): boolean {
  const raw = String(process.env[name] || "").toLowerCase();
  return raw === "1" || raw === "true" || raw === "on" || raw === "yes";
}

/** Typing indicators. No live push exists, so off = static "typing…" for AT only. */
export const messagesTypingEnabled = () => flagOn("EXPO_PUBLIC_MESSAGES_TYPING");
/** Presence dots. Only shown when the product exposes mutual presence. */
export const messagesPresenceEnabled = () => flagOn("EXPO_PUBLIC_MESSAGES_PRESENCE");
/** The new-message row-reorder animation. Off = list still updates, no motion. */
export const messagesRealtimeReorderEnabled = () => flagOn("EXPO_PUBLIC_MESSAGES_REALTIME");
/** Deterministic MOCK commerce associations for design review only. */
export const messagesMockChipsEnabled = () => flagOn("EXPO_PUBLIC_MESSAGES_MOCK_CHIPS");
/** Away mode / auto-reply. No live field, so the toggle is optimistic-local only. */
export const messagesAwayModeEnabled = () => flagOn("EXPO_PUBLIC_MESSAGES_AWAY");
/**
 * The "under {X} keeps your fast-responder badge" incentive framing. No badge /
 * ranking system was found in the app, so this is OFF: the stat is shown without
 * the unsourced ranking claim. Flip only when a real badge rule exists.
 */
export const replyBadgeIncentiveEnabled = () => flagOn("EXPO_PUBLIC_MESSAGES_REPLY_BADGE");

/* ------------------------------------------------------------------ *
 * MOCK-DATA ledger — every field with no live backend source.
 * ------------------------------------------------------------------ */

export type InboxMockGap = {
  field: string;
  backendWork: string;
  gatedBy: string;
};

export const INBOX_MOCK_DATA_GAPS: InboxMockGap[] = [
  {
    field: "conversation → offer/order/listing association (context chip)",
    backendWork:
      "Join conversations to their commerce object server-side (offer_id / order_id / listing_id on the conversation, or a resolver endpoint).",
    gatedBy: "real association always renders; MOCK behind EXPO_PUBLIC_MESSAGES_MOCK_CHIPS"
  },
  {
    field: "avg reply time",
    backendWork: "Compute per-seller median first-response latency and return it on the inbox payload.",
    gatedBy: "MOCK-DATA (shown only when a real stat is present)"
  },
  {
    field: "fast-responder badge / ranking rule",
    backendWork: "Define the badge threshold rule; only then show the incentive framing.",
    gatedBy: "EXPO_PUBLIC_MESSAGES_REPLY_BADGE (off — no rule found)"
  },
  {
    field: "away mode / auto-reply state + text",
    backendWork: "Persist away flag + auto-reply template; apply it server-side to incoming threads.",
    gatedBy: "EXPO_PUBLIC_MESSAGES_AWAY (optimistic-local only until then)"
  },
  {
    field: "saved-reply templates count",
    backendWork: "Store saved replies per seller; return the count.",
    gatedBy: "MOCK-DATA (count hidden when unknown)"
  },
  {
    field: "spam / blocked filtered counts",
    backendWork: "Expose spam-classified + blocked thread counts.",
    gatedBy: "MOCK-DATA (count hidden when unknown)"
  },
  {
    field: "starred / archived conversation state",
    backendWork: "Persist starred + archived per conversation and return on the list.",
    gatedBy: "best-effort from conversation fields; absent = filter shows empty honestly"
  },
  {
    field: "offer expiry TTL (72h)",
    backendWork: "Confirm the real offer TTL once an offers backend exists.",
    gatedBy: "marketplaceOffers OFFER_TTL_HOURS (proposed); banner gated by MARKETPLACE_OFFERS_ENABLED"
  },
  {
    field: "conversation_domain (SOCIAL / MARKETPLACE / STORE_SUPPORT / DISPUTE / EVENT)",
    backendWork:
      "Stamp every conversation with its domain at creation and return it on the conversation list, so the split stops being a client-side guess.",
    gatedBy:
      "derived at the read boundary by deriveConversationDomain; explicit field → conversation_type → commerce association → SOCIAL fallback. Split behaviour behind EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT"
  },
  {
    field: "returns / return requests",
    backendWork:
      "There is no returns object anywhere in the app — no model, no route, no state machine. Build one, then link its threads.",
    gatedBy: "Returns filter ships with an honest empty state and can never be non-empty until then"
  },
  {
    field: "store-support vs. dispute distinction on a thread",
    backendWork:
      "Distinguish a support question from a contested order; today only an explicit conversation type can tell them apart.",
    gatedBy: "derivation reads conversation_type only; unlabelled commerce threads land in Marketplace"
  }
];

export const INBOX_MOCK_DATA_GAP_COUNT = 11;

/* ------------------------------------------------------------------ *
 * Deterministic avatar colour
 * ------------------------------------------------------------------ */

/**
 * A person's avatar gradient, derived from a stable key (user/public id) so the
 * same buyer is the same colour everywhere and across sessions. Pure hash → one
 * of the five palette gradients; never random.
 */
export function avatarGradientFor(key: string | number | undefined) {
  const s = String(key ?? "");
  let hash = 0;
  for (let i = 0; i < s.length; i += 1) {
    hash = (hash * 31 + s.charCodeAt(i)) | 0;
  }
  const index = Math.abs(hash) % MESSAGES_AVATAR_GRADIENTS.length;
  return MESSAGES_AVATAR_GRADIENTS[index];
}

/* ------------------------------------------------------------------ *
 * Context-chip data contract  (reused by the thread-view pinned card)
 * ------------------------------------------------------------------ */

/**
 * The normalized commerce association a conversation is about. This is the shape
 * a backend join would return; the resolver produces it from a real field or a
 * MOCK fixture. Kept free of React/navigation so the thread-view mission can reuse
 * it verbatim for its pinned context card.
 */
export type CommerceLink =
  | { kind: "offer"; offer: MarketplaceOffer }
  | {
      kind: "order";
      orderId: number;
      /** e.g. "ships today 5pm", "in transit", "delivered Tue". */
      statusLine: string;
    }
  | {
      kind: "pickup";
      orderId?: number;
      day: string;
      time: string;
      item: string;
      amountMinor: number;
      currency?: string;
    }
  | {
      kind: "question";
      listingId?: number;
      listing: string;
      priceMinor: number;
      currency?: string;
      /** The linked listing sold since the question — saves a dead reply. */
      sold?: boolean;
    }
  | {
      kind: "completed";
      orderId?: number;
      item: string;
      /** Buyer's rating 1..5 when present. */
      ratingStars?: number;
    };

/** A navigation intent that deep-links to the OBJECT, not the thread. */
export type ContextChipTarget = {
  screen: string;
  params?: Record<string, unknown>;
};

/** What the ContextChip component renders + where its tap goes. */
export type ContextChipData = {
  kind: MessagesChipKind;
  /** The single ellipsizing line, key facts joined by " · ". */
  line: string;
  /** Full spoken form for assistive tech. */
  a11yLabel: string;
  /** Deep-link to the object; null when the object has no reachable screen. */
  target: ContextChipTarget | null;
};

/* ------------------------------------------------------------------ *
 * Money + time formatting (display only)
 * ------------------------------------------------------------------ */

/** Cents → "$95" or "$95.50". Never used to compute money, only to show it. */
export function formatMinor(amountMinor: number, currency = "USD"): string {
  const sign = currency === "USD" ? "$" : "";
  const whole = amountMinor / 100;
  const text = Number.isInteger(whole) ? String(whole) : whole.toFixed(2);
  return `${sign}${text}`;
}

/** Milliseconds remaining → compact "5h", "2d", "30m", or "now" past zero. */
export function formatRemaining(ms: number): string {
  if (ms <= 0) return "now";
  const minutes = Math.floor(ms / 60000);
  if (minutes < 60) return `${Math.max(1, minutes)}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}d`;
}

function stars(n: number): string {
  const count = Math.max(0, Math.min(5, Math.round(n)));
  return "★★★★★".slice(0, count) + "☆☆☆☆☆".slice(0, 5 - count);
}

/* ------------------------------------------------------------------ *
 * Chip builder — one CommerceLink → one ContextChipData
 * ------------------------------------------------------------------ */

/**
 * Turn a commerce association into the chip line + deep-link. The offer variant
 * reads the SAME expiry the Marketplace mission owns (`resolveExpiry` /
 * `offerExpiresAt`) — there is no second expiry clock here. An accepted offer is
 * rendered as a completed-green chip rather than an offer chip, because the money
 * fact changed.
 */
export function buildContextChip(link: CommerceLink, now: number): ContextChipData {
  switch (link.kind) {
    case "offer": {
      const offer = resolveExpiry(link.offer, now);
      const amount = formatMinor(offer.amountMinor, offer.currency);
      const item = offer.itemTitle;
      if (offer.state === "accepted") {
        return {
          kind: "completed",
          line: `Offer accepted · ${item} · ${amount}`,
          a11yLabel: `Accepted offer: ${amount} for ${item}`,
          target: offer.listingId
            ? { screen: "MarketplaceDetail", params: { listingId: Number(offer.listingId) } }
            : null
        };
      }
      if (offer.state !== "open") {
        // declined / withdrawn / countered / expired — no longer an open offer.
        return {
          kind: "question",
          line: `Offer ${offer.state} · ${item}`,
          a11yLabel: `Offer ${offer.state} for ${item}`,
          target: offer.listingId
            ? { screen: "MarketplaceDetail", params: { listingId: Number(offer.listingId) } }
            : null
        };
      }
      const remaining = formatRemaining(offerExpiresAt(offer) - now);
      return {
        kind: "offer",
        line: `Offer ${amount} · ${item} · expires ${remaining}`,
        a11yLabel: `Linked offer: ${amount} for ${item}, expires in ${remaining}`,
        target: offer.listingId
          ? { screen: "MarketplaceDetail", params: { listingId: Number(offer.listingId) } }
          : null
      };
    }
    case "order":
      return {
        kind: "order",
        line: `Order #${link.orderId} · ${link.statusLine}`,
        a11yLabel: `Linked order number ${link.orderId}, ${link.statusLine}`,
        target: { screen: "BuyerOrderDetail", params: { orderId: link.orderId } }
      };
    case "pickup": {
      const amount = formatMinor(link.amountMinor, link.currency);
      return {
        kind: "pickup",
        line: `Pickup ${link.day} ${link.time} · ${link.item} · ${amount}`,
        a11yLabel: `Pickup ${link.day} at ${link.time} for ${link.item}, ${amount}`,
        target: link.orderId
          ? { screen: "BuyerOrderDetail", params: { orderId: link.orderId } }
          : null
      };
    }
    case "question": {
      const price = formatMinor(link.priceMinor, link.currency);
      if (link.sold) {
        return {
          kind: "question",
          line: `Sold · ${link.listing}`,
          a11yLabel: `Question about ${link.listing}, which has sold`,
          target: link.listingId
            ? { screen: "MarketplaceDetail", params: { listingId: link.listingId } }
            : null
        };
      }
      return {
        kind: "question",
        line: `Asking about · ${link.listing} · ${price}`,
        a11yLabel: `Question about ${link.listing}, ${price}`,
        target: link.listingId
          ? { screen: "MarketplaceDetail", params: { listingId: link.listingId } }
          : null
      };
    }
    case "completed": {
      const rated = link.ratingStars ? ` · rated you ${stars(link.ratingStars)}` : "";
      return {
        kind: "completed",
        line: `Delivered · ${link.item}${rated}`,
        a11yLabel: `Completed: ${link.item} delivered${
          link.ratingStars ? `, rated you ${link.ratingStars} of 5 stars` : ""
        }`,
        target: link.orderId
          ? { screen: "BuyerOrderDetail", params: { orderId: link.orderId } }
          : null
      };
    }
  }
}

/* ------------------------------------------------------------------ *
 * Context-chip resolution — batched, cached, non-blocking
 * ------------------------------------------------------------------ */

/** In-module cache: conversation id → resolved link (or null = resolved-to-none). */
const chipCache = new Map<number, CommerceLink | null>();

/** Read any real association a conversation already carries. */
function realLinkFor(conversation: MessengerConversation): CommerceLink | null {
  const raw = (conversation as unknown as { commerce_link?: CommerceLink }).commerce_link;
  return raw ?? null;
}

/**
 * Deterministic MOCK association for design review (behind the mock flag). The id
 * decides the kind so a given thread is stable across renders. This is the only
 * fabricated data in the layer and it never runs unless the flag is on.
 */
function mockLinkFor(conversation: MessengerConversation, now: number): CommerceLink | null {
  const id = conversation.id;
  const item = conversation.title || "Listing";
  switch (((Math.abs(id) % 6) + 6) % 6) {
    case 1:
      return {
        kind: "offer",
        offer: {
          id: `mock-offer-${id}`,
          listingId: String(1000 + (Math.abs(id) % 500)),
          amountMinor: 6000 + (Math.abs(id) % 40) * 100,
          currency: "USD",
          listPriceMinor: 8000,
          direction: "buyer_to_seller",
          state: "open",
          createdAt: now - 4 * 60 * 60 * 1000,
          updatedAt: now - 4 * 60 * 60 * 1000,
          buyerName: conversation.title || "Buyer",
          itemTitle: item
        }
      };
    case 2:
      return { kind: "order", orderId: 2000 + (Math.abs(id) % 900), statusLine: "in transit" };
    case 3:
      return {
        kind: "pickup",
        orderId: 3000 + (Math.abs(id) % 900),
        day: "Sat",
        time: "2pm",
        item,
        amountMinor: 9500,
        currency: "USD"
      };
    case 4:
      return {
        kind: "question",
        listingId: 1000 + (Math.abs(id) % 500),
        listing: item,
        priceMinor: 4500,
        currency: "USD",
        sold: id % 12 === 0
      };
    case 5:
      return {
        kind: "completed",
        orderId: 4000 + (Math.abs(id) % 900),
        item,
        ratingStars: id % 3 === 0 ? 5 : undefined
      };
    default:
      return null; // some threads have no commerce object — no chip.
  }
}

/**
 * Resolve context links for a batch of conversations. Batched (one call for the
 * whole visible set), cached (a resolved id is never re-resolved), and — by
 * contract — NEVER on the row-render path: the screen renders rows first, then
 * calls this and fills chips in. Returns only the ids that resolved to a link.
 *
 * There is no resolver endpoint, so "batched network fetch" is represented by a
 * single async tick; when a real endpoint lands, only the body of this function
 * changes and its callers keep working.
 */
export async function resolveContextChips(
  conversations: MessengerConversation[],
  now = Date.now()
): Promise<Map<number, CommerceLink>> {
  const out = new Map<number, CommerceLink>();
  const unresolved: MessengerConversation[] = [];

  for (const conversation of conversations) {
    if (chipCache.has(conversation.id)) {
      const cached = chipCache.get(conversation.id);
      if (cached) out.set(conversation.id, cached);
      continue;
    }
    const real = realLinkFor(conversation);
    if (real) {
      chipCache.set(conversation.id, real);
      out.set(conversation.id, real);
      continue;
    }
    unresolved.push(conversation);
  }

  if (unresolved.length) {
    // One async hop stands in for the batched backend resolve.
    await Promise.resolve();
    const useMock = messagesMockChipsEnabled();
    for (const conversation of unresolved) {
      const link = useMock ? mockLinkFor(conversation, now) : null;
      chipCache.set(conversation.id, link);
      if (link) out.set(conversation.id, link);
    }
  }

  return out;
}

/** Test/debug seam: drop the resolution cache. */
export function __resetChipCache() {
  chipCache.clear();
}

/* ------------------------------------------------------------------ *
 * Inbox row model
 * ------------------------------------------------------------------ */

export type InboxRow = {
  id: number;
  /**
   * Carried through from the conversation, never re-derived here. The rail reads
   * it; nothing in this file guesses it.
   */
  domain: ConversationDomain;
  title: string;
  avatarUrl?: string;
  /** Stable key that drives the deterministic avatar colour. */
  colorKey: string;
  snippet: string;
  /** True when the last message is the seller's own ("You:" prefix). */
  ownLast: boolean;
  timestamp?: string;
  unreadCount: number;
  presence?: string;
  typing: boolean;
  starred: boolean;
  archived: boolean;
  spam: boolean;
  blocked: boolean;
  /** Filled asynchronously after first render; undefined until resolved. */
  chip?: ContextChipData;
};

function truthy(value: unknown): boolean {
  return value === true || value === 1 || value === "1" || value === "true";
}

/** Normalize one live conversation into an inbox row (chip resolved later). */
export function toInboxRow(conversation: MessengerConversation): InboxRow {
  const anyConv = conversation as unknown as Record<string, unknown>;
  const snippet = String(
    conversation.latest_message || conversation.last_message_preview || ""
  );
  return {
    id: conversation.id,
    domain: conversation.conversation_domain,
    title: conversation.title || conversation.name || "Conversation",
    avatarUrl: conversation.avatar_url,
    colorKey: String(conversation.other_public_player_id || conversation.public_player_id || conversation.id),
    snippet,
    ownLast: truthy(anyConv.last_from_me) || truthy(anyConv.own_last),
    timestamp: conversation.last_activity_at || conversation.updated_at,
    unreadCount: Number(conversation.unread_count || 0),
    presence: conversation.presence,
    typing: Boolean(conversation.typing),
    starred: truthy(anyConv.starred) || truthy(conversation.pinned),
    archived: truthy(anyConv.archived) || truthy(anyConv.archive),
    spam: truthy(anyConv.spam) || truthy(anyConv.is_spam),
    blocked: truthy(anyConv.blocked) || truthy(anyConv.is_blocked)
  };
}

/* ------------------------------------------------------------------ *
 * Filters
 * ------------------------------------------------------------------ */

/**
 * The triage vocabulary.
 *
 * Tier 0.4 replaces the old rail (Offers / Orders / Starred / Archived) with the
 * one the review asked for: Marketplace / Store support / Orders / Returns /
 * Disputes. `all` and `unread` survive because they were carrying weight — `all`
 * is the only way to reach a domain nobody drew a chip for, and `unread` is the
 * one control that means "somebody is waiting on you".
 *
 * The pre-0.4 keys stay in the union rather than being deleted, because the rail
 * itself is behind `EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT` and the old rail has to
 * keep working while the flag is off. Every function below answers for every key,
 * so neither rail can reach an undefined branch.
 */
export type InboxFilter =
  // Shared by both rails.
  | "all"
  | "unread"
  // The Tier 0.4 rail.
  | "marketplace"
  | "store_support"
  | "orders"
  | "returns"
  | "disputes"
  // The pre-0.4 rail, live until the split flag is on.
  | "offers"
  | "starred"
  | "archived";

/** The rail the person actually sees, which depends on the split flag. */
export const COMMERCE_SPLIT_FILTERS: InboxFilter[] = [
  "all",
  "unread",
  "marketplace",
  "store_support",
  "orders",
  "returns",
  "disputes"
];

export const LEGACY_INBOX_FILTERS: InboxFilter[] = [
  "all",
  "unread",
  "offers",
  "orders",
  "starred",
  "archived"
];

export function inboxFilterRail(): InboxFilter[] {
  return conversationSplitEnabled() ? COMMERCE_SPLIT_FILTERS : LEGACY_INBOX_FILTERS;
}

/**
 * Which rows a filter shows. Spam and blocked threads are excluded from every
 * filter here (they live only behind the Spam & blocked tool) — the one exception
 * being that `archived` still shows archived non-spam threads.
 *
 * Marketplace / Store support / Disputes read the conversation's DOMAIN, which is
 * the discriminator the data layer owns. Orders reads the resolved context chip,
 * because an order thread is identified by the money object it points at rather
 * than by which half of the app it lives in. Returns reads a field that does not
 * exist yet and therefore always answers false — see the MOCK-DATA ledger.
 */
export function rowMatchesFilter(row: InboxRow, filter: InboxFilter): boolean {
  if (row.blocked) return false;
  if (filter === "archived") return row.archived;
  // Non-archived, non-spam base set for every other filter.
  if (row.archived || row.spam) return false;
  switch (filter) {
    case "all":
      return true;
    case "unread":
      return row.unreadCount > 0;
    case "marketplace":
      return row.domain === "MARKETPLACE";
    case "store_support":
      return row.domain === "STORE_SUPPORT";
    case "disputes":
      return row.domain === "DISPUTE";
    case "returns":
      // No returns object exists anywhere in the app. The filter ships and stays
      // honestly empty rather than being hidden and quietly forgotten.
      return false;
    case "offers":
      return row.chip?.kind === "offer";
    case "orders":
      return row.chip?.kind === "order" || row.chip?.kind === "pickup";
    case "starred":
      return row.starred;
  }
}

export type FilterCounts = Record<InboxFilter, number>;

export function filterCounts(rows: InboxRow[]): FilterCounts {
  const base = rows.filter((r) => !r.blocked && !r.archived && !r.spam);
  return {
    all: base.length,
    unread: base.filter((r) => r.unreadCount > 0).length,
    marketplace: base.filter((r) => r.domain === "MARKETPLACE").length,
    store_support: base.filter((r) => r.domain === "STORE_SUPPORT").length,
    disputes: base.filter((r) => r.domain === "DISPUTE").length,
    returns: 0,
    offers: base.filter((r) => r.chip?.kind === "offer").length,
    orders: base.filter((r) => r.chip?.kind === "order" || r.chip?.kind === "pickup").length,
    starred: base.filter((r) => r.starred).length,
    archived: rows.filter((r) => !r.blocked && r.archived).length
  };
}

/* ------------------------------------------------------------------ *
 * Reply-time stat
 * ------------------------------------------------------------------ */

export type ReplyStat = {
  /** Human label, e.g. "2h" or "15m". Absent = seller has no reply history. */
  avgLabel?: string;
  /** Only true when a real badge rule sources the incentive framing. */
  showIncentive: boolean;
  /** The threshold copy, only meaningful when showIncentive is true. */
  incentiveThreshold?: string;
};

/**
 * Derive the reply-time strip model. There is no live avg-reply field, so this
 * reads an optional value off the payload and otherwise returns "no history"
 * (the strip hides). The incentive framing is gated on a real badge rule.
 */
export function deriveReplyStat(raw?: { avg_reply_label?: string; threshold?: string }): ReplyStat {
  const avgLabel = raw?.avg_reply_label;
  return {
    avgLabel,
    showIncentive: Boolean(avgLabel) && replyBadgeIncentiveEnabled(),
    incentiveThreshold: raw?.threshold
  };
}

/* ------------------------------------------------------------------ *
 * Away mode + inbox tools
 * ------------------------------------------------------------------ */

export type AwayState = {
  /** Whether the seller has away mode active. Optimistic-local until backed. */
  on: boolean;
  /** Subtitle reflecting live state. */
  subtitle: string;
};

export function awaySubtitle(on: boolean): string {
  return on ? "Auto-reply on · until changed" : "Auto-reply off";
}

export type InboxTools = {
  savedRepliesCount?: number;
  awayOn: boolean;
  spamBlockedCount?: number;
  notificationsSummary?: string;
};

/* ------------------------------------------------------------------ *
 * Expiring-offer banner — reads the offer state machine, no second clock
 * ------------------------------------------------------------------ */

export type ExpiryBanner = {
  offerId: string;
  buyerName: string;
  amountLabel: string;
  itemTitle: string;
  remainingLabel: string;
  /** Conversation to deep-link "Open conversation ›" to. */
  conversationId?: number;
  /** How many OTHER offers also qualify ("+N more expiring today"). */
  moreCount: number;
};

/** Offers within this window of expiry are "time-critical" for the banner. */
export const OFFER_URGENCY_WINDOW_MS = 24 * 60 * 60 * 1000;

/**
 * The single most-urgent expiring offer, or null. Gated by
 * `MARKETPLACE_OFFERS_ENABLED` — with no offers backend the banner is dark, which
 * is the honest default. Expiry is read from `marketplaceOffers`; this function
 * owns no expiry logic of its own.
 */
export function deriveExpiryBanner(
  offers: readonly MarketplaceOffer[],
  offerConversationId: (offer: MarketplaceOffer) => number | undefined,
  now = Date.now()
): ExpiryBanner | null {
  if (!MARKETPLACE_OFFERS_ENABLED) return null;
  const urgent = offers
    .map((o) => resolveExpiry(o, now))
    .filter((o) => o.state === "open")
    .map((o) => ({ offer: o, expiresAt: offerExpiresAt(o) }))
    .filter(({ expiresAt }) => expiresAt - now > 0 && expiresAt - now <= OFFER_URGENCY_WINDOW_MS)
    .sort((a, b) => a.expiresAt - b.expiresAt);

  if (!urgent.length) return null;
  const soonest = urgent[0].offer;
  return {
    offerId: soonest.id,
    buyerName: soonest.buyerName,
    amountLabel: formatMinor(soonest.amountMinor, soonest.currency),
    itemTitle: soonest.itemTitle,
    remainingLabel: formatRemaining(urgent[0].expiresAt - now),
    conversationId: offerConversationId(soonest),
    moreCount: urgent.length - 1
  };
}

/* ------------------------------------------------------------------ *
 * Loader
 * ------------------------------------------------------------------ */

export type InboxModel = {
  /** Normalized rows (chips unresolved — the screen fills them after render). */
  rows: InboxRow[];
  /** The raw conversations the rows came from, so the screen can resolve chips
   *  and apply live `subscribeConversationUpdates` in the same currency. */
  conversations: MessengerConversation[];
  offline: boolean;
  error?: string;
};

/**
 * Load the inbox rows with the same live→cache fallback the Messenger tab uses.
 * Chips are NOT resolved here — the screen resolves them after first render so
 * rows never wait on the (mock or future) association fetch.
 */
export async function loadInboxModel(): Promise<InboxModel> {
  try {
    // "commerce" scope, not a filter: a social thread is never in the result, so
    // this list and the Messenger tab's list can never be the same query.
    const conversations = await listConversations("commerce");
    return { rows: conversations.map(toInboxRow), conversations, offline: false };
  } catch (error) {
    try {
      const cached = await loadCachedConversations("commerce");
      return {
        rows: cached.map(toInboxRow),
        conversations: cached,
        offline: true,
        error: error instanceof Error ? error.message : "Messages could not load."
      };
    } catch {
      return { rows: [], conversations: [], offline: true, error: "Messages could not load." };
    }
  }
}
