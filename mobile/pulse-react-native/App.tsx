import React, { useEffect } from "react";
import { ActivityIndicator, View } from "react-native";
import { NavigationContainer } from "@react-navigation/native";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { AppNavigator } from "./navigation/AppNavigator";
import { linking } from "./navigation/linking";
import { colors, screenStyles } from "./components/theme";
import { useAuthStore } from "./store/authStore";
import { registerForPushNotifications, wireNotificationLinks } from "./services/push";

export default function PulseSocMobileApp() {
  const ready = useAuthStore(state => state.ready);
  const signedIn = useAuthStore(state => state.signedIn);
  const restoreSession = useAuthStore(state => state.restoreSession);

  useEffect(() => {
    restoreSession().catch(() => undefined);
    const subscription = wireNotificationLinks();
    return () => subscription.remove();
  }, [restoreSession]);

  useEffect(() => {
    if (signedIn) {
      registerForPushNotifications().catch(() => undefined);
    }
  }, [signedIn]);

  return (
    <SafeAreaProvider>
      <NavigationContainer linking={linking} fallback={<Loading />}>
        {ready ? <AppNavigator /> : <Loading />}
      </NavigationContainer>
    </SafeAreaProvider>
  );
}

function Loading() {
  return (
    <View style={screenStyles.centered}>
      <ActivityIndicator color={colors.accent} />
    </View>
  );
}
