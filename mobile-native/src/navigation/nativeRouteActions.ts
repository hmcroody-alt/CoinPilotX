import { profileNavigationParams, profileTargetFromUrl } from "../api/profileTarget";
import { openDashboardRoute } from "./dashboardRouting";

export type NativeRouteNavigation = {
  navigate: (...args: any[]) => void;
};

type CanonicalNativeRoute = {
  path: string;
  query: URLSearchParams;
  hash: string;
  relative: string;
};

export function canonicalNativeRoute(routePath: string): CanonicalNativeRoute {
  const raw = String(routePath || "").trim();
  try {
    const absolute = new URL(raw);
    const scheme = absolute.protocol.toLowerCase();
    const path = scheme === "pulsesoc:"
      ? `/${absolute.hostname}${absolute.pathname}`.replace(/\/+/g, "/")
      : absolute.pathname;
    return {
      path: path || "/",
      query: absolute.searchParams,
      hash: absolute.hash,
      relative: `${path || "/"}${absolute.search}${absolute.hash}`
    };
  } catch {
    const [withoutHash, hashPart = ""] = raw.split("#", 2);
    const [pathPart, queryPart = ""] = withoutHash.split("?", 2);
    const path = `/${pathPart}`.replace(/\/+/g, "/");
    return {
      path,
      query: new URLSearchParams(queryPart),
      hash: hashPart ? `#${hashPart}` : "",
      relative: `${path}${queryPart ? `?${queryPart}` : ""}${hashPart ? `#${hashPart}` : ""}`
    };
  }
}

function positiveId(value: string | undefined): number {
  const parsed = Number(value || 0);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : 0;
}

function safeDecode(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

export type NativeObjectDestination = {
  screen: string;
  params?: Record<string, unknown>;
};

export function nativeObjectDestination(routePath: string): NativeObjectDestination | null {
  const route = canonicalNativeRoute(routePath);
  const { path, query } = route;
  const objectMatch = path.match(/^\/pulse\/(?:post|posts)\/([1-9]\d*)\/?$/);
  const reelMatch = path.match(/^\/pulse\/reels\/([1-9]\d*)\/?$/);
  const statusMatch = path.match(/^\/pulse\/status\/([1-9]\d*)\/?$/);
  const liveMatch = path.match(/^\/pulse\/live\/([1-9]\d*)\/?$/);
  const listingMatch = path.match(/^\/pulse\/marketplace\/([1-9]\d*)\/?$/);
  const messageMatch = path.match(/^\/pulse\/messages\/([1-9]\d*)\/?$/);
  const notificationMatch = path.match(/^\/pulse\/notifications\/([1-9]\d*)\/?$/);
  const eventMatch = path.match(/^\/pulse\/events\/([1-9]\d*)\/?$/);
  const storeMatch = path.match(/^\/pulse\/(?:stores?|business(?:es)?)\/([^/]+)\/?$/);
  const adMatch = path.match(/^\/pulse\/(?:ads?|advertisements?)\/([1-9]\d*)\/?$/);
  const undxTaskMatch = path.match(/^\/pulse\/(?:undx|ai)\/tasks\/([^/]+)\/?$/);
  const callMatch = path.match(/^\/pulse\/calls\/([^/]+)\/?$/);

  if (objectMatch) return { screen: "PostDetail", params: { postId: positiveId(objectMatch[1]), title: "Post" } };
  if (reelMatch) return { screen: "ReelDetail", params: { reelId: positiveId(reelMatch[1]), title: "Reel" } };
  if (statusMatch) return { screen: "StatusDetail", params: { statusId: positiveId(statusMatch[1]), title: "Status" } };
  if (liveMatch) return { screen: "LiveDetail", params: { liveId: positiveId(liveMatch[1]), title: "Live" } };
  if (listingMatch) return { screen: "MarketplaceDetail", params: { listingId: positiveId(listingMatch[1]), title: "Marketplace" } };
  if (messageMatch) return { screen: "Chat", params: { conversationId: positiveId(messageMatch[1]), title: "Conversation" } };
  if (notificationMatch) return { screen: "NotificationCenter", params: { notificationId: positiveId(notificationMatch[1]) } };
  if (eventMatch) return { screen: "EventDetail", params: { eventId: positiveId(eventMatch[1]), title: "Event" } };
  if (storeMatch) return { screen: "MerchantProfile", params: { sellerId: safeDecode(storeMatch[1]), title: "Business" } };
  if (adMatch) return { screen: "GrowthCenter", params: { contentType: "advertisement", contentId: positiveId(adMatch[1]), title: "Advertisement" } };
  if (undxTaskMatch) return { screen: "Tabs", params: { screen: "PulseAI", params: { taskId: safeDecode(undxTaskMatch[1]) } } };
  if (callMatch) return {
    screen: "Call",
    params: {
      callId: safeDecode(callMatch[1]),
      callType: query.get("type") === "audio" ? "audio" : "video",
      title: "Call"
    }
  };
  return null;
}

export function openNativeRoute(navigation: NativeRouteNavigation, routePath: string) {
  const route = canonicalNativeRoute(routePath);
  const { path, query } = route;
  if (path === "/pulse/profile/edit") {
    navigation.navigate("ProfileEdit");
    return;
  }
  const profileParams = profileNavigationParams(profileTargetFromUrl(route.relative), "Profile");
  if (profileParams) {
    navigation.navigate("ProfileDetail", profileParams);
    return;
  }
  const cameraMatch = path.match(/^\/pulse\/camera\/([^/]+)\/?$/);
  const objectDestination = nativeObjectDestination(route.relative);

  if (objectDestination) navigation.navigate(objectDestination.screen, objectDestination.params);
  else if (path === "/pulse") navigation.navigate("Tabs", { screen: "Home" });
  else if (path === "/pulse/dashboard") navigation.navigate("Tabs", { screen: "Dashboard" });
  else if (path === "/pulse/search") navigation.navigate("Tabs", { screen: "Search", params: { query: query.get("q") || query.get("query") || undefined } });
  else if (path === "/search") navigation.navigate("Search", { query: query.get("q") || query.get("query") || undefined, title: "Search" });
  else if (path === "/pulse/activity") navigation.navigate("ActivityInbox", { title: "Activity Inbox" });
  else if (path === "/pulse/notifications") navigation.navigate("NotificationCenter");
  else if (path === "/pulse/messages/new") navigation.navigate("NewChat", { initialQuery: query.get("q") || undefined, targetUserId: positiveId(query.get("user") || undefined) || undefined });
  else if (path === "/pulse/messages") navigation.navigate("Tabs", { screen: "Messenger" });
  else if (path === "/pulse/profile") navigation.navigate("Tabs", { screen: "Profile" });
  else if (path === "/pulse/settings") navigation.navigate("Tabs", { screen: "Settings" });
  else if (path === "/pulse/settings/privacy") navigation.navigate("AccountPrivacy", { title: "Privacy Center" });
  else if (path === "/pulse/compose") navigation.navigate("Tabs", { screen: "Home", params: { openComposer: true } });
  else if (cameraMatch) navigation.navigate("CameraStudio", {
    target: (query.get("target") || "feed") as any,
    mode: safeDecode(cameraMatch[1]) as any,
    conversationId: positiveId(query.get("conversation_id") || undefined) || undefined,
    title: "Camera"
  });
  else if (path === "/pulse/status/create") navigation.navigate("Tabs", { screen: "Status", params: { openCreator: true } });
  else if (path === "/pulse/status") navigation.navigate("Tabs", { screen: "Status" });
  else if (path === "/pulse/reels") navigation.navigate("Tabs", { screen: "Reels" });
  else if (path === "/pulse/groups") navigation.navigate("Tabs", { screen: "Groups" });
  else if (path === "/pulse/saved") navigation.navigate("Tabs", { screen: "Saved" });
  else if (path === "/pulse/live") navigation.navigate("Tabs", { screen: "Live" });
  else if (path.startsWith("/pulse/music")) navigation.navigate("Music", musicParamsFromRoute(route.relative));
  else if (path === "/pulse/events") navigation.navigate("Events", { title: "Events" });
  else if (path === "/pulse/marketplace") navigation.navigate("Tabs", { screen: "Marketplace" });
  else if (path === "/pulse/marketplace/create") navigation.navigate("MarketplaceCreateGateway", { title: "Create Listing" });
  else if (path === "/pulse/seller-store") navigation.navigate("SellerStore", { title: "Seller / Store", sellerId: query.get("seller_id") || undefined });
  else if (path === "/pulse/orders") navigation.navigate("BuyerOrders", { title: "Purchase History" });
  else if (path === "/pulse/premium") navigation.navigate("Premium");
  else if (path === "/pulse/creator-studio") navigation.navigate("CreatorStudio");
  else if (path === "/pulse/growth") navigation.navigate("GrowthCenter", { title: "Growth Center" });
  else if (path === "/pulse/safety") navigation.navigate("SafetyHub", { title: "Safety Hub" });
  else if (path === "/scam-shield/scan") navigation.navigate("ScamShield", { title: "Scam Shield" });
  else if (path === "/pulse/verification") navigation.navigate("VerificationCenter", { title: "Verification Center" });
  else if (path === "/pulse/account-health" || path === "/dashboard/account/health" || path === "/account/health") {
    navigation.navigate("AccountHealth", { title: "Account Health" });
  }
  else if (path === "/pulse/support" || path === "/support") navigation.navigate("TrustSafetySupport", { title: "Support" });
  else if (path === "/pulse/ai") navigation.navigate("Tabs", { screen: "PulseAI" });
  else if (path === "/pulse/undx/actions") navigation.navigate("UndxActionCenter", {
    orgId: query.get("org_id") || undefined,
    actor: query.get("actor") || undefined,
    productArea: query.get("product_area") || undefined,
    title: "UNDX Action Center"
  });
  else if (path === "/pulse/intelligence") navigation.navigate("IntelligenceCenter", { title: "Intelligence" });
  else if (path === "/pulse/alerts") navigation.navigate("AlertManagement", { title: "Alerts" });
  // No `title` param on purpose. The header falls back to
  // `t("common:screens.portfolio")` only when none is passed, so the hardcoded
  // English titles elsewhere in this chain are the reason a French member sees
  // "Watchlists" above a translated screen. `/pulse/premium` sets the same
  // precedent one line above.
  else if (path === "/pulse/portfolio") navigation.navigate("Portfolio");
  else if (path === "/pulse/courses") navigation.navigate("Courses", { title: "Courses" });
  else if (path === "/terms" || path === "/privacy") navigation.navigate("Tabs", { screen: "Settings" });
  else openDashboardRoute(navigation, route.relative);
}

function musicParamsFromRoute(routePath: string) {
  const query = String(routePath || "").split("?")[1]?.split("#")[0] || "";
  const params = new URLSearchParams(query);
  const trackId = params.get("track") || params.get("music") || params.get("music_track_id") || "";
  const artistId = Number(params.get("artist") || params.get("artist_id") || 0);
  return {
    ...(trackId ? { trackId } : {}),
    ...(artistId ? { artistId } : {}),
    ...(routePath.includes("#music-upload") ? { openUpload: true } : {}),
    title: "PulseSoc Music"
  };
}
