import { Linking } from "react-native";
import * as Notifications from "expo-notifications";
import { createNavigationContainerRef } from "@react-navigation/native";
import { PULSE_API_BASE_URL } from "../api/config";
import { RootStackParamList } from "./types";

export const navigationRef = createNavigationContainerRef<RootStackParamList>();

export type NotificationRouteResult = {
  handled: boolean;
  target: string;
  reason?: string;
};

export function setupNotificationResponseRouting() {
  return Notifications.addNotificationResponseReceivedListener((response) => {
    routeNotificationData(response.notification.request.content.data).catch(() => undefined);
  });
}

export async function routeNotificationData(data: Notifications.NotificationContent["data"] = {}): Promise<NotificationRouteResult> {
  const target = normalizeNotificationTarget(
    String(data?.target_url || data?.deep_link || data?.url || data?.web_url || data?.native_url || data?.app_url || "")
  );
  return routeNotificationTarget(target);
}

export async function routeNotificationTarget(target: string): Promise<NotificationRouteResult> {
  const normalized = normalizeNotificationTarget(target);
  if (!normalized) {
    navigateToNotifications();
    return { handled: true, target: "/pulse/notifications", reason: "missing_target" };
  }

  const callMatch = normalized.match(/^\/pulse\/calls\/([^/?#]+)/);
  if (callMatch?.[1] && navigationRef.isReady()) {
    navigationRef.navigate("Call", { callId: decodeURIComponent(callMatch[1]), direction: "incoming", title: "PulseSoc Call" });
    return { handled: true, target: normalized };
  }

  const messageMatch = normalized.match(/^\/pulse\/messages\/(\d+)/);
  if (messageMatch?.[1] && navigationRef.isReady()) {
    const callId = extractStringQueryValue(normalized, "call_id") || extractStringQueryValue(normalized, "call");
    if (callId) {
      navigationRef.navigate("Call", {
        callId,
        conversationId: Number(messageMatch[1]),
        direction: "incoming",
        title: "PulseSoc Call"
      });
    } else {
      navigationRef.navigate("Chat", { conversationId: Number(messageMatch[1]), title: "Messenger" });
    }
    return { handled: true, target: normalized };
  }

  const postMatch = normalized.match(/^\/pulse\/post\/(\d+)/);
  if (postMatch?.[1] && navigationRef.isReady()) {
    navigationRef.navigate("PostDetail", { postId: Number(postMatch[1]), title: "Post" });
    return { handled: true, target: normalized };
  }

  if (normalized.startsWith("/pulse/search") && navigationRef.isReady()) {
    const query = extractStringQueryValue(normalized, "q") || extractStringQueryValue(normalized, "query");
    navigationRef.navigate("Tabs", { screen: "Search", params: query ? { query } : undefined });
    return { handled: true, target: normalized };
  }

  if (normalized.startsWith("/pulse/saved") && navigationRef.isReady()) {
    navigationRef.navigate("Tabs", { screen: "Saved" });
    return { handled: true, target: normalized };
  }

  const groupMatch = normalized.match(/^\/pulse\/groups\/([^/?#]+)/);
  if (groupMatch?.[1] && navigationRef.isReady()) {
    navigationRef.navigate("GroupDetail", { groupSlug: decodeURIComponent(groupMatch[1]), title: "Community" });
    return { handled: true, target: normalized };
  }

  if (normalized.startsWith("/pulse/groups") && navigationRef.isReady()) {
    navigationRef.navigate("Tabs", { screen: "Groups" });
    return { handled: true, target: normalized };
  }

  const livePathMatch = normalized.match(/^\/pulse\/live\/(\d+)/);
  if (livePathMatch?.[1] && navigationRef.isReady()) {
    navigationRef.navigate("LiveDetail", { liveId: Number(livePathMatch[1]), title: "Live" });
    return { handled: true, target: normalized };
  }

  if (normalized.startsWith("/pulse/live/studio") && navigationRef.isReady()) {
    const webTarget = `${PULSE_API_BASE_URL}${normalized}`;
    await Linking.openURL(webTarget).catch(() => undefined);
    return { handled: false, target: normalized, reason: "live_studio_web_fallback" };
  }

  if (normalized.startsWith("/pulse/live") && navigationRef.isReady()) {
    const queryLiveId = extractNumericQueryValue(normalized, "live") || extractNumericQueryValue(normalized, "live_id");
    if (queryLiveId) {
      navigationRef.navigate("LiveDetail", { liveId: queryLiveId, title: "Live" });
    } else {
      navigationRef.navigate("Tabs", { screen: "Live" });
    }
    return { handled: true, target: normalized };
  }

  if (normalized.startsWith("/pulse/messages") && normalized.includes("room=") && navigationRef.isReady()) {
    navigationRef.navigate("Tabs", { screen: "Groups" });
    return { handled: true, target: normalized, reason: "room_target" };
  }

  const reelMatch = normalized.match(/^\/pulse\/reels\/(\d+)/);
  if (reelMatch?.[1] && navigationRef.isReady()) {
    navigationRef.navigate("ReelDetail", { reelId: Number(reelMatch[1]), title: "Reel" });
    return { handled: true, target: normalized };
  }

  if (normalized.startsWith("/pulse/reels") && navigationRef.isReady()) {
    const queryReelId = extractNumericQueryValue(normalized, "reel") || extractNumericQueryValue(normalized, "reel_id");
    const queryLiveId = extractNumericQueryValue(normalized, "live") || extractNumericQueryValue(normalized, "live_id");
    if (queryLiveId) {
      navigationRef.navigate("LiveDetail", { liveId: queryLiveId, title: "Live" });
    } else if (queryReelId) {
      navigationRef.navigate("ReelDetail", { reelId: queryReelId, title: "Reel" });
    } else {
      navigationRef.navigate("Tabs", { screen: "Reels" });
    }
    return { handled: true, target: normalized };
  }

  const statusMatch = normalized.match(/^\/pulse\/status\/(\d+)/);
  if (statusMatch?.[1] && navigationRef.isReady()) {
    navigationRef.navigate("StatusDetail", { statusId: Number(statusMatch[1]), title: "Status" });
    return { handled: true, target: normalized };
  }

  const mobileStatusMatch = normalized.match(/^\/status\/(\d+)/);
  if (mobileStatusMatch?.[1] && navigationRef.isReady()) {
    navigationRef.navigate("StatusDetail", { statusId: Number(mobileStatusMatch[1]), title: "Status" });
    return { handled: true, target: normalized };
  }

  if (normalized.startsWith("/pulse/status") && navigationRef.isReady()) {
    const queryStatusId = extractNumericQueryValue(normalized, "status_id") || extractNumericQueryValue(normalized, "status");
    if (queryStatusId) {
      navigationRef.navigate("StatusDetail", { statusId: queryStatusId, title: "Status" });
    } else {
      navigationRef.navigate("Tabs", { screen: "Status" });
    }
    return { handled: true, target: normalized };
  }

  if (normalized === "/pulse/messages" && navigationRef.isReady()) {
    navigationRef.navigate("Tabs", { screen: "Messenger" });
    return { handled: true, target: normalized };
  }

  if (normalized === "/pulse/profile/edit" && navigationRef.isReady()) {
    navigationRef.navigate("ProfileEdit");
    return { handled: true, target: normalized };
  }

  const profileMatch = normalized.match(/^\/pulse\/profile\/([^/?#]+)/);
  if (profileMatch?.[1] && navigationRef.isReady()) {
    navigationRef.navigate("ProfileDetail", { profileKey: decodeURIComponent(profileMatch[1]), title: "Profile" });
    return { handled: true, target: normalized };
  }

  if (normalized === "/pulse/profile" && navigationRef.isReady()) {
    navigationRef.navigate("Tabs", { screen: "Profile" });
    return { handled: true, target: normalized };
  }

  if (normalized.startsWith("/pulse/premium") && navigationRef.isReady()) {
    navigationRef.navigate("Premium");
    return { handled: true, target: normalized };
  }

  if ((normalized.startsWith("/pulse/creator-studio") || normalized.startsWith("/pulse/creator/dashboard")) && navigationRef.isReady()) {
    navigationRef.navigate("CreatorStudio");
    return { handled: true, target: normalized };
  }

  if ((normalized.startsWith("/pulse/growth") || normalized.startsWith("/pulse/promote")) && navigationRef.isReady()) {
    navigationRef.navigate("GrowthCenter", {
      contentType: extractStringQueryValue(normalized, "content_type") || undefined,
      contentId: extractStringQueryValue(normalized, "content_id") || undefined,
      title: "Growth Center"
    });
    return { handled: true, target: normalized };
  }

  if ((normalized.startsWith("/dashboard/intelligence") || normalized.startsWith("/pulse/intelligence")) && navigationRef.isReady()) {
    const subsystem = normalized.match(/^\/dashboard\/intelligence\/([^/?#]+)/)?.[1] || "";
    navigationRef.navigate("IntelligenceCenter", { subsystem: subsystem ? decodeURIComponent(subsystem) : undefined, title: "Intelligence" });
    return { handled: true, target: normalized };
  }

  if ((normalized.startsWith("/dashboard/crypto/alerts") || normalized.startsWith("/pulse/crypto/alerts") || normalized.startsWith("/pulse/alerts")) && navigationRef.isReady()) {
    const alertId = extractNumericQueryValue(normalized, "alert_id") || extractNumericQueryValue(normalized, "id");
    const pathAlertId = normalized.match(/^\/pulse\/alerts\/(\d+)/)?.[1];
    navigationRef.navigate("AlertManagement", { alertId: alertId || Number(pathAlertId || 0) || undefined, title: alertId || pathAlertId ? "Alert Detail" : "Alerts" });
    return { handled: true, target: normalized };
  }

  const accountSection = accountSectionForTarget(normalized);
  if (accountSection && navigationRef.isReady()) {
    navigationRef.navigate("AccountCenter", { section: accountSection, title: accountSectionTitle(accountSection) });
    return { handled: true, target: normalized };
  }

  const marketplacePathMatch = normalized.match(/^\/pulse\/marketplace\/(\d+)/);
  if (marketplacePathMatch?.[1] && navigationRef.isReady()) {
    navigationRef.navigate("MarketplaceDetail", { listingId: Number(marketplacePathMatch[1]), title: "Marketplace" });
    return { handled: true, target: normalized };
  }

  if (normalized.startsWith("/pulse/marketplace") && navigationRef.isReady()) {
    const queryListingId = extractNumericQueryValue(normalized, "listing") || extractNumericQueryValue(normalized, "listing_id");
    if (queryListingId) {
      navigationRef.navigate("MarketplaceDetail", { listingId: queryListingId, title: "Marketplace" });
    } else {
      navigationRef.navigate("Tabs", { screen: "Marketplace" });
    }
    return { handled: true, target: normalized };
  }

  if (normalized.startsWith("/pulse/notifications") && navigationRef.isReady()) {
    navigationRef.navigate("NotificationCenter");
    return { handled: true, target: normalized };
  }

  const webTarget = `${PULSE_API_BASE_URL}${normalized}`;
  const supported = await Linking.canOpenURL(webTarget).catch(() => false);
  if (supported) {
    await Linking.openURL(webTarget);
    return { handled: false, target: normalized, reason: "opened_web_fallback" };
  }

  navigateToNotifications();
  return { handled: true, target: "/pulse/notifications", reason: "unsupported_target" };
}

export function normalizeNotificationTarget(raw: string) {
  let value = String(raw || "").trim();
  if (!value || /[\r\n\t]/.test(value)) return "";
  const lowered = value.toLowerCase();
  if (lowered.startsWith("javascript:") || lowered.startsWith("data:") || lowered.startsWith("file:")) return "";
  if (lowered.startsWith("pulsesoc://")) {
    value = customSchemePath(value, "pulsesoc://");
  } else if (lowered.startsWith("pulse://")) {
    value = customSchemePath(value, "pulse://");
  } else if (lowered.startsWith("http://") || lowered.startsWith("https://")) {
    const parsed = new URL(value);
    if (!["pulsesoc.com", "www.pulsesoc.com"].includes(parsed.hostname.toLowerCase())) return "";
    value = `${parsed.pathname}${parsed.search}${parsed.hash}`;
  }
  if (!value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return "";
  if (value.startsWith("/api/") || value.startsWith("/static/") || value.startsWith("/admin/")) return "";
  return value;
}

function navigateToNotifications() {
  if (navigationRef.isReady()) {
    navigationRef.navigate("NotificationCenter");
  }
}

function extractNumericQueryValue(target: string, name: string) {
  const query = target.split("?")[1]?.split("#")[0] || "";
  if (!query) return 0;
  const value = new URLSearchParams(query).get(name);
  return Number(value || 0);
}

function extractStringQueryValue(target: string, name: string) {
  const query = target.split("?")[1]?.split("#")[0] || "";
  if (!query) return "";
  return new URLSearchParams(query).get(name) || "";
}

function accountSectionForTarget(target: string): "account" | "security" | "privacy" | "devices" | "" {
  if (target.startsWith("/pulse/settings/security") || target.startsWith("/dashboard/account/security") || target.startsWith("/account/security")) {
    return "security";
  }
  if (target.startsWith("/pulse/settings/privacy") || target.startsWith("/privacy-center")) {
    return "privacy";
  }
  if (target.startsWith("/pulse/settings/devices")) {
    return "devices";
  }
  if (target.startsWith("/pulse/settings/account") || target.startsWith("/dashboard/account/settings") || target.startsWith("/account/settings")) {
    return "account";
  }
  return "";
}

function accountSectionTitle(section: "account" | "security" | "privacy" | "devices") {
  if (section === "security") return "Security Center";
  if (section === "privacy") return "Privacy Center";
  if (section === "devices") return "Sessions and Devices";
  return "Account Center";
}

function customSchemePath(value: string, prefix: string) {
  const rest = value.slice(prefix.length);
  const [pathPart, queryPart = ""] = rest.split("?");
  const normalizedPath = `/${pathPart.replace(/^\/+/, "")}`;
  return queryPart ? `${normalizedPath}?${queryPart}` : normalizedPath;
}
