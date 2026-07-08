import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { Linking } from "react-native";
import { PULSE_API_BASE_URL } from "../api/config";
import { dashboardModuleGroups, DashboardModuleGroup, DashboardModuleItem } from "../data/dashboardModules";
import { RootStackParamList } from "./types";

export type DashboardNavigation = NativeStackNavigationProp<RootStackParamList>;
export type DashboardModuleRouteMatch = {
  group: DashboardModuleGroup;
  module: DashboardModuleItem;
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
  if (normalized === "/dashboard" || normalized === "/pulse/dashboard") return;
  if (normalized === "/pulse" || normalized === "/dashboard/creator/posts") {
    navigation.navigate("Tabs", { screen: "Home" });
    return;
  }
  if (normalized.includes("/notifications") || normalized.includes("/activity")) {
    navigation.navigate("ActivityInbox", { title: "Activity Inbox" });
    return;
  }
  if (normalized.includes("/messages")) {
    navigation.navigate("Tabs", { screen: "Messenger" });
    return;
  }
  if (normalized.includes("/network/groups")) {
    navigation.navigate("Tabs", { screen: "Groups" });
    return;
  }
  if (normalized.includes("/reels")) {
    navigation.navigate("Reels", { title: "Reels" });
    return;
  }
  if (normalized.includes("/status") || normalized.includes("/statuses")) {
    navigation.navigate("Tabs", { screen: "Status" });
    return;
  }
  if (normalized.includes("/live")) {
    navigation.navigate("Tabs", { screen: "Live" });
    return;
  }
  if (normalized.includes("/marketplace")) {
    navigation.navigate("Tabs", { screen: "Marketplace" });
    return;
  }
  if (normalized.includes("/seller-tools") || normalized.includes("/merchant")) {
    navigation.navigate("SellerStore", { title: "Seller / Store" });
    return;
  }
  if (normalized.includes("/subscriptions") || normalized.includes("/premium")) {
    navigation.navigate("Premium");
    return;
  }
  if (normalized.includes("/verification")) {
    navigation.navigate("VerificationCenter", { title: "Verification Center" });
    return;
  }
  if (normalized.includes("/account/health")) {
    navigation.navigate("AccountHealth", { title: "Account Health" });
    return;
  }
  if (normalized.includes("/account/security")) {
    navigation.navigate("AccountCenter", { section: "security", title: "Security Center" });
    return;
  }
  if (normalized.includes("/account/settings")) {
    navigation.navigate("Tabs", { screen: "Settings" });
    return;
  }
  if (normalized.includes("/account/profile")) {
    navigation.navigate("Tabs", { screen: "Profile" });
    return;
  }
  if (normalized.includes("/support") || normalized.includes("/profile/security") || normalized.includes("/scam-shield")) {
    navigation.navigate("SafetyHub", { title: "Safety Hub" });
    return;
  }
  if (normalized.includes("/creator/content-planner")) {
    navigation.navigate("ContentPlanner", { mode: "planner", title: "Content Planner" });
    return;
  }
  if (normalized.includes("/creator/post-scheduler")) {
    navigation.navigate("ContentPlanner", { mode: "scheduler", title: "Post Scheduler" });
    return;
  }
  if (normalized.includes("/creator/draft-studio")) {
    navigation.navigate("ContentPlanner", { mode: "drafts", title: "Draft Studio" });
    return;
  }
  if (normalized.includes("/creator")) {
    navigation.navigate("CreatorStudio");
    return;
  }
  if (normalized.includes("/growth") || normalized.includes("/ads")) {
    navigation.navigate("GrowthCenter", { title: "Growth Center" });
    return;
  }
  if (normalized.includes("/crypto/alerts") || normalized.includes("/dashboard/crypto")) {
    navigation.navigate("AlertManagement", { title: "Alerts" });
    return;
  }
  if (normalized.includes("/intelligence") || normalized.includes("/signals") || normalized.includes("/briefing") || normalized.includes("/forecasts")) {
    navigation.navigate("IntelligenceCenter", { title: "Intelligence" });
    return;
  }
  if (normalized.includes("/saved")) {
    navigation.navigate("Saved");
    return;
  }
  if (normalized.includes("/videos") || normalized.includes("/music")) {
    openDashboardWebFallback(normalized);
    return;
  }
  if (normalized.includes("/ai")) {
    navigation.navigate("Tabs", { screen: "PulseAI" });
    return;
  }
  if (normalized.includes("/system")) {
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
  return new RegExp("/pulse($|/)|/dashboard/(account|network|creator|intelligence|economy|media|crypto|safety|ads|ai|system)|/support|/scam-shield").test(route || "");
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
