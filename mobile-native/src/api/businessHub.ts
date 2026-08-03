/**
 * Business Hub — the model behind the seller's front door.
 *
 * The hub owns almost nothing. Every number, badge and state line on it belongs
 * to a section screen built by another mission, and this module's whole job is
 * to consume those sources faithfully. Two failure modes are designed against
 * explicitly:
 *
 *   1. The hub inventing or duplicating state that then disagrees with the
 *      section screen. Guarded by rule: no arithmetic here that an owner module
 *      could do. Where an owner kept its derivation inside its own screen, that
 *      derivation was MOVED into the owner's api module and both now read it
 *      (`ordersAwaitingSeller`, `storeHealthCounts`) — the hub never re-derives.
 *
 *   2. The hub becoming slow. This is the app's front door and must paint
 *      instantly, so every binding is independent: one source failing, or one
 *      source arriving late, touches exactly one card. See `core/hubBindings`.
 *
 * Everything in THIS file is pure. Given a card's own source data and a clock it
 * returns the one line that card should show. That makes the state-line priority
 * contract — the part most likely to be got subtly wrong — testable without
 * rendering anything.
 *
 * ON THE SHAPE OF THE HONESTY HERE: this mission was run while several of its
 * owner missions were still being written. Rather than invent the numbers those
 * missions will one day expose, each unavailable line is a named flag that is
 * OFF, a declared gap in `HUB_DATA_GAPS`, and a card that falls back to its
 * static subtitle. A hub that lies fast is worse than a hub that is quiet.
 */

import { AdAccount, AdAnalytics, adAccountCanTransact } from "./businessOs";
import { WalletSummary } from "./adsDashboard";
import { InsightsComparison, InsightsSummary, compareToPrior } from "./insightsDashboard";
import { SellerApplicationView } from "./sellerApplication";
import { StoreHealthCounts } from "./storeDashboard";
import { VerificationState, VerificationStatus } from "./verification";

/* ------------------------------------------------------------------ *
 * Feature flags — every one of these is a line the design asks for that
 * this platform has no source for today. They are constants rather than
 * environment reads because they are not experiments: they are waiting on
 * backend work, and the report names the work each needs.
 * ------------------------------------------------------------------ */

/**
 * Orders deadline pressure — "2 orders ship by 4 PM", the amber urgent card and
 * the bobbing clock. OFF: the live seller-orders payload carries no fulfillment
 * SLA (declared in `ORDERS_MOCK_DATA_GAPS`), so a due-today count, an overdue
 * count and a carrier cutoff are all uncomputable. The Orders card shows the
 * count it genuinely knows — orders awaiting the seller — and says "to fulfil",
 * never "due today".
 *
 * This is the single most consequential flag on the screen: with it off, NO card
 * can take the urgent treatment, which is why the grid never goes amber. That is
 * the correct outcome. An urgent countdown computed from a deadline nobody sent
 * is precisely the invention this hub exists not to do.
 */
export const HUB_ORDER_DEADLINES = false;

/** Marketplace offers. OFF because `MARKETPLACE_OFFERS_ENABLED` is false — the
 * offer state machine is local-only with no endpoint behind it. Kills the offer
 * count, the soonest-expiry text, the violet badge and the Offers strip cell. */
export const HUB_MARKETPLACE_OFFERS = false;

/** Today's ad spend on the Advertising line. OFF: `getAdAnalytics` takes no date
 * range, so its spend figure is lifetime. Labelling a lifetime number "today"
 * would overstate the day's cost by however long the account has run. */
export const HUB_ADS_TODAY_SPEND = false;

/** "Limited by budget" as a distinct Advertising state. OFF: the backend
 * collapses learning and limited into `active` (declared in the ads gaps). */
export const HUB_ADS_LIMITED_STATE = false;

/** Payout figures and next-payout day on the Payments line. OFF: there is no
 * payout contract — only the ad wallet and billing. Per the standing money rule
 * this degrades to real wallet balance or to nothing; it is never estimated, and
 * it is deliberately absent from `HUB_DATA_GAPS`. */
export const HUB_PAYOUTS = false;

/** Live typing on the Messages line. OFF: the conversation payload carries a
 * `typing` flag, but reading it at the hub would mean holding a conversation
 * subscription open on the front door for one word of copy. Not worth the
 * battery, and the mission makes it conditional on product policy anyway. */
export const HUB_MESSAGES_TYPING = false;

/** The whole Events card, and the live banner. OFF: `api/eventsManager` exposes
 * the lifecycle helpers but no loader for the seller's OWN hosted events yet.
 * `listScheduledLiveEvents` is platform-wide discovery — someone else's event
 * rendered as "Next in 6 days" would be a lie about the seller's calendar. */
export const HUB_EVENTS = false;

/** Verification review ETA. OFF: `VerificationState` has no estimated-review
 * field. The card says "In review" and stops, rather than promising a duration
 * nothing in the backend knows. Absent from `HUB_DATA_GAPS` per the rule that
 * verification status is never mocked. */
export const HUB_VERIFICATION_ETA = false;

/* ------------------------------------------------------------------ *
 * Declared gaps
 * ------------------------------------------------------------------ */

export type HubDataGap = {
  /** The design element that cannot be rendered. */
  field: string;
  /** The card or strip cell it belongs to. */
  surface: string;
  /** What the backend would have to expose for it to become real. */
  backendWork: string;
};

/**
 * The design asks for these; this platform cannot source them. None of them
 * render — this list is the reason they do not.
 *
 * Money and verification status are deliberately NOT on this table. The standing
 * rule is that those two never appear as mocked data anywhere, so where they are
 * unavailable the surface degrades to silence instead (see `HUB_PAYOUTS` and
 * `HUB_VERIFICATION_ETA`).
 */
export const HUB_DATA_GAPS: readonly HubDataGap[] = [
  {
    field: "Orders due today, overdue count, and carrier cutoff time",
    surface: "Orders card (urgent treatment) + Ship-today strip cell",
    backendWork: "a per-order fulfillment SLA on the live seller-orders payload"
  },
  {
    field: "Open offer count and soonest expiry",
    surface: "Marketplace card + Offers strip cell",
    backendWork: "an offers endpoint behind the existing local state machine"
  },
  {
    field: "Today's ad spend",
    surface: "Advertising card",
    backendWork: "a date range on getAdAnalytics — today's figures are lifetime"
  },
  {
    field: "Campaign limited-by-budget phase",
    surface: "Advertising card",
    backendWork: "a delivery-phase field; the backend collapses limited into active"
  },
  {
    field: "Live typing indicator",
    surface: "Messages card",
    backendWork: "a hub-cheap presence channel, plus a product decision to show it here"
  },
  {
    field: "Seller's own hosted events and live session",
    surface: "Events card + LiveNowBanner",
    backendWork: "a hosted-events loader on api/eventsManager (mission in flight)"
  }
];

/** Pinned by a test, so closing a gap with a real source — or quietly faking one
 * — changes this number and fails loudly. */
export const HUB_DATA_GAP_COUNT = HUB_DATA_GAPS.length;

/* ------------------------------------------------------------------ *
 * State-line vocabulary
 * ------------------------------------------------------------------ */

/**
 * LED tones. These map to colours, but colour is never the signal: every state
 * line's words carry its meaning on their own, which is what makes the screen
 * legible to a screen reader and to anyone who cannot separate the greens from
 * the ambers.
 */
export type HubTone = "green" | "warn" | "critical" | "review" | "violet" | "muted";

export type HubStateLine = {
  /** Which branch of the priority contract produced this. Asserted in tests so a
   *  reordering is caught by name, not by reading copy. */
  key: string;
  tone: HubTone;
  /** The line as shown, and as announced. */
  text: string;
  /** Steady, or the tone's documented blink cadence. */
  blink: boolean;
  /**
   * Deadline-critical. Drives the amber card variant and the bobbing clock.
   * Nothing sets this today — see `HUB_ORDER_DEADLINES`.
   */
  urgent: boolean;
};

function line(
  key: string,
  tone: HubTone,
  text: string,
  options: { blink?: boolean; urgent?: boolean } = {}
): HubStateLine {
  return { key, tone, text, blink: options.blink ?? false, urgent: options.urgent ?? false };
}

/**
 * `null` from any resolver means "say nothing" — the card keeps its static
 * subtitle and nothing else. That is the correct answer whenever the owner
 * source failed, is missing, or has no opinion; it is never an error card, and
 * it never blocks the other nine.
 */
export type HubStateLineResult = HubStateLine | null;

/* ------------------------------------------------------------------ *
 * Thresholds — every number the contract turns on, named once.
 * ------------------------------------------------------------------ */

export const HUB_THRESHOLDS = {
  /** At or above this, Business Profile reads "Complete" rather than a figure. */
  profileComplete: 100,
  /**
   * A revenue move smaller than this reads as flat rather than as a direction.
   * Matches `compareToPrior`'s own dead band, so the hub and the Insights screen
   * call the same week flat.
   */
  trendFlatBand: 0.0005
} as const;

/* ------------------------------------------------------------------ *
 * Per-card resolvers. One exported function per card, each taking only
 * that card's own source. The narrow signatures are the point: a resolver
 * that cannot see another card's data cannot accidentally couple to it.
 * ------------------------------------------------------------------ */

/**
 * Business Profile — completeness, with the mini-bar.
 * Priority: complete → in progress. There is no failure branch; a failed load
 * returns `null` at the call site and the card falls back to its subtitle.
 */
export function profileStateLine(application: SellerApplicationView | null): HubStateLineResult {
  if (!application) return null;
  const percent = Math.round(application.completeness);
  if (percent >= HUB_THRESHOLDS.profileComplete) {
    return line("profile.complete", "green", "Complete");
  }
  return line("profile.progress", "muted", `${percent}% complete`);
}

/** The completeness bar is a separate read so the card can render the figure and
 *  the bar from one number without the renderer parsing the copy back out. */
export function profileCompletenessFraction(application: SellerApplicationView | null): number | null {
  if (!application) return null;
  return Math.max(0, Math.min(1, application.completeness / 100));
}

/**
 * Store — stock trouble first, because a listing buyers cannot order is a live
 * loss, where a healthy catalogue is merely good news that can wait.
 * Priority: out-of-stock and/or low-stock → active listings → no listings.
 */
export function storeStateLine(counts: StoreHealthCounts | null): HubStateLineResult {
  if (!counts) return null;
  const parts: string[] = [];
  if (counts.low > 0) parts.push(`${counts.low} low stock`);
  if (counts.out > 0) parts.push(`${counts.out} sold out`);
  if (parts.length > 0) {
    return line("store.attention", "warn", parts.join(" · "), { blink: true });
  }
  if (counts.active > 0) {
    return line("store.active", "green", `${counts.active} active listing${counts.active === 1 ? "" : "s"}`);
  }
  return line("store.empty", "muted", "No listings yet");
}

/**
 * Marketplace — offers first when they exist, then the catalogue, then the
 * invitation.
 *
 * The offers branch is unreachable today (`HUB_MARKETPLACE_OFFERS`). It is kept
 * in the contract, and tested behind the flag, so the day an endpoint appears
 * the ordering is already decided and reviewed rather than invented in a hurry.
 */
export function marketplaceStateLine(
  input: {
    openOffers: number | null;
    soonestExpiryHours: number | null;
    activeItems: number | null;
    /**
     * True when the offers snapshot is older than `HUB_STALENESS_MS.offers`.
     * This is the staleness degradation in its concrete form: the COUNT still
     * shows, because an offer that existed five minutes ago almost certainly
     * still exists, but the "expires in 5h" clause is dropped, because five
     * minutes of drift is enough to make a displayed hour wrong. The line
     * degrades to its non-deadline fallback rather than disappearing.
     */
    offersStale?: boolean;
  }
): HubStateLineResult {
  if (HUB_MARKETPLACE_OFFERS && input.openOffers && input.openOffers > 0) {
    const suffix =
      !input.offersStale && input.soonestExpiryHours !== null && input.soonestExpiryHours >= 0
        ? ` · 1 expires in ${Math.max(1, Math.round(input.soonestExpiryHours))}h`
        : "";
    return line(
      "marketplace.offers",
      "violet",
      `${input.openOffers} offer${input.openOffers === 1 ? "" : "s"}${suffix}`
    );
  }
  if (input.activeItems === null) return null;
  if (input.activeItems > 0) {
    return line("marketplace.items", "green", `${input.activeItems} item${input.activeItems === 1 ? "" : "s"} listed`);
  }
  return line("marketplace.empty", "muted", "List your first item");
}

/**
 * Advertising — the blocker outranks the good news, because a seller whose
 * campaigns cannot deliver needs to know that before they know how many they
 * have.
 * Priority: blocked on verification → limited by budget → delivering → none.
 */
export function advertisingStateLine(
  input: { accounts: AdAccount[] | null; analytics: AdAnalytics | null }
): HubStateLineResult {
  const { accounts, analytics } = input;
  if (!accounts) return null;

  const campaigns = analytics?.campaigns || [];
  const delivering = campaigns.filter((row) => String(row.status) === "active").length;

  if (accounts.length === 0) {
    return line("advertising.noAccount", "muted", "Create your first campaign");
  }

  const canTransact = accounts.some(adAccountCanTransact);
  if (!canTransact && campaigns.length > 0) {
    const blocked = campaigns.length;
    return line(
      "advertising.blocked",
      "warn",
      `${blocked} campaign${blocked === 1 ? "" : "s"} waiting on verification`,
      { blink: true }
    );
  }

  if (delivering > 0) {
    // HUB_ADS_TODAY_SPEND is off, so this says how many are delivering and stops.
    // The design's "· $12.40 today" is omitted rather than filled with the
    // lifetime figure that getAdAnalytics actually returns.
    return line("advertising.delivering", "green", `${delivering} campaign${delivering === 1 ? "" : "s"} delivering`);
  }

  if (campaigns.length > 0) {
    return line("advertising.idle", "muted", "No campaigns delivering");
  }
  return line("advertising.noCampaigns", "muted", "Create your first campaign");
}

/**
 * Orders — what is waiting on the seller.
 *
 * The design's first two branches (due today with a cutoff, then overdue) are
 * both unreachable: `HUB_ORDER_DEADLINES` is off because no order carries an
 * SLA. What remains is real and useful — how many orders are open and
 * unfulfilled — and the copy says "to fulfil" so it cannot be misread as a
 * deadline the platform did not set.
 */
export function ordersStateLine(awaiting: number | null): HubStateLineResult {
  if (awaiting === null) return null;
  if (awaiting > 0) {
    return line("orders.awaiting", "warn", `${awaiting} order${awaiting === 1 ? "" : "s"} to fulfil`);
  }
  return line("orders.clear", "muted", "No open orders");
}

/**
 * Messages — unread, or calm.
 * The typing branch is gated off; see `HUB_MESSAGES_TYPING`.
 */
export function messagesStateLine(unread: number | null): HubStateLineResult {
  if (unread === null) return null;
  if (unread > 0) {
    return line("messages.unread", "green", `${unread} unread conversation${unread === 1 ? "" : "s"}`);
  }
  return line("messages.clear", "muted", "All caught up");
}

/**
 * Insights — the seven-day revenue direction.
 *
 * `compareToPrior` returns two distinct refusals rather than a number, and both
 * matter here: a seller who did not exist last week must not see ▲100%, and a
 * seller whose last week was zero must not see a percentage of zero. Both land
 * on "Collecting data", which is what is actually true.
 */
export function insightsStateLine(summary: InsightsSummary | null): HubStateLineResult {
  if (!summary) return null;
  const comparison: InsightsComparison = compareToPrior(
    summary.totals.revenue_minor,
    summary.prior_totals?.revenue_minor,
    summary.has_prior_period
  );
  if (comparison.kind === "none") {
    return line("insights.collecting", "muted", "Collecting data");
  }
  if (comparison.direction === "flat") {
    return line("insights.flat", "muted", "Revenue level · 7d");
  }
  const arrow = comparison.direction === "up" ? "▲" : "▼";
  const percent = Math.round(Math.abs(comparison.ratio) * 100);
  return line(
    "insights.trend",
    comparison.direction === "up" ? "green" : "warn",
    `Revenue ${arrow} ${percent}% · 7d`
  );
}

/**
 * Payments — the only real money this platform can state here.
 *
 * Every branch the design asks for (failed payout, refund awaiting response,
 * next payout day, no bank account) needs a payout contract that does not exist.
 * Rather than estimate any of them, this shows the ad wallet balance — a number
 * the server computes and the Advertising mission already renders from the same
 * field — and shows nothing at all when the wallet call failed. Never a stale
 * or fabricated zero.
 */
export function paymentsStateLine(wallet: WalletSummary | null): HubStateLineResult {
  if (HUB_PAYOUTS) {
    // Intentionally unreachable. When a payout contract lands, its branches go
    // here, above the wallet, and this flag turns on.
  }
  if (!wallet) return null;
  // `balanceLabel` is the Advertising mission's own formatting of the server's
  // `spendable_balance_cents`. Taking the label rather than the cents means the
  // hub performs no money arithmetic and no money formatting at all, and the
  // figure here is character-for-character the one the Advertising screen shows.
  return line("payments.wallet", "muted", `Ad wallet ${wallet.balanceLabel}`);
}

/**
 * Events — entirely unavailable today (`HUB_EVENTS`). Kept as a named resolver
 * returning `null` so the card's fallback path is the same code path every other
 * card uses, and so wiring the loader later is one call site rather than a new
 * branch in the screen.
 */
export function eventsStateLine(): HubStateLineResult {
  return null;
}

/**
 * Verification — trouble first, then progress, then the good news.
 * Priority: needs info / rejected / suspended → in review → approved → not
 * started. The ETA the design asks for is omitted; nothing exposes one.
 */
export function verificationStateLine(state: VerificationState | null): HubStateLineResult {
  if (!state) return null;
  const status = state.status as VerificationStatus | undefined;
  if (!status) return null;

  if (status === "rejected" || status === "suspended") {
    return line("verification.rejected", "critical", "Verification declined — appeal available", { blink: true });
  }
  if (status === "needs_more_info") {
    return line("verification.needsInfo", "warn", "More information needed", { blink: true });
  }
  if (status === "submitted" || status === "in_review" || status === "appealed") {
    return line("verification.inReview", "review", "In review", { blink: true });
  }
  if (status === "approved") {
    return line("verification.approved", "green", "Verified");
  }
  if (status === "draft") {
    return line("verification.draft", "muted", "Finish your application");
  }
  return line("verification.notStarted", "muted", "Start verification");
}

/* ------------------------------------------------------------------ *
 * Header verification tick
 * ------------------------------------------------------------------ */

export type HubTickState = "none" | "review" | "verified" | "problem";

/**
 * The tick beside the business name, and the Verification card, read the SAME
 * status. Two renderings of one source is the fan-out the mission asks to
 * demonstrate; two derivations of one status would be the drift it warns about.
 */
export function verificationTick(state: VerificationState | null): HubTickState {
  const status = state?.status as VerificationStatus | undefined;
  if (!status || status === "not_started" || status === "draft") return "none";
  if (status === "approved") return "verified";
  if (status === "rejected" || status === "suspended" || status === "needs_more_info") return "problem";
  return "review";
}

/** The header's context line, reflecting the real status rather than a constant. */
export function hubContextLine(state: VerificationState | null): string {
  switch (verificationTick(state)) {
    case "verified":
      return "Business hub · verified";
    case "review":
      return "Business hub · verification in review";
    case "problem":
      return "Business hub · verification needs you";
    default:
      return "Business hub";
  }
}

/* ------------------------------------------------------------------ *
 * Badges — counts that are waiting on the seller, and nothing else.
 * ------------------------------------------------------------------ */

/**
 * Badges are for things awaiting the seller, not vanity numbers. Active listings
 * and delivering campaigns are achievements, not queues, so they never badge.
 * Only three cards can carry one, and each maps to a queue the seller can empty.
 */
export function hubBadge(card: HubCardKey, value: number | null): number | null {
  if (value === null || value <= 0) return null;
  if (card === "orders" || card === "messages") return value;
  if (card === "marketplace") return HUB_MARKETPLACE_OFFERS ? value : null;
  return null;
}

/* ------------------------------------------------------------------ *
 * Cards
 * ------------------------------------------------------------------ */

export type HubCardKey =
  | "profile"
  | "store"
  | "marketplace"
  | "advertising"
  | "orders"
  | "messages"
  | "insights"
  | "payments"
  | "events"
  | "verification"
  | "settings";

/**
 * Icon tints. Each card reuses the tint its own domain established in its own
 * mission, so the grid reads as a legend of the app's colour semantics rather
 * than as decoration: money is gold, content promotion violet, analytics blue.
 */
export const HUB_CARD_TINTS: Record<HubCardKey, string> = {
  profile: "#067D62",
  insights: "#0B8A6F",
  payments: "#12886B",
  store: "#2563C9",
  messages: "#2F6FDB",
  marketplace: "#7C4DDB",
  advertising: "#C98A16",
  orders: "#D2761B",
  events: "#D4453C",
  verification: "#3E6DB5",
  settings: "#5A6B7F"
};

/* ------------------------------------------------------------------ *
 * Staleness — the rule that stops a cached countdown from lying.
 * ------------------------------------------------------------------ */

/**
 * How old a source's data may be before its line stops being trustworthy.
 *
 * The rule only bites on time-critical lines: a listing count from four minutes
 * ago is still true, but a deadline from four minutes ago may already have
 * passed. Anything not listed here has no window — it is a fact about the world
 * that does not expire between refreshes, and degrading it would blank the
 * screen for no gain.
 *
 * `Infinity` is written out deliberately for the non-critical sources so that
 * adding a new binding forces a decision rather than inheriting a default.
 */
export const HUB_STALENESS_MS: Record<string, number> = {
  /** Never cached at all — a live session that ended two minutes ago is not
   *  "slightly stale", it is wrong, and it is the loudest thing on the screen. */
  live: 0,
  /** Offer expiry counts down in hours; a window shorter than the smallest unit
   *  it displays keeps it from ever showing an hour that has already elapsed. */
  offers: 5 * 60 * 1000,
  /** Ship-by pressure, when a source for it exists. */
  orderDeadlines: 5 * 60 * 1000,
  orders: Number.POSITIVE_INFINITY,
  store: Number.POSITIVE_INFINITY,
  insightsToday: Number.POSITIVE_INFINITY,
  insights7d: Number.POSITIVE_INFINITY,
  ads: Number.POSITIVE_INFINITY,
  wallet: Number.POSITIVE_INFINITY,
  verification: Number.POSITIVE_INFINITY,
  profile: Number.POSITIVE_INFINITY,
  unread: Number.POSITIVE_INFINITY
};

/**
 * True when a value is too old to be shown as a deadline.
 *
 * A stale time-critical value degrades to its non-deadline fallback — the caller
 * drops the countdown and keeps the count — rather than rendering a possibly
 * wrong number. Showing "expires in 5h" from a five-minute-old snapshot is the
 * one failure the seller cannot detect for themselves.
 */
export function isStale(sourceKey: string, loadedAt: number, now: number): boolean {
  const window = HUB_STALENESS_MS[sourceKey];
  if (window === undefined) return false;
  if (!Number.isFinite(window)) return false;
  if (!loadedAt) return true;
  return now - loadedAt > window;
}

/* ------------------------------------------------------------------ *
 * Today strip
 * ------------------------------------------------------------------ */

export type HubStripCell = {
  key: "sales" | "fulfil" | "offers" | "unread";
  label: string;
  /** Already formatted, or "—" when the source is unavailable. */
  value: string;
  /** Amber treatment. Only ever true for a real, non-zero pressure count. */
  hot: boolean;
  /** Announced destination, so the cell reads as a link and not a statistic. */
  destination: string;
};

const DASH = "—";

/**
 * The four cells.
 *
 * DEVIATION, deliberate and visible: the design's second cell is "Ship today".
 * That label asserts a deadline, and no order on this platform carries one. The
 * cell is labelled "To fulfil" and shows the count of open unfulfilled orders —
 * a real number under an honest label — rather than "—" under a label promising
 * something better. It still deep-links to Orders. When an SLA lands, the label
 * and the hot variant come back together.
 *
 * A cell with no source renders "—" and links anyway: the section behind it can
 * answer the question even when the hub cannot summarise it.
 */
export function todayStripCells(input: {
  salesLabel: string | null;
  awaitingFulfilment: number | null;
  openOffers: number | null;
  unread: number | null;
}): HubStripCell[] {
  return [
    {
      key: "sales",
      label: "Today's sales",
      value: input.salesLabel ?? DASH,
      hot: false,
      destination: "Insights"
    },
    {
      key: "fulfil",
      label: "To fulfil",
      value: input.awaitingFulfilment === null ? DASH : String(input.awaitingFulfilment),
      // The design's hot variant marks deadline pressure. Without an SLA there is
      // no deadline, so this stays cool however large the queue gets — a big
      // number is not the same claim as a late one.
      hot: HUB_ORDER_DEADLINES && (input.awaitingFulfilment ?? 0) > 0,
      destination: "Orders"
    },
    {
      key: "offers",
      label: "Offers",
      value: HUB_MARKETPLACE_OFFERS && input.openOffers !== null ? String(input.openOffers) : DASH,
      hot: false,
      destination: "Marketplace"
    },
    {
      key: "unread",
      label: "Unread",
      value: input.unread === null ? DASH : String(input.unread),
      hot: false,
      destination: "Messages"
    }
  ];
}

/* ------------------------------------------------------------------ *
 * Accessibility
 * ------------------------------------------------------------------ */

/**
 * One card, one announcement. Title, subtitle and state line are read as a
 * single element so a screen-reader user hears the card the way a sighted user
 * sees it, and urgency arrives as a word rather than as a colour.
 */
export function cardAccessibilityLabel(input: {
  title: string;
  subtitle: string;
  state: HubStateLineResult;
  badge: number | null;
}): string {
  const parts = [`${input.title}.`, `${input.subtitle}`];
  if (input.state) {
    parts.push(input.state.urgent ? `Urgent: ${input.state.text}.` : `${input.state.text}.`);
  }
  if (input.badge !== null && input.badge > 0) {
    parts.push(badgeAnnouncement(input.title, input.badge));
  }
  return parts.join(" ").replace(/\s+/g, " ").trim();
}

/** Badges announce a count with its meaning, never a bare number. */
export function badgeAnnouncement(title: string, count: number): string {
  const noun =
    title === "Messages"
      ? `unread conversation${count === 1 ? "" : "s"}`
      : title === "Orders"
        ? `order${count === 1 ? "" : "s"} awaiting you`
        : `item${count === 1 ? "" : "s"} awaiting you`;
  return `${count} ${noun}.`;
}
