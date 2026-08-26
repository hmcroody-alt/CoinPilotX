import { getSession, PulseUser } from "./auth";
import { loadActivityInboxState, ActivityInboxState } from "./activity";
import { getActiveCalls, PulseCall } from "./calls";
import { getCreatorState, CreatorState, creatorScore, creatorRecommendations } from "./creator";
import { dashboardModuleGroups, dashboardQuickActions, DashboardModuleGroup, DashboardQuickAction } from "../data/dashboardModules";
import { listFeed, PulsePost } from "./feed";
import { getGrowthState, GrowthState } from "./growth";
import { getIntelligenceState, IntelligenceState } from "./intelligence";
import { loadAccountHealthState, AccountHealthState } from "./accountHealth";
import { listMarketplaceSellerListings, loadSellerStoreSnapshot, MarketplaceListing, SellerStoreSnapshot, searchMarketplace } from "./marketplace";
import { listConversations, MessengerConversation } from "./messenger";
import { listBuyerOrders, BuyerOrder } from "./orders";
import { getPremiumStatus, PremiumStatus, premiumPlanLabel } from "./premium";
import { getMyProfile, PulseProfile } from "./profile";
import { loadSafetyState, SafetyState } from "./safety";
import { loadVerificationState, VerificationState } from "./verification";

export type DashboardModuleKey =
  | "home"
  | "activity"
  | "messenger"
  | "calls"
  | "profile"
  | "reels"
  | "status"
  | "marketplace"
  | "seller"
  | "orders"
  | "premium"
  | "verification"
  | "security"
  | "trust"
  | "creator"
  | "growth"
  | "intelligence"
  | "camera";

export type DashboardCard = {
  key: DashboardModuleKey;
  title: string;
  value: string;
  detail: string;
  state: "ready" | "attention" | "fallback" | "offline";
};

export type UserDashboardState = {
  loadedAt: string;
  loadedFromCache: boolean;
  user: PulseUser | null;
  profile: PulseProfile | null;
  activity: ActivityInboxState | null;
  conversations: MessengerConversation[];
  calls: PulseCall[];
  posts: PulsePost[];
  marketplaceListings: MarketplaceListing[];
  sellerStore: SellerStoreSnapshot | null;
  buyerOrders: BuyerOrder[];
  premium: PremiumStatus | null;
  verification: VerificationState | null;
  accountHealth: AccountHealthState | null;
  safety: SafetyState | null;
  creator: CreatorState | null;
  growth: GrowthState | null;
  intelligence: IntelligenceState | null;
  cards: DashboardCard[];
  quickActions: DashboardCard[];
  moduleGroups: DashboardModuleGroup[];
  dashboardQuickActionLinks: DashboardQuickAction[];
  recentActivity: Array<{ id: string; title: string; body: string; target?: string }>;
  warnings: string[];
};

type Settled<T> = PromiseSettledResult<T>;
type DashboardLoadedState = Omit<UserDashboardState, "cards" | "quickActions" | "moduleGroups" | "dashboardQuickActionLinks" | "recentActivity">;

export async function loadUserDashboardState(): Promise<UserDashboardState> {
  const [
    session,
    profile,
    activity,
    conversations,
    calls,
    feed,
    marketplace,
    sellerListings,
    sellerStore,
    buyerOrders,
    premium,
    verification,
    accountHealth,
    safety,
    creator,
    growth,
    intelligence
  ] = await Promise.allSettled([
    getSession(),
    getMyProfile(),
    loadActivityInboxState({ limit: 40 }),
    listConversations(),
    getActiveCalls(),
    listFeed({ feed: "for_you", limit: 12, offset: 0 }),
    searchMarketplace({ limit: 12 }),
    listMarketplaceSellerListings({ limit: 20 }),
    loadSellerStoreSnapshot(),
    listBuyerOrders({ limit: 20 }),
    getPremiumStatus(),
    loadVerificationState(),
    loadAccountHealthState(),
    loadSafetyState(),
    getCreatorState(),
    getGrowthState(),
    getIntelligenceState()
  ]);

  const normalized: DashboardLoadedState = {
    loadedAt: new Date().toISOString(),
    loadedFromCache: Boolean(value(activity)?.loadedFromCache),
    user: value(session)?.user || null,
    profile: value(profile) || null,
    activity: value(activity) || null,
    conversations: value(conversations) || [],
    calls: value(calls)?.calls || [],
    posts: value(feed)?.posts || [],
    marketplaceListings: value(marketplace)?.items || [],
    sellerStore: value(sellerStore) || {
      listings: value(sellerListings)?.items || [],
      orders: []
    },
    buyerOrders: value(buyerOrders)?.orders || [],
    premium: value(premium) || null,
    verification: value(verification) || null,
    accountHealth: value(accountHealth) || null,
    safety: value(safety) || null,
    creator: value(creator) || null,
    growth: value(growth) || null,
    intelligence: value(intelligence) || null,
    warnings: warningsFor([
      ["Activity", activity],
      ["Messenger", conversations],
      ["Calls", calls],
      ["Feed", feed],
      ["Marketplace", marketplace],
      ["Seller", sellerStore],
      ["Orders", buyerOrders],
      ["Premium", premium],
      ["Verification", verification],
      ["Account Health", accountHealth],
      ["Safety", safety],
      ["Creator", creator],
      ["Growth", growth],
      ["Intelligence", intelligence]
    ])
  };

  const cards = buildDashboardCards(normalized);
  return {
    ...normalized,
    cards,
    quickActions: cards.filter((card) => ["camera", "activity", "messenger", "seller", "creator", "intelligence"].includes(card.key)),
    moduleGroups: dashboardModuleGroups,
    dashboardQuickActionLinks: dashboardQuickActions,
    recentActivity: buildRecentActivity(normalized)
  };
}

function buildDashboardCards(state: DashboardLoadedState): DashboardCard[] {
  const unread = Number(state.activity?.unreadTotal || 0);
  const activeCalls = state.calls.filter((call) => !["ended", "declined", "missed", "failed"].includes(String(call.status || "").toLowerCase())).length;
  const sellerListings = state.sellerStore?.listings || [];
  const liveListings = sellerListings.filter((listing) => String(listing.status || listing.approval_status || "").toLowerCase().includes("approved")).length;
  const pendingListings = sellerListings.filter((listing) => String(listing.status || listing.approval_status || "").toLowerCase().includes("pending")).length;
  const unpaidOrders = state.buyerOrders.filter((order) => ["pending", "failed"].includes(String(order.status_group || order.status || "").toLowerCase())).length;
  const creatorMetric = creatorScore(state.creator);
  const intelligenceScore = Number(state.intelligence?.intelligence?.hub?.overall_intelligence_score || 0);
  const accountScore = Number(state.accountHealth?.accountScore || state.accountHealth?.score || 0);
  const verificationStatus = String(state.verification?.status || state.profile?.verification_status || "not started");

  return [
    {
      key: "profile",
      title: "Identity",
      value: state.profile?.display_name || state.user?.display_name || "PulseSoc member",
      detail: `@${state.profile?.username || state.user?.username || "profile"} · ${verificationStatus}`,
      state: verificationStatus.includes("approved") ? "ready" : "attention"
    },
    {
      key: "activity",
      title: "Activity",
      value: `${unread} unread`,
      detail: `${state.activity?.items.length || 0} recent signals across messages, calls, commerce, and trust.`,
      state: unread ? "attention" : "ready"
    },
    {
      key: "messenger",
      title: "Messenger",
      value: `${state.conversations.length} conversations`,
      detail: "Unread chats, summarised in your Activity Inbox.",
      state: "ready"
    },
    {
      key: "calls",
      title: "Calls",
      value: `${activeCalls} active`,
      detail: "Incoming and active calls open in the call screen.",
      state: activeCalls ? "attention" : "ready"
    },
    {
      key: "home",
      title: "Content Pulse",
      value: `${state.posts.length} feed items`,
      detail: "Your posts, status, reels, comments, reactions, and saves.",
      state: "ready"
    },
    {
      key: "marketplace",
      title: "Marketplace",
      value: `${state.marketplaceListings.length} listings`,
      detail: "Browse listings and their photos.",
      state: "ready"
    },
    {
      key: "seller",
      title: "Seller System",
      value: `${sellerListings.length} owned`,
      detail: `${liveListings} live · ${pendingListings} pending review.`,
      state: pendingListings ? "attention" : "ready"
    },
    {
      key: "orders",
      title: "Buyer Orders",
      value: `${state.buyerOrders.length} orders`,
      detail: unpaidOrders ? `${unpaidOrders} need attention.` : "Purchase history and receipts are available here.",
      state: unpaidOrders ? "attention" : "ready"
    },
    {
      key: "premium",
      title: "Premium",
      value: premiumPlanLabel(state.premium),
      detail: "Plan, billing, entitlement, and Founder status remain server verified.",
      state: state.premium?.premium_active ? "ready" : "fallback"
    },
    {
      key: "verification",
      title: "Verification",
      value: verificationStatus.replace(/_/g, " "),
      detail: `${state.verification?.score || 0}% trust readiness with private review handoffs.`,
      state: verificationStatus.includes("approved") ? "ready" : "attention"
    },
    {
      key: "security",
      title: "Security",
      value: `${accountScore || 0}%`,
      detail: `${state.accountHealth?.warnings || 0} warnings · ${state.accountHealth?.restrictions || 0} restrictions.`,
      state: state.accountHealth?.restrictions ? "attention" : "ready"
    },
    {
      key: "trust",
      title: "Safety",
      value: `${state.safety?.network.networkTrustScore || 0}%`,
      detail: `${state.safety?.cases.length || 0} support cases · ${state.safety?.reports.length || 0} reports tracked.`,
      state: state.safety?.cases.length ? "attention" : "ready"
    },
    {
      key: "creator",
      title: "Creator",
      value: `${creatorMetric || 0}%`,
      detail: creatorRecommendations(state.creator)[0] || "Creator Studio, planner, drafts, and AI tools are all linked here.",
      state: creatorMetric ? "ready" : "fallback"
    },
    {
      key: "growth",
      title: "Growth",
      value: `${state.growth?.growth?.growth_score || 0}%`,
      detail: `${state.growth?.portal?.cards?.length || 0} growth cards · campaign launch stays fallback-safe.`,
      state: "ready"
    },
    {
      key: "intelligence",
      title: "Intelligence",
      value: `${intelligenceScore || 0}%`,
      detail: `${state.intelligence?.intelligence?.hub?.active_threats || 0} active threats · alert center linked.`,
      state: intelligenceScore ? "ready" : "fallback"
    },
    {
      key: "camera",
      title: "Camera Studio",
      value: "Ready",
      detail: "Camera, media uploads, and the places you can post to are all connected.",
      state: "ready"
    }
  ];
}

function buildRecentActivity(state: DashboardLoadedState) {
  const activity = (state.activity?.items || []).slice(0, 5).map((item) => ({
    id: item.id,
    title: item.title,
    body: item.body,
    target: item.targetUrl
  }));
  const orders = state.buyerOrders.slice(0, 3).map((order) => ({
    id: `order-${order.id}`,
    title: order.item_title || "Purchase",
    body: `${String(order.status_group || order.status || "pending")} · ${order.seller?.display_name || "PulseSoc Seller"}`,
    target: `/pulse/orders/${order.id}`
  }));
  return [...activity, ...orders].slice(0, 7);
}

function value<T>(result: Settled<T>): T | null {
  return result.status === "fulfilled" ? result.value : null;
}

function warningsFor(results: Array<[string, Settled<unknown>]>) {
  return results
    .filter(([, result]) => result.status === "rejected")
    .map(([label, result]) => `${label}: ${result.status === "rejected" && result.reason instanceof Error ? result.reason.message : "Unavailable"}`)
    .slice(0, 8);
}
