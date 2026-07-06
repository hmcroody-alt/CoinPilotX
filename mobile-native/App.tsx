import { NavigationContainer, DefaultTheme } from "@react-navigation/native";
import { StatusBar } from "expo-status-bar";
import { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Linking, View } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { IncomingCallLayer } from "./src/calls/IncomingCallLayer";
import { AppNavigator } from "./src/navigation/AppNavigator";
import { AuthNavigator } from "./src/navigation/AuthNavigator";
import { linking } from "./src/navigation/linking";
import { navigationRef, routeNotificationTarget, setupNotificationResponseRouting } from "./src/navigation/notificationRouting";
import { RootStackParamList } from "./src/navigation/types";
import { AuthContext, AuthState, restoreSession } from "./src/session/auth";
import { tryHandleQaSimulatorAuthUrl } from "./src/session/qaSimulatorAuth";
import { colors } from "./src/theme/colors";

export default function App() {
  const [authState, setAuthState] = useState<AuthState>({ status: "loading", user: null });
  const [pendingQaCameraRoute, setPendingQaCameraRoute] = useState<RootStackParamList["CameraStudio"] | null>(null);
  const [pendingQaRedirectTarget, setPendingQaRedirectTarget] = useState("");

  useEffect(() => {
    restoreSession().then(setAuthState).catch(() => setAuthState({ status: "signedOut", user: null }));
  }, []);

  useEffect(() => {
    const subscription = setupNotificationResponseRouting();
    return () => subscription.remove();
  }, []);

  useEffect(() => {
    let mounted = true;

    async function handleQaSimulatorAuthUrl(url: string | null) {
      if (!url) return;
      try {
        const result = await tryHandleQaSimulatorAuthUrl(url);
        if (!mounted || !result.handled) return;
        if (result.authState) setAuthState(result.authState);
        if (result.cameraRoute) setPendingQaCameraRoute(result.cameraRoute);
        if (result.redirectTarget) setPendingQaRedirectTarget(result.redirectTarget);
      } catch {
        // QA-only simulator auth bootstrap must never interrupt normal auth.
      }
    }

    Linking.getInitialURL().then(handleQaSimulatorAuthUrl).catch(() => undefined);
    const subscription = Linking.addEventListener("url", (event) => {
      handleQaSimulatorAuthUrl(event.url).catch(() => undefined);
    });
    return () => {
      mounted = false;
      subscription.remove();
    };
  }, []);

  useEffect(() => {
    if (authState.status !== "signedIn" || !pendingQaCameraRoute) return;
    let attempts = 0;
    const interval = setInterval(() => {
      attempts += 1;
      if (navigationRef.isReady()) {
        navigationRef.navigate("CameraStudio", pendingQaCameraRoute);
        setPendingQaCameraRoute(null);
        clearInterval(interval);
      } else if (attempts >= 20) {
        clearInterval(interval);
      }
    }, 150);
    return () => clearInterval(interval);
  }, [authState.status, pendingQaCameraRoute]);

  useEffect(() => {
    if (authState.status !== "signedIn" || !pendingQaRedirectTarget || pendingQaCameraRoute) return;
    let attempts = 0;
    const interval = setInterval(() => {
      attempts += 1;
      if (navigationRef.isReady()) {
        routeNotificationTarget(pendingQaRedirectTarget).catch(() => undefined);
        setPendingQaRedirectTarget("");
        clearInterval(interval);
      } else if (attempts >= 20) {
        clearInterval(interval);
      }
    }, 150);
    return () => clearInterval(interval);
  }, [authState.status, pendingQaCameraRoute, pendingQaRedirectTarget]);

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
        <IncomingCallLayer signedIn={authState.status === "signedIn"} currentUserId={authState.user?.user_id} />
      </AuthContext.Provider>
    </GestureHandlerRootView>
  );
}
