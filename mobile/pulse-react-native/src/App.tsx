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
import { colors, screenStyles } from "./styles/theme";

const Tab = createBottomTabNavigator<PulseRootParamList>();

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
      <Tab.Screen name="Feed">{() => <ApiListScreen title="Feed" endpoint="/api/pulse/feed" listKey="posts" empty="Create the first Pulse post." />}</Tab.Screen>
      <Tab.Screen name="Reels">{() => <ApiListScreen title="Reels" endpoint="/api/pulse/reels/feed" listKey="reels" empty="No reels yet." />}</Tab.Screen>
      <Tab.Screen name="Videos">{() => <ApiListScreen title="Videos" endpoint="/api/pulse/videos" listKey="videos" empty="No videos yet." />}</Tab.Screen>
      <Tab.Screen name="Messages">{() => <ApiListScreen title="Messages" endpoint="/api/pulse/messages/conversations" listKey="conversations" empty="No conversations yet." />}</Tab.Screen>
      <Tab.Screen name="Notifications">{() => <ApiListScreen title="Notifications" endpoint="/api/pulse/notifications" listKey="notifications" empty="No notifications yet." />}</Tab.Screen>
      <Tab.Screen name="Profile">{() => <ProfileScreen />}</Tab.Screen>
      <Tab.Screen name="Premium">{() => <ApiListScreen title="Premium" endpoint="/api/pulse/profile/me" listKey="premium_features" empty="Premium status loads from your Pulse profile." />}</Tab.Screen>
    </Tab.Navigator>
  );
}

function ProfileScreen() {
  const auth = useAuth();
  return (
    <View style={screenStyles.screen}>
      <Text style={screenStyles.title}>Profile</Text>
      <View style={screenStyles.card}>
        <Text style={screenStyles.cardTitle}>{auth.user?.display_name || auth.user?.full_name || auth.user?.username || "Pulse member"}</Text>
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
