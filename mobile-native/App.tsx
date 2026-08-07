import { NavigationContainer, DefaultTheme } from "@react-navigation/native";
import { StatusBar } from "expo-status-bar";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, AppState, Linking, Platform, Pressable, Text, View } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider, initialWindowMetrics } from "react-native-safe-area-context";
import { IncomingCallLayer } from "./src/calls/IncomingCallLayer";
import { InAppNotificationBanner } from "./src/components/InAppNotificationBanner";
import { PulseBackground } from "./src/components/PulseBackground";
import { TimeZoneProvider } from "./src/core/TimeZoneContext";
import { I18nProvider, useI18n, useTranslation } from "./src/i18n";
import { AppNavigator } from "./src/navigation/AppNavigator";
import { AuthNavigator } from "./src/navigation/AuthNavigator";
import { linking } from "./src/navigation/linking";
import { navigationRef, routeNotificationTarget, setupNotificationResponseRouting } from "./src/navigation/notificationRouting";
import { RootStackParamList } from "./src/navigation/types";
import { AuthContext, AuthState, expiredState, fatalErrorState, restoreSession, stateFor } from "./src/session/auth";
import { isQaSimulatorAuthEnabled, tryHandleQaSimulatorAuthUrl } from "./src/session/qaSimulatorAuth";
import { SettingsProviders } from "./src/settings/SettingsProviders";
import { colors } from "./src/theme/colors";
import { useTheme } from "./src/theme/ThemeContext";
import { registerPushDevice, syncPushDeviceRegistration } from "./src/api/push";
import { startPresenceSession, stopPresenceSession } from "./src/api/presenceSession";
import { registerSessionInvalidationHandler } from "./src/api/pulseApi";
import { configurePerfTracing, perfNow, recordDuration, setPerfContext, startSpan } from "./src/core/perfTrace";
import { isFlagValueOn } from "./src/core/envFlag";
import { PerfOverlay } from "./src/components/PerfOverlay";
import { TranslationPreferencesBootstrap } from "./src/components/TranslationPreferencesBootstrap";
import { configurePulseShareCenter } from "./src/sharing/nativeShare";

// Captured at module evaluation so app.interactive reflects time-to-first-interactive-frame.
const APP_MODULE_START = perfNow();
setPerfContext({ osVersion: String(Platform.Version) });

// Perf tracing is on automatically in dev; the QA flag turns it on (plus the
// on-device overlay) for Release/QA device builds so real baselines can be captured.
//
// The `__DEV__` term short-circuits the flag and is kept exactly as it was: in a
// development build the overlay is on whatever the environment says, and the flag
// only ever has to answer for Release and QA builds. The flag read itself moved
// onto the shared reader — it had been the app's sixth parsing rule, accepting
// "1", "true" and "on" but not "yes", which no other flag rejected.
//
// The variable is spelled literally because Release is the only build this term
// answers for. `babel-preset-expo` substitutes `process.env.X` only when the key
// is a StringLiteral, so a name routed through a computed lookup is never
// inlined and reads undefined in a release bundle — the overlay could not be
// turned on in exactly the builds it exists to measure.
const PERF_OVERLAY_ENABLED =
  (typeof __DEV__ !== "undefined" && __DEV__) ||
  isFlagValueOn(process.env.EXPO_PUBLIC_PULSESOC_PERF_OVERLAY);
if (PERF_OVERLAY_ENABLED) configurePerfTracing({ enabled: true });

/**
 * The localization provider wraps everything, including the pre-navigation
 * bootstrap and error screens. Those screens render before any navigator
 * exists, so if the provider sat lower in the tree they would be the one part
 * of PulseSoc a user could still only read in English.
 */
export default function App() {
  return (
    <I18nProvider>
      <AppRoot />
    </I18nProvider>
  );
}

function AppRoot() {
  const { ready: i18nReady } = useI18n();
  const { t } = useTranslation();
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

  // One presence heartbeat per signed-in app instance, started here rather than
  // inside any single screen so this device counts as online everywhere in
  // PulseSoc -- not only while Messenger happens to be mounted. The runner owns
  // its own AppState subscription for foreground/background transitions; see
  // src/api/presenceSession.ts for why app termination needs no handling at all.
  useEffect(() => {
    if (authState.status !== "signedIn") return;
    startPresenceSession();
    return () => {
      stopPresenceSession().catch(() => undefined);
    };
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
    if (authState.status !== "signedIn") {
      configurePulseShareCenter(null);
      return;
    }
    configurePulseShareCenter((metadata) => {
      if (!navigationRef.isReady()) return false;
      navigationRef.navigate("PulseShare", metadata);
      return true;
    });
    return () => configurePulseShareCenter(null);
  }, [authState.status]);

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

  // Holding the splash until the core catalogs are resident is what guarantees
  // the first rendered frame is already in the user's language — no screen ever
  // paints English and then swaps.
  if (!i18nReady || authState.phase === "BOOTSTRAPPING") {
    return (
      <View
        accessibilityLabel={i18nReady ? t("common:a11y.loading") : undefined}
        style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.background }}
      >
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  if (authState.phase === "RECOVERABLE_ERROR" || authState.phase === "FATAL_ERROR") {
    const recoverable = authState.phase === "RECOVERABLE_ERROR";
    return (
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.background, padding: 24 }}>
        <Text style={{ color: colors.text, fontSize: 18, fontWeight: "700", textAlign: "center", marginBottom: 8 }}>
          {recoverable ? t("errors:network.title") : t("errors:generic.title")}
        </Text>
        <Text style={{ color: colors.muted, fontSize: 14, textAlign: "center", marginBottom: 20 }}>
          {recoverable ? t("errors:startup.sessionUnconfirmed") : t("errors:startup.launchFailed")}
        </Text>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={t("common:actions.retry")}
          onPress={bootstrapSession}
          style={{ backgroundColor: colors.accent, borderRadius: 12, paddingHorizontal: 28, paddingVertical: 12 }}
        >
          <Text style={{ color: colors.background, fontWeight: "700" }}>{t("common:actions.retry")}</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider initialMetrics={initialWindowMetrics}>
        <TimeZoneProvider>
          <AuthContext.Provider value={auth}>
            {/*
              Preferences and theme sit inside AuthContext and outside the
              NavigationContainer. Inside auth because the store's sync half
              needs a session to talk to; outside navigation because the theme
              feeds `NavigationContainer`'s own chrome and because every screen
              below — not just the settings tree — calls `useTheme`.

              `syncEnabled` follows the session rather than gating the whole
              provider on it: a signed-out user still gets their persisted theme
              and text size on the auth screens, we just don't issue preference
              requests that would 401.
            */}
            <SettingsProviders syncEnabled={authState.status === "signedIn"}>
              {/*
                The app's one background. Mounted here — inside `SafeAreaProvider`
                so it fills the window, inside `SettingsProviders` so it can read
                the active theme, and *above* `NavigationContainer` in the file so
                it is never remounted by a navigation event.

                It is childless, so it renders as an absolutely-positioned,
                `pointerEvents="none"` layer that takes no space in the column and
                intercepts nothing. Being the first child is what puts it behind
                everything else: without `zIndex` React Native paints siblings in
                document order.

                Rendering it *inside* `NavigationContainer` was rejected: the
                container is deliberately not keyed on the theme epoch (see the
                comment on `ThemedNavigationShell`) and giving it children
                complicates that reasoning for no benefit.

                What it deliberately does not cover: the two pre-navigation states
                above (they return before this tree exists and have no theme to
                read), and anything inside an RN `Modal`, which is a separate
                native window with no inheritance path.
              */}
              <PulseBackground />
              {authState.status === "signedIn" ? (
                <TranslationPreferencesBootstrap key={authState.user?.user_id || "signed-in"} />
              ) : null}
              <ThemedNavigationShell
                signedIn={authState.status === "signedIn"}
              />
              {authState.status === "signedIn" ? <InAppNotificationBanner /> : null}
              <IncomingCallLayer signedIn={authState.status === "signedIn"} currentUserId={authState.user?.user_id} />
              {PERF_OVERLAY_ENABLED ? <PerfOverlay /> : null}
            </SettingsProviders>
          </AuthContext.Provider>
        </TimeZoneProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}

/**
 * The point where the active theme becomes system chrome.
 *
 * Sits inside `SettingsProviders` so `useTheme()` reflects the stored
 * preference from the very first frame — the store hydrates from AsyncStorage
 * synchronously enough that a user on Black never sees a blue-dark flash.
 *
 * Three jobs:
 *  1. Status bar style comes from the theme (light content on Dark/Black,
 *     dark content on Light Futuristic/White) instead of the previous
 *     hard-coded `"light"` which was wrong on every light theme.
 *  2. React Navigation gets a theme built from the *live* palette, so screen
 *     transition backgrounds and headers can never flash the wrong scheme.
 * Deliberately NOT keyed on the theme epoch: remounting the container resets
 * navigation state, which would throw the user out of Settings → Appearance
 * the moment they tapped a theme. Legacy screens whose module-scope
 * StyleSheets captured launch-time colors are converted to `useThemedStyles`
 * screen by screen instead.
 */
function ThemedNavigationShell({ signedIn }: { signedIn: boolean }) {
  const theme = useTheme();
  const navigationTheme = useMemo(
    () => ({
      ...DefaultTheme,
      dark: theme.scheme === "dark",
      colors: {
        // Stays a real, opaque colour. This is not a rendering surface — it is
        // the colour visible in the gap between two cards mid-transition, and
        // behind the tab scenes on themes where `PulseBackground` draws nothing
        // (White). Making it transparent is exactly what produces a white or
        // black flash on push/pop, because what shows through then is whatever
        // the platform put behind the navigator. Set the atmosphere above it,
        // never instead of it. Pinned by
        // `src/navigation/__tests__/backgroundSurfaces.test.ts`.
        ...DefaultTheme.colors,
        background: theme.colors.background,
        card: theme.colors.surface,
        text: theme.colors.text,
        primary: theme.colors.accent,
        border: theme.colors.border
      }
    }),
    [theme]
  );
  return (
    <NavigationContainer
      ref={navigationRef}
      theme={navigationTheme}
      linking={signedIn ? linking : undefined}
    >
      <StatusBar style={theme.statusBarStyle} />
      {signedIn ? <AppNavigator /> : <AuthNavigator />}
    </NavigationContainer>
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
