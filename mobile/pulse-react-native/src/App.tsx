import React, { useEffect } from "react";
import { ActivityIndicator, Text, TouchableOpacity, View } from "react-native";
import { NavigationContainer } from "@react-navigation/native";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { AuthProvider, useAuth } from "./auth/AuthProvider";
import { linking, PulseRootParamList } from "./navigation/linking";
import { bindNotificationRouting, registerPushToken } from "./notifications/notifications";
import { ApiListScreen } from "./screens/ApiListScreen";
import { AuthScreen } from "./screens/AuthScreen";
import { CommunicationsScreen } from "./screens/CommunicationsScreen";
import { NotificationsScreen } from "./screens/NotificationsScreen";
import { colors, screenStyles } from "./styles/theme";

const Tab = createBottomTabNavigator<PulseRootParamList>();

const pulseApiEndpoints = {
  feed: "/api/pulse/feed",
  reels: "/api/pulse/reels/feed",
  videos: "/api/pulse/videos",
  messages: "/api/pulse/messages/conversations",
  notifications: "/api/pulse/notifications",
  profile: "/api/pulse/profile/me"
};

export default function PulseNativeApp() {
  useEffect(() => {
    const subscription = bindNotificationRouting();
    return () => subscription.remove();
  }, []);

  return (
    <SafeAreaProvider>
      <AuthProvider>
        <NavigationContainer linking={linking} fallback={<Loading />}>
          <RootNavigator />
        </NavigationContainer>
      </AuthProvider>
    </SafeAreaProvider>
  );
}

function RootNavigator() {
  const auth = useAuth();
  useEffect(() => {
    if (auth.signedIn) {
      registerPushToken().catch(() => undefined);
    }
  }, [auth.signedIn]);

  if (!auth.ready) return <Loading />;
  if (!auth.signedIn) {
    return <AuthScreen />;
  }
  return (
    <Tab.Navigator
      screenOptions={{
        headerStyle: { backgroundColor: colors.background },
        headerTintColor: colors.text,
        tabBarStyle: { backgroundColor: colors.surface, borderTopColor: colors.border },
        tabBarActiveTintColor: colors.accent,
        tabBarInactiveTintColor: colors.muted
      }}
    >
      <Tab.Screen name="Feed">{() => <ApiListScreen title="Feed" endpoint={pulseApiEndpoints.feed} listKey="posts" empty="Create the first PulseSoc post." />}</Tab.Screen>
      <Tab.Screen name="Reels">{() => <ApiListScreen title="Reels" endpoint={pulseApiEndpoints.reels} listKey="reels" empty="No reels yet." />}</Tab.Screen>
      <Tab.Screen name="Videos">{() => <ApiListScreen title="Videos" endpoint={pulseApiEndpoints.videos} listKey="videos" empty="No videos yet." />}</Tab.Screen>
      <Tab.Screen name="Messages" component={CommunicationsScreen} />
      <Tab.Screen name="Notifications" component={NotificationsScreen} />
      <Tab.Screen name="Profile">{() => <ProfileScreen />}</Tab.Screen>
      <Tab.Screen name="Premium">{() => <ApiListScreen title="Premium" endpoint={pulseApiEndpoints.profile} listKey="premium_features" empty="Premium status loads from your PulseSoc profile." />}</Tab.Screen>
    </Tab.Navigator>
  );
}

function ProfileScreen() {
  const auth = useAuth();
  return (
    <View style={screenStyles.screen}>
      <Text style={screenStyles.title}>Profile</Text>
      <View style={screenStyles.card}>
        <Text style={screenStyles.cardTitle}>{auth.user?.display_name || auth.user?.full_name || auth.user?.username || "PulseSoc member"}</Text>
        <Text style={screenStyles.muted}>{auth.user?.email || "PulseSoc.com"}</Text>
      </View>
      <TouchableOpacity onPress={auth.logout} style={screenStyles.button}>
        <Text style={screenStyles.buttonText}>Log Out</Text>
      </TouchableOpacity>
    </View>
  );
}

function Loading() {
  return (
    <View style={screenStyles.centered}>
      <ActivityIndicator color={colors.accent} />
    </View>
  );
}
