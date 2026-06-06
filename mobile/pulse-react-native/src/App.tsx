import React, { useEffect } from "react";
import { NavigationContainer } from "@react-navigation/native";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { AuthProvider, useAuth } from "./auth/AuthProvider";
import { linking } from "./navigation/linking";
import { bindNotificationRouting, registerPushToken } from "./notifications/notifications";
import { ApiListScreen } from "./screens/ApiListScreen";
import { AuthScreen } from "./screens/AuthScreen";

const Tab = createBottomTabNavigator();

function Tabs() {
  useEffect(() => {
    const subscription = bindNotificationRouting();
    registerPushToken().catch(() => undefined);
    return () => subscription.remove();
  }, []);

  return (
    <Tab.Navigator screenOptions={{ headerShown: false }}>
      <Tab.Screen name="Feed">{() => <ApiListScreen title="Feed" endpoint="/api/pulse/feed" listKey="posts" />}</Tab.Screen>
      <Tab.Screen name="Reels">{() => <ApiListScreen title="Reels" endpoint="/api/pulse/reels/feed" listKey="reels" />}</Tab.Screen>
      <Tab.Screen name="Videos">{() => <ApiListScreen title="Videos" endpoint="/api/pulse/videos" listKey="videos" />}</Tab.Screen>
      <Tab.Screen name="Messages">{() => <ApiListScreen title="Messages" endpoint="/api/pulse/messages/conversations" listKey="conversations" />}</Tab.Screen>
      <Tab.Screen name="Notifications">{() => <ApiListScreen title="Notifications" endpoint="/api/pulse/notifications" listKey="notifications" />}</Tab.Screen>
      <Tab.Screen name="Profile">{() => <ApiListScreen title="Profile" endpoint="/api/pulse/profile/me" listKey="items" />}</Tab.Screen>
    </Tab.Navigator>
  );
}

function Root() {
  const auth = useAuth();
  if (!auth.ready) return null;
  return auth.signedIn ? <Tabs /> : <AuthScreen />;
}

export default function App() {
  return (
    <SafeAreaProvider>
      <AuthProvider>
        <NavigationContainer linking={linking}>
          <Root />
        </NavigationContainer>
      </AuthProvider>
    </SafeAreaProvider>
  );
}
