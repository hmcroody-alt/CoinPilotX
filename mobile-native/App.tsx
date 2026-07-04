import { NavigationContainer, DefaultTheme } from "@react-navigation/native";
import { StatusBar } from "expo-status-bar";
import { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, View } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { AppNavigator } from "./src/navigation/AppNavigator";
import { AuthNavigator } from "./src/navigation/AuthNavigator";
import { linking } from "./src/navigation/linking";
import { navigationRef, setupNotificationResponseRouting } from "./src/navigation/notificationRouting";
import { AuthContext, AuthState, restoreSession } from "./src/session/auth";
import { colors } from "./src/theme/colors";

export default function App() {
  const [authState, setAuthState] = useState<AuthState>({ status: "loading", user: null });

  useEffect(() => {
    restoreSession().then(setAuthState).catch(() => setAuthState({ status: "signedOut", user: null }));
  }, []);

  useEffect(() => {
    const subscription = setupNotificationResponseRouting();
    return () => subscription.remove();
  }, []);

  const auth = useMemo(() => ({ authState, setAuthState }), [authState]);
  const theme = useMemo(
    () => ({
      ...DefaultTheme,
      colors: {
        ...DefaultTheme.colors,
        background: colors.background,
        card: colors.surface,
        text: colors.text,
        primary: colors.accent,
        border: colors.border
      }
    }),
    []
  );

  if (authState.status === "loading") {
    return (
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.background }}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <AuthContext.Provider value={auth}>
        <NavigationContainer ref={navigationRef} theme={theme} linking={authState.status === "signedIn" ? linking : undefined}>
          <StatusBar style="light" />
          {authState.status === "signedIn" ? <AppNavigator /> : <AuthNavigator />}
        </NavigationContainer>
      </AuthContext.Provider>
    </GestureHandlerRootView>
  );
}
