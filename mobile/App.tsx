import React, { useEffect } from "react";
import { ActivityIndicator, StatusBar, View } from "react-native";
import { NavigationContainer } from "@react-navigation/native";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { AppNavigator } from "./navigation/AppNavigator";
import { linking } from "./navigation/linking";
import { useAuthStore } from "./store/authStore";
import { registerForPushNotifications, wireNotificationLinks } from "./services/push";
import { colors, layout } from "./components/theme";

export default function App() {
  const ready = useAuthStore(state => state.ready);
  const restoreSession = useAuthStore(state => state.restoreSession);
  const signedIn = useAuthStore(state => state.signedIn);

  useEffect(() => {
    restoreSession();
  }, [restoreSession]);

  useEffect(() => {
    const subscription = wireNotificationLinks();
    if (signedIn) {
      registerForPushNotifications().catch(() => undefined);
    }
    return () => subscription.remove();
  }, [signedIn]);

  return (
    <SafeAreaProvider>
      <StatusBar barStyle="light-content" />
      <NavigationContainer linking={linking} fallback={<Loading />}>
        {ready ? <AppNavigator /> : <Loading />}
      </NavigationContainer>
    </SafeAreaProvider>
  );
}

function Loading() {
  return (
    <View style={layout.centeredScreen}>
      <ActivityIndicator color={colors.accent} />
    </View>
  );
}
