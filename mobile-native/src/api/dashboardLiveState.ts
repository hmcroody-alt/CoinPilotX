import { DashboardModuleGroup, DashboardModuleItem } from "../data/dashboardModules";
import { listCryptoAlerts } from "./intelligence";
import { loadUserDashboardState, UserDashboardState } from "./dashboard";
import { creatorRecommendations, creatorScore } from "./creator";
import { growthMoney } from "./growth";
import { premiumPlanLabel } from "./premium";

export type DashboardLiveMetric = {
  label: string;
  value: string;
  detail: string;
  state: "ready" | "attention" | "fallback" | "offline";
};

export type DashboardLiveStatePanel = {
  title: string;
  source: string;
  loadedAt: string;
  loadedFromCache: boolean;
  metrics: DashboardLiveMetric[];
  signals: string[];
  warnings: string[];
  fallbackOnly: boolean;
};

export async function loadDashboardModuleLiveState(group: DashboardModuleGroup, module: DashboardModuleItem): Promise<DashboardLiveStatePanel> {
  const [dashboardResult, alertsResult] = await Promise.allSettled([
    loadUserDashboardState(),
    group.key === "crypto-command-center" || group.key === "intelligence" ? listCryptoAlerts() : Promise.resolve({ alerts: [] })
  ]);
  if (dashboardResult.status === "rejected") {
    throw dashboardResult.reason;
  }
  const alerts = alertsResult.status === "fulfilled" ? alertsResult.value.alerts || [] : [];
  return buildDashboardModuleLiveState(dashboardResult.value, group, module, alerts.length);
}

export function buildDashboardModuleLiveState(state: UserDashboardState, group: DashboardModuleGroup, module: DashboardModuleItem, alertCount = 0): DashboardLiveStatePanel {
  const commonWarnings = (state.warnings || []).slice(0, 3);
  const fallbackOnly = module.status === "COMING_SOON" || module.access === "fallback";
  const panel: DashboardLiveStatePanel = {
    title: `${module.title} live state`,
    source: "Your PulseSoc account, brought together in one place",
    loadedAt: state.loadedAt,
    loadedFromCache: Boolean(state.loadedFromCache),
    metrics: metricsForGroup(state, group.key, module.key, alertCount),
    signals: signalsForGroup(state, group.key, module.key),
    warnings: fallbackOnly ? ["This module uses the shared dashboard view until its own detailed view is ready.", ...commonWarnings] : commonWarnings,
    fallbackOnly
  };
  return {
    ...panel,
    metrics: panel.metrics.length ? panel.metrics : fallbackMetrics(state, group, module),
    signals: panel.signals.length ? panel.signals : ["These figures come from your PulseSoc dashboard.", "Some advanced actions for this module still open on the PulseSoc website."]
  };
}

function metricsForGroup(state: UserDashboardState, groupKey: string, moduleKey: string, alertCount: number): DashboardLiveMetric[] {
  if (groupKey === "account-command-center") return accountMetrics(state, moduleKey);
  if (groupKey === "pulse-network") return networkMetrics(state, moduleKey);
  if (groupKey === "creator-studio") return creatorMetrics(state, moduleKey);
  if (groupKey === "intelligence") return intelligenceMetrics(state, moduleKey, alertCount);
  if (groupKey === "economy-earnings") return economyMetrics(state, moduleKey);
  if (groupKey === "pulse-radio-media") return mediaMetrics(state, moduleKey);
  if (groupKey === "crypto-command-center") return cryptoMetrics(state, moduleKey, alertCount);
  if (groupKey === "moderation-safety") return safetyMetrics(state, moduleKey);
  if (groupKey === "ads-sponsorships") return adsMetrics(state, moduleKey);
  if (groupKey === "pulsesoc-ai") return aiMetrics(state, moduleKey);
  if (groupKey === "system-status") return systemMetrics(state, moduleKey);
  return [];
}

function accountMetrics(state: UserDashboardState, moduleKey: string): DashboardLiveMetric[] {
  const verificationStatus = String(state.verification?.status || state.profile?.verification_status || "not started").replace(/_/g, " ");
  const healthScore = Number(state.accountHealth?.accountScore || state.accountHealth?.score || 0);
  const securityEvents = state.accountHealth?.securityEvents.length || 0;
  return [
    metric("Account health", `${healthScore}%`, `${state.accountHealth?.warnings || 0} warnings and ${state.accountHealth?.restrictions || 0} restrictions reported by account health.`, state.accountHealth?.restrictions ? "attention" : "ready"),
    metric("Verification", verificationStatus, `${state.verification?.score || 0}% of the way through verification.`, verificationStatus.includes("approved") ? "ready" : "attention"),
    metric("Security events", String(securityEvents), "Owner-visible login, device, and security event count from existing security history.", securityEvents ? "attention" : "ready"),
    metric("Profile identity", state.profile?.display_name || state.user?.display_name || "Member", `@${state.profile?.username || state.user?.username || "profile"} · ${moduleKey.replace(/_/g, " ")}`, "ready")
  ];
}

function networkMetrics(state: UserDashboardState, moduleKey: string): DashboardLiveMetric[] {
  const unread = Number(state.activity?.unreadTotal || 0);
  const calls = state.calls.filter((call) => !["ended", "declined", "missed", "failed"].includes(String(call.status || "").toLowerCase())).length;
  const messages = state.conversations.length;
  return [
    metric("Unread activity", String(unread), "Unread messages, calls, social, commerce, and trust signals from Activity Inbox.", unread ? "attention" : "ready"),
    metric("Conversations", String(messages), "Summaries only. What people wrote is never shown here.", "ready"),
    metric("Active calls", String(calls), "Incoming and active calls are handled by the calls screen in the app.", calls ? "attention" : "ready"),
    metric("Category signal", networkCategoryValue(state, moduleKey), "Category-specific signal derived from Activity Inbox and dashboard aggregation.", "ready")
  ];
}

function creatorMetrics(state: UserDashboardState, moduleKey: string): DashboardLiveMetric[] {
  const creator = state.creator;
  const score = creatorScore(creator);
  const drafts = matchingCreatorCardCount(state, "draft");
  return [
    metric("Creator score", `${score}%`, creatorRecommendations(creator)[0] || "Your creator score, with suggestions for raising it.", score ? "ready" : "fallback"),
    metric("Posts", String(creator?.posts?.total ?? state.posts.length), "Everything you have published, and how much of it is recent.", "ready"),
    metric("Reels / videos", `${creator?.reels?.total || 0} / ${creator?.videos?.total || 0}`, "Short-form and long-form creator media totals.", "ready"),
    metric("Draft planning", String(drafts), `Planner state for ${moduleKey.replace(/_/g, " ")} is read-only in this shell.`, drafts ? "attention" : "ready")
  ];
}

function intelligenceMetrics(state: UserDashboardState, moduleKey: string, alertCount: number): DashboardLiveMetric[] {
  const hub = state.intelligence?.intelligence?.hub;
  const cards = state.intelligence?.intelligence?.cards || [];
  return [
    metric("Intelligence score", `${Number(hub?.overall_intelligence_score || 0)}%`, hub?.personalized_daily_brief || "Your daily brief will appear here once there is enough to say.", hub?.overall_intelligence_score ? "ready" : "fallback"),
    metric("Active alerts", String(alertCount), "Crypto and market alerts you have set up.", alertCount ? "attention" : "ready"),
    metric("Opportunities", String(Number(hub?.new_opportunities || 0)), "Trending opportunities and recommendations from intelligence hub.", hub?.new_opportunities ? "attention" : "ready"),
    metric("Source cards", String(cards.length), `Source cards mapped for ${moduleKey.replace(/_/g, " ")}.`, cards.length ? "ready" : "fallback")
  ];
}

function economyMetrics(state: UserDashboardState, moduleKey: string): DashboardLiveMetric[] {
  const orders = state.buyerOrders.length;
  const sellerListings = state.sellerStore?.listings || [];
  const sellerOrders = state.sellerStore?.orders || [];
  return [
    metric("Buyer orders", String(orders), "What you have bought, and where each order has got to.", orders ? "attention" : "ready"),
    metric("Seller listings", String(sellerListings.length), "Seller-owned listings include pending, paused, approved, and deleted-safe states.", sellerListings.length ? "ready" : "fallback"),
    metric("Seller orders", String(sellerOrders.length), `Seller-side order summary for ${moduleKey.replace(/_/g, " ")}.`, sellerOrders.length ? "attention" : "ready"),
    metric("Premium plan", premiumPlanLabel(state.premium), "Premium, subscription, Founder, and entitlement status stay server verified.", state.premium?.premium_active ? "ready" : "fallback")
  ];
}

function mediaMetrics(state: UserDashboardState, moduleKey: string): DashboardLiveMetric[] {
  const postsWithMedia = state.posts.filter((post) => (post.media || []).length > 0).length;
  const creator = state.creator;
  return [
    metric("Feed media", String(postsWithMedia), "Recent posts from your feed that include photos or video.", postsWithMedia ? "ready" : "fallback"),
    metric("Reels", String(creator?.reels?.total || 0), "Creator reel totals from the creator dashboard state.", creator?.reels?.total ? "ready" : "fallback"),
    metric("Videos", String(creator?.videos?.total || 0), "How many videos you have, and how many are still being processed.", creator?.videos?.processing ? "attention" : "ready"),
    metric("Module media surface", moduleKey.replace(/_/g, " "), "Upload, library, music, and radio tools are partly on the PulseSoc website.", "fallback")
  ];
}

function cryptoMetrics(state: UserDashboardState, moduleKey: string, alertCount: number): DashboardLiveMetric[] {
  const hub = state.intelligence?.intelligence?.hub;
  const sourceCards = state.intelligence?.intelligence?.cards || [];
  return [
    metric("Crypto alerts", String(alertCount), "Crypto alerts you set up. Only you can see them.", alertCount ? "attention" : "ready"),
    metric("Watchlist sources", String(sourceCards.length), "Available intelligence source cards and watchlist-adjacent signals.", sourceCards.length ? "ready" : "fallback"),
    metric("Market opportunities", String(Number(hub?.new_opportunities || 0)), "Opportunity count comes from the intelligence hub.", hub?.new_opportunities ? "attention" : "ready"),
    metric("Risk posture", moduleKey.replace(/_/g, " "), "Crypto screens remain educational and server-bounded; no local trading logic is added.", "ready")
  ];
}

function safetyMetrics(state: UserDashboardState, moduleKey: string): DashboardLiveMetric[] {
  const safety = state.safety;
  return [
    metric("Network trust", `${safety?.network.networkTrustScore || 0}%`, "Trust score from safety/network state.", safety?.network.networkTrustScore ? "ready" : "fallback"),
    metric("Reports", String(safety?.reports.length || 0), "Reports and safety requests you have sent in.", safety?.reports.length ? "attention" : "ready"),
    metric("Cases", String(safety?.cases.length || 0), "Linked support and moderation cases visible to the owner.", safety?.cases.length ? "attention" : "ready"),
    metric("Account standing", state.accountHealth?.status || moduleKey.replace(/_/g, " "), "Account health remains the source of enforcement and appeal state.", state.accountHealth?.restrictions ? "attention" : "ready")
  ];
}

function adsMetrics(state: UserDashboardState, moduleKey: string): DashboardLiveMetric[] {
  const growth = state.growth?.growth;
  const portal = state.growth?.portal;
  return [
    metric("Growth score", `${growth?.growth_score || 0}%`, "How ready your account is to promote and grow.", growth?.growth_score ? "ready" : "fallback"),
    metric("Campaign cards", String(portal?.cards?.length || 0), "Campaign, audience, and analytics cards exposed by the growth portal.", portal?.cards?.length ? "ready" : "fallback"),
    metric("Wallet credits", growthMoney(growth?.wallet?.credits_cents || 0, growth?.wallet?.currency || "usd"), "Your budget and wallet, as PulseSoc last recorded them.", growth?.wallet?.credits_cents ? "attention" : "ready"),
    metric("Ad module", moduleKey.replace(/_/g, " "), "Launching campaigns, billing, and partner tools still open on the PulseSoc website.", "fallback")
  ];
}

function aiMetrics(state: UserDashboardState, moduleKey: string): DashboardLiveMetric[] {
  const hub = state.intelligence?.intelligence?.hub;
  const recommendations = hub?.recommended_next_actions || [];
  return [
    metric("AI readiness", `${Number(hub?.overall_intelligence_score || 0)}%`, "Your AI readiness, taken from the Intelligence dashboard.", hub?.overall_intelligence_score ? "ready" : "fallback"),
    metric("Recommendations", String(recommendations.length), recommendations[0] || "AI recommendations are sourced from approved dashboard intelligence.", recommendations.length ? "attention" : "ready"),
    metric("Active threats", String(Number(hub?.active_threats || 0)), "Threat and safety signals are read-only in the dashboard shell.", hub?.active_threats ? "attention" : "ready"),
    metric("AI module", moduleKey.replace(/_/g, " "), "Advanced AI tools still open on the PulseSoc website.", "fallback")
  ];
}

function systemMetrics(state: UserDashboardState, moduleKey: string): DashboardLiveMetric[] {
  return [
    metric("Last refresh", formatTime(state.loadedAt), "When the app last loaded your dashboard.", "ready"),
    metric("Sync mode", state.loadedFromCache ? "Cached" : "Live", state.loadedFromCache ? "Offline/cache fallback is active." : "Server state refreshed successfully.", state.loadedFromCache ? "offline" : "ready"),
    metric("Warnings", String(state.warnings.length), "Subsystem load warnings captured during dashboard aggregation.", state.warnings.length ? "attention" : "ready"),
    metric("System module", moduleKey.replace(/_/g, " "), `${state.activity?.unreadTotal || 0} unread activity items · ${state.marketplaceListings.length} marketplace listings.`, "ready")
  ];
}

function fallbackMetrics(state: UserDashboardState, group: DashboardModuleGroup, module: DashboardModuleItem): DashboardLiveMetric[] {
  return [
    metric("Dashboard source", group.label, "This module is represented by the production dashboard module map.", "fallback"),
    metric("Native shell", module.status.replace(/_/g, " "), "The reusable shell is active while dedicated data contracts are completed.", module.status === "COMING_SOON" ? "fallback" : "ready"),
    metric("Server refresh", formatTime(state.loadedAt), "Everything on this dashboard is up to date.", state.loadedFromCache ? "offline" : "ready")
  ];
}

function signalsForGroup(state: UserDashboardState, groupKey: string, moduleKey: string): string[] {
  if (groupKey === "account-command-center") {
    return [
      `Verification is ${String(state.verification?.status || "not started").replace(/_/g, " ")}.`,
      `${state.accountHealth?.appealsAvailable || 0} appeals available through account health.`,
      `${state.accountHealth?.securityEvents.length || 0} owner-visible security events are available.`
    ];
  }
  if (groupKey === "pulse-network") {
    return [
      `${state.activity?.unreadTotal || 0} unread activity items are feeding the inbox.`,
      `${state.conversations.length} conversation summaries are available without exposing private bodies.`,
      `${state.calls.length} call records are visible in the app.`
    ];
  }
  if (groupKey === "creator-studio") {
    return [
      `${state.creator?.posts?.total ?? state.posts.length} posts are visible to creator state.`,
      `${creatorRecommendations(state.creator).length} creator recommendations are available.`,
      `${state.creator?.event_bus?.length || 0} creator event-bus entries are exposed.`
    ];
  }
  if (groupKey === "economy-earnings") {
    return [
      `${state.buyerOrders.length} buyer orders are available.`,
      `${state.sellerStore?.listings.length || 0} seller-owned listings are available.`,
      `${state.marketplaceListings.length} public marketplace listings are available.`
    ];
  }
  if (groupKey === "moderation-safety") {
    return [
      `${state.safety?.reports.length || 0} reports are visible.`,
      `${state.safety?.cases.length || 0} safety/support cases are visible.`,
      `${state.accountHealth?.strikes || 0} strikes and ${state.accountHealth?.restrictions || 0} restrictions are reported.`
    ];
  }
  if (groupKey === "system-status") {
    return [
      `${state.warnings.length} subsystem warnings were captured during refresh.`,
      `Dashboard refreshed at ${formatTime(state.loadedAt)}.`,
      `${state.activity?.items.length || 0} activity items are available for sync inspection.`
    ];
  }
  return [
    `${moduleKey.replace(/_/g, " ")} is backed by existing PulseSoc state where available.`,
    "A few advanced operations still open on the PulseSoc website.",
    `Dashboard refreshed at ${formatTime(state.loadedAt)}.`
  ];
}

function networkCategoryValue(state: UserDashboardState, moduleKey: string) {
  if (moduleKey.includes("message")) return `${state.conversations.length} conversations`;
  if (moduleKey.includes("notification")) return `${state.activity?.categoryCounts.social || 0} social`;
  if (moduleKey.includes("group") || moduleKey.includes("community")) return `${state.activity?.categoryCounts.social || 0} community`;
  if (moduleKey.includes("security")) return `${state.activity?.categoryCounts.safety || 0} safety`;
  return `${state.activity?.items.length || 0} events`;
}

function matchingCreatorCardCount(state: UserDashboardState, needle: string) {
  const lower = needle.toLowerCase();
  return (state.creator?.cards || []).filter((card) => `${card.key} ${card.label}`.toLowerCase().includes(lower)).length;
}

function metric(label: string, value: string, detail: string, state: DashboardLiveMetric["state"]): DashboardLiveMetric {
  return { label, value, detail, state };
}

function formatTime(value?: string) {
  if (!value) return "Unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}
