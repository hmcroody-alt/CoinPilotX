import { NavigationContainer, DefaultTheme } from "@react-navigation/native";
import { StatusBar } from "expo-status-bar";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, AppState, Linking, Platform, View } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { IncomingCallLayer } from "./src/calls/IncomingCallLayer";
import { AppNavigator } from "./src/navigation/AppNavigator";
import { AuthNavigator } from "./src/navigation/AuthNavigator";
import { linking } from "./src/navigation/linking";
import { navigationRef, routeNotificationTarget, setupNotificationResponseRouting } from "./src/navigation/notificationRouting";
import { RootStackParamList } from "./src/navigation/types";
import { AuthContext, AuthState, restoreSession } from "./src/session/auth";
import { isQaSimulatorAuthEnabled, tryHandleQaSimulatorAuthUrl } from "./src/session/qaSimulatorAuth";
import { colors } from "./src/theme/colors";
import { registerPushDevice, syncPushDeviceRegistration } from "./src/api/push";
import { registerSessionInvalidationHandler } from "./src/api/pulseApi";

export default function App() {
  const [authState, setAuthState] = useState<AuthState>({ status: "loading", user: null });
  const [pendingQaCameraRoute, setPendingQaCameraRoute] = useState<RootStackParamList["CameraStudio"] | null>(null);
  const [pendingQaRedirectTarget, setPendingQaRedirectTarget] = useState("");
  const [pendingNotificationTarget, setPendingNotificationTarget] = useState("");

  useEffect(() => {
    if (!isQaSimulatorAuthEnabled()) return;
    const startRoute = String(process.env.EXPO_PUBLIC_PULSESOC_QA_START_ROUTE || "").trim();
    if (startRoute.startsWith("/") && !startRoute.startsWith("//")) setPendingQaRedirectTarget(startRoute.slice(0, 240));
  }, []);

  useEffect(() => {
    restoreSession().then(setAuthState).catch(() => setAuthState({ status: "signedOut", user: null }));
  }, []);

  const requestReauthentication = useCallback((redirectTarget = "") => {
    if (redirectTarget) setPendingQaRedirectTarget(redirectTarget.slice(0, 240));
    setAuthState({ status: "signedOut", user: null });
  }, []);

  useEffect(() => registerSessionInvalidationHandler(({ path }) => {
    requestReauthentication(path.includes("/reels") ? "/pulse/reels" : "");
  }), [requestReauthentication]);

  useEffect(() => {
    if (authState.status !== "signedIn") return;
    registerPushDevice().catch(() => undefined);
    const appState = AppState.addEventListener("change", (state) => {
      if (state === "active") syncPushDeviceRegistration().catch(() => undefined);
    });
    return () => appState.remove();
  }, [authState.status, authState.user?.user_id]);

  useEffect(() => {
    const subscription = setupNotificationResponseRouting({
      canRoute: () => authState.status === "signedIn",
      onDeferred: (target) => setPendingNotificationTarget(target)
    });
    return () => subscription.remove();
  }, [authState.status]);

  useEffect(() => {
    if (authState.status !== "signedIn" || !pendingNotificationTarget) return;
    let attempts = 0;
    const interval = setInterval(() => {
      attempts += 1;
      if (navigationRef.isReady()) {
        routeNotificationTarget(pendingNotificationTarget).catch(() => undefined);
        setPendingNotificationTarget("");
        clearInterval(interval);
      } else if (attempts >= 20) {
        clearInterval(interval);
      }
    }, 150);
    return () => clearInterval(interval);
  }, [authState.status, pendingNotificationTarget]);

  useEffect(() => {
    let mounted = true;

    async function handleQaSimulatorAuthUrl(url: string | null) {
      if (!url) return;
      try {
        const result = await tryHandleQaSimulatorAuthUrl(url);
        if (!mounted) return;
        if (!result.handled) {
          const redirectTarget = authenticatedRedirectTarget(url);
          if (redirectTarget) setPendingQaRedirectTarget(redirectTarget);
          return;
        }
        if (result.authState) setAuthState(result.authState);
        if (result.cameraRoute) setPendingQaCameraRoute(result.cameraRoute);
        if (result.redirectTarget) setPendingQaRedirectTarget(result.redirectTarget);
      } catch {
        // QA-only simulator auth bootstrap must never interrupt normal auth.
      }
    }

    if (Platform.OS === "web" && typeof window !== "undefined") {
      handleQaSimulatorAuthUrl(window.location.href).catch(() => undefined);
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

  const auth = useMemo(() => ({ authState, setAuthState, requestReauthentication }), [authState, requestReauthentication]);
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

function authenticatedRedirectTarget(rawUrl: string) {
  try {
    const url = new URL(rawUrl);
    if (url.protocol === "https:" || url.protocol === "http:") {
      if (!/(^|\.)pulsesoc\.com$/i.test(url.hostname)) return "";
      return `${url.pathname}${url.search}`.slice(0, 240);
    }
    if (url.protocol === "pulsesoc:") {
      const path = `/${url.hostname}${url.pathname}`.replace(/\/{2,}/g, "/");
      return `${path}${url.search}`.slice(0, 240);
    }
  } catch {
    return "";
  }
  return "";
}
