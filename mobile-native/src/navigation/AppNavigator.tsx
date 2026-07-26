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
import { BusinessOsAdvertisingScreen } from "../screens/BusinessOsAdvertisingScreen";
import { BusinessOsInsightsScreen } from "../screens/BusinessOsInsightsScreen";
import { BusinessOsPaymentsScreen } from "../screens/BusinessOsPaymentsScreen";
import { BusinessOsScreen } from "../screens/BusinessOsScreen";
import { BuyerOrdersScreen } from "../screens/BuyerOrdersScreen";
import { CameraStudioScreen } from "../screens/CameraStudioScreen";
import { CallScreen } from "../screens/CallScreen";
import { ContentPlannerScreen } from "../screens/ContentPlannerScreen";
import { ContentPreviewScreen } from "../screens/ContentPreviewScreen";
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
import { LiveStudioScreen } from "../screens/LiveStudioScreen";
import { LiveHostSessionScreen } from "../screens/LiveHostSessionScreen";
import { MarketplaceScreen } from "../screens/MarketplaceScreen";
import { MessengerScreen } from "../screens/MessengerScreen";
import { MusicScreen } from "../screens/MusicScreen";
import { NewChatScreen } from "../screens/NewChatScreen";
import { NotificationCenterScreen } from "../screens/NotificationCenterScreen";
import { NotificationPreferencesScreen } from "../screens/NotificationPreferencesScreen";
import { RegionTimeScreen } from "../screens/RegionTimeScreen";
import { PulseQueueScreen } from "../screens/PulseQueueScreen";
import { PostDetailScreen } from "../screens/PostDetailScreen";
import { PremiumScreen } from "../screens/PremiumScreen";
import { ProfileEditScreen } from "../screens/ProfileEditScreen";
import { PulseShareScreen } from "../screens/PulseShareScreen";
import { PulseAiScreen } from "../screens/PulseAiScreen";
import { ProfileScreen } from "../screens/ProfileScreen";
import { ReelsScreen } from "../screens/ReelsScreen";
import { ReplayViewerScreen } from "../screens/ReplayViewerScreen";
import { SavedScreen } from "../screens/SavedScreen";
import { SafetyHubScreen } from "../screens/SafetyHubScreen";
import { SearchScreen } from "../screens/SearchScreen";
import { SellerListingComposerScreen } from "../screens/SellerListingComposerScreen";
import { SellerStoreScreen } from "../screens/SellerStoreScreen";
import { SettingsScreen } from "../screens/SettingsScreen";
import { AboutSettingsScreen } from "../screens/settings/AboutSettingsScreen";
import { AccessibilitySettingsScreen } from "../screens/settings/AccessibilitySettingsScreen";
import { AppearanceSettingsScreen } from "../screens/settings/AppearanceSettingsScreen";
import { BlockedUsersScreen } from "../screens/settings/BlockedUsersScreen";
import { DataPrivacySettingsScreen } from "../screens/settings/DataPrivacySettingsScreen";
import { DeveloperSettingsScreen } from "../screens/settings/DeveloperSettingsScreen";
import { HelpSettingsScreen } from "../screens/settings/HelpSettingsScreen";
import { LanguageSettingsScreen } from "../screens/settings/LanguageSettingsScreen";
import { LegalSettingsScreen } from "../screens/settings/LegalSettingsScreen";
import { MutedUsersScreen } from "../screens/settings/MutedUsersScreen";
import { NotificationSettingsScreen } from "../screens/settings/NotificationSettingsScreen";
import { PermissionsSettingsScreen } from "../screens/settings/PermissionsSettingsScreen";
import { PrivacySettingsScreen } from "../screens/settings/PrivacySettingsScreen";
import { SecuritySettingsScreen } from "../screens/settings/SecuritySettingsScreen";
import { SessionsDevicesScreen } from "../screens/settings/SessionsDevicesScreen";
import { StorageSettingsScreen } from "../screens/settings/StorageSettingsScreen";
import { StatusScreen } from "../screens/StatusScreen";
import { TrustSafetyScreen } from "../screens/TrustSafetyScreen";
import { UndxActionCenterScreen } from "../screens/UndxActionCenterScreen";
import { VerificationCenterScreen } from "../screens/VerificationCenterScreen";
import { UserDashboardScreen } from "../screens/UserDashboardScreen";
import { ChatScreen } from "../screens/ChatScreen";
import { colors } from "../theme/colors";
import { BottomNavVisibilityProvider } from "./BottomNavVisibility";
import { useAuth } from "../session/auth";
import { useTranslation } from "../i18n";
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
  // Titles are read through `t` at render time rather than being hoisted to
  // module scope, so switching language relabels the whole tab bar and every
  // header on the next render — no remount, no navigation state lost.
  const { t } = useTranslation();

  return (
    <BottomNavVisibilityProvider>
      <Tabs.Navigator
        initialRouteName="Home"
        tabBar={(props) => <LogiNexusBottomNavigation {...props} badges={badges} />}
        screenOptions={({ navigation, route }) => ({
          header: ({ options }) => (
            <LogiNexusGlobalHeader
              title={String(options.title || route.name)}
              subtitle={subtitleForTab(t, route.name)}
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
        <Tabs.Screen name="Dashboard" component={UserDashboardScreen} options={{ title: t("common:tabs.missionControl") }} />
        <Tabs.Screen name="Home" options={{ headerShown: false, title: t("common:tabs.home") }}>
          {() => <HomeScreen badges={badges} identity={identity} />}
        </Tabs.Screen>
        <Tabs.Screen name="Search" component={SearchScreen} options={{ title: t("common:tabs.search") }} />
        <Tabs.Screen name="Saved" component={SavedScreen} options={{ title: t("common:tabs.saved") }} />
        <Tabs.Screen name="Groups" component={GroupsScreen} options={{ title: t("common:tabs.communities") }} />
        <Tabs.Screen name="Live" component={LiveScreen} options={{ title: t("common:tabs.live") }} />
        <Tabs.Screen name="Reels" component={ReelsScreen} options={{ headerShown: false }} />
        <Tabs.Screen name="Create" component={CreateTabScreen} options={{ title: t("common:tabs.create") }} />
        <Tabs.Screen name="Status" component={StatusScreen} options={{ title: t("common:tabs.status") }} />
        <Tabs.Screen name="Messenger" component={MessengerScreen} options={{ headerShown: false, title: t("common:tabs.messages") }} />
        <Tabs.Screen name="Notifications" component={ActivityInboxScreen} options={{ title: t("common:tabs.activity") }} />
        <Tabs.Screen name="PulseAI" component={PulseAiScreen} options={{ title: t("common:tabs.undx") }} />
        <Tabs.Screen name="Profile" component={ProfileScreen} options={{ title: t("common:tabs.profile") }} />
        <Tabs.Screen name="Marketplace" component={MarketplaceScreen} options={{ title: t("common:tabs.marketplace") }} />
        <Tabs.Screen name="Settings" component={SettingsScreen} options={{ title: t("common:tabs.settings") }} />
      </Tabs.Navigator>
    </BottomNavVisibilityProvider>
  );
}

export function AppNavigator() {
  const { authState } = useAuth();
  const { t } = useTranslation();
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
    displayName:
      profile?.display_name ||
      authState.user?.display_name ||
      authState.user?.full_name ||
      t("common:identity.member"),
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
              title={String(options.title || stackTitle(t, route.name))}
              subtitle={subtitleForStack(t, route.name)}
              mode={route.name === "IntelligenceCenter" ? "intelligence" : "standard"}
              canGoBack={Boolean(back)}
              showDrawer={!back}
              onBack={() => navigation.goBack()}
              onOpenDrawer={() => setDrawerOpen(true)}
              onOpenSearch={() => navigation.navigate("Tabs", { screen: "Search" })}
              onOpenActivity={() =>
                navigation.navigate("ActivityInbox", { title: t("common:screens.activityInbox") })
              }
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
      <Stack.Screen name="UserDashboard" component={UserDashboardScreen} options={{ title: t("common:screens.dashboard") }} />
      <Stack.Screen name="UserDashboardWeb" component={UserDashboardScreen} options={{ title: t("common:screens.dashboard") }} />
      <Stack.Screen name="DashboardComposeAlias" component={DashboardActionAliasScreen} options={{ title: t("common:screens.createPost") }} />
      <Stack.Screen name="DashboardMusicAlias" component={DashboardActionAliasScreen} options={{ title: t("common:screens.pulseRadio") }} />
      <Stack.Screen name="Music" component={MusicScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.music") })} />
      <Stack.Screen name="PulseQueue" component={PulseQueueScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.queue") })} />
      <Stack.Screen name="DashboardLegacyModule" component={DashboardLegacyModuleScreen} options={{ title: t("common:screens.dashboardModule") }} />
      <Stack.Screen name="DashboardModuleDetail" component={DashboardModuleDetailScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.dashboardModule") })} />
      <Stack.Screen name="CameraStudio" component={CameraStudioScreen} options={{ headerShown: false }} />
      <Stack.Screen name="ContentPreview" component={ContentPreviewScreen} options={{ headerShown: false, presentation: "fullScreenModal" }} />
      <Stack.Screen name="Call" component={CallScreen} options={{ headerShown: false }} />
      <Stack.Screen name="Chat" component={ChatScreen} options={{ headerShown: false }} />
      <Stack.Screen name="NewChat" component={NewChatScreen} options={{ title: t("common:screens.newChat") }} />
      <Stack.Screen name="PulseShare" component={PulseShareScreen} options={{ title: t("common:screens.share"), presentation: "modal" }} />
      <Stack.Screen name="PostDetail" component={PostDetailScreen} options={({ route }) => ({ title: route.params.title || t("common:screens.post") })} />
      <Stack.Screen name="Reels" component={ReelsScreen} options={{ headerShown: false }} />
      <Stack.Screen name="ReelDetail" component={ReelsScreen} options={{ headerShown: false }} />
      <Stack.Screen name="StatusDetail" component={StatusScreen} options={({ route }) => ({ title: route.params.title || t("common:screens.status") })} />
      <Stack.Screen name="MarketplaceDetail" component={MarketplaceScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.marketplace") })} />
      <Stack.Screen name="BusinessOs" component={BusinessOsScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.businessOs") })} />
      <Stack.Screen name="BusinessOsAdvertising" component={BusinessOsAdvertisingScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.businessOsAdvertising") })} />
      <Stack.Screen name="BusinessOsInsights" component={BusinessOsInsightsScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.businessOsInsights") })} />
      <Stack.Screen name="BusinessOsPayments" component={BusinessOsPaymentsScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.businessOsPayments") })} />
      <Stack.Screen name="SellerStore" component={SellerStoreScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.sellerStore") })} />
      <Stack.Screen name="BuyerOrders" component={BuyerOrdersScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.purchaseHistory") })} />
      <Stack.Screen name="BuyerOrderDetail" component={BuyerOrdersScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.orderDetail") })} />
      <Stack.Screen name="BuyerPurchases" component={BuyerOrdersScreen} options={{ title: t("common:screens.purchaseHistory") }} />
      <Stack.Screen name="BuyerOrdersDashboard" component={BuyerOrdersScreen} options={{ title: t("common:screens.purchaseHistory") }} />
      <Stack.Screen name="MerchantApply" component={SellerStoreScreen} options={{ title: t("common:screens.merchantApplication") }} />
      <Stack.Screen name="MerchantDashboard" component={SellerStoreScreen} options={{ title: t("common:screens.merchantDashboard") }} />
      <Stack.Screen name="MerchantProfile" component={SellerStoreScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.merchantProfile") })} />
      <Stack.Screen name="MarketplaceCreateGateway" component={SellerListingComposerScreen} options={{ title: t("common:screens.createListing") }} />
      <Stack.Screen name="Search" component={SearchScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.search") })} />
      <Stack.Screen name="Saved" component={SavedScreen} options={{ title: t("common:screens.saved") }} />
      <Stack.Screen name="GroupDetail" component={GroupsScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.community") })} />
      <Stack.Screen name="LiveDetail" component={LiveScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.live") })} />
      <Stack.Screen name="LiveStudio" component={LiveStudioScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.liveStudio") })} />
      <Stack.Screen name="NativeLiveHost" component={LiveHostSessionScreen} options={{ headerShown: false, gestureEnabled: false }} />
      <Stack.Screen name="ReplayViewer" component={ReplayViewerScreen} options={{ headerShown: false }} />
      <Stack.Screen name="Events" component={EventsScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.events") })} />
      <Stack.Screen name="EventDetail" component={EventsScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.event") })} />
      <Stack.Screen name="LiveScheduleGateway" component={EventsScreen} options={{ title: t("common:screens.scheduleLive") }} />
      <Stack.Screen name="LiveEventCreateGateway" component={EventsScreen} options={{ title: t("common:screens.createLiveEvent") }} />
      <Stack.Screen name="ProfileDetail" component={ProfileScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.profile") })} />
      <Stack.Screen name="ProfileEdit" component={ProfileEditScreen} options={{ title: t("common:screens.editProfile") }} />
      <Stack.Screen name="Premium" component={PremiumScreen} options={{ title: t("common:screens.premium") }} />
      <Stack.Screen name="CreatorStudio" component={CreatorStudioScreen} options={{ title: t("common:screens.creatorStudio") }} />
      <Stack.Screen name="CreatorStudioAlias" component={CreatorStudioScreen} options={{ title: t("common:screens.creatorStudio") }} />
      <Stack.Screen name="ContentPlanner" component={ContentPlannerScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.contentPlanner") })} />
      <Stack.Screen name="ContentPlannerWeb" component={ContentPlannerScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.contentPlanner") })} />
      <Stack.Screen name="ContentPlannerPulseAlias" component={ContentPlannerScreen} options={{ title: t("common:screens.contentPlanner") }} />
      <Stack.Screen name="PostScheduler" component={ContentPlannerScreen} options={{ title: t("common:screens.scheduledPublishing") }} />
      <Stack.Screen name="PostSchedulerPulseAlias" component={ContentPlannerScreen} options={{ title: t("common:screens.scheduledPublishing") }} />
      <Stack.Screen name="DraftStudio" component={ContentPlannerScreen} options={{ title: t("common:screens.draftStudio") }} />
      <Stack.Screen name="DraftStudioPulseAlias" component={ContentPlannerScreen} options={{ title: t("common:screens.draftStudio") }} />
      <Stack.Screen name="Courses" component={CoursesLearningScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.courses") })} />
      <Stack.Screen name="CourseDetail" component={CoursesLearningScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.course") })} />
      <Stack.Screen name="LearningLessonDetail" component={CoursesLearningScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.lesson") })} />
      <Stack.Screen name="TeacherProfileGateway" component={CoursesLearningScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.teacher") })} />
      <Stack.Screen name="TeacherDashboardGateway" component={CoursesLearningScreen} options={{ title: t("common:screens.teacherDashboard") }} />
      <Stack.Screen name="GrowthCenter" component={GrowthCenterScreen} options={{ title: t("common:screens.growthCenter") }} />
      <Stack.Screen name="IntelligenceCenter" component={IntelligenceCenterScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.intelligence") })} />
      <Stack.Screen name="UndxActionCenter" component={UndxActionCenterScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.undxActionCenter") })} />
      <Stack.Screen name="AlertManagement" component={AlertManagementScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.alerts") })} />
      <Stack.Screen name="CryptoAlertManagement" component={AlertManagementScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.alerts") })} />
      <Stack.Screen name="AccountCenter" component={AccountCenterScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.accountCenter") })} />
      <Stack.Screen name="AccountSettings" component={AccountCenterScreen} options={{ title: t("common:screens.accountCenter") }} />
      <Stack.Screen name="AccountSecurity" component={AccountCenterScreen} options={{ title: t("common:screens.securityCenter") }} />
      <Stack.Screen name="AccountWebSettings" component={AccountCenterScreen} options={{ title: t("common:screens.accountCenter") }} />
      <Stack.Screen name="AccountWebSecurity" component={AccountCenterScreen} options={{ title: t("common:screens.securityCenter") }} />
      <Stack.Screen name="AccountPrivacy" component={AccountCenterScreen} options={{ title: t("common:screens.privacyCenter") }} />
      <Stack.Screen name="AccountDevices" component={AccountCenterScreen} options={{ title: t("common:screens.sessionsDevices") }} />
      <Stack.Screen name="AccountHealth" component={AccountHealthAppealsScreen} options={{ title: t("common:screens.accountHealth") }} />
      <Stack.Screen name="AccountHealthWeb" component={AccountHealthAppealsScreen} options={{ title: t("common:screens.accountHealth") }} />
      <Stack.Screen name="SafetyHub" component={SafetyHubScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.safetyHub") })} />
      <Stack.Screen name="SafetyWebHub" component={SafetyHubScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.safetyHub") })} />
      <Stack.Screen name="TrustSafety" component={TrustSafetyScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.trustSafety") })} />
      <Stack.Screen name="TrustSafetySupport" component={TrustSafetyScreen} options={{ title: t("common:screens.support") }} />
      <Stack.Screen name="TrustSafetyHelp" component={TrustSafetyScreen} options={{ title: t("common:screens.help") }} />
      <Stack.Screen name="TrustCenter" component={TrustSafetyScreen} options={{ title: t("common:screens.trustCenter") }} />
      <Stack.Screen name="SecurityReport" component={TrustSafetyScreen} options={{ title: t("common:screens.securityReport") }} />
      <Stack.Screen name="ScamShield" component={TrustSafetyScreen} options={{ title: t("common:screens.scamShield") }} />
      <Stack.Screen name="VerificationCenter" component={VerificationCenterScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.verificationCenter") })} />
      <Stack.Screen name="VerificationWebCenter" component={VerificationCenterScreen} options={{ title: t("common:screens.verificationCenter") }} />
      <Stack.Screen name="ActivityInbox" component={ActivityInboxScreen} options={({ route }) => ({ title: route.params?.title || t("common:screens.activityInbox") })} />
      <Stack.Screen name="ActivityInboxLegacyInbox" component={ActivityInboxScreen} options={{ title: t("common:screens.activityInbox") }} />
      <Stack.Screen name="ActivityInboxWebActivity" component={ActivityInboxScreen} options={{ title: t("common:screens.activityInbox") }} />
      <Stack.Screen name="ActivityInboxWebInbox" component={ActivityInboxScreen} options={{ title: t("common:screens.activityInbox") }} />
      <Stack.Screen name="NotificationCenter" component={NotificationCenterScreen} options={{ title: t("common:screens.notifications") }} />
      <Stack.Screen name="NotificationPreferences" component={NotificationPreferencesScreen} options={{ title: t("common:screens.notificationPreferences") }} />
      <Stack.Screen name="RegionTime" component={RegionTimeScreen} options={{ title: t("common:screens.languageRegionTime") }} />

      {/*
        Settings destinations. These titles are duplicated from
        `src/settings/registry.ts` on purpose: the registry drives the index
        list, the search index, and the deep-link table, but a native-stack
        header title has to be a static screen option so the header renders
        correctly on the very first frame — before the registry lookup that a
        dynamic `options` callback would need. Keeping them in sync is a
        two-line edit; the alternative was a header that flickers its title.
      */}
      <Stack.Screen name="NotificationSettings" component={NotificationSettingsScreen} options={{ title: t("common:screens.notifications") }} />
      <Stack.Screen name="AppearanceSettings" component={AppearanceSettingsScreen} options={{ title: t("common:screens.appearance") }} />
      <Stack.Screen name="AccessibilitySettings" component={AccessibilitySettingsScreen} options={{ title: t("common:screens.accessibility") }} />
      <Stack.Screen name="LanguageSettings" component={LanguageSettingsScreen} options={{ title: t("common:screens.languageRegion") }} />
      <Stack.Screen name="StorageSettings" component={StorageSettingsScreen} options={{ title: t("common:screens.storageData") }} />
      <Stack.Screen name="PermissionsSettings" component={PermissionsSettingsScreen} options={{ title: t("common:screens.devicePermissions") }} />
      <Stack.Screen name="PrivacySettings" component={PrivacySettingsScreen} options={{ title: t("common:screens.privacy") }} />
      <Stack.Screen name="SecuritySettings" component={SecuritySettingsScreen} options={{ title: t("common:screens.security") }} />
      <Stack.Screen name="SessionsDevices" component={SessionsDevicesScreen} options={{ title: t("common:screens.sessionsDevices") }} />
      <Stack.Screen name="BlockedUsers" component={BlockedUsersScreen} options={{ title: t("common:screens.blockedAccounts") }} />
      <Stack.Screen name="MutedUsers" component={MutedUsersScreen} options={{ title: t("common:screens.mutedAccounts") }} />
      <Stack.Screen name="DataPrivacySettings" component={DataPrivacySettingsScreen} options={{ title: t("common:screens.dataPersonalization") }} />
      <Stack.Screen name="HelpSettings" component={HelpSettingsScreen} options={{ title: t("common:screens.help") }} />
      <Stack.Screen name="AboutSettings" component={AboutSettingsScreen} options={{ title: t("common:screens.about") }} />
      <Stack.Screen name="LegalSettings" component={LegalSettingsScreen} options={{ title: t("common:screens.legal") }} />
      <Stack.Screen name="DeveloperSettings" component={DeveloperSettingsScreen} options={{ title: t("common:screens.developerOptions") }} />
    </Stack.Navigator>
      <MasterNavigationDrawer visible={drawerOpen} identity={identity} onClose={() => setDrawerOpen(false)} onOpenRoute={openDrawerRoute} />
    </>
  );
}

/**
 * `t` is threaded in as an argument rather than these helpers reaching for the
 * non-React `translate()`. Both would produce the right string, but only the
 * argument form makes the dependency visible to React: the caller is inside a
 * component subscribed to the locale, so a language change re-renders and these
 * are re-evaluated. Calling `translate()` from module scope would return a
 * correct string that then sat stale on screen until something else re-rendered.
 */
type Translate = (key: string, options?: Record<string, unknown>) => string;

function subtitleForTab(t: Translate, name: string) {
  if (name === "Dashboard") return t("common:navSubtitles.missionControl");
  if (name === "Messenger") return t("common:navSubtitles.pulseCommand");
  if (name === "PulseAI") return t("common:navSubtitles.intelligence");
  if (name === "Marketplace") return t("common:navSubtitles.commerce");
  if (name === "Notifications") return t("common:navSubtitles.activity");
  return t("common:app.name");
}

const SETTINGS_ROUTE_NAMES = new Set([
  "NotificationSettings",
  "AppearanceSettings",
  "AccessibilitySettings",
  "LanguageSettings",
  "StorageSettings",
  "PermissionsSettings",
  "PrivacySettings",
  "SecuritySettings",
  "SessionsDevices",
  "BlockedUsers",
  "MutedUsers",
  "DataPrivacySettings",
  "HelpSettings",
  "AboutSettings",
  "LegalSettings",
  "DeveloperSettings"
]);

function subtitleForStack(t: Translate, name: string) {
  // Checked before the substring tests below, which would otherwise mislabel
  // these: "PrivacySettings" contains neither "Account" nor "Safety", so it
  // would fall through to the generic "Native PulseSoc route".
  if (SETTINGS_ROUTE_NAMES.has(name)) return t("common:tabs.settings");
  if (name.includes("Dashboard")) return t("common:navSubtitles.missionControl");
  if (name.includes("Account") || name.includes("Safety") || name.includes("Verification"))
    return t("common:navSubtitles.trustIdentity");
  if (name.includes("Marketplace") || name.includes("Seller") || name.includes("Buyer"))
    return t("common:navSubtitles.commerce");
  if (name.includes("Call") || name.includes("Chat")) return t("common:navSubtitles.pulseCommand");
  return t("common:navSubtitles.nativeRoute");
}

/**
 * Last-resort header title for a route that declares none.
 *
 * The de-camel-casing is deliberately *not* translated: it produces English
 * words out of an English identifier, so localizing the result is impossible.
 * Every route that a user can actually reach declares a `title` from
 * `common:screens.*` above, and the navigator test asserts that; this exists so
 * a newly added route shows something recognisable instead of a blank bar
 * during development.
 */
function stackTitle(t: Translate, name: string) {
  return name.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/Web|Alias/g, "").trim() || t("common:app.name");
}
