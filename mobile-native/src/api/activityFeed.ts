/**
 * Activity feed — the derivation layer the Activity center renders.
 *
 * THE UNIFIED-FEED FINDING (top-priority gap): there is no backend notification
 * service that returns one aggregated, typed, read-stateful feed. The app's
 * `api/activity.ts` already fans out to notifications + messages + calls and
 * merges them client-side; this module builds the seller Activity surface on top
 * of the notifications source (the closest thing to a unified feed) and applies
 * a DOCUMENTED CLIENT aggregation rule, because the backend does not aggregate.
 * That gap is the #1 item in the report — the right long-term fix is a server
 * feed with types, read state, and collapsing.
 *
 * Everything here is pure so the type→filter mapping, the collapse rule, the day
 * grouping, and the offer-state-aware inline actions are all unit-testable and
 * carry no navigation/React imports.
 *
 * SINGLE SOURCES OF TRUTH: offer inline actions read the Marketplace mission's
 * offer state machine (an expired/answered offer drops its buttons and rewrites
 * its copy — no second expiry clock). Money in payment notifications is never
 * invented; amounts only render when the notification actually carries them.
 */

import { PulseNotification } from "./notifications";
import { MarketplaceOffer, OfferState, isTerminal, resolveExpiry } from "./marketplaceOffers";

/* ------------------------------------------------------------------ *
 * Domain semantics + filter mapping
 * ------------------------------------------------------------------ */

/** Domain key — drives the type-icon circle colour (app-wide semantics). */
export type ActivityDomain = "social" | "marketplace" | "orders" | "payments" | "live" | "system";

/** The five filter chips. Every domain maps to exactly one. */
export type FeedFilter = "all" | "social" | "marketplace" | "orders" | "system";

/**
 * Domain → filter chip. Documented and exhaustive:
 *   social      → Social       (likes, comments, follows, mentions, reels)
 *   marketplace → Marketplace  (offers, listing questions, price drops)
 *   orders      → Orders       (order placed, shipping, delivery, deadlines)
 *   payments    → Orders       (payouts/refunds are money tied to commerce; no
 *                               separate Payments chip in this design)
 *   live        → System       (live-now alerts are urgent system events)
 *   system      → System       (verification, security, policy, account)
 */
export const DOMAIN_TO_FILTER: Record<ActivityDomain, FeedFilter> = {
  social: "social",
  marketplace: "marketplace",
  orders: "orders",
  payments: "orders",
  live: "system",
  system: "system"
};

const DOMAIN_RULES: Array<{ domain: ActivityDomain; test: RegExp }> = [
  { domain: "live", test: /\b(live|livestream|going[_-]?live|stream[_-]?start)\b/ },
  { domain: "marketplace", test: /\b(offer|listing|marketplace|price[_-]?drop|counter)\b/ },
  { domain: "payments", test: /\b(payout|payment|refund|charge|invoice|wallet|ledger|balance)\b/ },
  { domain: "orders", test: /\b(order|shipping|shipped|delivery|delivered|fulfil|dispatch|tracking)\b/ },
  { domain: "social", test: /\b(like|reaction|react|comment|reply|follow|mention|tag|reel|post|share)\b/ },
  { domain: "system", test: /\b(verification|verify|security|policy|account|system|alert|warning|appeal)\b/ }
];

/** Classify a raw notification into a domain from its type/category text. */
export function classifyDomain(n: Pick<PulseNotification, "type" | "category">): ActivityDomain {
  // Notification types arrive underscore/dot/dash-delimited ("offer_received",
  // "live.start"). `\b` treats "_" as a word char, so normalize every separator
  // to a space first — otherwise the word-boundary rules never fire.
  const hay = `${n.type || ""} ${n.category || ""}`.toLowerCase().replace(/[^a-z0-9]+/g, " ");
  for (const rule of DOMAIN_RULES) {
    if (rule.test.test(hay)) return rule.domain;
  }
  return "system";
}

export function filterForDomain(domain: ActivityDomain): FeedFilter {
  return DOMAIN_TO_FILTER[domain];
}

/* ------------------------------------------------------------------ *
 * Feed model
 * ------------------------------------------------------------------ */

export type InlineAction = {
  key: string;
  label: string;
  /** a11y: full-context label ("View Devon's offer of sixty dollars"). */
  a11yLabel: string;
  /** Where the action routes, when it navigates rather than mutating in place. */
  target?: string;
  /** Marks the destructive/primary intent for styling. */
  intent?: "primary" | "neutral";
};

export type FeedNotification = {
  id: number;
  domain: ActivityDomain;
  filter: FeedFilter;
  /** Human actor for actor rows; undefined for system events. */
  actorName?: string;
  actorAvatarUrl?: string;
  isSystem: boolean;
  /** Plain-language sentence — never a raw template string. */
  sentence: string;
  subjectThumbUrl?: string;
  /** Epoch ms. */
  timestamp: number;
  unread: boolean;
  /** Deep-link target (path or url) to the SUBJECT. */
  target?: string;
  /** True for live-now urgency (pulsing red treatment + assertive a11y). */
  live: boolean;
  /** >0 when this row collapses N same-domain actions on the same subject. */
  collapsedCount: number;
  /** True when the subject no longer exists (deleted post / expired offer). */
  subjectGone: boolean;
  inlineActions: InlineAction[];
  a11yLabel: string;
  /** Key used to collapse same-subject rows. */
  subjectKey: string;
};

function subjectKeyFor(n: PulseNotification): string {
  const md = n.metadata || {};
  const id =
    (md.subject_id as string | number | undefined) ??
    (md.post_id as string | number | undefined) ??
    (md.reel_id as string | number | undefined) ??
    (md.offer_id as string | number | undefined) ??
    (md.order_id as string | number | undefined) ??
    n.target_url ??
    n.deep_link;
  return String(id ?? n.id);
}

/**
 * Normalize a raw notification into a feed row. The sentence prefers a
 * server-provided plain-language body; it never surfaces a raw template token.
 */
export function toFeedNotification(n: PulseNotification, now: number): FeedNotification {
  const domain = classifyDomain(n);
  const md = n.metadata || {};
  const actorName = (md.actor_name as string | undefined) || undefined;
  const isSystem = !actorName;
  const subjectGone = Boolean(md.subject_deleted) || Boolean(md.deleted);
  const unread = n.read === true || Boolean(n.read_at) ? false : true;

  let sentence = (n.body || n.message || n.title || "").trim();
  if (subjectGone && domain === "marketplace") sentence = "This offer has expired";
  else if (subjectGone && !sentence) sentence = "This item is no longer available";
  if (!sentence) sentence = n.title || "New activity";

  const live = domain === "live";
  const target = subjectGone ? gracefulLanding(domain, n) : n.deep_link || n.target_url;

  const row: FeedNotification = {
    id: n.id,
    domain,
    filter: filterForDomain(domain),
    actorName,
    actorAvatarUrl: (md.actor_avatar_url as string | undefined) || undefined,
    isSystem,
    sentence,
    subjectThumbUrl: (md.subject_thumb_url as string | undefined) || undefined,
    timestamp: n.created_at ? Date.parse(n.created_at) : now,
    unread,
    target,
    live,
    collapsedCount: 0,
    subjectGone,
    inlineActions: [],
    subjectKey: subjectKeyFor(n),
    a11yLabel: ""
  };
  row.a11yLabel = describeRow(row);
  return row;
}

/** A deleted subject deep-links to a graceful landing, not a dead/blank screen. */
function gracefulLanding(domain: ActivityDomain, _n: PulseNotification): string | undefined {
  if (domain === "marketplace") return "/pulse/marketplace";
  if (domain === "orders" || domain === "payments") return "/pulse/orders";
  return undefined;
}

function describeRow(row: FeedNotification): string {
  const who = row.isSystem ? "" : `${row.actorName}, `;
  const when = relativeShort(row.timestamp, row.timestamp); // filled properly at group time
  void when;
  const count = row.collapsedCount > 0 ? ` and ${row.collapsedCount} others` : "";
  const unread = row.unread ? ", unread" : "";
  const urgent = row.live ? ", live now" : "";
  return `${who}${row.sentence}${count}${urgent}${unread}`.trim();
}

/* ------------------------------------------------------------------ *
 * Aggregation — documented client collapse rule
 * ------------------------------------------------------------------ */

export type AggregateOptions = {
  /** Rolling window; actions on the same subject within it collapse. */
  windowMs?: number;
  /** Cap on how many collapsed actors we name in the sentence. */
  namesInSentence?: number;
};

const DEFAULT_WINDOW_MS = 6 * 60 * 60 * 1000; // 6h rolling window

/**
 * Collapse same-domain actions on the same subject inside a rolling window into
 * one row: "Maya R. and 12 others reacted to your Reel". This is the CLIENT rule
 * (the backend does not aggregate). The collapsed row keeps the newest
 * timestamp, deep-links to the subject (where the full reactor list lives), and
 * is unread if any collapsed member was unread. Non-collapsible domains (orders,
 * payments, marketplace offers, system) pass through untouched.
 */
export function aggregateFeed(rows: FeedNotification[], options: AggregateOptions = {}): FeedNotification[] {
  const windowMs = options.windowMs ?? DEFAULT_WINDOW_MS;
  const collapsible: ReadonlySet<ActivityDomain> = new Set<ActivityDomain>(["social"]);
  const sorted = [...rows].sort((a, b) => b.timestamp - a.timestamp);
  const out: FeedNotification[] = [];
  const anchors = new Map<string, FeedNotification>();
  // The verb phrase of each anchor's ORIGINAL sentence, captured once. Without
  // this we'd re-collapse an already-collapsed sentence and stack "and N others".
  const anchorVerb = new Map<FeedNotification, string>();

  for (const row of sorted) {
    if (!collapsible.has(row.domain)) {
      out.push(row);
      continue;
    }
    const key = `${row.domain}:${row.subjectKey}`;
    const anchor = anchors.get(key);
    if (anchor && anchor.timestamp - row.timestamp <= windowMs) {
      anchor.collapsedCount += 1;
      anchor.unread = anchor.unread || row.unread;
      anchor.sentence = collapsedSentence(anchor, anchorVerb.get(anchor)!);
      anchor.a11yLabel = describeRow(anchor);
    } else {
      const fresh = { ...row };
      anchors.set(key, fresh);
      anchorVerb.set(fresh, stripLeadingActor(fresh.sentence, fresh.actorName));
      out.push(fresh);
    }
  }
  return out;
}

function collapsedSentence(anchor: FeedNotification, verbPhrase: string): string {
  const base = anchor.actorName || "Someone";
  return `${base} and ${anchor.collapsedCount} others ${verbPhrase}`.trim();
}

function stripLeadingActor(sentence: string, actor?: string): string {
  if (actor && sentence.startsWith(actor)) return sentence.slice(actor.length).trim();
  // Fall back to a generic verb phrase if the sentence has no clear actor prefix.
  return sentence.replace(/^\w+\s/, "").trim() || "reacted to your post";
}

/* ------------------------------------------------------------------ *
 * Day grouping — New / Today / Yesterday / dates
 * ------------------------------------------------------------------ */

export type FeedSection = {
  key: string;
  title: string;
  items: FeedNotification[];
};

export type GroupOptions = {
  /** Everything unread *and* newer than this is grouped under "New". */
  lastVisitMs?: number;
};

const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * Group rows newest-first into New / Today / Yesterday / dated sections. "New" =
 * unread since the last visit; those rows are lifted out of their day section so
 * the seller sees what changed first.
 */
export function groupFeedByDay(rows: FeedNotification[], now: number, options: GroupOptions = {}): FeedSection[] {
  const lastVisit = options.lastVisitMs ?? 0;
  const sorted = [...rows].sort((a, b) => b.timestamp - a.timestamp);

  const newItems: FeedNotification[] = [];
  const rest: FeedNotification[] = [];
  for (const row of sorted) {
    if (row.unread && row.timestamp >= lastVisit && lastVisit > 0) newItems.push(row);
    else rest.push(row);
  }

  const startOfToday = startOfDay(now);
  const buckets = new Map<string, FeedNotification[]>();
  const order: string[] = [];
  const push = (key: string, row: FeedNotification) => {
    if (!buckets.has(key)) {
      buckets.set(key, []);
      order.push(key);
    }
    buckets.get(key)!.push(row);
  };

  for (const row of rest) {
    if (row.timestamp >= startOfToday) push("today", row);
    else if (row.timestamp >= startOfToday - DAY_MS) push("yesterday", row);
    else push(dateLabel(row.timestamp), row);
  }

  const sections: FeedSection[] = [];
  if (newItems.length) sections.push({ key: "new", title: "New", items: newItems });
  for (const key of order) {
    const title = key === "today" ? "Today" : key === "yesterday" ? "Yesterday" : key;
    sections.push({ key, title, items: buckets.get(key)! });
  }
  return sections;
}

function startOfDay(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

function dateLabel(ms: number): string {
  return new Date(ms).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Compact relative time: "3h ago · Marketplace" is composed by the row. */
export function relativeShort(ms: number, now: number): string {
  const diff = Math.max(0, now - ms);
  if (diff < 60 * 1000) return "now";
  if (diff < 60 * 60 * 1000) return `${Math.floor(diff / (60 * 1000))}m ago`;
  if (diff < DAY_MS) return `${Math.floor(diff / (60 * 60 * 1000))}h ago`;
  return `${Math.floor(diff / DAY_MS)}d ago`;
}

/** The domain suffix shown after the time ("· Marketplace"). */
export function domainSuffix(domain: ActivityDomain): string {
  switch (domain) {
    case "social":
      return "Social";
    case "marketplace":
      return "Marketplace";
    case "orders":
      return "Orders";
    case "payments":
      return "Payments";
    case "live":
      return "Live";
    default:
      return "System";
  }
}

/* ------------------------------------------------------------------ *
 * Inline actions — offer rows read the Marketplace offer state machine
 * ------------------------------------------------------------------ */

export type OfferContext = {
  offer: MarketplaceOffer;
  amountLabel: string; // display-only, formatted by the caller from real minor units
};

/**
 * Inline actions for a row (max two). Offer rows read the SAME offer state
 * machine the Marketplace mission owns: an expired or answered offer drops its
 * buttons and the row copy already reflects the terminal state — no second
 * expiry clock, no action that the deep-linked offer sheet wouldn't also apply.
 * Live rows get a single "Open live". Everything else has no inline action.
 */
export function inlineActionsFor(
  row: FeedNotification,
  now: number,
  offerCtx?: OfferContext
): InlineAction[] {
  if (row.subjectGone) return [];

  if (row.live) {
    return [
      {
        key: "open_live",
        label: "Open live",
        a11yLabel: "Open the live session",
        target: row.target,
        intent: "primary"
      }
    ];
  }

  if (row.domain === "marketplace" && offerCtx) {
    const resolved = resolveExpiry(offerCtx.offer, now);
    if (isTerminal(resolved.state)) return []; // expired/answered → row copy carries it
    return [
      {
        key: "view_offer",
        label: "View offer",
        a11yLabel: `View the offer of ${offerCtx.amountLabel}`,
        target: row.target,
        intent: "primary"
      },
      {
        key: "message",
        label: "Message",
        a11yLabel: `Message ${row.actorName || "the buyer"} about their offer of ${offerCtx.amountLabel}`,
        intent: "neutral"
      }
    ];
  }

  return [];
}

/** Whether an offer is in a state that still shows action buttons. */
export function offerActionsLive(offer: MarketplaceOffer, now: number): boolean {
  const state: OfferState = resolveExpiry(offer, now).state;
  return !isTerminal(state);
}

/* ------------------------------------------------------------------ *
 * Filter counts + matching
 * ------------------------------------------------------------------ */

export type FilterCounts = Record<FeedFilter, number>;

/** Unread counts per filter chip. `all` counts every unread row once. */
export function filterUnreadCounts(rows: FeedNotification[]): FilterCounts {
  const counts: FilterCounts = { all: 0, social: 0, marketplace: 0, orders: 0, system: 0 };
  for (const row of rows) {
    if (!row.unread) continue;
    counts.all += 1;
    counts[row.filter] += 1;
  }
  return counts;
}

export function rowMatchesFilter(row: FeedNotification, filter: FeedFilter): boolean {
  return filter === "all" || row.filter === filter;
}

/* ------------------------------------------------------------------ *
 * MOCK-DATA / gap ledger
 * ------------------------------------------------------------------ */

export type ActivityGap = { field: string; backendWork: string; gatedBy: string };

export const ACTIVITY_MOCK_DATA_GAPS: ActivityGap[] = [
  {
    field: "unified notification feed (types, read state, aggregation)",
    backendWork:
      "A server notification service returning one aggregated typed feed. Today the client synthesizes it from the notifications source; collapsing is a documented client rule.",
    gatedBy: "TOP PRIORITY — client-synthesized until a backend feed exists"
  },
  {
    field: "cursor pagination",
    backendWork: "Cursor-paginated notifications endpoint; See-earlier loads more",
    gatedBy: "limit-based fetch today (listNotifications takes limit, no cursor)"
  },
  {
    field: "offer amount on offer notifications",
    backendWork: "Notifications carry the offer id; offer state read from marketplaceOffers (flag-gated OFF)",
    gatedBy: "MARKETPLACE_OFFERS_ENABLED — inline offer actions honour real state when present"
  },
  {
    field: "aggregation window length",
    backendWork: "Server-defined collapse window; client uses a 6h rolling window as the documented rule",
    gatedBy: "client rule (aggregateFeed windowMs)"
  }
];

export const ACTIVITY_MOCK_DATA_GAP_COUNT = ACTIVITY_MOCK_DATA_GAPS.length;
