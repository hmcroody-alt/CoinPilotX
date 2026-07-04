import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import * as Notifications from "expo-notifications";
import { useCallback, useEffect, useState } from "react";
import { AppState } from "react-native";
import { getNotificationBadgeCounts, unreadCount } from "../api/notifications";
import { HomeScreen } from "../screens/HomeScreen";
import { MessengerScreen } from "../screens/MessengerScreen";
import { NotificationCenterScreen } from "../screens/NotificationCenterScreen";
import { NotificationPreferencesScreen } from "../screens/NotificationPreferencesScreen";
import { PostDetailScreen } from "../screens/PostDetailScreen";
import { PulseAiScreen } from "../screens/PulseAiScreen";
import { ProfileScreen } from "../screens/ProfileScreen";
import { SettingsScreen } from "../screens/SettingsScreen";
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
      <Tabs.Screen name="Messenger" component={MessengerScreen} />
      <Tabs.Screen name="Notifications" component={NotificationCenterScreen} options={{ tabBarBadge: notificationUnread || undefined }} />
      <Tabs.Screen name="PulseAI" component={PulseAiScreen} options={{ title: "Pulse AI" }} />
      <Tabs.Screen name="Profile" component={ProfileScreen} />
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
      <Stack.Screen name="Chat" component={ChatScreen} options={({ route }) => ({ title: route.params.title || "Chat" })} />
      <Stack.Screen name="PostDetail" component={PostDetailScreen} options={({ route }) => ({ title: route.params.title || "Post" })} />
      <Stack.Screen name="NotificationCenter" component={NotificationCenterScreen} options={{ title: "Notifications" }} />
      <Stack.Screen name="NotificationPreferences" component={NotificationPreferencesScreen} options={{ title: "Notification Preferences" }} />
    </Stack.Navigator>
  );
}
