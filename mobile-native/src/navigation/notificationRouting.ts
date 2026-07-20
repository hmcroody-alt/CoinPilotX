import { Linking } from "react-native";
import * as Notifications from "expo-notifications";
import { createNavigationContainerRef } from "@react-navigation/native";
import { PULSE_API_BASE_URL } from "../api/config";
import { profileNavigationParams, profileTargetFromUrl } from "../api/profileTarget";
import { dashboardModuleParamsForRoute } from "./dashboardRouting";
import { RootStackParamList } from "./types";

export const navigationRef = createNavigationContainerRef<RootStackParamList>();

export type NotificationRouteResult = {
  handled: boolean;
  target: string;
  reason?: string;
};

type NotificationResponseRoutingOptions = {
  canRoute?: () => boolean;
  onDeferred?: (target: string) => void;
  includeLastResponse?: boolean;
};

let lastNotificationResponseKey = "";
let lastNotificationResponseAt = 0;

export function setupNotificationResponseRouting(options: NotificationResponseRoutingOptions = {}) {
  const handleResponse = (response: Notifications.NotificationResponse | null) => {
    if (!response) return;
    const target = notificationTargetFromData(response.notification.request.content.data);
    const responseKey = String(response.notification.request.identifier || target || "notification");
    const now = Date.now();
    if (responseKey === lastNotificationResponseKey && now - lastNotificationResponseAt < 5000) return;
    lastNotificationResponseKey = responseKey;
    lastNotificationResponseAt = now;
    if (options.canRoute && !options.canRoute()) {
      options.onDeferred?.(target || "/pulse/notifications");
      return;
    }
    routeNotificationTarget(target).catch(() => undefined);
  };
  const subscription = Notifications.addNotificationResponseReceivedListener(handleResponse);
  if (options.includeLastResponse !== false) {
    Notifications.getLastNotificationResponseAsync().then(handleResponse).catch(() => undefined);
  }
  return subscription;
}

export async function routeNotificationData(data: Notifications.NotificationContent["data"] = {}): Promise<NotificationRouteResult> {
  return routeNotificationTarget(notificationTargetFromData(data));
}

export function notificationTargetFromData(data: Notifications.NotificationContent["data"] = {}) {
  const nested = data?.data && typeof data.data === "object" && !Array.isArray(data.data)
    ? data.data as Record<string, unknown>
    : {};
  const payload = { ...nested, ...data } as Record<string, unknown>;
  const explicit = stringPayloadValue(payload, "target_url", "deep_link", "route", "url", "web_url", "native_url", "app_url", "mobile_deep_link", "deepLink");
  if (explicit) return normalizeNotificationTarget(explicit);

  const eventType = stringPayloadValue(payload, "event_type", "notification_type", "push_type", "type").toLowerCase();
  const callId = stringPayloadValue(payload, "call_id", "callId");
  const conversationId = numericPayloadValue(payload, "conversation_id", "conversationId");
  const reelId = numericPayloadValue(payload, "reel_id", "reelId");
  const postId = numericPayloadValue(payload, "post_id", "postId");
  const statusId = numericPayloadValue(payload, "status_id", "statusId");
  const groupSlug = stringPayloadValue(payload, "group_slug", "groupSlug");
  const actorProfile = stringPayloadValue(payload, "actor_public_player_id", "public_player_id", "username");

  if (callId) return `/pulse/calls/${encodeURIComponent(callId)}`;
  if (conversationId && /(message|chat|call|reply|mention)/.test(eventType)) return `/pulse/messages/${conversationId}`;
  if (reelId) return `/pulse/reels/${reelId}`;
  if (postId) return `/pulse/post/${postId}`;
  if (statusId) return `/pulse/status/${statusId}`;
  if (groupSlug) return `/pulse/groups/${encodeURIComponent(groupSlug)}`;
  if (conversationId) return `/pulse/messages/${conversationId}`;
  if (actorProfile && /(follow|profile|verification)/.test(eventType)) return `/pulse/profile/${encodeURIComponent(actorProfile.replace(/^@/, ""))}`;
  return "/pulse/notifications";
}

export async function routeNotificationTarget(target: string): Promise<NotificationRouteResult> {
  const normalized = normalizeNotificationTarget(target);
  if (!normalized) {
    navigateToNotifications();
    return { handled: true, target: "/pulse/notifications", reason: "missing_target" };
  }

  if ((normalized === "/dashboard" || normalized === "/dashboard/home" || normalized === "/pulse/dashboard") && navigationRef.isReady()) {
    navigationRef.navigate("Tabs", { screen: "Dashboard" });
    return { handled: true, target: normalized };
  }

  const dashboardModule = dashboardModuleParamsForRoute(normalized);
  if (dashboardModule && navigationRef.isReady()) {
    navigationRef.navigate("DashboardModuleDetail", dashboardModule);
    return { handled: true, target: normalized };
  }

  const callMatch = normalized.match(/^\/pulse\/calls\/([^/?#]+)/);
  if (callMatch?.[1] && navigationRef.isReady()) {
    navigationRef.navigate("Call", { callId: decodeURIComponent(callMatch[1]), direction: "incoming", title: "PulseSoc Call" });
    return { handled: true, target: normalized };
  }

  const activityTarget = activityRouteTarget(normalized);
  if (activityTarget && navigationRef.isReady()) {
    navigationRef.navigate("ActivityInbox", activityTarget);
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

  if (normalized.startsWith("/pulse/music") && navigationRef.isReady()) {
    navigationRef.navigate("Music", musicRouteParams(normalized));
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

  if (normalized.startsWith("/pulse/live/events/create") && navigationRef.isReady()) {
    navigationRef.navigate("LiveEventCreateGateway", { title: "Create Live Event" });
    return { handled: true, target: normalized };
  }

  if (normalized.startsWith("/pulse/live/schedule") && navigationRef.isReady()) {
    navigationRef.navigate("LiveScheduleGateway", { title: "Schedule Live" });
    return { handled: true, target: normalized };
  }

  if (normalized.startsWith("/pulse/live/studio") && navigationRef.isReady()) {
    navigationRef.navigate("LiveStudio", { title: "Live Studio" });
    return { handled: true, target: normalized };
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

  const eventMatch = normalized.match(/^\/pulse\/events\/(\d+)/);
  if (eventMatch?.[1] && navigationRef.isReady()) {
    navigationRef.navigate("EventDetail", { eventId: Number(eventMatch[1]), title: "Event" });
    return { handled: true, target: normalized };
  }

  if (normalized.startsWith("/pulse/events") && navigationRef.isReady()) {
    const queryEventId = extractNumericQueryValue(normalized, "event") || extractNumericQueryValue(normalized, "event_id") || extractNumericQueryValue(normalized, "live_id");
    if (queryEventId) {
      navigationRef.navigate("EventDetail", { eventId: queryEventId, title: "Event" });
    } else {
      navigationRef.navigate("Events", { title: "Events" });
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

  if (normalized === "/pulse/status/create" && navigationRef.isReady()) {
    navigationRef.navigate("Tabs", { screen: "Status", params: { openCreator: true } });
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

  const profileTarget = profileTargetFromUrl(normalized);
  const profileParams = profileNavigationParams(profileTarget, "Profile");
  if (profileParams && navigationRef.isReady()) {
    navigationRef.navigate("ProfileDetail", profileParams);
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

  const plannerTarget = contentPlannerTarget(normalized);
  if (plannerTarget && navigationRef.isReady()) {
    navigationRef.navigate("ContentPlanner", plannerTarget);
    return { handled: true, target: normalized };
  }

  const learningTarget = learningRouteTarget(normalized);
  if (learningTarget && navigationRef.isReady()) {
    if (learningTarget.route === "lesson") {
      navigationRef.navigate("LearningLessonDetail", { lessonSlug: learningTarget.lessonSlug, title: "Lesson" });
    } else if (learningTarget.route === "course-detail") {
      navigationRef.navigate("CourseDetail", { courseId: learningTarget.courseId, title: "Course" });
    } else if (learningTarget.route === "teacher-dashboard") {
      navigationRef.navigate("TeacherDashboardGateway", { title: "Teacher Dashboard" });
    } else if (learningTarget.route === "teacher") {
      navigationRef.navigate("TeacherProfileGateway", { teacherId: learningTarget.teacherId, title: "Teacher" });
    } else {
      navigationRef.navigate("Courses", { category: learningTarget.category, title: "Courses" });
    }
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

  if (accountHealthTarget(normalized) && navigationRef.isReady()) {
    navigationRef.navigate("AccountHealth", { title: "Account Health" });
    return { handled: true, target: normalized };
  }

  const safetyTarget = safetyRouteTarget(normalized);
  if (safetyTarget && navigationRef.isReady()) {
    navigationRef.navigate("SafetyHub", safetyTarget);
    return { handled: true, target: normalized };
  }

  const verificationTarget = verificationRouteTarget(normalized);
  if (verificationTarget && navigationRef.isReady()) {
    navigationRef.navigate("VerificationCenter", verificationTarget);
    return { handled: true, target: normalized };
  }

  const trustSafety = trustSafetyTarget(normalized);
  if (trustSafety && navigationRef.isReady()) {
    navigationRef.navigate("TrustSafety", trustSafety);
    return { handled: true, target: normalized };
  }

  if (normalized.startsWith("/pulse/marketplace/create") && navigationRef.isReady()) {
    navigationRef.navigate("MarketplaceCreateGateway", { title: "Create Listing" });
    return { handled: true, target: normalized };
  }

  const sellerStore = sellerStoreTarget(normalized);
  if (sellerStore && navigationRef.isReady()) {
    navigationRef.navigate("SellerStore", sellerStore);
    return { handled: true, target: normalized };
  }

  const buyerOrderTarget = buyerOrderRouteTarget(normalized);
  if (buyerOrderTarget && navigationRef.isReady()) {
    if (buyerOrderTarget.orderId) navigationRef.navigate("BuyerOrderDetail", { orderId: buyerOrderTarget.orderId, source: buyerOrderTarget.source, title: buyerOrderTarget.title });
    else navigationRef.navigate("BuyerOrders", buyerOrderTarget);
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
    navigationRef.navigate("ActivityInbox", { title: "Activity Inbox" });
    return { handled: true, target: normalized };
  }

  if (normalized === "/pulse/compose" && navigationRef.isReady()) {
    navigationRef.navigate("Tabs", { screen: "Home", params: { openComposer: true } });
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
  value = normalizeNativeShorthandPath(value);
  if (!value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return "";
  if (value.startsWith("/api/") || value.startsWith("/static/") || value.startsWith("/admin/")) return "";
  return value;
}

function normalizeNativeShorthandPath(value: string) {
  const mappings: Array<[RegExp, string]> = [
    [/^\/post\/(\d+)/, "/pulse/post/$1"],
    [/^\/reel(?:s)?\/(\d+)/, "/pulse/reels/$1"],
    [/^\/message(?:s)?\/(\d+)/, "/pulse/messages/$1"],
    [/^\/call(?:s)?\/([^/?#]+)/, "/pulse/calls/$1"],
    [/^\/profile\/([^/?#]+)/, "/pulse/profile/$1"]
  ];
  for (const [pattern, replacement] of mappings) {
    if (pattern.test(value)) return value.replace(pattern, replacement);
  }
  return value;
}

function stringPayloadValue(payload: Record<string, unknown>, ...keys: string[]) {
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) return value.trim().slice(0, 500);
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return "";
}

function numericPayloadValue(payload: Record<string, unknown>, ...keys: string[]) {
  const value = Number(stringPayloadValue(payload, ...keys) || 0);
  return Number.isFinite(value) && value > 0 ? Math.trunc(value) : 0;
}

function navigateToNotifications() {
  if (navigationRef.isReady()) {
    navigationRef.navigate("ActivityInbox", { title: "Activity Inbox" });
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

function musicRouteParams(target: string): RootStackParamList["Music"] {
  const trackId = extractStringQueryValue(target, "track") || extractStringQueryValue(target, "music") || extractStringQueryValue(target, "music_track_id");
  const artistId = extractNumericQueryValue(target, "artist") || extractNumericQueryValue(target, "artist_id");
  return {
    ...(trackId ? { trackId } : {}),
    ...(artistId ? { artistId } : {}),
    ...(target.includes("#music-upload") ? { openUpload: true } : {}),
    title: "PulseSoc Music"
  };
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

function activityRouteTarget(target: string): {
  title: string;
  category?: "all" | "messages" | "calls" | "social" | "safety" | "verification" | "marketplace" | "creator_growth" | "intelligence_alerts";
} | null {
  if (
    target.startsWith("/pulse/activity") ||
    target.startsWith("/pulse/inbox") ||
    target.startsWith("/dashboard/activity") ||
    target.startsWith("/dashboard/inbox")
  ) {
    const rawCategory = extractStringQueryValue(target, "category") || target.match(/^\/pulse\/activity\/([^/?#]+)/)?.[1] || "";
    const category = normalizeActivityRouteCategory(rawCategory);
    return { title: "Activity Inbox", ...(category ? { category } : {}) };
  }
  return null;
}

function normalizeActivityRouteCategory(
  category: string
): "all" | "messages" | "calls" | "social" | "safety" | "verification" | "marketplace" | "creator_growth" | "intelligence_alerts" | "" {
  const value = String(category || "").toLowerCase().replace(/-/g, "_");
  if (
    value === "all" ||
    value === "messages" ||
    value === "calls" ||
    value === "social" ||
    value === "safety" ||
    value === "verification" ||
    value === "marketplace" ||
    value === "creator_growth" ||
    value === "intelligence_alerts"
  ) {
    return value;
  }
  return "";
}

function accountHealthTarget(target: string) {
  return target.startsWith("/dashboard/account/health") || target.startsWith("/pulse/account-health") || target.startsWith("/account/health");
}

function safetyRouteTarget(target: string): { title: string; section?: "overview" | "blocks" | "mutes" | "reports" } | null {
  if (
    target.startsWith("/pulse/safety") ||
    target.startsWith("/pulse/blocks") ||
    target.startsWith("/pulse/mutes") ||
    target.startsWith("/pulse/reports") ||
    target.startsWith("/dashboard/network/network-security") ||
    target.startsWith("/dashboard/network/blocks-mutes")
  ) {
    const section = normalizeSafetySection(
      extractStringQueryValue(target, "section") ||
      target.match(/^\/pulse\/safety\/([^/?#]+)/)?.[1] ||
      (target.includes("blocks") ? "blocks" : target.includes("mutes") ? "mutes" : target.includes("reports") ? "reports" : "")
    );
    return { title: "Safety Hub", ...(section ? { section } : {}) };
  }
  return null;
}

function normalizeSafetySection(section: string): "overview" | "blocks" | "mutes" | "reports" | "" {
  const value = String(section || "").toLowerCase();
  if (value === "overview" || value === "blocks" || value === "mutes" || value === "reports") return value;
  return "";
}

function trustSafetyTarget(target: string): { title: string; mode: "support" | "security" | "scam" | "trust" } | null {
  if (target.startsWith("/pulse/help") || target.startsWith("/support") || target.startsWith("/help")) {
    return { title: "Support", mode: "support" };
  }
  if (target.startsWith("/trust-center") || target.startsWith("/community-rules")) {
    return { title: "Trust Center", mode: "trust" };
  }
  if (target.startsWith("/security")) {
    return { title: "Security Report", mode: "security" };
  }
  if (target.startsWith("/scam-shield")) {
    return { title: "Scam Shield", mode: "scam" };
  }
  return null;
}

function verificationRouteTarget(target: string): { title: string; track?: "identity" | "blue_check" | "business" | "government_id" } | null {
  if (target.startsWith("/dashboard/account/verification") || target.startsWith("/pulse/verification")) {
    const rawTrack = extractStringQueryValue(target, "track") || extractStringQueryValue(target, "verification_type") || target.match(/^\/pulse\/verification\/([^/?#]+)/)?.[1] || "";
    const track = normalizeVerificationTrack(rawTrack);
    return { title: "Verification Center", ...(track ? { track } : {}) };
  }
  return null;
}

function normalizeVerificationTrack(track: string): "identity" | "blue_check" | "business" | "government_id" | "" {
  const value = String(track || "").toLowerCase();
  if (value === "blue_check" || value === "business" || value === "government_id" || value === "identity") return value;
  return "";
}

function sellerStoreTarget(target: string): { title: string; mode?: "overview" | "apply" | "dashboard" | "profile" | "create" | "payouts"; sellerId?: string } | null {
  if (target.startsWith("/pulse/seller-store")) {
    const mode = normalizeSellerStoreMode(extractStringQueryValue(target, "mode"));
    return { title: "Seller / Store", ...(mode ? { mode } : {}) };
  }
  if (target.startsWith("/pulse/merchant/apply")) {
    return { title: "Merchant Application", mode: "apply" };
  }
  if (target.startsWith("/pulse/merchant/dashboard")) {
    return { title: "Merchant Dashboard", mode: "dashboard" };
  }
  if (target.startsWith("/pulse/merchant/payouts")) {
    return { title: "Merchant Payouts", mode: "payouts" };
  }
  const merchantMatch = target.match(/^\/pulse\/merchant\/([^/?#]+)/);
  if (merchantMatch?.[1]) {
    return { title: "Merchant Profile", mode: "profile", sellerId: decodeURIComponent(merchantMatch[1]) };
  }
  return null;
}

function normalizeSellerStoreMode(mode: string): "overview" | "apply" | "dashboard" | "profile" | "create" | "payouts" | "" {
  const value = String(mode || "").toLowerCase();
  if (value === "overview" || value === "apply" || value === "dashboard" || value === "profile" || value === "create" || value === "payouts") return value;
  return "";
}

function buyerOrderRouteTarget(target: string): { orderId?: number; source?: string; title: string } | null {
  if (target.startsWith("/pulse/orders") || target.startsWith("/pulse/purchases") || target.startsWith("/dashboard/orders")) {
    const pathOrderId = target.match(/^\/pulse\/orders\/(\d+)/)?.[1];
    const orderId = Number(pathOrderId || extractNumericQueryValue(target, "order_id") || extractNumericQueryValue(target, "orderId") || extractNumericQueryValue(target, "id") || 0) || undefined;
    const source = extractStringQueryValue(target, "source") || undefined;
    return { orderId, source, title: orderId ? "Order Detail" : "Purchase History" };
  }
  return null;
}

function contentPlannerTarget(target: string): { title: string; mode?: "planner" | "scheduler" | "drafts" } | null {
  if (
    target.startsWith("/pulse/content-planner") ||
    target.startsWith("/dashboard/creator/content-planner") ||
    target.startsWith("/pulse/dashboard/content-planner")
  ) {
    return { title: "Content Planner", mode: "planner" };
  }
  if (
    target.startsWith("/dashboard/creator/post-scheduler") ||
    target.startsWith("/pulse/dashboard/post-scheduler")
  ) {
    return { title: "Scheduled Publishing", mode: "scheduler" };
  }
  if (
    target.startsWith("/dashboard/creator/draft-studio") ||
    target.startsWith("/pulse/dashboard/draft-studio")
  ) {
    return { title: "Draft Studio", mode: "drafts" };
  }
  return null;
}

function learningRouteTarget(target: string):
  | { route: "courses"; category?: string }
  | { route: "course-detail"; courseId: number }
  | { route: "lesson"; lessonSlug: string }
  | { route: "teacher"; teacherId?: string }
  | { route: "teacher-dashboard" }
  | null {
  const lessonMatch = target.match(/^\/education\/lesson\/([^/?#]+)/);
  if (lessonMatch?.[1]) {
    return { route: "lesson", lessonSlug: decodeURIComponent(lessonMatch[1]) };
  }
  if (target.startsWith("/pulse/teacher-dashboard")) {
    return { route: "teacher-dashboard" };
  }
  const teacherMatch = target.match(/^\/pulse\/teachers\/([^/?#]+)/);
  if (teacherMatch?.[1]) {
    return { route: "teacher", teacherId: decodeURIComponent(teacherMatch[1]) };
  }
  if (target.startsWith("/pulse/teachers")) {
    return { route: "teacher" };
  }
  const courseMatch = target.match(/^\/pulse\/courses\/(\d+)/);
  if (courseMatch?.[1]) {
    return { route: "course-detail", courseId: Number(courseMatch[1]) };
  }
  if (target.startsWith("/pulse/courses") || target.startsWith("/education")) {
    return { route: "courses", category: extractStringQueryValue(target, "category") || undefined };
  }
  return null;
}

function customSchemePath(value: string, prefix: string) {
  const rest = value.slice(prefix.length);
  const [pathPart, queryPart = ""] = rest.split("?");
  const normalizedPath = `/${pathPart.replace(/^\/+/, "")}`;
  return queryPart ? `${normalizedPath}?${queryPart}` : normalizedPath;
}
