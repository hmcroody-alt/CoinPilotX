import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { openDashboardRoute } from "./dashboardRouting";
import { RootStackParamList } from "./types";

export type NativeRouteNavigation = NativeStackNavigationProp<RootStackParamList>;

export function openNativeRoute(navigation: NativeRouteNavigation, routePath: string) {
  if (routePath === "/pulse") navigation.navigate("Tabs", { screen: "Home" });
  else if (routePath === "/pulse/dashboard") navigation.navigate("Tabs", { screen: "Dashboard" });
  else if (routePath === "/pulse/search") navigation.navigate("Tabs", { screen: "Search" });
  else if (routePath === "/pulse/activity" || routePath === "/pulse/notifications") navigation.navigate("ActivityInbox", { title: "Activity Inbox" });
  else if (routePath === "/pulse/messages") navigation.navigate("Tabs", { screen: "Messenger" });
  else if (routePath === "/pulse/calls/qa-call-1") navigation.navigate("Call", { callId: "qa-call-1", callType: "video", title: "Call" });
  else if (routePath === "/pulse/profile") navigation.navigate("Tabs", { screen: "Profile" });
  else if (routePath === "/pulse/profile/edit") navigation.navigate("ProfileEdit");
  else if (routePath === "/pulse/settings") navigation.navigate("Tabs", { screen: "Settings" });
  else if (routePath === "/pulse/settings/privacy") navigation.navigate("AccountPrivacy", { title: "Privacy Center" });
  else if (routePath === "/pulse/compose") navigation.navigate("Tabs", { screen: "Home", params: { openComposer: true } });
  else if (routePath === "/pulse/camera/photo?target=feed") navigation.navigate("CameraStudio", { target: "feed", mode: "photo", title: "Camera" });
  else if (routePath === "/pulse/status/create") navigation.navigate("Tabs", { screen: "Status", params: { openCreator: true } });
  else if (routePath === "/pulse/status") navigation.navigate("Tabs", { screen: "Status" });
  else if (routePath === "/pulse/reels") navigation.navigate("Tabs", { screen: "Reels" });
  else if (routePath === "/pulse/groups") navigation.navigate("Tabs", { screen: "Groups" });
  else if (routePath === "/pulse/saved") navigation.navigate("Tabs", { screen: "Saved" });
  else if (routePath === "/pulse/live") navigation.navigate("Tabs", { screen: "Live" });
  else if (routePath === "/pulse/events") navigation.navigate("Events", { title: "Events" });
  else if (routePath === "/pulse/marketplace") navigation.navigate("Tabs", { screen: "Marketplace" });
  else if (routePath === "/pulse/marketplace/create") navigation.navigate("MarketplaceCreateGateway", { title: "Create Listing" });
  else if (routePath === "/pulse/seller-store") navigation.navigate("SellerStore", { title: "Seller / Store" });
  else if (routePath === "/pulse/orders") navigation.navigate("BuyerOrders", { title: "Purchase History" });
  else if (routePath === "/pulse/premium") navigation.navigate("Premium");
  else if (routePath === "/pulse/creator-studio") navigation.navigate("CreatorStudio");
  else if (routePath === "/pulse/growth") navigation.navigate("GrowthCenter", { title: "Growth Center" });
  else if (routePath === "/pulse/safety") navigation.navigate("SafetyHub", { title: "Safety Hub" });
  else if (routePath === "/scam-shield/scan") navigation.navigate("ScamShield", { title: "Scam Shield" });
  else if (routePath === "/pulse/verification") navigation.navigate("VerificationCenter", { title: "Verification Center" });
  else if (routePath === "/pulse/account-health") navigation.navigate("AccountHealth", { title: "Account Health" });
  else if (routePath === "/pulse/support" || routePath === "/support") navigation.navigate("TrustSafetySupport", { title: "Support" });
  else if (routePath === "/pulse/ai") navigation.navigate("Tabs", { screen: "PulseAI" });
  else if (routePath === "/pulse/intelligence") navigation.navigate("IntelligenceCenter", { title: "Intelligence" });
  else if (routePath === "/pulse/alerts") navigation.navigate("AlertManagement", { title: "Alerts" });
  else if (routePath === "/pulse/courses") navigation.navigate("Courses", { title: "Courses" });
  else if (routePath === "/terms") navigation.navigate("TrustSafetyHelp", { title: "Terms" });
  else if (routePath === "/privacy") navigation.navigate("TrustSafetyHelp", { title: "Privacy Policy" });
  else openDashboardRoute(navigation, routePath);
}
