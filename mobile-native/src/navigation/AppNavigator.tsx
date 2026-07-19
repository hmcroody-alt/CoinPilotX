import { BottomTabNavigationProp, createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { useNavigation } from "@react-navigation/native";
import * as Notifications from "expo-notifications";
import { useCallback, useEffect, useState } from "react";
import { AppState } from "react-native";
import { alertUnreadCount, chatUnreadCount, getNotificationBadgeCounts, totalUnreadCount } from "../api/notifications";
import { getMyProfile, PulseProfile } from "../api/profile";
import { MasterNavigationDrawer } from "../components/MasterNavigationDrawer";
import { invalidateNativeSync, registerSyncInvalidation, startNativeEventSync } from "../core/eventSync";
import { AccountCenterScreen } from "../screens/AccountCenterScreen";
import { AccountHealthAppealsScreen } from "../screens/AccountHealthAppealsScreen";
import { ActivityInboxScreen } from "../screens/ActivityInboxScreen";
import { AlertManagementScreen } from "../screens/AlertManagementScreen";
import { BuyerOrdersScreen } from "../screens/BuyerOrdersScreen";
import { CameraStudioScreen } from "../screens/CameraStudioScreen";
import { CallScreen } from "../screens/CallScreen";
import { ContentPlannerScreen } from "../screens/ContentPlannerScreen";
import { CoursesLearningScreen } from "../screens/CoursesLearningScreen";
import { CreatorStudioScreen } from "../screens/CreatorStudioScreen";
import { DashboardLegacyModuleScreen } from "../screens/DashboardLegacyModuleScreen";
import { DashboardModuleDetailScreen } from "../screens/DashboardModuleDetailScreen";
import { DashboardActionAliasScreen } from "../screens/DashboardActionAliasScreen";
import { GrowthCenterScreen } from "../screens/GrowthCenterScreen";
import { GroupsScreen } from "../screens/GroupsScreen";
import { HomeScreen } from "../screens/HomeScreen";
import { IntelligenceCenterScreen } from "../screens/IntelligenceCenterScreen";
import { EventsScreen } from "../screens/EventsScreen";
import { LiveScreen } from "../screens/LiveScreen";
import { MarketplaceScreen } from "../screens/MarketplaceScreen";
import { MessengerScreen } from "../screens/MessengerScreen";
import { NewChatScreen } from "../screens/NewChatScreen";
import { NotificationCenterScreen } from "../screens/NotificationCenterScreen";
import { NotificationPreferencesScreen } from "../screens/NotificationPreferencesScreen";
import { PostDetailScreen } from "../screens/PostDetailScreen";
import { PremiumScreen } from "../screens/PremiumScreen";
import { ProfileEditScreen } from "../screens/ProfileEditScreen";
import { PulseAiScreen } from "../screens/PulseAiScreen";
import { ProfileScreen } from "../screens/ProfileScreen";
import { ReelsScreen } from "../screens/ReelsScreen";
import { SavedScreen } from "../screens/SavedScreen";
import { SafetyHubScreen } from "../screens/SafetyHubScreen";
import { SearchScreen } from "../screens/SearchScreen";
import { SellerListingComposerScreen } from "../screens/SellerListingComposerScreen";
import { SellerStoreScreen } from "../screens/SellerStoreScreen";
import { SettingsScreen } from "../screens/SettingsScreen";
import { StatusScreen } from "../screens/StatusScreen";
import { TrustSafetyScreen } from "../screens/TrustSafetyScreen";
import { VerificationCenterScreen } from "../screens/VerificationCenterScreen";
import { UserDashboardScreen } from "../screens/UserDashboardScreen";
import { ChatScreen } from "../screens/ChatScreen";
import { colors } from "../theme/colors";
import { BottomNavVisibilityProvider } from "./BottomNavVisibility";
import { useAuth } from "../session/auth";
import { GlobalNavigationBadges, GlobalNavigationIdentity, LogiNexusBottomNavigation, LogiNexusGlobalHeader } from "./GlobalNavigation";
import { AppTabParamList, RootStackParamList } from "./types";
import { openNativeRoute } from "./nativeRouteActions";
import { navigationRef } from "./notificationRouting";
import { PULSESOC_QA_REELS_FIXTURES } from "../api/config";

const Stack = createNativeStackNavigator<RootStackParamList>();
const Tabs = createBottomTabNavigator<AppTabParamList>();

function CreateTabScreen() {
  const navigation = useNavigation<BottomTabNavigationProp<AppTabParamList>>();

  useEffect(() => {
    navigation.navigate("Home", { openComposer: true });
  }, [navigation]);

  return null;
}

function TabNavigator({
  badges,
  identity,
  onOpenDrawer
}: {
  badges: GlobalNavigationBadges;
  identity?: GlobalNavigationIdentity;
  onOpenDrawer: () => void;
}) {
  return (
    <BottomNavVisibilityProvider>
      <Tabs.Navigator
        tabBar={(props) => <LogiNexusBottomNavigation {...props} badges={badges} />}
        screenOptions={({ navigation, route }) => ({
          header: ({ options }) => (
            <LogiNexusGlobalHeader
              title={String(options.title || route.name)}
              subtitle={subtitleForTab(route.name)}
              mode={route.name === "PulseAI" ? "intelligence" : "standard"}
              showDrawer
              onOpenDrawer={onOpenDrawer}
              onOpenSearch={() => navigation.navigate("Search")}
              onOpenActivity={() => navigation.navigate("Notifications")}
              onOpenMessages={() => navigation.navigate("Messenger")}
              onOpenProfile={() => navigation.navigate("Profile")}
              badges={badges}
              identity={identity}
            />
          )
        })}
      >
        <Tabs.Screen name="Dashboard" component={UserDashboardScreen} options={{ title: "Mission Control" }} />
        <Tabs.Screen name="Home" options={{ headerShown: false, title: "Home" }}>
          {() => <HomeScreen badges={badges} identity={identity} />}
        </Tabs.Screen>
        <Tabs.Screen name="Search" component={SearchScreen} options={{ title: "Search" }} />
        <Tabs.Screen name="Saved" component={SavedScreen} options={{ title: "Saved" }} />
        <Tabs.Screen name="Groups" component={GroupsScreen} options={{ title: "Communities" }} />
        <Tabs.Screen name="Live" component={LiveScreen} options={{ title: "Live" }} />
        <Tabs.Screen name="Reels" component={ReelsScreen} options={{ headerShown: false }} />
        <Tabs.Screen name="Create" component={CreateTabScreen} options={{ title: "Create" }} />
        <Tabs.Screen name="Status" component={StatusScreen} options={{ title: "Status" }} />
        <Tabs.Screen name="Messenger" component={MessengerScreen} options={{ headerShown: false, title: "Messages" }} />
        <Tabs.Screen name="Notifications" component={ActivityInboxScreen} options={{ title: "Activity" }} />
        <Tabs.Screen name="PulseAI" component={PulseAiScreen} options={{ title: "UNDX" }} />
        <Tabs.Screen name="Profile" component={ProfileScreen} options={{ title: "Profile" }} />
        <Tabs.Screen name="Marketplace" component={MarketplaceScreen} options={{ title: "Marketplace" }} />
        <Tabs.Screen name="Settings" component={SettingsScreen} options={{ title: "Settings" }} />
      </Tabs.Navigator>
    </BottomNavVisibilityProvider>
  );
}

export function AppNavigator() {
  const { authState } = useAuth();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [badges, setBadges] = useState<GlobalNavigationBadges>({});
  const [profile, setProfile] = useState<PulseProfile | null>(null);

  const refreshBadges = useCallback(async () => {
    try {
      const counts = await getNotificationBadgeCounts();
      const activity = totalUnreadCount(counts);
      const messages = chatUnreadCount(counts);
      const alerts = alertUnreadCount(counts);
      setBadges({ activity, messages, alerts });
      await Notifications.setBadgeCountAsync(activity).catch(() => undefined);
    } catch {
      setBadges({});
    }
  }, []);

  useEffect(() => {
    refreshBadges().catch(() => undefined);
    const refreshBadgeSync = () => refreshBadges();
    const unregisterNotifications = registerSyncInvalidation("notifications", refreshBadgeSync);
    const unregisterActivity = registerSyncInvalidation("activity", refreshBadgeSync);
    const stopSync = startNativeEventSync({
      fullResyncOnStart: true,
      subsystems: ["activity", "notifications", "orders", "marketplace", "seller_inventory", "status", "reels"]
    });
    const appState = AppState.addEventListener("change", (state) => {
      if (state === "active") refreshBadges().catch(() => undefined);
    });
    const received = Notifications.addNotificationReceivedListener(() => {
      invalidateNativeSync(["notifications", "activity"], "notification_received").catch(() => undefined);
      refreshBadges().catch(() => undefined);
    });
    return () => {
      unregisterNotifications();
      unregisterActivity();
      stopSync();
      appState.remove();
      received.remove();
    };
  }, [refreshBadges]);

  useEffect(() => {
    getMyProfile().then(setProfile).catch(() => setProfile(null));
  }, []);

  const identity: GlobalNavigationIdentity = {
    displayName: profile?.display_name || authState.user?.display_name || authState.user?.full_name || "PulseSoc member",
    username: profile?.username || authState.user?.username || "",
    avatarUrl: profile?.avatar_thumbnail_url || profile?.avatar_url || authState.user?.avatar_url || "",
    verified: Boolean(profile?.verified_badge),
    premium: ["active", "premium", "founder"].includes(String(profile?.premium_status || authState.user?.premium_status || "").toLowerCase()),
    attention: String(profile?.account_status || authState.user?.account_status || "active").toLowerCase() !== "active"
  };

  function openDrawerRoute(routePath: string) {
    setDrawerOpen(false);
    if (navigationRef.isReady()) openNativeRoute(navigationRef, routePath);
  }

  return (
    <>
      <Stack.Navigator
        initialRouteName={PULSESOC_QA_REELS_FIXTURES ? "Reels" : "Tabs"}
        screenOptions={({ route, navigation }) => ({
          contentStyle: { backgroundColor: colors.background },
          header: ({ back, options }) => (
            <LogiNexusGlobalHeader
              title={String(options.title || stackTitle(route.name))}
              subtitle={subtitleForStack(route.name)}
              mode={route.name === "IntelligenceCenter" ? "intelligence" : "standard"}
              canGoBack={Boolean(back)}
              showDrawer={!back}
              onBack={() => navigation.goBack()}
              onOpenDrawer={() => setDrawerOpen(true)}
              onOpenSearch={() => navigation.navigate("Tabs", { screen: "Search" })}
              onOpenActivity={() => navigation.navigate("ActivityInbox", { title: "Activity Inbox" })}
              onOpenMessages={() => navigation.navigate("Tabs", { screen: "Messenger" })}
              onOpenProfile={() => navigation.navigate("Tabs", { screen: "Profile" })}
              badges={badges}
              identity={identity}
            />
          )
        })}
      >
      <Stack.Screen name="Tabs" options={{ headerShown: false }}>
        {() => <TabNavigator badges={badges} identity={identity} onOpenDrawer={() => setDrawerOpen(true)} />}
      </Stack.Screen>
      <Stack.Screen name="UserDashboard" component={UserDashboardScreen} options={{ title: "Dashboard" }} />
      <Stack.Screen name="UserDashboardWeb" component={UserDashboardScreen} options={{ title: "Dashboard" }} />
      <Stack.Screen name="DashboardComposeAlias" component={DashboardActionAliasScreen} options={{ title: "Create Post" }} />
      <Stack.Screen name="DashboardMusicAlias" component={DashboardActionAliasScreen} options={{ title: "Pulse Radio" }} />
      <Stack.Screen name="DashboardLegacyModule" component={DashboardLegacyModuleScreen} options={{ title: "Dashboard Module" }} />
      <Stack.Screen name="DashboardModuleDetail" component={DashboardModuleDetailScreen} options={({ route }) => ({ title: route.params?.title || "Dashboard Module" })} />
      <Stack.Screen name="CameraStudio" component={CameraStudioScreen} options={{ headerShown: false }} />
      <Stack.Screen name="Call" component={CallScreen} options={{ headerShown: false }} />
      <Stack.Screen name="Chat" component={ChatScreen} options={{ headerShown: false }} />
      <Stack.Screen name="NewChat" component={NewChatScreen} options={{ title: "New Chat" }} />
      <Stack.Screen name="PostDetail" component={PostDetailScreen} options={({ route }) => ({ title: route.params.title || "Post" })} />
      <Stack.Screen name="Reels" component={ReelsScreen} options={{ headerShown: false }} />
      <Stack.Screen name="ReelDetail" component={ReelsScreen} options={{ headerShown: false }} />
      <Stack.Screen name="StatusDetail" component={StatusScreen} options={({ route }) => ({ title: route.params.title || "Status" })} />
      <Stack.Screen name="MarketplaceDetail" component={MarketplaceScreen} options={({ route }) => ({ title: route.params?.title || "Marketplace" })} />
      <Stack.Screen name="SellerStore" component={SellerStoreScreen} options={({ route }) => ({ title: route.params?.title || "Seller / Store" })} />
      <Stack.Screen name="BuyerOrders" component={BuyerOrdersScreen} options={({ route }) => ({ title: route.params?.title || "Purchase History" })} />
      <Stack.Screen name="BuyerOrderDetail" component={BuyerOrdersScreen} options={({ route }) => ({ title: route.params?.title || "Order Detail" })} />
      <Stack.Screen name="BuyerPurchases" component={BuyerOrdersScreen} options={{ title: "Purchase History" }} />
      <Stack.Screen name="BuyerOrdersDashboard" component={BuyerOrdersScreen} options={{ title: "Purchase History" }} />
      <Stack.Screen name="MerchantApply" component={SellerStoreScreen} options={{ title: "Merchant Application" }} />
      <Stack.Screen name="MerchantDashboard" component={SellerStoreScreen} options={{ title: "Merchant Dashboard" }} />
      <Stack.Screen name="MerchantProfile" component={SellerStoreScreen} options={({ route }) => ({ title: route.params?.title || "Merchant Profile" })} />
      <Stack.Screen name="MarketplaceCreateGateway" component={SellerListingComposerScreen} options={{ title: "Create Listing" }} />
      <Stack.Screen name="Search" component={SearchScreen} options={({ route }) => ({ title: route.params?.title || "Search" })} />
      <Stack.Screen name="Saved" component={SavedScreen} options={{ title: "Saved" }} />
      <Stack.Screen name="GroupDetail" component={GroupsScreen} options={({ route }) => ({ title: route.params?.title || "Community" })} />
      <Stack.Screen name="LiveDetail" component={LiveScreen} options={({ route }) => ({ title: route.params?.title || "Live" })} />
      <Stack.Screen name="Events" component={EventsScreen} options={({ route }) => ({ title: route.params?.title || "Events" })} />
      <Stack.Screen name="EventDetail" component={EventsScreen} options={({ route }) => ({ title: route.params?.title || "Event" })} />
      <Stack.Screen name="LiveScheduleGateway" component={EventsScreen} options={{ title: "Schedule Live" }} />
      <Stack.Screen name="LiveEventCreateGateway" component={EventsScreen} options={{ title: "Create Live Event" }} />
      <Stack.Screen name="ProfileDetail" component={ProfileScreen} options={({ route }) => ({ title: route.params?.title || "Profile" })} />
      <Stack.Screen name="ProfileEdit" component={ProfileEditScreen} options={{ title: "Edit Profile" }} />
      <Stack.Screen name="Premium" component={PremiumScreen} options={{ title: "Premium" }} />
      <Stack.Screen name="CreatorStudio" component={CreatorStudioScreen} options={{ title: "Creator Studio" }} />
      <Stack.Screen name="CreatorStudioAlias" component={CreatorStudioScreen} options={{ title: "Creator Studio" }} />
      <Stack.Screen name="ContentPlanner" component={ContentPlannerScreen} options={({ route }) => ({ title: route.params?.title || "Content Planner" })} />
      <Stack.Screen name="ContentPlannerWeb" component={ContentPlannerScreen} options={({ route }) => ({ title: route.params?.title || "Content Planner" })} />
      <Stack.Screen name="ContentPlannerPulseAlias" component={ContentPlannerScreen} options={{ title: "Content Planner" }} />
      <Stack.Screen name="PostScheduler" component={ContentPlannerScreen} options={{ title: "Scheduled Publishing" }} />
      <Stack.Screen name="PostSchedulerPulseAlias" component={ContentPlannerScreen} options={{ title: "Scheduled Publishing" }} />
      <Stack.Screen name="DraftStudio" component={ContentPlannerScreen} options={{ title: "Draft Studio" }} />
      <Stack.Screen name="DraftStudioPulseAlias" component={ContentPlannerScreen} options={{ title: "Draft Studio" }} />
      <Stack.Screen name="Courses" component={CoursesLearningScreen} options={({ route }) => ({ title: route.params?.title || "Courses" })} />
      <Stack.Screen name="CourseDetail" component={CoursesLearningScreen} options={({ route }) => ({ title: route.params?.title || "Course" })} />
      <Stack.Screen name="LearningLessonDetail" component={CoursesLearningScreen} options={({ route }) => ({ title: route.params?.title || "Lesson" })} />
      <Stack.Screen name="TeacherProfileGateway" component={CoursesLearningScreen} options={({ route }) => ({ title: route.params?.title || "Teacher" })} />
      <Stack.Screen name="TeacherDashboardGateway" component={CoursesLearningScreen} options={{ title: "Teacher Dashboard" }} />
      <Stack.Screen name="GrowthCenter" component={GrowthCenterScreen} options={{ title: "Growth Center" }} />
      <Stack.Screen name="IntelligenceCenter" component={IntelligenceCenterScreen} options={({ route }) => ({ title: route.params?.title || "Intelligence" })} />
      <Stack.Screen name="AlertManagement" component={AlertManagementScreen} options={({ route }) => ({ title: route.params?.title || "Alerts" })} />
      <Stack.Screen name="CryptoAlertManagement" component={AlertManagementScreen} options={({ route }) => ({ title: route.params?.title || "Alerts" })} />
      <Stack.Screen name="AccountCenter" component={AccountCenterScreen} options={({ route }) => ({ title: route.params?.title || "Account Center" })} />
      <Stack.Screen name="AccountSettings" component={AccountCenterScreen} options={{ title: "Account Center" }} />
      <Stack.Screen name="AccountSecurity" component={AccountCenterScreen} options={{ title: "Security Center" }} />
      <Stack.Screen name="AccountWebSettings" component={AccountCenterScreen} options={{ title: "Account Center" }} />
      <Stack.Screen name="AccountWebSecurity" component={AccountCenterScreen} options={{ title: "Security Center" }} />
      <Stack.Screen name="AccountPrivacy" component={AccountCenterScreen} options={{ title: "Privacy Center" }} />
      <Stack.Screen name="AccountDevices" component={AccountCenterScreen} options={{ title: "Sessions and Devices" }} />
      <Stack.Screen name="AccountHealth" component={AccountHealthAppealsScreen} options={{ title: "Account Health" }} />
      <Stack.Screen name="AccountHealthWeb" component={AccountHealthAppealsScreen} options={{ title: "Account Health" }} />
      <Stack.Screen name="SafetyHub" component={SafetyHubScreen} options={({ route }) => ({ title: route.params?.title || "Safety Hub" })} />
      <Stack.Screen name="SafetyWebHub" component={SafetyHubScreen} options={({ route }) => ({ title: route.params?.title || "Safety Hub" })} />
      <Stack.Screen name="TrustSafety" component={TrustSafetyScreen} options={({ route }) => ({ title: route.params?.title || "Trust & Safety" })} />
      <Stack.Screen name="TrustSafetySupport" component={TrustSafetyScreen} options={{ title: "Support" }} />
      <Stack.Screen name="TrustSafetyHelp" component={TrustSafetyScreen} options={{ title: "Help" }} />
      <Stack.Screen name="TrustCenter" component={TrustSafetyScreen} options={{ title: "Trust Center" }} />
      <Stack.Screen name="SecurityReport" component={TrustSafetyScreen} options={{ title: "Security Report" }} />
      <Stack.Screen name="ScamShield" component={TrustSafetyScreen} options={{ title: "Scam Shield" }} />
      <Stack.Screen name="VerificationCenter" component={VerificationCenterScreen} options={({ route }) => ({ title: route.params?.title || "Verification Center" })} />
      <Stack.Screen name="VerificationWebCenter" component={VerificationCenterScreen} options={{ title: "Verification Center" }} />
      <Stack.Screen name="ActivityInbox" component={ActivityInboxScreen} options={({ route }) => ({ title: route.params?.title || "Activity Inbox" })} />
      <Stack.Screen name="ActivityInboxLegacyInbox" component={ActivityInboxScreen} options={{ title: "Activity Inbox" }} />
      <Stack.Screen name="ActivityInboxWebActivity" component={ActivityInboxScreen} options={{ title: "Activity Inbox" }} />
      <Stack.Screen name="ActivityInboxWebInbox" component={ActivityInboxScreen} options={{ title: "Activity Inbox" }} />
      <Stack.Screen name="NotificationCenter" component={NotificationCenterScreen} options={{ title: "Notifications" }} />
      <Stack.Screen name="NotificationPreferences" component={NotificationPreferencesScreen} options={{ title: "Notification Preferences" }} />
    </Stack.Navigator>
      <MasterNavigationDrawer visible={drawerOpen} identity={identity} onClose={() => setDrawerOpen(false)} onOpenRoute={openDrawerRoute} />
    </>
  );
}

function subtitleForTab(name: string) {
  if (name === "Dashboard") return "Mission Control";
  if (name === "Messenger") return "Pulse Command";
  if (name === "PulseAI") return "PulseSoc Intelligence";
  if (name === "Marketplace") return "Commerce layer";
  if (name === "Notifications") return "Activity and notification signals";
  return "PulseSoc";
}

function subtitleForStack(name: string) {
  if (name.includes("Dashboard")) return "Mission Control";
  if (name.includes("Account") || name.includes("Safety") || name.includes("Verification")) return "Trust and identity layer";
  if (name.includes("Marketplace") || name.includes("Seller") || name.includes("Buyer")) return "Commerce layer";
  if (name.includes("Call") || name.includes("Chat")) return "Pulse Command";
  return "Native PulseSoc route";
}

function stackTitle(name: string) {
  return name.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/Web|Alias/g, "").trim() || "PulseSoc";
}
