import { Linking } from "react-native";
import { PULSE_API_BASE_URL } from "../api/config";
import { dashboardModuleGroups, DashboardModuleGroup, DashboardModuleItem } from "../data/dashboardModules";
import { RootStackParamList } from "./types";

export type DashboardNavigation = {
  navigate: (...args: any[]) => void;
};
export type DashboardModuleRouteMatch = {
  group: DashboardModuleGroup;
  module: DashboardModuleItem;
};
export type DashboardActionRouteKind = "native_route" | "native_shell_route" | "safe_web_fallback" | "missing_invalid_route";
export type DashboardActionRouteClassification = {
  kind: DashboardActionRouteKind;
  label: string;
  detail: string;
  route: string;
};

export const DASHBOARD_LEGACY_GROUPS: Record<string, string> = {
  account: "account-command-center",
  network: "pulse-network",
  creator: "creator-studio",
  intelligence: "intelligence",
  economy: "economy-earnings",
  media: "pulse-radio-media",
  crypto: "crypto-command-center",
  safety: "moderation-safety",
  ads: "ads-sponsorships",
  ai: "pulsesoc-ai",
  system: "system-status"
};

export function openDashboardRoute(navigation: DashboardNavigation, route: string) {
  const normalized = route || "/dashboard";
  const path = normalizeDashboardPath(normalized);
  if (path === "/dashboard" || path === "/pulse/dashboard") return;
  if (path === "/pulse/compose") {
    navigation.navigate("Tabs", { screen: "Home", params: { openComposer: true } });
    return;
  }
  if (path === "/pulse" || path === "/dashboard/creator/posts") {
    navigation.navigate("Tabs", { screen: "Home", params: queryBoolean(normalized, "composer") || queryBoolean(normalized, "openComposer") ? { openComposer: true } : undefined });
    return;
  }
  if (path.startsWith("/pulse/camera")) {
    const target = queryValue(normalized, "target") as "feed" | "post" | "status" | "reel" | "message" | "avatar" | "cover" | "creator" | "marketplace" | "";
    const modeFromPath = path.match(/^\/pulse\/camera\/([^/]+)/)?.[1] || "";
    const mode = queryValue(normalized, "mode") || modeFromPath;
    navigation.navigate("CameraStudio", {
      target: target || "feed",
      mode: mode === "video" || mode === "status" || mode === "reel" ? mode : "photo",
      title: "Camera Studio"
    });
    return;
  }
  if (path.startsWith("/pulse/live/studio") || path.startsWith("/dashboard/creator/live-studio")) {
    openDashboardWebFallback(normalized);
    return;
  }
  if (path.includes("/notifications") || path.includes("/activity")) {
    navigation.navigate("ActivityInbox", { title: "Activity Inbox" });
    return;
  }
  if (path.includes("/messages")) {
    navigation.navigate("Tabs", { screen: "Messenger" });
    return;
  }
  if (path.includes("/network/groups")) {
    navigation.navigate("Tabs", { screen: "Groups" });
    return;
  }
  if (path.includes("/reels")) {
    navigation.navigate("Reels", { title: "Reels" });
    return;
  }
  if (path.includes("/status") || path.includes("/statuses")) {
    navigation.navigate("Tabs", { screen: "Status", params: queryBoolean(normalized, "openCreator") ? { openCreator: true } : undefined });
    return;
  }
  if (path.includes("/live")) {
    navigation.navigate("Tabs", { screen: "Live" });
    return;
  }
  if (path.startsWith("/pulse/marketplace/create")) {
    navigation.navigate("MarketplaceCreateGateway", { title: "Create Listing" });
    return;
  }
  if (path.includes("/marketplace")) {
    navigation.navigate("Tabs", { screen: "Marketplace" });
    return;
  }
  if (path.includes("/seller-tools") || path.includes("/seller-store") || path.includes("/merchant")) {
    navigation.navigate("SellerStore", { title: "Seller / Store" });
    return;
  }
  if (path.includes("/orders") || path.includes("/purchases")) {
    navigation.navigate("BuyerOrders", { title: "Purchase History" });
    return;
  }
  if (path.includes("/subscriptions") || path.includes("/premium")) {
    navigation.navigate("Premium");
    return;
  }
  if (path.includes("/verification")) {
    navigation.navigate("VerificationCenter", { title: "Verification Center" });
    return;
  }
  if (path.includes("/account/health")) {
    navigation.navigate("AccountHealth", { title: "Account Health" });
    return;
  }
  if (path.includes("/account/security")) {
    navigation.navigate("AccountCenter", { section: "security", title: "Security Center" });
    return;
  }
  if (path.includes("/account/settings")) {
    navigation.navigate("Tabs", { screen: "Settings" });
    return;
  }
  if (path.includes("/account/profile")) {
    navigation.navigate("Tabs", { screen: "Profile" });
    return;
  }
  if (path.includes("/support") || path.includes("/profile/security") || path.includes("/scam-shield") || path.includes("/safety")) {
    navigation.navigate("SafetyHub", { title: "Safety Hub" });
    return;
  }
  if (path.includes("/creator/content-planner")) {
    navigation.navigate("ContentPlanner", { mode: "planner", title: "Content Planner" });
    return;
  }
  if (path.includes("/creator/post-scheduler")) {
    navigation.navigate("ContentPlanner", { mode: "scheduler", title: "Post Scheduler" });
    return;
  }
  if (path.includes("/creator/draft-studio")) {
    navigation.navigate("ContentPlanner", { mode: "drafts", title: "Draft Studio" });
    return;
  }
  if (path.includes("/creator")) {
    navigation.navigate("CreatorStudio");
    return;
  }
  if (path.includes("/growth") || path.includes("/ads")) {
    navigation.navigate("GrowthCenter", { title: "Growth Center" });
    return;
  }
  if (path.includes("/crypto/alerts")) {
    navigation.navigate("AlertManagement", { title: "Alerts" });
    return;
  }
  if (path.includes("/intelligence") || path.includes("/signals") || path.includes("/briefing") || path.includes("/forecasts")) {
    navigation.navigate("IntelligenceCenter", { title: "Intelligence" });
    return;
  }
  if (path.includes("/saved")) {
    navigation.navigate("Saved");
    return;
  }
  const moduleParams = dashboardModuleParamsForRoute(path);
  if (moduleParams) {
    navigation.navigate("DashboardModuleDetail", moduleParams);
    return;
  }
  if (path.includes("/videos") || path.includes("/music")) {
    openDashboardWebFallback(normalized);
    return;
  }
  if (path.includes("/ai")) {
    navigation.navigate("Tabs", { screen: "PulseAI" });
    return;
  }
  if (path.includes("/system")) {
    navigation.navigate("IntelligenceCenter", { title: "System Status" });
    return;
  }
  openDashboardWebFallback(normalized);
}

export function openDashboardAccessRoute(navigation: DashboardNavigation, module: DashboardModuleItem) {
  if (module.lockReason?.includes("Premium")) {
    navigation.navigate("Premium");
    return;
  }
  if (module.lockReason?.includes("Seller")) {
    navigation.navigate("SellerStore", { title: "Seller / Store" });
    return;
  }
  if (module.lockReason?.includes("Creator")) {
    navigation.navigate("CreatorStudio");
    return;
  }
  openDashboardRoute(navigation, module.route);
}

export function isNativeDashboardRoute(route: string) {
  const classification = classifyDashboardActionRoute(route);
  return classification.kind === "native_route" || classification.kind === "native_shell_route";
}

export function classifyDashboardActionRoute(route: string): DashboardActionRouteClassification {
  const raw = String(route || "").trim();
  const path = normalizeDashboardPath(raw);
  if (!raw || isUnsafeDashboardRoute(raw) || isUnsafeDashboardRoute(path)) {
    return {
      kind: "missing_invalid_route",
      label: "Invalid",
      detail: "This dashboard action does not have a safe route.",
      route: raw
    };
  }
  if (dashboardModuleParamsForRoute(path)) {
    return {
      kind: "native_shell_route",
      label: "Native shell",
      detail: "Opens the native dashboard module shell backed by the production module map.",
      route: raw
    };
  }
  if (isKnownSafeFallbackPath(path)) {
    return {
      kind: "safe_web_fallback",
      label: "Safe fallback",
      detail: "Uses the protected production route until dedicated native support exists.",
      route: raw
    };
  }
  if (isKnownNativeDashboardPath(path)) {
    return {
      kind: "native_route",
      label: "Native",
      detail: "Opens an existing native PulseSoc surface.",
      route: raw
    };
  }
  if (path.startsWith("/dashboard/")) {
    return {
      kind: "safe_web_fallback",
      label: "Safe fallback",
      detail: "Legacy dashboard URL is not represented in the native module registry yet.",
      route: raw
    };
  }
  return {
    kind: "missing_invalid_route",
    label: "Invalid",
    detail: "No native, shell, or safe fallback destination is registered for this action.",
    route: raw
  };
}

export function dashboardWebUrl(route: string) {
  return route.startsWith("http") ? route : `${PULSE_API_BASE_URL}${route.startsWith("/") ? route : `/${route}`}`;
}

export function openDashboardWebFallback(route: string) {
  Linking.openURL(dashboardWebUrl(route)).catch(() => undefined);
}

export function normalizeDashboardPath(route: string) {
  if (!route) return "/dashboard";
  let path = route.trim();
  try {
    if (/^https?:\/\//i.test(path)) {
      path = new URL(path).pathname;
    }
  } catch {
    path = route.trim();
  }
  path = path.split("#")[0].split("?")[0];
  if (!path.startsWith("/")) path = `/${path}`;
  return path.length > 1 ? path.replace(/\/+$/, "") : path;
}

export function dashboardModuleParamsForRoute(route: string): RootStackParamList["DashboardModuleDetail"] | undefined {
  const match = findDashboardModuleByRoute(route);
  if (!match) return undefined;
  return {
    groupKey: match.group.key,
    moduleKey: match.module.key,
    title: match.module.title
  };
}

export function findDashboardModuleByRoute(route: string): DashboardModuleRouteMatch | undefined {
  const path = normalizeDashboardPath(route);
  const exactMatch = findExactDashboardModuleRoute(path);
  if (exactMatch) return exactMatch;
  return findLegacyDashboardAlias(path);
}

function findExactDashboardModuleRoute(path: string): DashboardModuleRouteMatch | undefined {
  for (const group of dashboardModuleGroups) {
    for (const module of group.modules) {
      if (normalizeDashboardPath(module.route) === path) {
        return { group, module };
      }
    }
  }
  return undefined;
}

function findLegacyDashboardAlias(path: string): DashboardModuleRouteMatch | undefined {
  const parts = path.replace(/^\/dashboard\/?/, "").split("/").filter(Boolean);
  const [legacyGroup, ...moduleParts] = parts;
  if (!legacyGroup || moduleParts.length === 0) return undefined;

  const groupKey = DASHBOARD_LEGACY_GROUPS[legacyGroup];
  const group = dashboardModuleGroups.find((candidate) => candidate.key === groupKey);
  if (!group) return undefined;

  const requestedSlug = moduleParts.join("/");
  const module = group.modules.find((candidate) => legacyModuleAliases(candidate, legacyGroup).includes(requestedSlug));
  return module ? { group, module } : undefined;
}

function legacyModuleAliases(module: DashboardModuleItem, legacyGroup: string) {
  const aliases = new Set<string>();
  aliases.add(slugify(module.key));
  aliases.add(slugify(module.title));
  const route = normalizeDashboardPath(module.route);
  const groupPrefix = `/dashboard/${legacyGroup}/`;
  if (route.startsWith(groupPrefix)) aliases.add(route.slice(groupPrefix.length));
  const routeParts = route.split("/").filter(Boolean);
  if (routeParts.length > 0) aliases.add(routeParts[routeParts.length - 1]);
  if (routeParts.length > 1) aliases.add(routeParts.slice(-2).join("/"));
  return Array.from(aliases).filter(Boolean);
}

function slugify(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/&/g, "and")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function queryValue(route: string, name: string) {
  const query = String(route || "").split("?")[1]?.split("#")[0] || "";
  if (!query) return "";
  return new URLSearchParams(query).get(name) || "";
}

function queryBoolean(route: string, name: string) {
  const value = queryValue(route, name).toLowerCase();
  return value === "1" || value === "true" || value === "yes";
}

function isUnsafeDashboardRoute(route: string) {
  const value = route.toLowerCase();
  return value.startsWith("javascript:") || value.startsWith("data:") || value.startsWith("file:") || value.startsWith("/api/") || value.startsWith("/admin/") || value.startsWith("/static/") || value.includes("\\");
}

function isKnownNativeDashboardPath(path: string) {
  if (path === "/dashboard" || path === "/pulse/dashboard" || path === "/pulse" || path === "/pulse/compose") return true;
  return [
    "/pulse/camera",
    "/pulse/activity",
    "/pulse/inbox",
    "/pulse/messages",
    "/pulse/reels",
    "/pulse/status",
    "/pulse/live",
    "/pulse/marketplace",
    "/pulse/seller-store",
    "/pulse/merchant",
    "/pulse/orders",
    "/pulse/purchases",
    "/pulse/notifications",
    "/pulse/search",
    "/pulse/saved",
    "/pulse/groups",
    "/pulse/events",
    "/pulse/premium",
    "/pulse/creator-studio",
    "/pulse/content-planner",
    "/pulse/dashboard/content-planner",
    "/pulse/dashboard/post-scheduler",
    "/pulse/dashboard/draft-studio",
    "/pulse/growth",
    "/pulse/intelligence",
    "/pulse/alerts",
    "/pulse/settings",
    "/pulse/account-health",
    "/pulse/safety",
    "/pulse/blocks",
    "/pulse/mutes",
    "/pulse/reports",
    "/pulse/verification",
    "/pulse/courses",
    "/pulse/teachers",
    "/pulse/teacher-dashboard",
    "/pulse/ai",
    "/education/lesson",
    "/support",
    "/scam-shield",
    "/trust-center",
    "/security",
    "/dashboard/orders",
    "/dashboard/activity",
    "/dashboard/inbox"
  ].some((prefix) => path === prefix || path.startsWith(`${prefix}/`));
}

function isKnownSafeFallbackPath(path: string) {
  return [
    "/pulse/live/studio",
    "/pulse/videos",
    "/pulse/music",
    "/dashboard/home"
  ].some((prefix) => path === prefix || path.startsWith(`${prefix}/`));
}
