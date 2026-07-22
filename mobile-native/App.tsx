import { NavigationContainer, DefaultTheme } from "@react-navigation/native";
import { StatusBar } from "expo-status-bar";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, AppState, Linking, Platform, Pressable, Text, View } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider, initialWindowMetrics } from "react-native-safe-area-context";
import { IncomingCallLayer } from "./src/calls/IncomingCallLayer";
import { TimeZoneProvider } from "./src/core/TimeZoneContext";
import { AppNavigator } from "./src/navigation/AppNavigator";
import { AuthNavigator } from "./src/navigation/AuthNavigator";
import { linking } from "./src/navigation/linking";
import { navigationRef, routeNotificationTarget, setupNotificationResponseRouting } from "./src/navigation/notificationRouting";
import { RootStackParamList } from "./src/navigation/types";
import { AuthContext, AuthState, expiredState, fatalErrorState, restoreSession, stateFor } from "./src/session/auth";
import { isQaSimulatorAuthEnabled, tryHandleQaSimulatorAuthUrl } from "./src/session/qaSimulatorAuth";
import { colors } from "./src/theme/colors";
import { registerPushDevice, syncPushDeviceRegistration } from "./src/api/push";
import { registerSessionInvalidationHandler } from "./src/api/pulseApi";
import { configurePerfTracing, perfNow, recordDuration, setPerfContext, startSpan } from "./src/core/perfTrace";
import { PerfOverlay } from "./src/components/PerfOverlay";

// Captured at module evaluation so app.interactive reflects time-to-first-interactive-frame.
const APP_MODULE_START = perfNow();
setPerfContext({ osVersion: String(Platform.Version) });

// Perf tracing is on automatically in dev; the QA flag turns it on (plus the
// on-device overlay) for Release/QA device builds so real baselines can be captured.
const PERF_OVERLAY_ENABLED =
  (typeof __DEV__ !== "undefined" && __DEV__) ||
  ["1", "true", "on"].includes(String(process.env.EXPO_PUBLIC_PULSESOC_PERF_OVERLAY || "").trim().toLowerCase());
if (PERF_OVERLAY_ENABLED) configurePerfTracing({ enabled: true });

export default function App() {
  const [authState, setAuthState] = useState<AuthState>(stateFor("BOOTSTRAPPING"));
  const [pendingQaCameraRoute, setPendingQaCameraRoute] = useState<RootStackParamList["CameraStudio"] | null>(null);
  const [pendingQaRedirectTarget, setPendingQaRedirectTarget] = useState("");
  const [pendingNotificationTarget, setPendingNotificationTarget] = useState("");

  useEffect(() => {
    if (!isQaSimulatorAuthEnabled()) return;
    const startRoute = String(process.env.EXPO_PUBLIC_PULSESOC_QA_START_ROUTE || "").trim();
    if (startRoute.startsWith("/") && !startRoute.startsWith("//")) setPendingQaRedirectTarget(startRoute.slice(0, 240));
  }, []);

  const bootstrapSession = useCallback(() => {
    const span = startSpan("app.restoreSession");
    setAuthState(stateFor("BOOTSTRAPPING"));
    restoreSession()
      .then((state) => {
        span.end({ status: state.phase });
        setAuthState(state);
      })
      .catch(() => {
        // restoreSession resolves to a terminal phase itself; a throw here is
        // unexpected, so surface it as FATAL_ERROR rather than a false sign-out.
        span.end({ status: "error" });
        setAuthState(fatalErrorState());
      });
  }, []);

  useEffect(() => {
    bootstrapSession();
  }, [bootstrapSession]);

  const interactiveRecorded = useRef(false);
  useEffect(() => {
    if (authState.status === "loading" || interactiveRecorded.current) return;
    interactiveRecorded.current = true;
    recordDuration("app.interactive", perfNow() - APP_MODULE_START, { status: authState.status });
  }, [authState.status]);

  const requestReauthentication = useCallback((redirectTarget = "") => {
    if (redirectTarget) setPendingQaRedirectTarget(redirectTarget.slice(0, 240));
    // Reauth is always triggered by an invalidated/expired live session, so mark
    // it SESSION_EXPIRED (distinct from a fresh, never-signed-in launch).
    setAuthState(expiredState());
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

  if (authState.phase === "BOOTSTRAPPING") {
    return (
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.background }}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  if (authState.phase === "RECOVERABLE_ERROR" || authState.phase === "FATAL_ERROR") {
    const recoverable = authState.phase === "RECOVERABLE_ERROR";
    return (
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.background, padding: 24 }}>
        <Text style={{ color: colors.text, fontSize: 18, fontWeight: "700", textAlign: "center", marginBottom: 8 }}>
          {recoverable ? "Can't reach PulseSoc" : "Something went wrong"}
        </Text>
        <Text style={{ color: colors.muted, fontSize: 14, textAlign: "center", marginBottom: 20 }}>
          {recoverable
            ? "We couldn't confirm your session. Check your connection and try again."
            : "We couldn't start PulseSoc. Please try again."}
        </Text>
        <Pressable
          accessibilityRole="button"
          onPress={bootstrapSession}
          style={{ backgroundColor: colors.accent, borderRadius: 12, paddingHorizontal: 28, paddingVertical: 12 }}
        >
          <Text style={{ color: colors.background, fontWeight: "700" }}>Try again</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider initialMetrics={initialWindowMetrics}>
        <TimeZoneProvider>
          <AuthContext.Provider value={auth}>
            <NavigationContainer ref={navigationRef} theme={theme} linking={authState.status === "signedIn" ? linking : undefined}>
              <StatusBar style="light" />
              {authState.status === "signedIn" ? <AppNavigator /> : <AuthNavigator />}
            </NavigationContainer>
            <IncomingCallLayer signedIn={authState.status === "signedIn"} currentUserId={authState.user?.user_id} />
            {PERF_OVERLAY_ENABLED ? <PerfOverlay /> : null}
          </AuthContext.Provider>
        </TimeZoneProvider>
      </SafeAreaProvider>
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
