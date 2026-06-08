import AsyncStorage from "@react-native-async-storage/async-storage";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Animated, BackHandler, Easing, Image, Linking, Modal, Platform, Pressable, ScrollView, Share, StatusBar, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import WebView, { WebViewMessageEvent, WebViewNavigation } from "react-native-webview";
import type { WebView as WebViewType } from "react-native-webview";
import { colors, screenStyles } from "./components/theme";
import { ensureNotificationPresentation, getNativePushToken, presentNativeDeviceAlert, wireNotificationLinks, wireNotificationPresentation } from "./services/push";

const PULSESOC_ORIGIN = "https://pulsesoc.com";
const PULSESOC_HOSTS = new Set(["pulsesoc.com", "www.pulsesoc.com"]);
const LANGUAGE_STORAGE_KEY = "pulsesoc.welcome.language";
const SESSION_CHECK_URL = `${PULSESOC_ORIGIN}/api/mobile/auth/session`;

type LaunchState = "checking" | "welcome" | "web";

const LANGUAGE_OPTIONS = [
  { label: "English", value: "en" },
  { label: "Français", value: "fr" },
  { label: "Kreyòl Ayisyen", value: "ht" },
  { label: "Español", value: "es" },
  { label: "Português", value: "pt" }
];

const WELCOME_FEATURES = [
  { icon: "C", label: "Join Communities" },
  { icon: "V", label: "Watch Videos & Reels" },
  { icon: "RT", label: "Chat in Real Time" },
  { icon: "AI", label: "AI-Powered Discovery" },
  { icon: "EX", label: "Exclusive Premium" },
  { icon: "PF", label: "Privacy First" }
];

export default function PulseSocMobileApp() {
  return (
    <SafeAreaProvider>
      <StatusBar barStyle="light-content" backgroundColor={colors.background} />
      <PulseSocWebShell />
    </SafeAreaProvider>
  );
}

function PulseSocWebShell() {
  const webViewRef = useRef<WebViewType>(null);
  const sessionCheckDoneRef = useRef(false);
  const [sourceUrl, setSourceUrl] = useState(PULSESOC_ORIGIN);
  const [canGoBack, setCanGoBack] = useState(false);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [launchState, setLaunchState] = useState<LaunchState>("checking");

  const injectedJavaScript = useMemo(() => createInjectedBridge(), []);

  const navigateToAppUrl = useCallback((incomingUrl: string) => {
    const next = toPulseSocWebUrl(incomingUrl);
    setOffline(false);
    setSourceUrl(next);
    webViewRef.current?.injectJavaScript(`window.location.href = ${JSON.stringify(next)}; true;`);
  }, []);

  const openWebFlow = useCallback((path: string) => {
    const next = `${PULSESOC_ORIGIN}${path}`;
    setSourceUrl(next);
    setOffline(false);
    setLoading(true);
    setLaunchState("web");
  }, []);

  useEffect(() => {
    ensureNotificationPresentation().catch(() => undefined);
    Linking.getInitialURL().then(url => {
      if (url) navigateToAppUrl(url);
    }).catch(() => undefined);

    const linkSubscription = Linking.addEventListener("url", event => navigateToAppUrl(event.url));
    const notificationSubscription = wireNotificationLinks(url => navigateToAppUrl(url));
    const presentationSubscription = wireNotificationPresentation();
    return () => {
      linkSubscription.remove();
      notificationSubscription.remove();
      presentationSubscription.remove();
    };
  }, [navigateToAppUrl]);

  useEffect(() => {
    if (launchState !== "checking") return undefined;
    const timer = setTimeout(() => {
      if (!sessionCheckDoneRef.current) setLaunchState("welcome");
    }, 3500);
    return () => clearTimeout(timer);
  }, [launchState]);

  useEffect(() => {
    if (Platform.OS !== "android") return undefined;
    const subscription = BackHandler.addEventListener("hardwareBackPress", () => {
      if (canGoBack) {
        webViewRef.current?.goBack();
        return true;
      }
      return false;
    });
    return () => subscription.remove();
  }, [canGoBack]);

  const handleMessage = useCallback(async (event: WebViewMessageEvent) => {
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(event.nativeEvent.data);
    } catch {
      return;
    }

    if (payload.type === "PULSESOC_REGISTER_PUSH") {
      const result = await getNativePushToken();
      if (!result.ok) {
        postToWeb(webViewRef.current, { type: "PULSESOC_PUSH_RESULT", ok: false, message: result.message });
        return;
      }
      injectPushTokenRegistration(webViewRef.current, result.token);
      return;
    }

    if (payload.type === "PULSESOC_SHARE") {
      const url = typeof payload.url === "string" ? payload.url : sourceUrl;
      const title = typeof payload.title === "string" ? payload.title : "PulseSoc";
      const text = typeof payload.text === "string" ? payload.text : title;
      await Share.share({ title, message: `${text}\n${url}`, url }).catch(() => undefined);
      return;
    }

    if (payload.type === "PULSESOC_NOTIFY_DEVICE") {
      await presentNativeDeviceAlert(payload).catch(() => undefined);
    }
  }, [sourceUrl]);

  const handleSessionCheckMessage = useCallback((event: WebViewMessageEvent) => {
    try {
      const payload = JSON.parse(event.nativeEvent.data);
      sessionCheckDoneRef.current = true;
      if (payload.authenticated) {
        setSourceUrl(`${PULSESOC_ORIGIN}/pulse`);
        setLaunchState("web");
        return;
      }
      setLaunchState("welcome");
    } catch {
      sessionCheckDoneRef.current = true;
      setLaunchState("welcome");
    }
  }, []);

  const runSessionCheckScript = useCallback(() => {
    webViewRef.current?.injectJavaScript(createSessionCheckScript());
  }, []);

  const shouldStartLoad = useCallback((request: { url: string }) => {
    if (request.url === "about:blank") return true;
    const nextUrl = normalizeUrl(request.url);
    if (!nextUrl) return false;
    if (nextUrl.protocol === "pulse:") {
      navigateToAppUrl(request.url);
      return false;
    }
    if (nextUrl.protocol !== "http:" && nextUrl.protocol !== "https:") {
      Linking.openURL(request.url).catch(() => undefined);
      return false;
    }
    if (PULSESOC_HOSTS.has(nextUrl.hostname)) return true;
    Linking.openURL(request.url).catch(() => undefined);
    return false;
  }, [navigateToAppUrl]);

  function handleNavigation(navState: WebViewNavigation) {
    setCanGoBack(navState.canGoBack);
  }

  if (offline) {
    return <OfflineScreen onRetry={() => {
      setOffline(false);
      webViewRef.current?.reload();
    }} />;
  }

  if (launchState === "checking") {
    return (
      <View style={{ flex: 1, backgroundColor: colors.background }}>
        <LoadingOverlay />
        <WebView
          ref={webViewRef}
          source={{ uri: SESSION_CHECK_URL }}
          style={{ width: 1, height: 1, opacity: 0 }}
          sharedCookiesEnabled
          thirdPartyCookiesEnabled
          domStorageEnabled
          javaScriptEnabled
          onMessage={handleSessionCheckMessage}
          onError={() => setLaunchState("welcome")}
          onHttpError={() => setLaunchState("welcome")}
          onLoadEnd={runSessionCheckScript}
          injectedJavaScript={createSessionCheckScript()}
        />
      </View>
    );
  }

  if (launchState === "welcome") {
    return (
      <PremiumWelcomeScreen
        onCreateAccount={() => openWebFlow("/register")}
        onSignIn={() => openWebFlow("/login?next=/pulse")}
      />
    );
  }

  return (
    <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: colors.background }}>
      <WebView
        ref={webViewRef}
        source={{ uri: sourceUrl }}
        style={{ flex: 1, backgroundColor: colors.background }}
        containerStyle={{ backgroundColor: colors.background }}
        originWhitelist={["https://*", "http://*", "pulse://*"]}
        javaScriptEnabled
        domStorageEnabled
        sharedCookiesEnabled
        thirdPartyCookiesEnabled
        cacheEnabled
        androidLayerType="hardware"
        decelerationRate={Platform.OS === "ios" ? "normal" : undefined}
        automaticallyAdjustContentInsets={false}
        contentInsetAdjustmentBehavior="never"
        bounces
        overScrollMode="always"
        nestedScrollEnabled
        allowsBackForwardNavigationGestures
        allowsInlineMediaPlayback
        mediaPlaybackRequiresUserAction={false}
        pullToRefreshEnabled
        setSupportMultipleWindows={false}
        startInLoadingState
        injectedJavaScriptBeforeContentLoaded={injectedJavaScript}
        onMessage={handleMessage}
        onShouldStartLoadWithRequest={shouldStartLoad}
        onNavigationStateChange={handleNavigation}
        onLoadStart={() => setLoading(true)}
        onLoadEnd={() => setLoading(false)}
        onError={() => setOffline(true)}
        onHttpError={event => {
          if (event.nativeEvent.statusCode >= 500) setOffline(true);
        }}
        renderLoading={() => <LoadingOverlay />}
      />
      {loading ? <LoadingOverlay compact /> : null}
    </SafeAreaView>
  );
}

function PremiumWelcomeScreen({ onCreateAccount, onSignIn }: { onCreateAccount: () => void; onSignIn: () => void }) {
  const fade = useRef(new Animated.Value(0)).current;
  const scale = useRef(new Animated.Value(0.92)).current;
  const ring = useRef(new Animated.Value(0)).current;
  const [language, setLanguage] = useState(LANGUAGE_OPTIONS[0]);
  const [languageOpen, setLanguageOpen] = useState(false);

  useEffect(() => {
    AsyncStorage.getItem(LANGUAGE_STORAGE_KEY).then(saved => {
      const next = LANGUAGE_OPTIONS.find(item => item.value === saved);
      if (next) setLanguage(next);
    }).catch(() => undefined);

    Animated.parallel([
      Animated.timing(fade, {
        toValue: 1,
        duration: 520,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true
      }),
      Animated.spring(scale, {
        toValue: 1,
        friction: 8,
        tension: 60,
        useNativeDriver: true
      }),
      Animated.loop(
        Animated.timing(ring, {
          toValue: 1,
          duration: 5200,
          easing: Easing.linear,
          useNativeDriver: true
        })
      )
    ]).start();
  }, [fade, ring, scale]);

  const rotate = ring.interpolate({
    inputRange: [0, 1],
    outputRange: ["0deg", "360deg"]
  });

  const chooseLanguage = (next: typeof LANGUAGE_OPTIONS[number]) => {
    setLanguage(next);
    setLanguageOpen(false);
    AsyncStorage.setItem(LANGUAGE_STORAGE_KEY, next.value).catch(() => undefined);
  };

  return (
    <SafeAreaView edges={["top", "bottom"]} style={welcomeStyles.screen}>
      <View style={welcomeStyles.cityGlow} />
      <View style={welcomeStyles.energyGlow} />

      <View style={welcomeStyles.topBar}>
        <View />
        <TouchableOpacity
          accessibilityRole="button"
          accessibilityLabel="Choose language"
          style={welcomeStyles.languageButton}
          onPress={() => setLanguageOpen(true)}
        >
          <Text style={welcomeStyles.languageText}>{language.label}</Text>
          <Text style={welcomeStyles.languageChevron}>v</Text>
        </TouchableOpacity>
      </View>

      <ScrollView
        contentContainerStyle={welcomeStyles.content}
        bounces={false}
        showsVerticalScrollIndicator={false}
      >
        <Animated.View style={[welcomeStyles.logoStage, { opacity: fade, transform: [{ scale }] }]}>
          <Animated.View style={[welcomeStyles.outerRing, { transform: [{ rotate }] }]} />
          <View style={welcomeStyles.logoGlow} />
          <Image source={require("./assets/icon.png")} style={welcomeStyles.logo} resizeMode="contain" />
        </Animated.View>

        <Text style={welcomeStyles.headline}>Join PulseSoc</Text>
        <Text style={welcomeStyles.slogan}>Connect. Create. Discover. Pulse the World.</Text>

        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={welcomeStyles.featureStrip}
        >
          {WELCOME_FEATURES.map(feature => (
            <View key={feature.label} style={welcomeStyles.featurePill}>
              <View style={welcomeStyles.featureIcon}>
                <Text style={welcomeStyles.featureIconText}>{feature.icon}</Text>
              </View>
              <Text style={welcomeStyles.featureLabel}>{feature.label}</Text>
            </View>
          ))}
        </ScrollView>

        <TouchableOpacity style={welcomeStyles.primaryButton} onPress={onCreateAccount} activeOpacity={0.86}>
          <Text style={welcomeStyles.primaryButtonText}>Create Your PulseSoc Account</Text>
        </TouchableOpacity>

        <TouchableOpacity style={welcomeStyles.secondaryButton} onPress={onSignIn} activeOpacity={0.86}>
          <Text style={welcomeStyles.secondaryButtonText}>Sign In to PulseSoc</Text>
        </TouchableOpacity>

        <Text style={welcomeStyles.footer}>PulseSoc™ • Built by CoinPilotXAI Inc.</Text>
      </ScrollView>

      <Modal visible={languageOpen} transparent animationType="fade" onRequestClose={() => setLanguageOpen(false)}>
        <Pressable style={welcomeStyles.modalBackdrop} onPress={() => setLanguageOpen(false)}>
          <View style={welcomeStyles.languageMenu}>
            {LANGUAGE_OPTIONS.map(option => (
              <TouchableOpacity key={option.value} style={welcomeStyles.languageOption} onPress={() => chooseLanguage(option)}>
                <Text style={[welcomeStyles.languageOptionText, option.value === language.value && welcomeStyles.languageOptionActive]}>
                  {option.label}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        </Pressable>
      </Modal>
    </SafeAreaView>
  );
}

function LoadingOverlay({ compact }: { compact?: boolean }) {
  return (
    <View pointerEvents="none" style={{
      position: "absolute",
      top: compact ? 10 : 0,
      right: compact ? 10 : 0,
      bottom: compact ? undefined : 0,
      left: compact ? undefined : 0,
      alignItems: "center",
      justifyContent: "center",
      backgroundColor: compact ? "transparent" : colors.background
    }}>
      <ActivityIndicator color={colors.accent} />
    </View>
  );
}

function OfflineScreen({ onRetry }: { onRetry: () => void }) {
  return (
    <SafeAreaView edges={["top", "bottom"]} style={screenStyles.centered}>
      <Text style={screenStyles.title}>PulseSoc is offline</Text>
      <Text style={[screenStyles.subtitle, { textAlign: "center" }]}>Check your connection and try again. The app will reload the live PulseSoc website when you are back online.</Text>
      <TouchableOpacity style={screenStyles.button} onPress={onRetry}>
        <Text style={screenStyles.buttonText}>Retry</Text>
      </TouchableOpacity>
    </SafeAreaView>
  );
}

function normalizeUrl(value: string) {
  try {
    return new URL(value);
  } catch {
    return null;
  }
}

function toPulseSocWebUrl(incomingUrl: string) {
  const parsed = normalizeUrl(incomingUrl);
  if (!parsed) return PULSESOC_ORIGIN;
  if (parsed.protocol === "pulse:") {
    const path = [parsed.hostname, parsed.pathname].filter(Boolean).join("").replace(/^\/?/, "/");
    return `${PULSESOC_ORIGIN}${path === "/" ? "" : path}${parsed.search}${parsed.hash}`;
  }
  if (PULSESOC_HOSTS.has(parsed.hostname)) {
    parsed.protocol = "https:";
    parsed.hostname = "pulsesoc.com";
    return parsed.toString();
  }
  return PULSESOC_ORIGIN;
}

function postToWeb(webView: WebViewType | null, payload: Record<string, unknown>) {
  webView?.injectJavaScript(`
    window.dispatchEvent(new CustomEvent('PulseSocNativeMessage', { detail: ${JSON.stringify(payload)} }));
    true;
  `);
}

function injectPushTokenRegistration(webView: WebViewType | null, token: string) {
  webView?.injectJavaScript(`
    (async function () {
      try {
        const response = await fetch('/api/push/subscribe', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            endpoint: ${JSON.stringify(token)},
            provider: 'expo',
            token: ${JSON.stringify(token)},
            subscription: { expo_push_token: ${JSON.stringify(token)} },
            device_type: 'native_webview'
          })
        });
        window.dispatchEvent(new CustomEvent('PulseSocNativeMessage', { detail: { type: 'PULSESOC_PUSH_RESULT', ok: response.ok } }));
      } catch (error) {
        window.dispatchEvent(new CustomEvent('PulseSocNativeMessage', { detail: { type: 'PULSESOC_PUSH_RESULT', ok: false, message: 'Push registration failed.' } }));
      }
    })();
    true;
  `);
}

function createInjectedBridge() {
  return `
    (function () {
      if (window.PulseSocNative) return true;
      function post(payload) {
        try {
          window.ReactNativeWebView && window.ReactNativeWebView.postMessage(JSON.stringify(payload));
        } catch (error) {}
      }
      window.PulseSocNative = {
        registerPush: function () { post({ type: 'PULSESOC_REGISTER_PUSH' }); },
        share: function (payload) { post(Object.assign({ type: 'PULSESOC_SHARE' }, payload || {})); },
        notify: function (payload) { post(Object.assign({ type: 'PULSESOC_NOTIFY_DEVICE' }, payload || {})); }
      };
      window.dispatchEvent(new CustomEvent('PulseSocNativeReady'));
      true;
    })();
  `;
}

function createSessionCheckScript() {
  return `
    (function () {
      function send(payload) {
        try {
          window.ReactNativeWebView && window.ReactNativeWebView.postMessage(JSON.stringify(payload));
        } catch (error) {}
      }
      try {
        var text = document.body ? document.body.innerText : "";
        var payload = text ? JSON.parse(text) : { authenticated: false };
        send({ authenticated: !!payload.authenticated });
      } catch (error) {
        send({ authenticated: false });
      }
      true;
    })();
  `;
}

const welcomeStyles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.background,
    overflow: "hidden"
  },
  cityGlow: {
    position: "absolute",
    left: -90,
    right: -90,
    bottom: -130,
    height: 320,
    backgroundColor: "#0f2940",
    opacity: 0.64,
    borderTopLeftRadius: 240,
    borderTopRightRadius: 240
  },
  energyGlow: {
    position: "absolute",
    top: 108,
    alignSelf: "center",
    width: 280,
    height: 280,
    borderRadius: 140,
    backgroundColor: "#18f7a4",
    opacity: 0.12
  },
  topBar: {
    minHeight: 58,
    paddingHorizontal: 18,
    paddingTop: 4,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    zIndex: 4
  },
  languageButton: {
    minHeight: 40,
    maxWidth: 190,
    borderWidth: 1,
    borderColor: "rgba(110, 223, 246, 0.46)",
    backgroundColor: "rgba(12, 27, 44, 0.88)",
    borderRadius: 999,
    paddingHorizontal: 14,
    flexDirection: "row",
    alignItems: "center",
    gap: 8
  },
  languageText: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "800"
  },
  languageChevron: {
    color: colors.accentAlt,
    fontSize: 11,
    fontWeight: "900"
  },
  content: {
    flexGrow: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 22,
    paddingTop: 8,
    paddingBottom: 26
  },
  logoStage: {
    width: 210,
    height: 210,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 18
  },
  outerRing: {
    position: "absolute",
    width: 204,
    height: 204,
    borderRadius: 102,
    borderTopWidth: 3,
    borderRightWidth: 2,
    borderBottomWidth: 1,
    borderLeftWidth: 2,
    borderTopColor: colors.accent,
    borderRightColor: colors.accentAlt,
    borderBottomColor: "rgba(54, 229, 143, 0.16)",
    borderLeftColor: "rgba(110, 223, 246, 0.62)"
  },
  logoGlow: {
    position: "absolute",
    width: 154,
    height: 154,
    borderRadius: 77,
    backgroundColor: "#66f4ff",
    opacity: 0.18
  },
  logo: {
    width: 142,
    height: 142,
    borderRadius: 36
  },
  headline: {
    color: colors.text,
    fontSize: 38,
    lineHeight: 44,
    fontWeight: "900",
    textAlign: "center",
    marginBottom: 10
  },
  slogan: {
    color: "#c8d8e4",
    fontSize: 17,
    lineHeight: 25,
    fontWeight: "700",
    textAlign: "center",
    maxWidth: 330,
    marginBottom: 20
  },
  featureStrip: {
    paddingHorizontal: 2,
    paddingBottom: 18,
    gap: 10
  },
  featurePill: {
    width: 136,
    minHeight: 92,
    borderRadius: 18,
    padding: 12,
    backgroundColor: "rgba(15, 31, 51, 0.86)",
    borderWidth: 1,
    borderColor: "rgba(110, 223, 246, 0.28)"
  },
  featureIcon: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(54, 229, 143, 0.16)",
    borderWidth: 1,
    borderColor: "rgba(54, 229, 143, 0.58)",
    marginBottom: 10
  },
  featureIconText: {
    color: colors.accentAlt,
    fontSize: 11,
    fontWeight: "900"
  },
  featureLabel: {
    color: colors.text,
    fontSize: 13,
    lineHeight: 17,
    fontWeight: "800"
  },
  primaryButton: {
    width: "100%",
    maxWidth: 370,
    minHeight: 58,
    borderRadius: 999,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.accent,
    shadowColor: colors.accent,
    shadowOpacity: 0.48,
    shadowRadius: 18,
    shadowOffset: { width: 0, height: 0 },
    elevation: 8,
    marginTop: 2
  },
  primaryButtonText: {
    color: "#04121e",
    fontSize: 16,
    fontWeight: "900",
    textAlign: "center"
  },
  secondaryButton: {
    width: "100%",
    maxWidth: 370,
    minHeight: 54,
    borderRadius: 999,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1.5,
    borderColor: colors.accentAlt,
    backgroundColor: "rgba(10, 21, 34, 0.54)",
    marginTop: 12
  },
  secondaryButtonText: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900",
    textAlign: "center"
  },
  footer: {
    color: "#8fa8b6",
    fontSize: 12,
    fontWeight: "700",
    textAlign: "center",
    marginTop: 24
  },
  modalBackdrop: {
    flex: 1,
    backgroundColor: "rgba(1, 8, 15, 0.58)",
    alignItems: "flex-end",
    paddingTop: 72,
    paddingRight: 16
  },
  languageMenu: {
    width: 220,
    borderRadius: 18,
    backgroundColor: "#0b1728",
    borderWidth: 1,
    borderColor: "rgba(110, 223, 246, 0.42)",
    paddingVertical: 8
  },
  languageOption: {
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 16
  },
  languageOptionText: {
    color: "#c9d7e2",
    fontSize: 15,
    fontWeight: "700"
  },
  languageOptionActive: {
    color: colors.accent
  }
});
