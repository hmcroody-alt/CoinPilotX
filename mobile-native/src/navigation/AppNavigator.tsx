import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import * as Notifications from "expo-notifications";
import { useCallback, useEffect, useState } from "react";
import { AppState } from "react-native";
import { getNotificationBadgeCounts, unreadCount } from "../api/notifications";
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
import { GrowthCenterScreen } from "../screens/GrowthCenterScreen";
import { GroupsScreen } from "../screens/GroupsScreen";
import { HomeScreen } from "../screens/HomeScreen";
import { IntelligenceCenterScreen } from "../screens/IntelligenceCenterScreen";
import { EventsScreen } from "../screens/EventsScreen";
import { LiveScreen } from "../screens/LiveScreen";
import { MarketplaceScreen } from "../screens/MarketplaceScreen";
import { MessengerScreen } from "../screens/MessengerScreen";
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
import { ChatScreen } from "../screens/ChatScreen";
import { colors } from "../theme/colors";
import { AppTabParamList, RootStackParamList } from "./types";

const Stack = createNativeStackNavigator<RootStackParamList>();
const Tabs = createBottomTabNavigator<AppTabParamList>();

function TabNavigator() {
  const [notificationUnread, setNotificationUnread] = useState(0);

  const refreshBadges = useCallback(async () => {
    try {
      const counts = await getNotificationBadgeCounts();
      const nextUnread = unreadCount(counts);
      setNotificationUnread(nextUnread);
      await Notifications.setBadgeCountAsync(nextUnread).catch(() => undefined);
    } catch {
      setNotificationUnread(0);
    }
  }, []);

  useEffect(() => {
    refreshBadges().catch(() => undefined);
    const appState = AppState.addEventListener("change", (state) => {
      if (state === "active") refreshBadges().catch(() => undefined);
    });
    const received = Notifications.addNotificationReceivedListener(() => {
      refreshBadges().catch(() => undefined);
    });
    return () => {
      appState.remove();
      received.remove();
    };
  }, [refreshBadges]);

  return (
    <Tabs.Navigator
      screenOptions={{
        headerStyle: { backgroundColor: colors.surface },
        headerTintColor: colors.text,
        tabBarStyle: { backgroundColor: colors.surface, borderTopColor: colors.border },
        tabBarActiveTintColor: colors.accent,
        tabBarInactiveTintColor: colors.muted
      }}
    >
      <Tabs.Screen name="Home" component={HomeScreen} />
      <Tabs.Screen name="Search" component={SearchScreen} />
      <Tabs.Screen name="Saved" component={SavedScreen} />
      <Tabs.Screen name="Groups" component={GroupsScreen} />
      <Tabs.Screen name="Live" component={LiveScreen} />
      <Tabs.Screen name="Reels" component={ReelsScreen} options={{ headerShown: false }} />
      <Tabs.Screen name="Status" component={StatusScreen} />
      <Tabs.Screen name="Messenger" component={MessengerScreen} />
      <Tabs.Screen name="Notifications" component={ActivityInboxScreen} options={{ title: "Activity", tabBarBadge: notificationUnread || undefined }} />
      <Tabs.Screen name="PulseAI" component={PulseAiScreen} options={{ title: "Pulse AI" }} />
      <Tabs.Screen name="Profile" component={ProfileScreen} />
      <Tabs.Screen name="Marketplace" component={MarketplaceScreen} />
      <Tabs.Screen name="Settings" component={SettingsScreen} />
    </Tabs.Navigator>
  );
}

export function AppNavigator() {
  return (
    <Stack.Navigator
      screenOptions={{
        headerStyle: { backgroundColor: colors.surface },
        headerTintColor: colors.text,
        contentStyle: { backgroundColor: colors.background }
      }}
    >
      <Stack.Screen name="Tabs" component={TabNavigator} options={{ headerShown: false }} />
      <Stack.Screen name="CameraStudio" component={CameraStudioScreen} options={{ headerShown: false }} />
      <Stack.Screen name="Call" component={CallScreen} options={{ headerShown: false }} />
      <Stack.Screen name="Chat" component={ChatScreen} options={({ route }) => ({ title: route.params.title || "Chat" })} />
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
  );
}
