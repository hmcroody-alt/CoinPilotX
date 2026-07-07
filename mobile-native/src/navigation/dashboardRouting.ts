import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { Linking } from "react-native";
import { PULSE_API_BASE_URL } from "../api/config";
import { DashboardModuleItem } from "../data/dashboardModules";
import { RootStackParamList } from "./types";

export type DashboardNavigation = NativeStackNavigationProp<RootStackParamList>;

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
  return new RegExp("/pulse($|/)|/dashboard/(account|network|creator|economy|crypto|ads|ai|system)|/support|/scam-shield").test(route || "");
}

export function dashboardWebUrl(route: string) {
  return route.startsWith("http") ? route : `${PULSE_API_BASE_URL}${route.startsWith("/") ? route : `/${route}`}`;
}

export function openDashboardWebFallback(route: string) {
  Linking.openURL(dashboardWebUrl(route)).catch(() => undefined);
}
